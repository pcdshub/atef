"""
Helper QObject classes for managing dataclass instances.

Contains utilities for synchronizing dataclass instances between
widgets.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple, Type

from qtpy import QtCore
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
    bridge.field.value_changed.connect(my_slot)
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
        for field in dataclasses.fields(data):
            # Need to figure out which category this is:
            # 1. Primitive value -> make a QDataclassValue
            # 2. Another dataclass -> make a QDataclassValue (object)
            # 3. A list of values -> make a QDataclassList
            # 4. A list of dataclasses -> QDataclassList (object)
            normalized = normalize_annotation(field.type)
            if List in normalized:
                NestedClass = QDataclassList
            else:
                NestedClass = QDataclassValue
            setattr(
                self,
                field.name,
                NestedClass.of_type(normalized[-1])(
                    data,
                    field.name,
                    parent=self,
                ),
            )


normalize_map = {
    'Optional': Optional,
    'List': List,
    'Number': float,
    'int': int,
    'str': str,
    'bool': bool,
    'Configuration': object,
    'IdentifierAndComparison': object,
    'Comparison': object,
    'Severity': int,
    'reduce.ReduceMethod': str,
    'PrimitiveType': object,
    'Sequence': List,
    'Value': object,
}


def normalize_annotation(annotation: str) -> Tuple[type]:
    """
    Change a string annotation into a tuple of the enclosing classes.

    For example: "Optional[List[SomeClass]]" becomes
    (Optional, List, object)

    Somewhat incomplete- only correct to the level needed for this
    application.

    Only supports the cases where we have exactly one element in each
    square bracket nesting level.

    There is definitely a better way to handle this, but I can't
    figure it out quickly and want to press forward to v0.
    """
    elems = []
    for text in annotation.strip(']').split('['):
        elems.append(normalize_map[text])
    return tuple(elems)


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
    def of_type(cls, data_type: type) -> Type[QDataclassValue]:
        """
        Create a QDataclass with a specific QSignal

        Parameters
        ----------
        data_type : any primitive type
        """
        try:
            return cls._registry[data_type]
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
        cls._registry[data_type] = new_class
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
    def of_type(cls, data_type: type) -> Type[QDataclassList]:
        """
        Create a QDataclass with a specific QSignal

        Parameters
        ----------
        data_type : any primitive type
        """
        try:
            return cls._registry[data_type]
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
                'changed_value': QSignal(data_type),
                'changed_index': QSignal(int),
            },
        )
        cls._registry[data_type] = new_class
        return new_class

    def get(self) -> List[Any]:
        """
        Return the current list of values.
        """
        return getattr(self.data, self.attr)

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


class ThreadPoolWorker(QtCore.QRunnable):
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

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    @QtCore.Slot()
    def run(self):
        try:
            self.func(*self.args, **self.kwargs)
        except Exception:
            logger.exception(
                "Failed to run %s(*%s, **%r) in thread pool",
                self.func,
                self.args,
                self.kwargs,
            )

    @classmethod
    def new_thread(cls, func: Callable, *args, start=True, **kwargs) -> ThreadPoolWorker:
        """Start a utility thread in the global QThreadPool."""
        worker = ThreadPoolWorker(func, *args, **kwargs)
        if start:
            QtCore.QThreadPool.globalInstance().start(worker)
        return worker
