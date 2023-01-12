"""
Top-level widgets that contain all the other widgets.
"""
from __future__ import annotations

import json
import logging
import os
import os.path
from copy import deepcopy
from pprint import pprint
from typing import Any, Dict, List, Optional, Tuple, Union

from apischema import ValidationError, deserialize, serialize
from qtpy import QtWidgets
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (QAction, QFileDialog, QMainWindow, QMessageBox,
                            QTabWidget, QTreeWidget, QWidget)

from atef.cache import DataCache
from atef.config import ConfigurationFile, ConfigurationGroup, PreparedFile
from atef.procedure import ProcedureFile

from ..archive_viewer import get_archive_viewer
from ..core import DesignerDisplay
from .page import (AtefItem, ConfigurationGroupPage, ProcedureGroupPage,
                   link_page)
from .run import RunPage, make_run_page
from .utils import Toggle

logger = logging.getLogger(__name__)


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
        if show_welcome:
            QTimer.singleShot(0, self.welcome_user)

    def welcome_user(self):
        """
        On open, ask the user what they'd like to do (new config? load?)
        """
        welcome_box = QMessageBox()
        welcome_box.setIcon(QMessageBox.Question)
        welcome_box.setWindowTitle('Welcome')
        welcome_box.setText('Welcome to atef config!')
        welcome_box.setInformativeText('Please select a startup action')
        open_button = welcome_box.addButton(QMessageBox.Open)
        new_button = welcome_box.addButton('New', QMessageBox.AcceptRole)
        welcome_box.addButton(QMessageBox.Close)
        open_button.clicked.connect(self.open_file)
        new_button.clicked.connect(self.new_file)
        welcome_box.exec()

    def get_tab_name(self, filename: Optional[str] = None):
        """
        Get a standardized tab name from a filename.
        """
        if filename is None:
            filename = self.user_default_filename
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

    def new_file(self, *args, **kwargs):
        """
        Create and populate a new edit tab.

        The parameters are open as to accept inputs from any signal.
        """
        # TODO add mode switch logic
        widget = EditTree(config_file=ConfigurationFile())
        self.tab_widget.addTab(widget, self.get_tab_name())

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
                logger.error('failed to open file as either active'
                             'or passive checkout')

        widget = DualTree(config_file=data, full_path=filename)

        self.tab_widget.addTab(widget, self.get_tab_name(filename))
        curr_idx = self.tab_widget.count() - 1
        self.tab_widget.setCurrentIndex(curr_idx)
        # set up edit-run toggle
        tab_bar = self.tab_widget.tabBar()
        widget.toggle.stateChanged.connect(widget.switch_mode)
        tab_bar.setTabButton(curr_idx, QtWidgets.QTabBar.RightSide, widget.toggle)

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
                ConfigurationFile,
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
        root_page = ConfigurationGroupPage(
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


_edit_to_run_page: Dict[type, RunPage] = {
    # temporary dummy page
    ConfigurationGroup: ConfigurationGroupPage
}


class RunTree(EditTree):
    """
    A tree that holds a checkout process.  Based on current EditTree.
    """
    def __init__(
        self,
        *args,
        config_file: ConfigurationFile,
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(config_file=config_file, full_path=full_path)
        if isinstance(config_file, ConfigurationFile):
            self.prepared_file = PreparedFile.from_config(config_file,
                                                          cache=DataCache())

        self._swap_to_run_widgets()

    # TODO: set up to use Procedure widgets instead of config ones
    @classmethod
    def from_edit_tree(cls, edit_tree: EditTree):
        """Create a RunTree from an EditTree"""
        # make a new widget with tree/widget connections

        return cls(
            config_file=edit_tree.config_file,
            full_path=edit_tree.full_path
        )

    def _swap_to_run_widgets(self) -> RunTree:
        """
        Swap out widgets for run widgets

        If a run-specific version of the widget exists, return that.
        Otherwise makes a read-only copy of the widget with run controls
        """
        if isinstance(self.config_file, ConfigurationFile):
            # generate prepared file to grab configs from
            prepared_file = PreparedFile.from_config(file=self.config_file)
        else:
            # add case for prepared procedure
            pass

        # walk through tree items, make an analogous widget for each
        # in edit tree
        it = QtWidgets.QTreeWidgetItemIterator(self.tree_widget)
        # this is not a pythonic iterator, treat it differently

        # gather sets of item(with widget), and a list of configs/comparisons
        item_config_list: List[Tuple[AtefItem, Any]] = []
        while it.value():
            item: AtefItem = it.value()
            # for each item, grab the relevant Configurations or Comparisons
            c_list = get_relevant_configs_comps(prepared_file, item.widget.data)
            item_config_list.append((item, c_list))

            it += 1

        self.item_config_list = item_config_list

        # replace widgets with run versions
        # start at the root of the config file
        ct = 0
        prev_widget = None
        for item, cfgs in item_config_list:
            if item.widget in _edit_to_run_page:
                print('swap page with run')
                run_widget_cls = _edit_to_run_page[type(item.widget)]
                run_widget = run_widget_cls(configs=cfgs)
                link_page(item, run_widget)
            else:
                run_widget = make_run_page(item.widget, cfgs)
                link_page(item, run_widget)

            if prev_widget:
                prev_widget.run_check.setup_next_button(item)

            prev_widget = run_widget
            ct += 1

            # update all statuses every time a step is run
            run_button: QtWidgets.QPushButton = run_widget.run_check.run_button
            run_button.clicked.connect(self.update_statuses)

        print(ct)

    def update_statuses(self) -> None:
        """ update every status icon based on stored config result """
        # walk through tree
        it = QtWidgets.QTreeWidgetItemIterator(self.tree_widget)
        while it.value():
            item: AtefItem = it.value()
            try:
                item.widget.run_check.update_status()
            except AttributeError as ex:
                logger.debug(f'Run Check widget not properly setup: {ex}')

            it += 1


class DualTree(QWidget):
    """
    A widget that exposes one of two tree widgets depending on the mode
    """

    def __init__(
        self,
        *args,
        config_file: ConfigurationFile,
        full_path: str,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.layout = QtWidgets.QHBoxLayout()
        edit_tree = EditTree(parent=self, config_file=config_file, full_path=full_path)
        self.layout.addWidget(edit_tree)
        self.setLayout(self.layout)
        self.trees = {'edit': edit_tree, 'run': None}
        self.mode = 'edit'
        self.last_edit_config = deepcopy(serialize(type(self.trees['edit'].config_file),
                                         self.trees['edit'].config_file))
        self._orig_config = deepcopy(self.last_edit_config)

        self.toggle = Toggle()
        self.show_widgets()

    def get_tree(self, mode=None) -> Union[EditTree, RunTree]:
        if mode:
            return self.trees[mode]

        if self.mode == 'run':
            # generate new run configuration
            if (self.trees['run'] is None):
                self.build_run_tree()

        return self.trees[self.mode]

    def switch_mode(self) -> None:
        # TODO: can this switching be made more elegant?
        if self.mode == 'edit':
            self.mode = 'run'
        else:
            self.mode = 'edit'
        self.show_widgets()

    def show_widgets(self) -> None:
        """show active widget, hide others. (re)generate RunTree if needed"""
        # TODO: this logic is gross please refactor this
        # Right now this only happens for the run tree

        # If run_tree requested check if there are changes
        # Do nothing if run tree exists and config has not changed
        update_run = False
        if self.mode == 'run':
            print('run requested')
            current_edit_config = deepcopy(serialize(type(self.trees['edit'].config_file),
                                           self.trees['edit'].config_file))

            if self.trees['run'] is None:
                update_run = True
            elif not (current_edit_config == self.last_edit_config):
                # run tree found, and edit configs are different
                print('change found')
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
        # TODO: Figure out if old versions get garbage collected via orphaning
        # otherwise build new tree widget
        if self.trees['run']:
            print('destroying old rwidget')
            old_r_widget = self.trees['run']
            old_r_widget.setParent(None)
            old_r_widget.deleteLater()
            self.trees['run'] = None

        r_widget = RunTree.from_edit_tree(self.trees['edit'])

        self.layout.addWidget(r_widget)
        self.trees['run'] = r_widget


def get_relevant_configs_comps(prepared_file: PreparedFile, original_c):
    """
    For passive checkout files only

    Gather all the PreparedConfiguration or PreparedComparison dataclasses
    that correspond to the original comparison or config.
    """
    matched_c = []

    for config in prepared_file.walk_groups():
        if config.config == original_c:
            matched_c.append(config)

    for comp in prepared_file.walk_comparisons():
        if comp.comparison == original_c:
            matched_c.append(comp)

    return matched_c
