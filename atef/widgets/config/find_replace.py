from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from functools import partial
from pathlib import Path
from typing import (TYPE_CHECKING, Any, ClassVar, Iterable, List, Optional,
                    Tuple, Union)

import happi
import qtawesome as qta
from apischema import ValidationError, serialize
from pcdsutils.qt.callbacks import WeakPartialMethodSlot
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Signal as QSignal

from atef.cache import get_signal_cache
from atef.config import ConfigurationFile, PreparedFile
from atef.find_replace import (FindReplaceAction, MatchFunction,
                               RegexFindReplace, ReplaceFunction,
                               get_deepest_dataclass_in_path,
                               get_default_match_fn, get_default_replace_fn,
                               get_item_from_path, patch_client_cache,
                               simplify_path, walk_find_match)
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.util import get_happi_client
from atef.widgets.config.run_base import create_tree_from_file
from atef.widgets.config.utils import (ConfigTreeModel, TableWidgetWithAddRow,
                                       walk_tree_items)
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import BusyCursorThread, insert_widget

if TYPE_CHECKING:
    from .window import Window

logger = logging.getLogger(__name__)


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
    verified, msg = file.validate()

    if not verified:
        QtWidgets.QMessageBox.warning(
            parent_widget,
            'Verification FAIL',
            'File could not be prepared, edits will not work.\n' + msg
        )
    else:
        QtWidgets.QMessageBox.information(
            parent_widget,
            'Verification PASS',
            'File prepared successfully, edits should work'
        )
    return verified


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

    tree_view: QtWidgets.QTreeView
    device_table: QtWidgets.QTableWidget
    # TODO?: filter by device type?  look at specific device types?
    vert_splitter: QtWidgets.QSplitter
    overview_splitter: QtWidgets.QSplitter
    details_list: QtWidgets.QListWidget
    edits_table: TableWidgetWithAddRow
    edits_table_placeholder: QtWidgets.QWidget
    staged_list: QtWidgets.QListWidget
    # TODO?: smart initialization?  Choosing edits by clicking on devices?
    # TODO?: starting device / string, happi selector for replace?

    stage_all_button: QtWidgets.QPushButton
    open_button: QtWidgets.QPushButton
    top_open_button: QtWidgets.QPushButton
    save_button: QtWidgets.QPushButton
    verify_button: QtWidgets.QPushButton

    busy_thread: Optional[BusyCursorThread]

    data_updated: ClassVar[QtCore.Signal] = QSignal()

    filename = 'fill_template_page.ui'

    def __init__(
        self,
        *args,
        filepath: Optional[str] = None,
        window: Optional[Window] = None,
        allowed_types: Optional[Tuple[Any]] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self._window = window
        self.fp = filepath
        self.orig_file = None
        self.allowed_types = allowed_types
        self.staged_actions: List[FindReplaceAction] = []
        self._signals: List[str] = []
        self._devices: List[str] = []
        self.busy_thread = None
        self._partial_slots: list[WeakPartialMethodSlot] = []
        self.file_name_label.hide()
        self.type_label.hide()
        if filepath:
            self.open_file(filename=filepath)
        self.setup_ui()

    def setup_ui(self) -> None:
        self.open_button.clicked.connect(self.open_file)
        self.top_open_button.clicked.connect(self.open_file)
        self.save_button.clicked.connect(self.save_file)
        self.stage_all_button.clicked.connect(self.stage_all)
        self.verify_button.clicked.connect(self.verify_changes)

        self.overview_splitter.setSizes([375, 375])  # in pixels, a good first shot
        self.vert_splitter.setSizes([200, 200, 200, 200,])

        horiz_header = self.device_table.horizontalHeader()
        horiz_header.setSectionResizeMode(horiz_header.Stretch)

    def setup_edits_table(self) -> None:
        # set up add row widget for edits
        self.edits_table = TableWidgetWithAddRow(
            add_row_text='add edit', title_text='Edits',
            row_widget_cls=partial(TemplateEditRowWidget, orig_file=self.orig_file)
        )
        insert_widget(self.edits_table, self.edits_table_placeholder)

        self.edits_table.setSelectionMode(self.edits_table.SingleSelection)
        self.edits_table.setSelectionBehavior(self.edits_table.SelectRows)
        # connect an update-change-list slot to edits_table.table_updated
        self.edits_table.itemSelectionChanged.connect(self.show_changes_from_edit)
        # refresh details on row interaction as well
        # this might call show_changes_from_edit twice if clicking refresh
        # on a row different from the currently selected one
        self.edits_table.row_interacted.connect(self.show_changes_from_edit)

        # reveal in tree when detail is highlighted.
        # (These may not need to be WPMS but I'll be safe)
        reveal_details_slot = WeakPartialMethodSlot(
            self.details_list, self.details_list.itemSelectionChanged,
            self.reveal_tree_item, self.details_list,
        )
        self._partial_slots.append(reveal_details_slot)

        reveal_staged_slot = WeakPartialMethodSlot(
            self.staged_list, self.staged_list.itemSelectionChanged,
            self.reveal_tree_item, self.staged_list,
        )
        self._partial_slots.append(reveal_staged_slot)

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
            if self.fp is None:
                QtWidgets.QMessageBox.warning(
                    self,
                    'Template Checkout type error',
                    'Loaded checkout is NOT one of the allowed types: '
                    f'{[t.__name__ for t in self.allowed_types]}'
                )
            self.details_list.clear()
            self.staged_list.clear()
            self.staged_actions.clear()
            self.setup_edits_table()
            self.setup_tree_view()
            self.setup_devices_list()
            self.update_title()
            self.vert_splitter.setSizes([200, 200, 200, 200,])
            self.data_updated.emit()

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

        if self.allowed_types and not isinstance(data, self.allowed_types):
            logger.error("loaded checkout is of a disallowed type: "
                         f"({type(data)})")
            self.fp = None
            self.orig_file = None
            return

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

    def setup_tree_view(self) -> None:
        """Populate tree view with preview of loaded file"""
        if self.orig_file is None:
            # clear tree
            self.tree_view.setModel(None)
            return
        root_item = create_tree_from_file(data=self.orig_file)

        model = ConfigTreeModel(data=root_item)

        self.tree_view.setModel(model)
        # Hide the irrelevant status column
        self.tree_view.setColumnHidden(1, True)
        self.tree_view.expandAll()

    def reveal_tree_item(
        self,
        this_list: QtWidgets.QListWidget,
        action: Optional[FindReplaceAction] = None
    ) -> None:
        """Reveal and highlight the tree-item referenced by ``action``"""
        if not action:
            curr_widget = this_list.itemWidget(this_list.currentItem())
            if curr_widget is None:  # selection has likely been removed
                return

            action: FindReplaceAction = curr_widget.data

        model: ConfigTreeModel = self.tree_view.model()

        closest_index = None
        # Gather objects in path, ignoring steps that jump into lists etc
        path_objs = [part[0] for part in action.path if not isinstance(part[0], str)]
        for tree_item in walk_tree_items(model.root_item):
            if tree_item.orig_data in path_objs:
                closest_index = model.index_from_item(tree_item)

        if closest_index:
            self.tree_view.setCurrentIndex(closest_index)
            self.tree_view.scrollTo(closest_index)

    def verify_changes(self) -> None:
        """Apply staged changes and validate copy of file"""
        if self.orig_file is None:
            return
        temp_file = copy.deepcopy(self.orig_file)

        edit_results = [e.apply(target=temp_file) for e in self.staged_actions]
        if not all(edit_results):
            fail_idx = [i for i, result in enumerate(edit_results) if not result]
            QtWidgets.QMessageBox.warning(
                self,
                'Verification FAIL',
                f'Some staged edits at {fail_idx} could not be applied:'
            )
            return

        verify_file_and_notify(temp_file, self)

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

    def prompt_apply(self) -> None:
        # message box with details on remaining changes
        if len(self.staged_actions) <= 0:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            'Apply staged edits?',
            (
                'Would you like to apply the remaining '
                f'({len(self.staged_actions)}) staged edits?'
            )
        )
        if reply == QtWidgets.QMessageBox.Yes:
            for action in self.staged_actions:
                action.apply()

            # clear all rows
            self.edits_table.clearContents()
            self.details_list.clear()

    def update_title(self) -> None:
        """
        Update the title.  Will be the name and the number of staged edits
        """
        if self.fp is None:
            self.type_label.hide()
            self.file_name_label.hide()
            self.top_open_button.show()
            return

        self.type_label.show()
        self.file_name_label.show()
        self.top_open_button.hide()

        file_name = os.path.basename(self.fp)
        if len(self.staged_actions) > 0:
            file_name += f'[{len(self.staged_actions)}]'
        self.file_name_label.setText(file_name)
        self.type_label.setText(type(self.orig_file).__name__)

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

            # Disconnect existing apply slot, replace with stage slot
            row_widget.button_box.accepted.disconnect(row_widget.apply_action)
            stage_slot = WeakPartialMethodSlot(
                row_widget, row_widget.button_box.accepted,
                self.stage_item_from_details, row_widget.data, l_item,
            )
            self._partial_slots.append(stage_slot)

            # reveal tree when deails selected
            reveal_slot = WeakPartialMethodSlot(
                row_widget, row_widget.details_button.pressed,
                self.reveal_tree_item, self.details_list, action=row_widget.data
            )
            self._partial_slots.append(reveal_slot)

    def remove_item_from_details(self, item: QtWidgets.QListWidgetItem) -> None:
        """remove an item from the details list"""
        self.details_list.takeItem(self.details_list.row(item))

    def remove_item_from_staged(self, item: QtWidgets.QListWidgetItem) -> None:
        """remove an item from the staged list, GUI and internal"""
        data = self.staged_list.itemWidget(item).data
        self.staged_actions.remove(data)
        self.staged_list.takeItem(self.staged_list.row(item))
        self.update_title()
        self.data_updated.emit()

    def stage_item_from_details(
        self,
        data: FindReplaceAction,
        item: QtWidgets.QListWidgetItem
    ) -> None:
        """stage an item from the details list"""
        if any([data.same_path(action.path) for action in self.staged_actions]):
            QtWidgets.QMessageBox.information(
                self,
                'Duplicate Edit Not Staged',
                'Edit was not staged, had a path matching an already staged path'
            )
            return
        # Add data to staged list
        self.stage_edit(data)
        self.refresh_staged_table()
        self.remove_item_from_details(item)

    def stage_all(self) -> None:
        """Move actions from edit details to staged_actions and refresh table"""
        for _ in range(self.details_list.count()):
            l_item = self.details_list.item(0)
            widget = self.details_list.itemWidget(l_item)
            if widget is None:
                return  # no details loaded, simple help text item

            data = widget.data
            self.stage_item_from_details(data, item=l_item)

    def stage_edit(self, edit: FindReplaceAction) -> None:
        """Add ``edit`` to the staging list, do nothing to the GUI"""
        self.staged_actions.append(edit)

    def refresh_staged_table(self) -> None:
        """Re-populate staged edits table"""
        self.staged_list.clear()
        for action in self.staged_actions:
            l_item = QtWidgets.QListWidgetItem()
            row_widget = FindReplaceRow(data=action)
            l_item.setSizeHint(QtCore.QSize(row_widget.width(), row_widget.height()))
            self.staged_list.addItem(l_item)
            self.staged_list.setItemWidget(l_item, row_widget)

            remove_slot = WeakPartialMethodSlot(
                row_widget, row_widget.remove_item,
                self.remove_item_from_staged, l_item
            )
            self._partial_slots.append(remove_slot)

            # reveal tree when deails selected
            reveal_slot = WeakPartialMethodSlot(
                row_widget, row_widget.details_button.pressed,
                self.reveal_tree_item, self.staged_list, action=row_widget.data
            )
            self._partial_slots.append(reveal_slot)

            # Hide ok button
            ok_button = row_widget.button_box.button(QtWidgets.QDialogButtonBox.Ok)
            row_widget.button_box.removeButton(ok_button)

        self.update_title()
        self.data_updated.emit()


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
            origin_action = RegexFindReplace(
                path=simplify_path(path),
                search_regex=self._search_regex.pattern,
                replace_text=self.replace_edit.text(),
                case_sensitive=self.case_button.isChecked(),
            )
            action = FindReplaceAction(target=self.orig_file, path=path,
                                       replace_fn=self._replace_fn,
                                       origin=origin_action)

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
