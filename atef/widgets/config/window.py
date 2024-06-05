"""
Top-level widgets that contain all the other widgets.
"""
from __future__ import annotations

import json
import logging
import os
import os.path
import traceback
import webbrowser
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from pprint import pprint
from typing import ClassVar, Dict, Generator, Optional

import qtawesome
from apischema import ValidationError, deserialize, serialize
from pcdsutils.qt.callbacks import WeakPartialMethodSlot
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Qt, QTimer
from qtpy.QtCore import Signal as QSignal
from qtpy.QtWidgets import (QAction, QFileDialog, QMainWindow, QMessageBox,
                            QTabWidget, QWidget)

from atef.cache import DataCache
from atef.config import ConfigurationFile, PreparedFile, TemplateConfiguration
from atef.exceptions import PreparationError
from atef.procedure import (DescriptionStep, PassiveStep,
                            PreparedProcedureFile, ProcedureFile, SetValueStep,
                            TemplateStep)
from atef.report import ActiveAtefReport, PassiveAtefReport
from atef.type_hints import AnyDataclass
from atef.walk import get_prepared_step, get_relevant_configs_comps
from atef.widgets.config.find_replace import (FillTemplatePage,
                                              FindReplaceWidget)
from atef.widgets.utils import reset_cursor, set_wait_cursor

from ..archive_viewer import get_archive_viewer
from ..core import DesignerDisplay
from .page import PAGE_MAP, FailPage, PageWidget, RunConfigPage, RunStepPage
from .result_summary import ResultsSummaryWidget
from .run_base import create_tree_from_file, make_run_page
from .utils import (ConfigTreeModel, MultiInputDialog, Toggle, TreeItem,
                    walk_tree_items)

logger = logging.getLogger(__name__)

TEST_CONFIG_PATH = Path(__file__).parent.parent.parent / 'tests' / 'configs'


class Window(DesignerDisplay, QMainWindow):
    """
    Main atef config window

    Has a tab widget for editing multiple files at once, and contains
    the menu bar for facilitating saving/loading.
    """
    filename = 'config_window.ui'
    user_default_filename = 'untitled'
    user_filename_ext = 'json'

    tab_widget: QTabWidget
    action_welcome_tab: QAction
    action_new_file: QAction
    action_open_file: QAction
    action_save: QAction
    action_save_as: QAction
    action_print_dataclass: QAction
    action_print_serialized: QAction
    action_open_archive_viewer: QAction
    action_print_report: QAction
    action_clear_results: QAction
    action_find_replace: QAction

    def __init__(
        self,
        *args,
        cache_size: int = 5,
        show_welcome: bool = True,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._partial_slots: list[WeakPartialMethodSlot] = []
        self.cache_size = cache_size
        self.setWindowTitle('atef config')
        self.action_welcome_tab.triggered.connect(self.welcome_user)
        self.action_new_file.triggered.connect(self.new_file)
        self.action_open_file.triggered.connect(self.open_file)
        self.action_save.triggered.connect(self.save)
        self.action_save_as.triggered.connect(self.save_as)
        self.action_print_dataclass.triggered.connect(self.print_dataclass)
        self.action_print_serialized.triggered.connect(self.print_serialized)
        self.action_open_archive_viewer.triggered.connect(
            self.open_archive_viewer
        )
        self.action_print_report.triggered.connect(self.print_report)
        self.action_clear_results.triggered.connect(self.clear_results)
        self.action_find_replace.triggered.connect(self.find_replace)

        tab_bar = self.tab_widget.tabBar()
        # always use scroll area and never truncate file names
        tab_bar.setUsesScrollButtons(True)
        tab_bar.setElideMode(Qt.ElideNone)
        # ensure tabbar close button on right, run toggle will be on left
        tab_bar.setStyleSheet(
            "QTabBar::close-button { subcontrol-position: right}"
        )

        self.tab_widget.tabCloseRequested.connect(
            self.tab_widget.removeTab
        )

        if show_welcome:
            QTimer.singleShot(0, self.welcome_user)

    def welcome_user(self):
        """
        On open, ask the user what they'd like to do (new config? load?)

        Set up the landing page
        """
        widget = LandingPage()
        self.tab_widget.addTab(widget, 'Welcome to Atef!')
        curr_idx = self.tab_widget.count() - 1
        self.tab_widget.setCurrentIndex(curr_idx)

        new_passive_slot = WeakPartialMethodSlot(
            widget.new_passive_button, widget.new_passive_button.clicked,
            self.new_file, checkout_type="passive"
        )
        self._partial_slots.append(new_passive_slot)
        new_active_slot = WeakPartialMethodSlot(
            widget.new_active_button, widget.new_active_button.clicked,
            self.new_file, checkout_type="active"
        )
        self._partial_slots.append(new_active_slot)

        widget.fill_template_button.clicked.connect(
            self.open_fill_template
        )

        sample_active_slot = WeakPartialMethodSlot(
            widget.sample_active_button, widget.sample_active_button.clicked,
            self.open_file, filename=str(TEST_CONFIG_PATH / 'active_test.json')
        )
        self._partial_slots.append(sample_active_slot)

        sample_passive_slot = WeakPartialMethodSlot(
            widget.sample_passive_button, widget.sample_passive_button.clicked,
            self.open_file, filename=str(TEST_CONFIG_PATH / 'all_fields.json')
        )
        self._partial_slots.append(sample_passive_slot)

        widget.open_button.clicked.connect(self.open_file)
        widget.exit_button.clicked.connect(self.close_all)

    def close_all(self):
        qapp = QtWidgets.QApplication.instance()
        qapp.closeAllWindows()

    def _passive_or_active(self) -> str:
        """
        Prompt user to select a passive or active checkout
        """
        choice_box = QMessageBox(self)
        choice_box.setIcon(QMessageBox.Question)
        choice_box.setWindowTitle('Select a checkout type...')
        choice_box.setText('Would you like a passive or active checkout?')
        choice_box.setDetailedText(
            'Passive checkouts: involves comparing current to expected values.\n'
            'Active checkouts: involves setting values or moving motors.\n'
            'Note that passive checkouts can be run as a step in an active checkout'
        )
        passive = choice_box.addButton('Passive', QMessageBox.AcceptRole)
        active = choice_box.addButton('Active', QMessageBox.AcceptRole)
        choice_box.addButton(QMessageBox.Close)
        choice_box.exec()
        if choice_box.clickedButton() == passive:
            return 'passive'
        elif choice_box.clickedButton() == active:
            return 'active'

    def get_tab_name(self, filename: Optional[str] = None):
        """
        Get a standardized tab name from a filename.
        """
        if filename is None:
            filename = self.user_default_filename
        filename = str(filename)
        if '.' not in filename:
            filename = '.'.join((filename, self.user_filename_ext))
        return os.path.basename(filename)

    def set_current_tab_name(self, filename: str):
        """
        Set the title of the current tab based on the filename.
        """
        self.tab_widget.setTabText(
            self.tab_widget.currentIndex(),
            self.get_tab_name(filename),
        )

    def get_current_tree(self) -> DualTree:
        """
        Return the DualTree widget for the current open tab.
        """
        return self.tab_widget.currentWidget()

    def new_file(self, *args, checkout_type: Optional[str] = None, **kwargs):
        """
        Create and populate a new edit tab.

        The parameters are open as to accept inputs from any signal.
        """
        # prompt user to select checkout type
        if checkout_type is None:
            checkout_type = self._passive_or_active()
        if checkout_type == 'passive':
            data = ConfigurationFile()
        elif checkout_type == 'active':
            data = ProcedureFile()
        else:
            return
        self._new_tab(data=data)

    def open_file(self, *args, filename: Optional[str] = None, **kwargs):
        """
        Open an existing file and create a new tab containing it.

        The parameters are open as to accept inputs from any signal.

        Parameters
        ----------
        filename : str, optional
            The name to save the file as. If omitted, a dialog will
            appear to prompt the user for a filepath.
        """
        if filename is None:
            filename, _ = QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a config',
                filter='Json Files (*.json)',
            )
        if not filename:
            return
        with open(filename, 'r') as fd:
            serialized = json.load(fd)

        # TODO: Consider adding submenus for user to choose
        try:
            data = deserialize(ConfigurationFile, serialized)
        except ValidationError:
            logger.debug('failed to open as passive checkout')
            try:
                data = deserialize(ProcedureFile, serialized)
            except ValidationError:
                logger.error('failed to open file as either active '
                             'or passive checkout')
                msg = QtWidgets.QMessageBox(parent=self)
                msg.setIcon(QtWidgets.QMessageBox.Critical)
                msg.setText('Failed to open file as either an active or passive '
                            'checkout.  The file may be corrupted or malformed.')
                msg.setWindowTitle('Could not open file')
                msg.exec_()
                return
        self._new_tab(data=data, filename=filename)

    def _new_tab(
        self,
        data: ConfigurationFile | ProcedureFile,
        filename: Optional[str] = None,
    ) -> None:
        """
        Open a new tab, setting up the tree widgets and run toggle.

        Parameters
        ----------
        data : ConfigurationFile or ProcedureFile
            The data to populate the widgets with. This is typically
            loaded from a file but does not need to be.
        filename : str, optional
            The full path to the file the data was opened from, if
            applicable. This lets us keep track of which filename to
            save back to.
        """
        widget = DualTree(orig_file=data, full_path=filename,
                          widget_cache_size=self.cache_size)
        self.tab_widget.addTab(widget, self.get_tab_name(filename))
        curr_idx = self.tab_widget.count() - 1
        self.tab_widget.setCurrentIndex(curr_idx)
        # set up edit-run toggle
        tab_bar = self.tab_widget.tabBar()
        widget.toggle.stateChanged.connect(widget.switch_mode)
        tab_bar.setTabButton(curr_idx, QtWidgets.QTabBar.LeftSide, widget.toggle)

    def save(self, *args, **kwargs):
        """
        Save the currently selected tab to the last used filename.

        Reverts back to save_as if no such filename exists.

        The parameters are open as to accept inputs from any signal.
        """
        current_tree = self.get_current_tree()
        self.save_as(filename=current_tree.full_path)

    def save_as(self, *args, filename: Optional[str] = None, **kwargs):
        """
        Save the currently selected tab, to a specific filename.

        The parameters are open as to accept inputs from any signal.

        Parameters
        ----------
        filename : str, optional
            The name to save the file as. If omitted, a dialog will
            appear to prompt the user for a filepath.
        """
        current_tree = self.get_current_tree()
        serialized = self.serialize_tree(current_tree)
        if serialized is None:
            return
        if filename is None:
            filename, _ = QFileDialog.getSaveFileName(
                parent=self,
                caption='Save as',
                filter='Json Files (*.json)',
            )
        if not filename.endswith('.json'):
            filename += '.json'
        try:
            with open(filename, 'w') as fd:
                json.dump(serialized, fd, indent=2)
                # Ends file on newline as per pre-commit
                fd.write('\n')
        except OSError:
            logger.exception(f'Error saving file {filename}')
        else:
            self.set_current_tab_name(filename)
            current_tree.full_path = filename

    def serialize_tree(self, tree: DualTree) -> dict:
        """
        Return the serialized data from a DualTree widget.
        """
        try:
            return serialize(type(tree.orig_file), tree.orig_file)
        except Exception:
            logger.exception('Error serializing file')

    def print_dataclass(self, *args, **kwargs):
        """
        Print the dataclass of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.get_current_tree().orig_file)

    def print_serialized(self, *args, **kwargs):
        """
        Print the serialized data structure of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.serialize_tree(self.get_current_tree()))

    def open_archive_viewer(self, *args, **kwargs):
        """Open the archive viewer"""
        widget = get_archive_viewer()
        widget.show()

    def print_report(self, *args, **kwargs):
        """Open save dialog for report output"""
        run_tree: DualTree = self.tab_widget.currentWidget()
        run_tree.print_report()

    def clear_results(self, *args, **kwargs):
        """clear results for the active file"""
        current_tree: DualTree = self.get_current_tree()
        config_file = current_tree.orig_file
        # ask for confirmation first
        reply = QMessageBox.question(
            self,
            'Confirm deletion',
            (
                'Are you sure you want to clear the results of the checkout: '
                f'{config_file.root.name}?'
            ),
        )
        if reply != QMessageBox.Yes:
            return

        orig_data = current_tree.current_item.orig_data
        current_tree.refresh_model()
        current_tree.select_by_data(orig_data)
        current_tree.update_statuses()

    def find_replace(self, *args, **kwargs):
        """find and replace in the current tree.  Open a find-replace widget"""
        try:
            curr_tree = self.get_current_tree()
            logger.debug(f'starting find-replace: {self.get_current_tree().full_path}')
        except AttributeError:
            curr_tree = None

        if curr_tree:
            self._find_widget = FindReplaceWidget(
                filepath=curr_tree.full_path, window=self
            )
        else:
            self._find_widget = FindReplaceWidget()
        self._find_widget.show()

    def open_fill_template(self, *args, **kwargs):
        """
        Open a fill-template page.
        """
        widget = FillTemplatePage(window=self)
        self.tab_widget.addTab(widget, 'fill template')
        curr_idx = self.tab_widget.count() - 1
        self.tab_widget.setCurrentIndex(curr_idx)


class LandingPage(DesignerDisplay, QWidget):
    """Landing Page for selecting a subsequent action"""
    filename = 'landing_page.ui'

    new_passive_button: QtWidgets.QPushButton
    new_active_button: QtWidgets.QPushButton
    fill_template_button: QtWidgets.QPushButton
    open_button: QtWidgets.QPushButton
    exit_button: QtWidgets.QPushButton

    sample_passive_button: QtWidgets.QPushButton
    sample_active_button: QtWidgets.QPushButton
    docs_button: QtWidgets.QPushButton
    source_button: QtWidgets.QPushButton

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_ui()

    def setup_ui(self):
        # icons for buttons
        self.open_button.setIcon(qtawesome.icon('fa.folder-open-o'))
        self.exit_button.setIcon(qtawesome.icon('fa.close'))
        self.new_passive_button.setIcon(qtawesome.icon('fa.file-text-o'))
        self.new_active_button.setIcon(qtawesome.icon('fa.file-code-o'))
        # links for buttons... maybe
        self.docs_button.clicked.connect(
            lambda: webbrowser.open('https://confluence.slac.stanford.edu/display/PCDS/atef')
        )
        self.source_button.clicked.connect(
            lambda: webbrowser.open('https://github.com/pcdshub/atef/tree/master')
        )


EDIT_TO_RUN_PAGE: Dict[type, PageWidget] = {
    TemplateConfiguration: RunConfigPage,
    DescriptionStep: RunStepPage,
    PassiveStep: RunStepPage,
    SetValueStep: RunStepPage,
    TemplateStep: RunStepPage,
}


class DualTree(DesignerDisplay, QWidget):
    """
    A widget that exposes one of two tree widgets depending on the mode
    """
    filename = 'dual_config_tree.ui'

    tree_view: QtWidgets.QTreeView
    splitter: QtWidgets.QSplitter
    last_selection: Optional[TreeItem]

    print_report_button: QtWidgets.QPushButton
    results_button: QtWidgets.QPushButton

    mode_switch_finished: ClassVar[QSignal] = QSignal()

    built_widgets: OrderedDict

    def __init__(
        self,
        *args,
        orig_file: ConfigurationFile | ProcedureFile,
        full_path: Optional[str] = None,
        widget_cache_size: int = 5,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.main_layout = QtWidgets.QHBoxLayout()
        self.edit_widget_cache: OrderedDict[TreeItem, QWidget] = OrderedDict()
        self.run_widget_cache: OrderedDict[TreeItem, QWidget] = OrderedDict()
        self.caches: Dict[str, OrderedDict[TreeItem, QWidget]] = {
            'edit': self.edit_widget_cache,
            'run': self.run_widget_cache
        }
        self.max_cache_size = widget_cache_size
        self.current_widget: QWidget = None
        self.orig_file = orig_file
        self.prepared_file = None
        self.full_path = full_path
        self.root_item = TreeItem()
        self._item_list: list[TreeItem] = []
        self.mode = 'edit'

        # basic tree setup, start in edit mode
        self.assemble_tree()

        # Connect other buttons
        self.print_report_button.clicked.connect(self.print_report)
        self._summary_widget: Optional[ResultsSummaryWidget] = None
        self.results_button.clicked.connect(self.show_results_summary)

        # store serialized edit
        serialized_edit_config = serialize(type(self.orig_file), self.orig_file)
        self.last_edit_config = deepcopy(serialized_edit_config)
        self._orig_config = deepcopy(self.last_edit_config)

        self.toggle = Toggle()

    def assemble_tree(self) -> None:
        """init-time tree setup.  Sets the tree into edit mode"""
        # self.tree_view = QtWidgets.QTreeView()
        self.refresh_model()
        self.tree_view.resizeColumnToContents(1)
        self.tree_view.header().swapSections(0, 1)
        # starting in edit mode, hide statuses
        self.tree_view.setColumnHidden(1, True)
        self.tree_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tree_view.expandAll()

        self.print_report_button.hide()
        self.results_button.hide()

    def refresh_prepared_file(self) -> None:
        """
        Refreshes the stored Prepared file (passive or active).
        Alone, this does not update the TreeView or ConfigTreeModel.
        """
        if isinstance(self.orig_file, ConfigurationFile):
            self.prepared_file = PreparedFile.from_config(self.orig_file,
                                                          cache=DataCache())
        if isinstance(self.orig_file, ProcedureFile):
            self.prepared_file = PreparedProcedureFile.from_origin(self.orig_file)

    def refresh_model(self) -> None:
        """
        Rebuild the model, refreshing the Prepared file primarily
        """
        # TODO: Make sure prepared file is attached at every tree item modification
        self.refresh_prepared_file()

        # Clear widget caches
        for cache in (self.edit_widget_cache, self.run_widget_cache):
            for _ in range(len(cache)):
                cache.popitem()

        self.root_item = create_tree_from_file(
            data=self.orig_file,
            prepared_file=self.prepared_file
        )
        self._item_list = list(walk_tree_items(self.root_item))
        self.model = ConfigTreeModel(data=self.root_item)
        self.tree_view.setModel(self.model)
        self.model.beginResetModel()
        self.model.endResetModel()
        self.tree_view.expandAll()

        # selection model tied to data model, need to re-connect on refresh
        self.tree_view.selectionModel().selectionChanged.connect(
            self.show_selected_display
        )

        # select top level root
        self.tree_view.setCurrentIndex(self.model.index(0, 0, QtCore.QModelIndex()))

    def select_by_item(self, item: TreeItem) -> None:
        """Select desired TreeItem(and show corresponding page) in TreeView"""
        # check if item is in tree before selecting?
        logger.debug(f'selecting page for item: {item.data(0)}')
        new_index = self.model.index_from_item(item)
        self.tree_view.setCurrentIndex(new_index)

    def select_by_data(self, data: AnyDataclass) -> None:
        """Select the TreeItem containing ``data``"""
        for item in walk_tree_items(self.root_item):
            if item.orig_data is data:
                self.select_by_item(item)
                return

    @property
    def current_item(self) -> Optional[TreeItem]:
        """return the currently selected item"""
        sel_model = self.tree_view.selectionModel()
        try:
            curr_index = sel_model.selectedIndexes()[0]
        except IndexError:
            return None
        return self.model.data(curr_index, Qt.UserRole)

    def show_selected_display(
        self,
        selected: QtCore.QItemSelection,
        previous: QtCore.QItemSelection
    ) -> None:
        """Show selected widget, construct it if necesary"""
        # TODO: show busy cursor?
        try:
            current = selected.indexes()[0]
        except IndexError:
            logger.debug('no selection made')
            return
        item: TreeItem = self.tree_view.model().data(current, Qt.UserRole)
        logger.debug(f'showing selected display: {item.data(0)}')
        self.show_page_for_data(item, mode=self.mode)

    def show_page_for_data(self, item: TreeItem, mode: str = 'edit') -> None:
        """
        Show PageWidget corresponding to ``data`` in ``mode``.
        If the item's widget is cached, retrieve and show it.  Otherwise the
        widget must be created.  Schedules oldest widget in cache for deletion
        if the cache is full.
        """
        curr_cache = self.caches[mode]
        oldest_widget = None
        if item in curr_cache:
            new_widget = curr_cache[item]

        else:
            # not in cache, need to build the widget.
            new_widget = self.create_widget(item, mode)

            curr_cache[item] = new_widget

        # TODO: make sure current widget is never None later on
        if (self.current_widget is None) or (self.splitter.widget(1) is None):
            self.splitter.addWidget(new_widget)
        else:
            self.current_widget.setVisible(False)
            self.splitter.replaceWidget(1, new_widget)

        self.current_widget = new_widget
        self.current_widget.setVisible(True)
        logger.debug(f'setting widget ({self.current_widget}) visible')

        # remove oldest if cache full.  Must destroy after new widget is shown
        if len(curr_cache) >= self.max_cache_size:
            _, oldest_widget = curr_cache.popitem(last=False)
            logger.debug(f'{mode} cache full, popping last widget: '
                         f'({oldest_widget})')

    def create_widget(self, item: TreeItem, mode: str) -> PageWidget:
        """Create the widget for ``item`` in ``mode``."""
        data = item.orig_data

        # TODO: Logic could be better, might not have to make edit widget when
        # separate run widget exists
        if mode == 'edit':
            # edit mode
            return PAGE_MAP[type(item.orig_data)](
                data=data, tree_item=item, full_tree=self
            )
        else:  # run mode
            if self.prepared_file is None:
                self.refresh_prepared_file()

            if isinstance(self.orig_file, ConfigurationFile):
                get_prepare_fn = get_relevant_configs_comps
            elif isinstance(self.orig_file, ProcedureFile):
                get_prepare_fn = get_prepared_step

            # TODO: Is this just the stored data now?  Yea I think so
            prepared_data = get_prepare_fn(self.prepared_file, data)
            if type(data) in EDIT_TO_RUN_PAGE:
                if len(prepared_data) != 1:
                    run_widget = FailPage(
                        reason=f'Found ({len(prepared_data)}) matching dataclasses'
                               ', failed to set up run step.  Check to make sure'
                               ' configuration is correct.'
                    )
                    return run_widget
                else:
                    run_widget_cls = EDIT_TO_RUN_PAGE[type(data)]
                    # expects a single (top-level) step currently
                    run_widget = run_widget_cls(
                        data=prepared_data[0], tree_item=item, full_tree=self)
            else:
                edit_widget = PAGE_MAP[type(item.orig_data)](
                    data=data, tree_item=item, full_tree=self
                )
                # can currently handle multiple prepared_data
                # (e.g. multiple comparisons shown in one page)
                run_widget = make_run_page(edit_widget, prepared_data)

            try:
                next_item = self._item_list[self._item_list.index(item) + 1]
            except IndexError:
                run_widget.run_check.next_button.hide()
            else:
                run_widget.run_check.setup_next_button(next_item)

            # update all statuses every time a step is run
            run_widget.run_check.results_updated.connect(self.update_statuses)
            # update tree statuses with every result update
            run_widget.run_check.results_updated.connect(
                self.model.data_updated
            )

            # disable last 'next' button
            return run_widget

    def update_statuses(self) -> None:
        """update every status icon based on stored config result"""
        for widget in self.run_widget_cache.values():
            try:
                widget.run_check.update_all_icons_tooltips()
            except AttributeError as ex:
                logger.debug(f'Run Check widget not properly setup: {ex}')

    def maybe_get_widget(self, item: TreeItem, mode: str = 'edit') -> Optional[PageWidget]:
        """
        Return the widget linked to ``data`` (or ``item``?) if it exists in the
        widget cache.  If not, return None
        """
        cache = self.caches[mode]
        if item in cache:
            return cache[item]

        return None

    @contextmanager
    def modifies_tree(self, select_prev: bool = True) -> Generator[None, None, None]:
        """context manager in calls to modify the model layout"""
        self.model.layoutAboutToBeChanged.emit()
        try:
            yield
        finally:
            self.model.layoutChanged.emit()

        # update flattened tree list
        self._item_list = list(walk_tree_items(self.root_item))

        if not select_prev:
            return
        # try to reset old selection
        try:
            self.select_by_item(self.current_item)
        except Exception as ex:
            # TODO: find real fail conditions
            logger.debug(f'failed to re-select previous item: {ex}')
            # root item is actually invisible, only its child is visible
            self.select_by_item(self.root_item.child(0))

    def switch_mode(self, value) -> None:
        """Switch tree modes between 'edit' and 'run'"""
        if (not value and self.mode == 'edit') or (value and self.mode == 'run'):
            return

        set_wait_cursor()
        try:
            self.mode_switch_finished.connect(reset_cursor)
            prev_toggle_state = not self.toggle.isChecked()
            if value:
                self.mode = 'run'
            else:
                self.mode = 'edit'
            self.show_mode_widgets()
        except Exception as ex:
            logger.exception(ex)
            # reset toggle and mode

            def reset_to_edit():
                self.toggle.setChecked(prev_toggle_state)
                self.show_mode_widgets()

            warning_msg = QMessageBox(QMessageBox.Critical, 'Warning',
                                      'Mode Switch Failed')
            warning_msg.setInformativeText('Your checkout may be misconfigured.')
            warning_msg.setDetailedText(
                ''.join(traceback.format_exception(None, ex, ex.__traceback__))
            )
            warning_msg.exec()
            QTimer.singleShot(0, reset_to_edit)
        finally:
            self.mode_switch_finished.emit()
            self.mode_switch_finished.disconnect(reset_cursor)

    def show_mode_widgets(self) -> None:
        """Show active widget, hide others. (re)generate RunTree if needed"""
        # If run_tree requested check if there are changes
        # Do nothing if run tree exists and config has not changed
        update_run = False
        if self.mode == 'run':
            # store a copy of the edit tree to detect diffs
            try:
                ser = serialize(type(self.orig_file), self.orig_file)
            except Exception:
                logger.debug(f'Unable to serialize file as defined: {self.orig_file}')
                raise PreparationError('Unable to serialize file with current settings')
            current_edit_config = deepcopy(ser)

            if self.prepared_file is None:
                update_run = True
            elif not (current_edit_config == self.last_edit_config):
                # run tree found, and edit configs are different
                # remember last edit config
                self.last_edit_config = deepcopy(current_edit_config)
                update_run = True

            if update_run:
                self.run_widget_cache.clear()
                # generate new tree with prep file
                self.refresh_model()

            self.print_report_button.show()
            self.results_button.show()
            self.tree_view.setColumnHidden(1, False)
        else:
            self.print_report_button.hide()
            self.results_button.hide()
            self.tree_view.setColumnHidden(1, True)

        # navigate away and back to trigger selectionChanged
        curr_item = self.current_item
        curr_index = self.model.index_from_item(self.current_item)
        alt_index = self.tree_view.indexBelow(curr_index)
        if self.model.data(alt_index, 0) is None:
            self.select_by_item(self.root_item)
        else:
            self.tree_view.setCurrentIndex(alt_index)

        if update_run:
            self.select_by_item(self.root_item.child(0))
        else:
            self.select_by_item(curr_item)

    def print_report(self, *args, **kwargs):
        """setup button to print the report"""
        filename, _ = QFileDialog.getSaveFileName(
            parent=self,
            caption='Print report to:',
            filter='PDF Files (*.pdf)',
        )

        if not filename:
            # Exit clause
            return
        if not filename.endswith('.pdf'):
            filename += '.pdf'

        # To differentiate between active and passive checkout reports
        if isinstance(self.prepared_file, PreparedFile):
            doc = PassiveAtefReport(filename, config=self.prepared_file)
        elif isinstance(self.prepared_file, PreparedProcedureFile):
            doc = ActiveAtefReport(filename, config=self.prepared_file)
        else:
            raise TypeError('Unsupported data-type for report generation')

        # allow user to customize header fields
        doc_info = doc.get_info()
        msg = self.show_report_cust_prompt(doc_info)
        if msg.result() == msg.Accepted:
            new_info = msg.get_info()
            doc.set_info(**new_info)
            doc.create_report()

    def show_report_cust_prompt(self, info):
        """generate a window allowing user to customize information"""
        msg = MultiInputDialog(parent=self, init_values=info)
        msg.exec()
        return msg

    def show_results_summary(self):
        """show the results summary widget"""
        self._summary_widget = ResultsSummaryWidget(file=self.prepared_file)
        self._summary_widget.setWindowTitle('Results Summary')
        self._summary_widget.show()
