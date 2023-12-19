from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from typing import (TYPE_CHECKING, Any, Callable, ClassVar, Generator,
                    Iterable, List, Optional, Tuple, Union, get_args)

import happi
import qtawesome as qta
from apischema import ValidationError, serialize
from pcdsutils.qt.callbacks import WeakPartialMethodSlot
from qtpy import QtCore, QtWidgets

from atef.cache import DataCache, get_signal_cache
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.type_hints import PrimitiveType
from atef.util import get_happi_client
from atef.widgets.config.utils import TableWidgetWithAddRow
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import BusyCursorThread, insert_widget

if TYPE_CHECKING:
    from .window import Window

logger = logging.getLogger(__name__)

ReplaceFunction = Callable[[Any], Any]
MatchFunction = Callable[[Any], bool]


@contextmanager
def patch_client_cache():
    old_happi_cache = happi.loader.cache
    try:
        happi.loader.cache = {}
        dcache = DataCache()
        dcache.signals.clear()
        yield
    finally:
        happi.loader.cache = old_happi_cache
        dcache.signals.clear()


def walk_find_match(
    item: Any,
    match: Callable,
    parent: List[Tuple[Any, Any]] = []
) -> Generator[List[Tuple[Any, Any]], None, None]:
    """
    Walk the dataclass and find every key / field where ``match`` evaluates to True.

    Yields a list of 'paths' to the matching key / field. A path is a list of
    (object, field) tuples that lead from the top level ``item`` to the matching
    key / field.
    - If the object is a dataclass, ``field`` will be a field in that dataclass
    - If the object is a list, ``field`` will be the index in that list
    - If the object is a dict, ``field`` will be a key in that dictionary

    ``match`` should be a Callable taking a single argument and returning a boolean,
    specifying whether that argument matched a search term or not.  This is
    commonly a simple lambda wrapping an equality or regex search.

    Ex:
    paths = walk_find_match(ConfigFile, lambda x: x == 5)
    paths = walk_find_match(ConfigFile, lambda x: re.compile('^warning$').search(x) is not None)

    Parameters
    ----------
    item : Any
        the item to search in.  A dataclass at the top level, but can also be a
        list or dict
    match : Callable
        a function that takes a single argument and returns a boolean
    parent : List[Tuple[Union[str, int], Any]], optional
        the 'path' traveled to arive at ``item`` at this point, by default []
        (used internally)

    Yields
    ------
    List[Tuple[Any, Any]]
        paths leading to keys or fields where ``match`` is True
    """
    if is_dataclass(item):
        # get fields, recurse through fields
        for field in fields(item):
            yield from walk_find_match(getattr(item, field.name), match,
                                       parent=parent + [(item, field.name)])
    elif isinstance(item, list):
        for idx, l_item in enumerate(item):
            # TODO: py3.10 allows isinstance with Unions
            if isinstance(l_item, get_args(PrimitiveType)) and match(l_item):
                yield parent + [('__list__', idx)]
            else:
                yield from walk_find_match(l_item, match,
                                           parent=parent + [('__list__', idx)])
    elif isinstance(item, dict):
        for d_key, d_value in item.items():
            # don't halt at first key match, values could also have matches
            if isinstance(d_value, get_args(PrimitiveType)) and match(d_value):
                yield parent + [('__dictvalue__', d_key)]
            else:
                yield from walk_find_match(d_value, match,
                                           parent=parent + [('__dictvalue__', d_key)])
            if match(d_key):
                yield parent + [('__dictkey__', d_key)]

    elif isinstance(item, Enum):
        if match(item.name):
            yield parent + [('__enum__', item)]

    elif match(item):
        yield parent


def get_deepest_dataclass_in_path(
    path: List[Tuple[Any, Any]],
    item: Optional[Any] = None
) -> Tuple[Any, str]:
    """
    Grab the deepest dataclass in the path, and return its segment

    Parameters
    ----------
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    item : Any
        An object to start the path from

    Returns
    -------
    Tuple[AnyDataclass, str]
        The deepest dataclass, and field name for the next step
    """
    rev_idx = -1
    while rev_idx > (-len(path) - 1):
        if is_dataclass(path[rev_idx][0]):
            break
        else:
            rev_idx -= 1
    if item:
        return get_item_from_path(path[:rev_idx], item), path[rev_idx][1]

    return path[rev_idx]


def get_item_from_path(
    path: List[Tuple[Any, Any]],
    item: Optional[Any] = None
) -> Any:
    """
    Get the item the path points to.  This can work for any subpath

    If ``item`` is not provided, use the stashed objects in ``path``.
    Item is expected to be top-level object, if provided.
    (i.e. analagous to path[0][0]).

    Parameters
    ----------
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    item : Optional[Any], optional
        the item of interest to explore, by default None

    Returns
    -------
    Any
        the object at the end of ``path``, starting from ``item``
    """
    if not item:
        item = path[0][0]
    for seg in path:
        if seg[0] == '__dictkey__':
            item = seg[1]
        elif seg[0] == '__dictvalue__':
            item = item[seg[1]]
        elif seg[0] == '__list__':
            item = item[seg[1]]
        elif seg[0] == '__enum__':
            item = item.name
        else:
            # general dataclass case
            item = getattr(item, seg[1])
    return item


def replace_item_from_path(
    item: Any,
    path: List[Tuple[Any, Any]],
    replace_fn: ReplaceFunction
) -> None:
    """
    replace some object in ``item`` located at the end of ``path``, according
    to ``replace_fn``.

    ``replace_fn`` should take the original value, and return the new value
    for insertion into ``item``.  This function frequently involves string
    substitution, and possibly type conversions

    Parameters
    ----------
    item : Any
        The object to replace information in
    path : List[Tuple[Any, Any]]
        A "path" to a search match, as returned by walk_find_match
    replace_fn : ReplaceFunction
        A function that returns the replacement object
    """
    # need the final step to specify what is being replaced
    final_step = path[-1]
    # need the item one step before the last to perform the assignment on
    parent_item = get_item_from_path(path[:-1], item=item)

    if final_step[0] == "__dictkey__":
        parent_item[replace_fn(final_step[1])] = parent_item.pop(final_step[1])
    elif final_step[0] in ("__dictvalue__", "__list__"):
        # replace value
        old_value = parent_item[final_step[1]]
        parent_item[final_step[1]] = replace_fn(old_value)
    elif final_step[0] == "__enum__":
        parent_item = get_item_from_path(path[:-2], item=item)
        old_enum: Enum = getattr(parent_item, path[-2][1])
        new_enum = getattr(final_step[1], replace_fn(old_enum.name))
        setattr(parent_item, path[-2][1], new_enum)
    else:
        # simple field paths don't have a final (__sth__, ?) segment
        old_value = getattr(parent_item, path[-1][1])
        setattr(parent_item, path[-1][1], replace_fn(old_value))


def get_default_match_fn(search_regex: re.Pattern) -> MatchFunction:
    """
    Returns a standard match function using the provided regex pattern

    Parameters
    ----------
    search_regex : re.Pattern
        compiled regex pattern to match items against

    Returns
    -------
    MatchFunction
        a match function to be used in ``walk_find_match``
    """
    def match_fn(match):
        return search_regex.search(str(match)) is not None

    return match_fn


def get_default_replace_fn(
    replace_text: str,
    search_regex: re.Pattern
) -> ReplaceFunction:
    """
    Returns a standard replace function, which attempts to match the type of the
    item being replaced

    Parameters
    ----------
    replace_text : str
        text to replace
    search_regex : re.Pattern
        the compiled regex search pattern, for use in string replacements

    Returns
    -------
    ReplaceFunction
        a replacement function for use in ``replace_item_from_path``
    """
    def replace_fn(value):
        if isinstance(value, str):
            return search_regex.sub(replace_text, value)
        elif isinstance(value, int):
            # cast to float first
            return int(float(value))
        else:  # try to cast as original type
            return type(value)(replace_text)

    return replace_fn


def verify_file_and_notify(
    file: Union[ConfigurationFile, ProcedureFile],
    parent_widget: QtWidgets.QWidget
) -> bool:
    """
    Verify the provided file is valid by attempting to prepare it.
    Requires a parent QWidget to spawn QMessageBox notices from.

    Parameters
    ----------
    file : Union[ConfigurationFile, ProcedureFile]
        the file to verify
    parent_widget : QtWidgets.QWidget
        Parent widget to bind the QMessageBox to

    Returns
    -------
    bool
        the verification success
    """
    verified = True
    try:
        if isinstance(file, ConfigurationFile):
            prep_file = PreparedFile.from_config(file)
            if len(prep_file.root.prepare_failures) > 0:
                verified = False
        elif isinstance(file, ProcedureFile):
            # clear all results when making a new run tree
            prep_file = PreparedProcedureFile.from_origin(file)
            if len(prep_file.root.prepare_failures) > 0:
                verified = False
        else:
            QtWidgets.QMessageBox.warning(
                parent_widget,
                'Verification FAIL',
                'File type not recognized.'
            )
            return False
    except Exception as ex:
        logger.debug(ex)
        QtWidgets.QMessageBox.warning(
            parent_widget,
            'Verification FAIL',
            f'Unknown Error: {ex}.'
        )
        return False

    if not verified:
        QtWidgets.QMessageBox.warning(
            parent_widget,
            'Verification FAIL',
            'File could not be prepared successfully, edits will not work'
        )
    else:
        QtWidgets.QMessageBox.information(
            parent_widget,
            'Verification PASS',
            'File prepared successfully, edits should work'
        )
    return verified


@dataclass
class FindReplaceAction:
    target: Union[ConfigurationFile, ProcedureFile]
    path: List[Tuple[Any, Any]]
    replace_fn: ReplaceFunction

    def apply(
        self,
        target: Optional[Union[ConfigurationFile, ProcedureFile]] = None,
        path: Optional[List[Tuple[Any, Any]]] = None,
        replace_fn: Optional[ReplaceFunction] = None
    ) -> bool:
        """
        Apply the find-replace action, return True if action was applied
        successfully.

        Can specify any of ``target``, ``path``, or ``replace_fn`` in order
        to use that object instead of the stored object

        Parameters
        ----------
        target : Optional[Union[ConfigurationFile, ProcedureFile]], optional
            The file to apply the find-replace action to, by default this applies
            to the current target of the action, by default None
        path : Optional[List[Tuple[Any, Any]]], optional
            A "path" to a search match, as returned by walk_find_match,
            by default None
        replace_fn : Optional[ReplaceFunction], optional
            A function that takes the value and returns the replaced value,
            by default None

        Returns
        -------
        bool
            the success of the apply action
        """

        target = target or self.target
        path = path or self.path
        replace_fn = replace_fn or self.replace_fn
        try:
            replace_item_from_path(target, path, replace_fn)
        except KeyError as ex:
            logger.warning(f'Unable to find key ({ex}) in file. '
                           'File may have already been edited')
            return False
        except Exception as ex:
            logger.warning(f'Unable to apply change. {ex}')
            return False

        return True


class FindReplaceWidget(DesignerDisplay, QtWidgets.QWidget):

    search_edit: QtWidgets.QLineEdit
    replace_edit: QtWidgets.QLineEdit

    case_button: QtWidgets.QToolButton
    regex_button: QtWidgets.QToolButton

    preview_button: QtWidgets.QPushButton
    verify_button: QtWidgets.QPushButton
    save_button: QtWidgets.QPushButton
    open_input_file_button: QtWidgets.QPushButton
    open_output_file_button: QtWidgets.QPushButton
    open_converted_button: QtWidgets.QPushButton

    change_list: QtWidgets.QListWidget

    filename = 'find_replace_widget.ui'

    def __init__(
        self,
        *args,
        filepath: Optional[str] = None,
        window: Optional[Window] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fp = filepath
        self._window = window
        self.match_paths: Iterable[List[Any]] = []
        self.orig_file = None
        self._partial_slots: list[WeakPartialMethodSlot] = []

        if not filepath:
            self.open_converted_button.hide()
        else:
            self.open_file(filename=filepath)

        if window:
            self.open_converted_button.clicked.connect(self.open_converted)
        else:
            self.open_converted_button.hide()

        self.setup_buttons()
        self.replace_edit.editingFinished.connect(self.update_replace_fn)
        self.search_edit.editingFinished.connect(self.update_match_fn)
        # placeholder no-op functions
        self._match_fn: MatchFunction = lambda x: False
        self._replace_fn: ReplaceFunction = lambda x: x

    def verify_changes(self) -> None:
        verify_file_and_notify(self.orig_file, self)

    def setup_buttons(self) -> None:
        self.open_input_file_button.clicked.connect(self.open_file)
        self.open_output_file_button.clicked.connect(self.open_out_file)
        self.preview_button.clicked.connect(self.preview_changes)
        self.verify_button.clicked.connect(self.verify_changes)
        self.save_button.clicked.connect(self.save_file)

    def open_file(self, *args, filename: Optional[str] = None, **kwargs) -> None:
        if filename is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a config',
                filter='Json Files (*.json)',
            )
        if not filename:
            return

        self.fp = filename
        self.orig_file = self.load_file(filename)
        short_fp = f'{Path(self.fp).parent.name}/{Path(self.fp).name}'
        self.open_input_file_button.setText(f'Input File: {short_fp}')
        self.open_input_file_button.setToolTip(str(self.fp))
        self.setWindowTitle(f'find and replace: ({os.path.basename(filename)})')

    def load_file(self, filepath) -> Union[ConfigurationFile, ProcedureFile]:
        try:
            data = ConfigurationFile.from_filename(filepath)
        except ValidationError:
            logger.debug('failed to open as passive checkout')
            try:
                data = ProcedureFile.from_filename(filepath)
            except ValidationError:
                logger.error('failed to open file as either active '
                             'or passive checkout')
                raise ValueError('Could not open the file as either active or '
                                 'passive checkout.')

        return data

    def open_out_file(self, *args, **kwargs) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption='Select an output file',
            filter='Json Files (*.json)',
        )
        if not filename:
            return

        self.output_fp = filename
        short_fp = f'{Path(self.output_fp).parent.name}/{Path(self.output_fp).name}'
        self.open_output_file_button.setText(f'Output File: {short_fp}')
        self.open_output_file_button.setToolTip(str(filename))

    def save_file(self) -> None:
        if not self.output_fp:
            logger.error('No ouptut file provided')
            return

        # get serialized
        serialized = serialize(type(self.orig_file), self.orig_file)
        try:
            with open(self.output_fp, 'w') as fd:
                json.dump(serialized, fd, indent=2)
                # Ends file on newline as per pre-commit
                fd.write('\n')
        except OSError:
            logger.exception(f'Error saving file {self.fp}')
            return

        QtWidgets.QMessageBox.information(
            self,
            'File Saved',
            f'File saved successfully to {self.output_fp}'
        )

    def update_replace_fn(self, *args, **kwargs) -> None:
        """
        Update the stored replacement function using the text in ``self.replace_edit``
        """
        replace_text = self.replace_edit.text()
        replace_fn = get_default_replace_fn(replace_text, self._search_regex)
        self._replace_fn = replace_fn

    def update_match_fn(self, *args, **kwargs) -> None:
        """
        Update the stored match function using the text in ``self.search_edit``
        Reads the settings buttons (match case, use_regex) to properly compile
        the regex search pattern
        """
        search_text = self.search_edit.text()

        flags = re.IGNORECASE if not self.case_button.isChecked() else 0
        use_regex = self.regex_button.isChecked()

        if use_regex:
            self._search_regex = re.compile(f'{search_text}', flags=flags)
        else:
            # exact match
            self._search_regex = re.compile(f'{re.escape(search_text)}', flags=flags)

        match_fn = get_default_match_fn(self._search_regex)
        self._match_fn = match_fn

    def _remove_item_from_change_list(self, list_item, *args, **kwargs):
        self.change_list.takeItem(self.change_list.row(list_item))

    def accept_change(self, list_item, *args, **kwargs):
        # make sure this only runs if action was successful
        self._remove_item_from_change_list(list_item)

    def preview_changes(self, *args, **kwargs) -> None:
        """
        Update the change list according to the provided find and replace settings
        """
        if not self.search_edit.text():
            return  # don't allow searching everything
        self.update_match_fn()
        self.update_replace_fn()

        self.change_list.clear()
        self.match_paths = list(walk_find_match(self.orig_file, self._match_fn))

        # generator can be unstable if dataclass changes during walk
        # this is only ok because we consume generator entirely
        for path in self.match_paths:
            find_replace_action = FindReplaceAction(target=self.orig_file,
                                                    path=path,
                                                    replace_fn=self._replace_fn)
            row_widget = FindReplaceRow(data=find_replace_action)

            l_item = QtWidgets.QListWidgetItem()
            l_item.setSizeHint(QtCore.QSize(row_widget.width(), row_widget.height()))
            self.change_list.addItem(l_item)
            self.change_list.setItemWidget(l_item, row_widget)

            accept_slot = WeakPartialMethodSlot(
                row_widget.button_box, row_widget.button_box.accepted,
                self.accept_change, l_item
            )
            self._partial_slots.append(accept_slot)
            reject_slot = WeakPartialMethodSlot(
                row_widget.button_box, row_widget.button_box.rejected,
                self._remove_item_from_change_list, l_item
            )
            self._partial_slots.append(reject_slot)

    def open_converted(self, *args, **kwargs) -> None:
        """open new file in new tab"""
        if self._window is not None:
            self._window._new_tab(data=self.orig_file, filename=self.fp)


class FindReplaceRow(DesignerDisplay, QtWidgets.QWidget):
    """A widget for displaying a single find/replace action"""
    button_box: QtWidgets.QDialogButtonBox
    dclass_label: QtWidgets.QLabel
    pre_label: QtWidgets.QLabel
    post_label: QtWidgets.QLabel
    details_button: QtWidgets.QToolButton

    remove_item: ClassVar[QtCore.Signal] = QtCore.Signal()

    filename = 'find_replace_row_widget.ui'

    def __init__(
        self,
        *args,
        data: FindReplaceAction,
        **kwargs
    ) -> None:
        super().__init__(*args, *kwargs)
        self.data = data

        last_dclass, attr = get_deepest_dataclass_in_path(self.data.path)
        dclass_type = type(last_dclass).__name__
        self.dclass_label.setText(f'{dclass_type}.{attr}')

        pre_text = str(get_item_from_path(self.data.path, item=self.data.target))
        # html wrapping to get some line wrapping
        self.pre_label.setText(f'<font>{pre_text}</font>')
        self.pre_label.setToolTip(f'<font>{pre_text}</font>')
        preview_file = copy.deepcopy(self.data.target)

        preview_success = data.apply(target=preview_file)
        if preview_success:
            post_text = str(get_item_from_path(self.data.path, item=preview_file))
        else:
            logger.warning('Unable to generate preview, provided replacement '
                           'text is invalid')
            post_text = '[INVALID]'
        self.post_label.setText(f'<font>{post_text}</font>')
        self.post_label.setToolTip(f'<font>{post_text}</font>')

        ok_button = self.button_box.button(QtWidgets.QDialogButtonBox.Ok)
        ok_button.setText('')
        ok_button.setIcon(qta.icon('ei.check'))
        delete_button = self.button_box.button(QtWidgets.QDialogButtonBox.Cancel)
        delete_button.setText('')
        delete_icon = self.style().standardIcon(
            QtWidgets.QStyle.SP_TitleBarCloseButton
        )
        delete_button.setIcon(delete_icon)

        # path details
        path_list = []
        for segment in self.data.path:
            if isinstance(segment[0], str):
                name = segment[0]
            else:
                name = type(segment[0]).__name__
            path_list.append(f'({name}, {segment[1]})')

        path_str = '>'.join(path_list)
        detail_widget = QtWidgets.QLabel(path_str + '\n')
        detail_widget.setWordWrap(True)

        widget_action = QtWidgets.QWidgetAction(self.details_button)
        widget_action.setDefaultWidget(detail_widget)

        widget_menu = QtWidgets.QMenu(self.details_button)
        widget_menu.addAction(widget_action)
        self.details_button.setMenu(widget_menu)

        self.button_box.accepted.connect(self.apply_action)
        self.button_box.rejected.connect(self.reject_action)

    def apply_action(self) -> None:
        success = self.data.apply()
        if not success:
            QtWidgets.QMessageBox.warning(
                self,
                'Edit was not applied successfully, and will be removed'
            )
        self.remove_item.emit()

    def reject_action(self) -> None:
        self.remove_item.emit()


class FillTemplatePage(DesignerDisplay, QtWidgets.QWidget):

    file_name_label: QtWidgets.QLabel
    type_label: QtWidgets.QLabel

    device_table: QtWidgets.QTableWidget
    # TODO?: filter by device type?  look at specific device types?
    vert_splitter: QtWidgets.QSplitter
    horiz_splitter: QtWidgets.QSplitter
    details_list: QtWidgets.QListWidget
    edits_table: TableWidgetWithAddRow
    edits_table_placeholder: QtWidgets.QWidget
    # TODO?: smart initialization?  Choosing edits by clicking on devices?
    # TODO?: starting device / string, happi selector for replace?

    apply_all_button: QtWidgets.QPushButton
    open_button: QtWidgets.QPushButton
    save_button: QtWidgets.QPushButton
    verify_button: QtWidgets.QPushButton

    busy_thread: Optional[BusyCursorThread]

    filename = 'fill_template_page.ui'

    def __init__(
        self,
        *args,
        filepath: Optional[str] = None,
        window: Optional[Window] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self._window = window
        self.fp = filepath
        self.all_actions: List[FindReplaceAction] = []
        self._signals: List[str] = []
        self._devices: List[str] = []
        self.busy_thread = None
        self._partial_slots: list[WeakPartialMethodSlot] = []
        if filepath:
            self.open_file(filename=filepath)
        self.setup_ui()

    def setup_ui(self) -> None:
        self.open_button.clicked.connect(self.open_file)
        self.save_button.clicked.connect(self.save_file)
        self.apply_all_button.clicked.connect(self.apply_all)
        self.verify_button.clicked.connect(self.verify_changes)

        self.horiz_splitter.setSizes([375, 375])  # in pixels, a good first shot
        self.vert_splitter.setSizes([175, 375])

        horiz_header = self.device_table.horizontalHeader()
        horiz_header.setSectionResizeMode(horiz_header.Stretch)

    def setup_edits_table(self) -> None:
        # set up add row widget for edits
        self.edits_table = TableWidgetWithAddRow(
            add_row_text='add edit', title_text='edits',
            row_widget_cls=partial(TemplateEditRowWidget, orig_file=self.orig_file)
        )
        insert_widget(self.edits_table, self.edits_table_placeholder)
        self.edits_table.table_updated.connect(
            self.update_change_list
        )

        self.edits_table.setSelectionMode(self.edits_table.SingleSelection)
        self.edits_table.setSelectionBehavior(self.edits_table.SelectRows)
        # connect an update-change-list slot to edits_table.table_updated
        self.edits_table.itemSelectionChanged.connect(self.show_changes_from_edit)
        # refresh details on row interaction as well
        # this might call show_changes_from_edit twice if clicking refresh
        # on a row different from the currently selected one
        self.edits_table.row_interacted.connect(self.show_changes_from_edit)

    def open_file(self, *args, filename: Optional[str] = None, **kwargs) -> None:
        if filename is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a config',
                filter='Json Files (*.json)',
            )
        if not filename:
            return

        def finish_setup():
            self.details_list.clear()
            self.setup_edits_table()
            self.setup_devices_list()
            self.update_title()

        self.busy_thread = BusyCursorThread(
            func=partial(self.load_file, filepath=filename)
        )
        self.busy_thread.task_finished.connect(finish_setup)
        self.busy_thread.start()

    def load_file(self, filepath: str) -> None:
        try:
            data = ConfigurationFile.from_filename(filepath)
        except ValidationError:
            logger.debug('failed to open as passive checkout')
            try:
                data = ProcedureFile.from_filename(filepath)
            except ValidationError:
                logger.error('failed to open file as either active '
                             'or passive checkout')

        self.fp = filepath
        self.orig_file = data

    def setup_devices_list(self) -> None:
        """
        Set up the devices list.  Runs the preparation and cache inspection
        in a BusyCursorThread, and fills the devices list after its completion
        """
        self.busy_thread = BusyCursorThread(func=self._get_devices_in_file)
        self.busy_thread.task_finished.connect(self._fill_devices_list)
        self.busy_thread.start()

    def _get_devices_in_file(self) -> None:
        """
        Gather devices and signal in the original file by preparing it and
        inspecting caches.  Temporarily clears the happi loader cache, but
        actually clears the DataCache
        """
        with patch_client_cache():
            client = get_happi_client()
            if isinstance(self.orig_file, ConfigurationFile):
                prep_file = PreparedFile.from_config(self.orig_file, client=client)
                asyncio.run(prep_file.fill_cache())
            elif isinstance(self.orig_file, ProcedureFile):
                prep_file = PreparedProcedureFile.from_origin(self.orig_file)

            cache = get_signal_cache()
            self._signals = list(cache.keys())
            self._devices = list(happi.loader.cache.keys())

    def _fill_devices_list(self) -> None:
        self.device_table.setRowCount(max(len(self._signals), len(self._devices)))
        for i, sig in enumerate(self._signals):
            self.device_table.setItem(i, 1, QtWidgets.QTableWidgetItem(sig))
        for i, dev in enumerate(self._devices):
            self.device_table.setItem(i, 0, QtWidgets.QTableWidgetItem(dev))

    def verify_changes(self) -> None:
        if self.orig_file is not None:
            verify_file_and_notify(self.orig_file, self)

    def save_file(self) -> None:
        if self.orig_file is None:
            return
        self.prompt_apply()
        verified = verify_file_and_notify(self.orig_file, self)

        if not verified:
            reply = QtWidgets.QMessageBox.question(
                self,
                'Continue Saving?',
                'Verification failed, save anyway?'
            )

            if reply == QtWidgets.QMessageBox.No:
                return

        # open save message box
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption='Save as',
            filter='Json Files (*.json)',
        )
        if not filename:
            return
        if not filename.endswith('.json'):
            filename += '.json'

        # get serialized
        serialized = serialize(type(self.orig_file), self.orig_file)
        try:
            with open(filename, 'w') as fd:
                json.dump(serialized, fd, indent=2)
                # Ends file on newline as per pre-commit
                fd.write('\n')
        except OSError:
            logger.exception(f'Error saving file {filename}')
            return

        # inform about saving
        if self._window:
            reply = QtWidgets.QMessageBox.question(
                self,
                'File saved',
                'File saved successfully, would you like to open this file?'
            )

            if reply == QtWidgets.QMessageBox.Yes:
                self._window._new_tab(data=self.orig_file, filename=filename)
        else:
            QtWidgets.QMessageBox.information(
                self,
                'File saved',
                'File saved successfully'
            )

    def apply_all(self) -> None:
        self.prompt_apply()
        self.update_title()

    def prompt_apply(self) -> None:
        # message box with details on remaining changes
        self.update_change_list()
        if len(self.all_actions) <= 0:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            'Apply remaning edits?',
            (
                'Would you like to apply the remaining '
                f'({len(self.all_actions)}) edits?'
            )
        )
        if reply == QtWidgets.QMessageBox.Yes:
            for action in self.all_actions:
                action.apply()

            # clear all rows
            self.edits_table.clearContents()
            self.details_list.clear()

    def update_title(self) -> None:
        """
        Update the title.  Will be the name and the number of unapplied edits
        """
        if self.fp is None:
            return

        file_name = os.path.basename(self.fp)
        if len(self.all_actions) > 0:
            file_name += f'[{len(self.all_actions)}]'
        self.file_name_label.setText(file_name)
        self.type_label.setText(type(self.orig_file).__name__)

    def update_change_list(self) -> None:
        """
        update the global change list, gathering all ``FindReplaceAction``'s
        """
        # walk through edits_table, gather list of list of paths
        self.all_actions = []
        for row_idx in range(self.edits_table.rowCount()):
            template_widget = self.edits_table.cellWidget(row_idx, 0)
            self.all_actions.extend(template_widget.get_actions())

        self.update_title()

    def show_changes_from_edit(self, *args, **kwargs) -> None:
        """
        Populate the details_list with each action from the selected edit.
        Provide helpful text if the edit is incomplete or invalid.
        """
        self.details_list.clear()
        # on selected callback, populate details table
        selected_ranges = self.edits_table.selectedRanges()
        if not selected_ranges:
            return

        edit_row_widget: TemplateEditRowWidget = self.edits_table.cellWidget(
            selected_ranges[0].topRow(), 0
        )

        if not isinstance(edit_row_widget, TemplateEditRowWidget):
            # placeholder text if nothing is selected
            l_item = QtWidgets.QListWidgetItem("Select an edit or click an edit's "
                                               "refresh button to show details.")
            self.details_list.addItem(l_item)
            return
        elif not edit_row_widget.get_details_rows():
            l_item = QtWidgets.QListWidgetItem(
                'Provide search and replace text to show details.\n'
                'If nothing appears after clicking the refresh button,\nthere'
                'are no matches.'
            )
            self.details_list.addItem(l_item)
            return

        row_widget: FindReplaceRow
        for row_widget in edit_row_widget.get_details_rows():
            l_item = QtWidgets.QListWidgetItem()
            l_item.setSizeHint(QtCore.QSize(row_widget.width(), row_widget.height()))
            self.details_list.addItem(l_item)
            self.details_list.setItemWidget(l_item, row_widget)

            remove_slot = WeakPartialMethodSlot(
                row_widget, row_widget.remove_item,
                self.remove_item_from_details, l_item
            )
            self._partial_slots.append(remove_slot)

    def remove_item_from_details(self, item: QtWidgets.QListWidgetItem) -> None:
        """remove an item from the details list"""
        self.details_list.takeItem(self.details_list.row(item))


class TemplateEditRowWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    A widget for specifying the information for find/replace actions

    Each edit can correspond to multiple ``FindReplaceAction``'s
    Generates ``FindReplaceRow``'s for each ``FindReplaceAction``
    """
    button_box: QtWidgets.QDialogButtonBox
    child_button: QtWidgets.QPushButton

    setting_button: QtWidgets.QToolButton
    regex_button: QtWidgets.QToolButton
    case_button: QtWidgets.QToolButton

    search_edit: QtWidgets.QLineEdit
    replace_edit: QtWidgets.QLineEdit

    filename = 'template_edit_row_widget.ui'

    def __init__(
        self,
        *args,
        data=None,
        orig_file: Union[ConfigurationFile, ProcedureFile],
        **kwargs
    ) -> None:
        # Expected SimpleRowWidgets are DataWidgets, expecting a dataclass
        super().__init__(*args, **kwargs)
        self.orig_file = orig_file
        self.match_paths: Iterable[List[Any]] = []
        self.actions: List[FindReplaceAction] = []
        self._match_fn: MatchFunction = lambda x: False
        self._replace_fn: ReplaceFunction = lambda x: x
        self._partial_slots: list[WeakPartialMethodSlot] = []
        self.setup_ui()

    def setup_ui(self):
        self.child_button.hide()
        refresh_button = self.button_box.button(QtWidgets.QDialogButtonBox.Retry)
        refresh_button.clicked.connect(self.refresh_paths)
        refresh_button.setText('')
        refresh_button.setToolTip('refresh edit details')
        refresh_button.setIcon(qta.icon('ei.refresh'))

        apply_button = self.button_box.button(QtWidgets.QDialogButtonBox.Apply)
        apply_button.clicked.connect(self.apply_edits)
        apply_button.setText('')
        apply_button.setToolTip('apply all changes')
        apply_button.setIcon(qta.icon('ei.check'))

        # settings menu (regex, case)
        self.setting_widget = QtWidgets.QWidget()
        self.setting_layout = QtWidgets.QHBoxLayout()
        self.regex_button = QtWidgets.QToolButton()
        self.regex_button.setCheckable(True)
        self.regex_button.setText('.*')
        self.regex_button.setToolTip('use regex')
        self.case_button = QtWidgets.QToolButton()
        self.case_button.setCheckable(True)
        self.case_button.setText('Aa')
        self.case_button.setToolTip('case sensitive')
        self.setting_layout.addWidget(self.regex_button)
        self.setting_layout.addWidget(self.case_button)
        self.setting_widget.setLayout(self.setting_layout)
        widget_action = QtWidgets.QWidgetAction(self.setting_button)
        widget_action.setDefaultWidget(self.setting_widget)

        widget_menu = QtWidgets.QMenu(self.setting_button)
        widget_menu.addAction(widget_action)
        self.setting_button.setMenu(widget_menu)
        self.setting_button.setIcon(qta.icon('fa.gear'))

    def update_replace_fn(self, *args, **kwargs) -> None:
        """Update the standard replace function for this edit"""
        replace_text = self.replace_edit.text()
        replace_fn = get_default_replace_fn(replace_text, self._search_regex)
        self._replace_fn = replace_fn

    def update_match_fn(self, *args, **kwargs) -> None:
        """Update the standard match function for this edit"""
        search_text = self.search_edit.text()

        flags = re.IGNORECASE if not self.case_button.isChecked() else 0
        use_regex = self.regex_button.isChecked()

        if use_regex:
            self._search_regex = re.compile(f'{search_text}', flags=flags)
        else:
            # exact match
            self._search_regex = re.compile(f'{re.escape(search_text)}',
                                            flags=flags)

        match_fn = get_default_match_fn(self._search_regex)
        self._match_fn = match_fn

    def apply_edits(self) -> None:
        """Apply all the actions corresponding to this edit"""
        self.refresh_paths()
        if len(self.actions) <= 0:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            'Apply remaning edits?',
            (
                'Would you like to apply the remaining '
                f'({len(self.actions)}) edits?'
            )
        )
        if reply == QtWidgets.QMessageBox.Yes:
            for action in self.actions:
                success = action.apply()
                if not success:
                    logger.warning(f'action failed {action}')

            self.actions.clear()

        self.refresh_paths()

    def refresh_paths(self) -> None:
        """
        Refresh the paths generated by the edit and create FindReplaceActions
        """
        if self.orig_file is None:
            return
        if not self.search_edit.text():
            return

        self.update_match_fn()
        self.update_replace_fn()

        self.actions.clear()
        self.match_paths = list(walk_find_match(self.orig_file, self._match_fn))
        # generator can be unstable if dataclass changes during walk
        # this is only ok because we consume generator entirely
        for path in self.match_paths:
            action = FindReplaceAction(target=self.orig_file, path=path,
                                       replace_fn=self._replace_fn)

            self.actions.append(action)

        # this works, but is smelly and bad.  Access parent table signals
        try:
            # TERW -> TableWidgetItem -> TableWidgetWithAddRow
            self.parent().parent().table_updated.emit()
            self.parent().parent().row_interacted.emit(self.row_num)
        except AttributeError:
            ...

    def get_details_rows(self) -> List[FindReplaceRow]:
        """return a ``FindReplaceRow`` for each action generated by the edit"""
        details_row_widgets = []
        for action in self.actions:
            row_widget = FindReplaceRow(data=action)

            remove_slot = WeakPartialMethodSlot(
                row_widget, row_widget.remove_item,
                self.remove_from_action_list, action=action
            )
            self._partial_slots.append(remove_slot)
            details_row_widgets.append(row_widget)

        return details_row_widgets

    def remove_from_action_list(
        self, *args, action: Optional[FindReplaceAction] = None, **kwargs
    ) -> None:
        """
        Helper to remove an action from the action list after application

        Parameters
        ----------
        action : Optional[FindReplaceAction], optional
            the action to be removed, by default None
        """
        try:
            self.actions.remove(action)
        except ValueError:
            return

    def get_actions(self) -> List[FindReplaceAction]:
        return self.actions
