"""
Helper QObject classes for managing dataclass instances.

Contains utilities for synchronizing dataclass instances between
widgets.
"""
from __future__ import annotations

import functools
import logging
import platform
from collections.abc import Sequence
from typing import (Any, Callable, ClassVar, Dict, Generator, List, Optional,
                    Tuple, Type, Union, get_args, get_origin, get_type_hints)

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QObject
from qtpy.QtCore import Signal as QSignal

logger = logging.getLogger(__name__)


class QDataclassBridge(QObject):
    """
    Convenience structure for managing a dataclass along with qt.

    Once created, you can navigate this object much like it was the
    dataclass. For example:

    @dataclass
    def my_class:
        field: int
        others: list[OtherClass]

    Would allow you to access:
    bridge.field.put(3)
    bridge.field.changed_value.connect(my_slot)
    bridge.others.append(OtherClass(4))

    This does not recursively dive down the tree of subdataclasses.
    For these, we need to make multiple bridges.

    Parameters
    ----------
    data : any dataclass
        The dataclass we want to bridge to
    """
    data: Any

    def __init__(self, data: Any, parent: Optional[QObject] = None):
        super().__init__(parent=parent)
        self.data = data
        fields = get_type_hints(type(data))
        for name, type_hint in fields.items():
            self.set_field_from_data(name, type_hint, data)

    def set_field_from_data(
        self,
        name: str,
        type_hint: Any,
        data: Any,
        optional: bool = False
    ):
        """
        Set a field for this bridge based on the data and its type

        Parameters
        ----------
        name : str
            name of the field
        type_hint : Any
            The type hint annotation, returned from typing.get_type_hints
        data : any
            The dataclass for this bridge
        """
        # Need to figure out which category this is:
        # 1. Primitive value -> make a QDataclassValue
        # 2. Another dataclass -> make a QDataclassValue (object)
        # 3. A list of values -> make a QDataclassList
        # 4. A list of dataclasses -> QDataclassList (object)
        origin = get_origin(type_hint)
        args = get_args(type_hint)

        if not origin:
            # a raw type, no Union, Optional, etc
            NestedClass = QDataclassValue
            dtype = type_hint
        elif origin is dict:
            # Use dataclass value and override to object type
            NestedClass = QDataclassValue
            dtype = object
        elif origin in (list, Sequence):
            # Make sure we have list manipulation methods
            # Sequence resolved as from collections.abc (even if defined from typing)
            NestedClass = QDataclassList
            dtype = args[0]
        elif (origin is Union) and (type(None) in args):
            # Optional, need to allow NoneType to be emitted by changed_value signal
            if len(args) > 2:
                # Optional + many other types, dispatch to complex Union case
                self.set_field_from_data(name, args[:-1], data, optional=True)
            else:
                self.set_field_from_data(name, args[0], data, optional=True)
            return
        else:
            # some complex Union? e.g. Union[str, int, bool, float]
            logger.debug(f'Complex type hint found: {type_hint} - ({origin}, {args})')
            NestedClass = QDataclassValue
            dtype = object

        # handle more complex datatype annotations
        if dtype not in (int, float, bool, str):
            dtype = object

        setattr(
            self,
            name,
            NestedClass.of_type(dtype, optional=optional)(
                data,
                name,
                parent=self,
            ),
        )


class QDataclassElem:
    """
    Base class for elements of the QDataclassBridge

    Parameters
    ----------
    data : any dataclass
        The data we want to access and update
    attr : str
        The dataclass attribute to connect to
    """
    data: Any
    attr: str
    updated: QSignal
    _registry: ClassVar[Dict[str, type]]

    def __init__(
        self,
        data: Any,
        attr: str,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent=parent)
        self.data = data
        self.attr = attr


class QDataclassValue(QDataclassElem):
    """
    A single value in the QDataclassBridge.
    """
    changed_value: QSignal

    _registry = {}

    @classmethod
    def of_type(
        cls,
        data_type: type,
        optional: bool = False
    ) -> Type[QDataclassValue]:
        """
        Create a QDataclass with a specific QSignal

        Parameters
        ----------
        data_type : any primitive type
        optional : bool
            if the value is optional, True if ``None`` is a valid value
        """
        if optional:
            data_type = object

        try:
            return cls._registry[(data_type, optional)]
        except KeyError:
            ...

        new_class = type(
            f'QDataclassValueFor{data_type.__name__}',
            (cls, QObject),
            {
                'updated': QSignal(),
                'changed_value': QSignal(data_type),
            },
        )
        cls._registry[(data_type, optional)] = new_class
        return new_class

    def get(self) -> Any:
        """
        Return the current value.
        """
        return getattr(self.data, self.attr)

    def put(self, value: Any):
        """
        Change a value on the dataclass and update consumers.

        Parameters
        ----------
        value : any primitive type
        """
        setattr(self.data, self.attr, value)
        self.changed_value.emit(self.get())
        self.updated.emit()


class QDataclassList(QDataclassElem):
    """
    A list of values in the QDataclassBridge.
    """
    added_value: QSignal
    added_index: QSignal
    removed_value: QSignal
    removed_index: QSignal
    changed_value: QSignal
    changed_index: QSignal

    _registry = {}

    @classmethod
    def of_type(
        cls,
        data_type: type,
        optional: bool = False
    ) -> Type[QDataclassList]:
        """
        Create a QDataclass with a specific QSignal

        Parameters
        ----------
        data_type : any primitive type
        optional : bool
            if the value is optional, True if ``None`` is a valid value
        """
        if optional:
            changed_value_type = object
        else:
            changed_value_type = data_type

        try:
            return cls._registry[(data_type, optional)]
        except KeyError:
            ...

        new_class = type(
            f'QDataclassListFor{data_type.__name__}',
            (cls, QObject),
            {
                'updated': QSignal(),
                'added_value': QSignal(data_type),
                'added_index': QSignal(int),
                'removed_value': QSignal(data_type),
                'removed_index': QSignal(int),
                'changed_value': QSignal(changed_value_type),
                'changed_index': QSignal(int),
            },
        )
        cls._registry[(data_type, optional)] = new_class
        return new_class

    def get(self) -> List[Any]:
        """
        Return the current list of values.
        """
        return getattr(self.data, self.attr)

    def put(self, values: List[Any]) -> None:
        """
        Replace the current list of values.
        """
        setattr(self.data, self.attr, values)
        self.updated.emit()

    def append(self, new_value: Any) -> None:
        """
        Add a new value to the end of the list and update consumers.
        """
        data_list = self.get()
        if data_list is None:
            data_list = []
            setattr(self.data, self.attr, data_list)
        data_list.append(new_value)
        self.added_value.emit(new_value)
        self.added_index.emit(len(data_list) - 1)
        self.updated.emit()

    def remove_value(self, removal: Any) -> None:
        """
        Remove a value from the list by value and update consumers.
        """
        index = self.get().index(removal)
        self.get().remove(removal)
        self.removed_value.emit(removal)
        self.removed_index.emit(index)
        self.updated.emit()

    def remove_index(self, index: int) -> None:
        """
        Remove a value from the list by index and update consumers.
        """
        value = self.get().pop(index)
        self.removed_value.emit(value)
        self.removed_index.emit(index)
        self.updated.emit()

    def put_to_index(self, index: int, new_value: Any) -> None:
        """
        Change a value in the list and update consumers.
        """
        self.get()[index] = new_value
        self.changed_value.emit(new_value)
        self.changed_index.emit(index)
        self.updated.emit()


class ThreadWorker(QtCore.QThread):
    """
    Worker thread helper.  For running a function in a background QThread.

    Parameters
    ----------
    func : callable
        The function to call when the thread starts.
    *args
        Arguments for the function call.
    **kwargs
        Keyword arguments for the function call.
    """

    error_raised = QtCore.Signal(Exception)
    returned = QtCore.Signal(object)
    func: Callable
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    return_value: Any

    def __init__(self, func: Callable, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.return_value = None

    @QtCore.Slot()
    def run(self):
        try:
            self.return_value = self.func(*self.args, **self.kwargs)
        except Exception as ex:
            logger.exception(
                "Failed to run %s(*%s, **%r) in thread pool",
                self.func,
                self.args,
                self.kwargs,
            )
            self.return_value = ex
            self.error_raised.emit(ex)
        else:
            self.returned.emit(self.return_value)


def run_in_gui_thread(func: Callable, *args, _start_delay_ms: int = 0, **kwargs):
    """Run the provided function in the GUI thread."""
    QtCore.QTimer.singleShot(_start_delay_ms, functools.partial(func, *args, **kwargs))


def get_clipboard() -> Optional[QtGui.QClipboard]:
    """Get the clipboard instance. Requires a QApplication."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None

    return QtWidgets.QApplication.clipboard()


def get_clipboard_modes() -> List[int]:
    """Get the clipboard modes for the current platform."""
    clipboard = get_clipboard()
    if clipboard is None:
        return []

    if platform.system() == "Linux":
        # Mode selection is only valid for X11.
        return [
            QtGui.QClipboard.Selection,
            QtGui.QClipboard.Clipboard
        ]

    return [QtGui.QClipboard.Clipboard]


def copy_to_clipboard(text, *, quiet: bool = False):
    """Copy ``text`` to the clipboard."""
    clipboard = get_clipboard()
    if clipboard is None:
        return None

    for mode in get_clipboard_modes():
        clipboard.setText(text, mode=mode)
        event = QtCore.QEvent(QtCore.QEvent.Clipboard)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.sendEvent(clipboard, event)

    if not quiet:
        logger.warning(
            (
                "Copied text to clipboard:\n"
                "-------------------------\n"
                "%s\n"
                "-------------------------\n"
            ),
            text
        )


def get_clipboard_text() -> str:
    """Get ``text`` from the clipboard. If unavailable or unset, empty string."""
    clipboard = get_clipboard()
    if clipboard is None:
        return ""
    for mode in get_clipboard_modes():
        text = clipboard.text(mode=mode)
        if text:
            return text
    return ""


def walk_tree_widget_items(
    tree_widget: QtWidgets.QTreeWidget
) -> Generator[Any, None, None]:
    """
    Walk a ``QtWidgets.QTreeWidget``'s tree items.  Steps through items depht-first

    Parameters
    ----------
    tree_widget : QtWidgets.QTreeWidget
        tree widget to walk through.

    Yields
    ------
    Generator[Any, None, None]
        each item in the QTreeWidget
    """
    # this is not a pythonic iterator, treat it differently
    iter = QtWidgets.QTreeWidgetItemIterator(tree_widget)

    while iter.value():
        yield iter.value()
        iter += 1
