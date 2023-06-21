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
from copy import deepcopy
from functools import partial
from pathlib import Path
from pprint import pprint
from typing import ClassVar, Dict, Optional, Union

import qtawesome
from apischema import ValidationError, deserialize, serialize
from qtpy import QtWidgets
from qtpy.QtCore import Qt, QTimer
from qtpy.QtCore import Signal as QSignal
from qtpy.QtWidgets import (QAction, QFileDialog, QMainWindow, QMessageBox,
                            QTabWidget, QTreeWidget, QWidget)

from atef.cache import DataCache
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import (DescriptionStep, PassiveStep,
                            PreparedProcedureFile, ProcedureFile, SetValueStep)
from atef.qt_helpers import walk_tree_widget_items
from atef.report import ActiveAtefReport, PassiveAtefReport
from atef.widgets.utils import reset_cursor, set_wait_cursor

from ..archive_viewer import get_archive_viewer
from ..core import DesignerDisplay
from .page import (AtefItem, ConfigurationGroupPage, PageWidget,
                   ProcedureGroupPage, RunStepPage, link_page)
from .run_base import (get_prepared_step, get_relevant_configs_comps,
                       make_run_page)
from .utils import MultiInputDialog, Toggle, clear_results

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
    action_new_file: QAction
    action_open_file: QAction
    action_save: QAction
    action_save_as: QAction
    action_print_dataclass: QAction
    action_print_serialized: QAction
    action_open_archive_viewer: QAction
    action_print_report: QAction
    action_clear_results: QAction

    def __init__(self, *args, show_welcome: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('atef config')
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

        widget.new_passive_button.clicked.connect(
            partial(self.new_file, checkout_type='passive')
        )
        widget.new_active_button.clicked.connect(
            partial(self.new_file, checkout_type='active')
        )
        widget.sample_active_button.clicked.connect(partial(
            self.open_file, filename=TEST_CONFIG_PATH / 'active_test.json'
        ))

        widget.sample_passive_button.clicked.connect(partial(
            self.open_file, filename=TEST_CONFIG_PATH / 'all_fields.json'
        ))

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

    def get_current_tree(self) -> Union[EditTree, RunTree]:
        """
        Return the widget of the current open tab.
        """
        return self.tab_widget.currentWidget().get_tree()

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
        widget = DualTree(config_file=data, full_path=filename)
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

    def serialize_tree(self, tree: EditTree) -> dict:
        """
        Return the serialized data from a Tree widget.
        """
        try:
            return serialize(
                type(tree.config_file),
                tree.config_file,
            )
        except Exception:
            logger.exception('Error serializing file')

    def print_dataclass(self, *args, **kwargs):
        """
        Print the dataclass of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.get_current_tree().config_file)

    def print_serialized(self, *args, **kwargs):
        """
        Print the serialized data structure of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.serialize_tree(self.get_current_tree()))

    def open_archive_viewer(self, *args, **kwargs):
        """ Open the archive viewer """
        widget = get_archive_viewer()
        widget.show()

    def print_report(self, *args, **kwargs):
        """ Open save dialog for report output """
        # get RunTree
        run_tree: RunTree = self.tab_widget.currentWidget().get_tree(mode='run')

        run_tree.print_report()

    def clear_results(self, *args, **kwargs):
        """ clear results for a given tree """
        current_tree = self.tab_widget.currentWidget().get_tree()

        if isinstance(current_tree, RunTree):
            config_file = current_tree.config_file
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

            if current_tree.prepared_file:
                clear_results(current_tree.prepared_file)
            else:
                clear_results(config_file)

            current_tree.update_statuses()


class LandingPage(DesignerDisplay, QWidget):
    """ Landing Page for selecting a subsequent action """
    filename = 'landing_page.ui'

    new_passive_button: QtWidgets.QPushButton
    new_active_button: QtWidgets.QPushButton
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


class EditTree(DesignerDisplay, QWidget):
    """
    The main per-file widget as a "native" view into the file.

    Consists of a tree visualization on the left that can be selected through
    to choose which part of the tree to edit in the widget space on the right.

    Parameters
    ----------
    config_file : ConfigurationFile
        The config file object to use to build the tree.
    full_path : str, optional
        The full path to the last file used to save or load the tree.
    """
    filename = 'config_tree.ui'

    tree_widget: QTreeWidget
    splitter: QtWidgets.QSplitter
    last_selection: Optional[AtefItem]

    full_path: str

    def __init__(
        self,
        *args,
        config_file: Union[ConfigurationFile, ProcedureFile],
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.config_file = config_file
        if isinstance(self.config_file, ConfigurationFile):
            self.page_class = ConfigurationGroupPage
        elif isinstance(self.config_file, ProcedureFile):
            self.page_class = ProcedureGroupPage
        self.full_path = full_path
        self.last_selection = None
        self.built_widgets = set()
        self.assemble_tree()
        self.tree_widget.itemSelectionChanged.connect(
            self.show_selected_display
        )
        self.tree_widget.setCurrentItem(self.root_item)

    def assemble_tree(self):
        """
        On startup, create the full tree.
        """
        self.tree_widget.setColumnCount(2)
        self.tree_widget.setHeaderLabels(['Node', 'Type'])
        root_configuration_group = self.config_file.root
        if not root_configuration_group.name:
            root_configuration_group.name = 'root'
        self.root_item = AtefItem(
            tree_parent=self.tree_widget,
            name=root_configuration_group.name,
            func_name='root',
        )
        root_page = self.page_class(
            data=root_configuration_group,
        )
        link_page(item=self.root_item, widget=root_page)
        root_page.parent_button.hide()

    def show_selected_display(self, *args, **kwargs):
        """
        Show the proper widget on the right when a tree row is selected.

        This works by hiding the previous widget and showing the new
        selection, creating the widget object if needed.
        """
        item = self.tree_widget.currentItem()
        if item is None:
            logger.error('Tree has no currentItem.  Cannot show selected')
            return
        if item is self.last_selection:
            return

        replace = bool(self.last_selection is not None)
        if self.last_selection is not None:
            self.last_selection.widget.setVisible(False)
        widget = item.widget
        if widget not in self.built_widgets:
            self.built_widgets.add(widget)

        if replace:
            self.splitter.replaceWidget(1, widget)
        else:
            self.splitter.addWidget(widget)
        widget.setVisible(True)
        self.last_selection = item


_edit_to_run_page: Dict[type, PageWidget] = {
    DescriptionStep: RunStepPage,
    PassiveStep: RunStepPage,
    SetValueStep: RunStepPage,
}


class RunTree(EditTree):
    """
    A tree that holds a checkout process.  Based on current EditTree.
    """
    filename = 'run_config_tree.ui'

    print_report_button: QtWidgets.QPushButton

    def __init__(
        self,
        *args,
        config_file: ConfigurationFile | ProcedureFile,
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(config_file=config_file, full_path=full_path)
        # Prepared file only exists for passive checkouts, None otherwise
        self.prepared_file: Optional[PreparedFile] = None
        if isinstance(config_file, ConfigurationFile):
            self.prepared_file = PreparedFile.from_config(config_file,
                                                          cache=DataCache())
        if isinstance(config_file, ProcedureFile):
            # clear all results when making a new run tree
            self.prepared_file = PreparedProcedureFile.from_origin(config_file)

        self._swap_to_run_widgets()
        self.print_report_button.clicked.connect(self.print_report)

    # TODO: set up to use Procedure widgets instead of config ones
    @classmethod
    def from_edit_tree(cls, edit_tree: EditTree):
        """Create a RunTree from an EditTree"""
        # make a new widget with tree/widget connections

        return cls(
            config_file=edit_tree.config_file,
            full_path=edit_tree.full_path
        )

    def _swap_to_run_widgets(self) -> None:
        """
        Swap out widgets for run widgets

        If a run-specific version of the widget exists, return that.
        Otherwise makes a read-only copy of the widget with run controls
        """
        # replace widgets with run versions
        # start at the root of the config file
        prev_widget = None
        if isinstance(self.config_file, ConfigurationFile):
            get_prepare_fn = get_relevant_configs_comps
        elif isinstance(self.config_file, ProcedureFile):
            get_prepare_fn = get_prepared_step
        for item in walk_tree_widget_items(self.tree_widget):
            data = item.widget.data  # Dataclass on widget
            prepared_data = get_prepare_fn(self.prepared_file, data)
            if type(data) in _edit_to_run_page:
                if len(prepared_data) != 1:
                    raise ValueError(
                        'number of prepared dataclasses is not 1, while the '
                        'target page expects one: '
                        f'{type(data)} -> {[type(d) for d in prepared_data]}')
                run_widget_cls = _edit_to_run_page[type(data)]
                run_widget = run_widget_cls(data=prepared_data[0])
                link_page(item, run_widget)
                run_widget.link_children()
            else:
                run_widget = make_run_page(item.widget, prepared_data)

            if prev_widget:
                prev_widget.run_check.setup_next_button(item)

            prev_widget = run_widget

            # update all statuses every time a step is run
            run_widget.run_check.results_updated.connect(self.update_statuses)

        # disable last 'next' button
        run_widget.run_check.next_button.hide()

    def update_statuses(self) -> None:
        """ update every status icon based on stored config result """
        for item in walk_tree_widget_items(self.tree_widget):
            try:
                item.widget.run_check.update_all_icons_tooltips()
            except AttributeError as ex:
                logger.debug(f'Run Check widget not properly setup: {ex}')

    def print_report(self, *args, **kwargs):
        """ setup button to print the report """
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
        """ generate a window allowing user to customize information """
        msg = MultiInputDialog(parent=self, init_values=info)
        msg.exec()
        return msg


class DualTree(QWidget):
    """
    A widget that exposes one of two tree widgets depending on the mode
    """
    mode_switch_finished: ClassVar[QSignal] = QSignal()

    def __init__(
        self,
        *args,
        config_file: ConfigurationFile | ProcedureFile,
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.layout = QtWidgets.QHBoxLayout()
        edit_tree = EditTree(parent=self, config_file=config_file,
                             full_path=full_path)
        self.layout.addWidget(edit_tree)
        self.setLayout(self.layout)
        self.trees = {'edit': edit_tree, 'run': None}
        self.mode = 'edit'
        serialized_edit_config = serialize(type(self.trees['edit'].config_file),
                                           self.trees['edit'].config_file)
        self.last_edit_config = deepcopy(serialized_edit_config)
        self._orig_config = deepcopy(self.last_edit_config)

        self.toggle = Toggle()
        self.show_widgets()

    def get_tree(self, mode: str = None) -> Union[EditTree, RunTree]:
        """
        Get the requested tree, either 'edit' or 'run'.
        Defaults to the currently active tree

        Parameters
        ----------
        mode : str, optional
            either 'edit' or 'run', by default None

        Returns
        -------
        Union[EditTree, RunTree]
            the requested tree
        """
        if mode is None:
            return self.trees[self.mode]

        if mode == 'run':
            # generate new run configuration
            if (self.trees['run'] is None):
                self.build_run_tree()

        return self.trees[mode]

    def switch_mode(self, value) -> None:
        """ Switch tree modes between 'edit' and 'run' """
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
            self.show_widgets()
        except Exception as ex:
            logger.exception(ex)
            # reset toggle and mode

            def reset_to_edit():
                self.toggle.setChecked(prev_toggle_state)
                self.show_widgets()

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

    def show_widgets(self) -> None:
        """ Show active widget, hide others. (re)generate RunTree if needed """
        # If run_tree requested check if there are changes
        # Do nothing if run tree exists and config has not changed
        update_run = False
        if self.mode == 'run':
            # store a copy of the edit tree to detect diffs
            current_edit_config = deepcopy(
                serialize(type(self.trees['edit'].config_file),
                          self.trees['edit'].config_file)
            )

            if self.trees['run'] is None:
                update_run = True
            elif not (current_edit_config == self.last_edit_config):
                # run tree found, and edit configs are different
                # remember last edit config
                self.last_edit_config = deepcopy(current_edit_config)
                update_run = True

            if update_run:
                self.build_run_tree()

        for widget in self.trees.values():
            if hasattr(widget, 'hide'):
                widget.hide()
        self.trees[self.mode].show()

    def build_run_tree(self) -> None:
        """
        Build the RunTree based on the current EditTree
        Removes the existing RunTree if it exists
        """
        if self.trees['run']:
            old_r_widget = self.trees['run']
            old_r_widget.setParent(None)
            old_r_widget.deleteLater()
            self.trees['run'] = None

        r_widget = RunTree.from_edit_tree(self.trees['edit'])
        r_widget.hide()
        self.layout.addWidget(r_widget)
        self.trees['run'] = r_widget
