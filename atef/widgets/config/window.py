"""
Top-level widgets that contain all the other widgets.
"""
from __future__ import annotations

import json
import logging
import os
import os.path
from pprint import pprint
from typing import Dict, Optional, Union

from apischema import deserialize, serialize
from qtpy import QtWidgets
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (QAction, QFileDialog, QMainWindow, QMessageBox,
                            QTabWidget, QTreeWidget, QWidget)

from atef.config import ConfigurationFile, ConfigurationGroup

from ..archive_viewer import get_archive_viewer
from ..core import DesignerDisplay
from .page import AtefItem, ConfigurationGroupPage, link_page
from .run import make_run_page, walk_tree_items
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
        data = deserialize(ConfigurationFile, serialized)

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
        config_file: ConfigurationFile,
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.config_file = config_file
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


_edit_to_run_page: Dict[type, type] = {
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

    def _swap_to_run_widgets(self):
        """ Swap out widgets for run widgets """
        # replace widgets with run versions
        for item in walk_tree_items(self.root_item):
            if item.widget in _edit_to_run_page:
                print('swap page with run')
            else:
                run_widget = make_run_page(item.widget)
                link_page(item, run_widget)


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
        self.run_config = None
        self.toggle = Toggle()
        self.show_widgets()

    def get_tree(self, mode=None) -> Union[EditTree, RunTree]:
        if mode:
            return self.trees[mode]

        if self.mode == 'run':
            # generate new run configuration
            if (self.trees['run'] is None) or self.trees['run'].config_file:
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
        for widget in self.trees.values():
            if getattr(widget, 'hide', False):
                widget.hide()

        # TODO: this logic is gross please refactor this
        # Right now this only happens for the run tree
        if self.trees[self.mode] is None:
            self.build_run_tree()
        self.trees[self.mode].show()

    def build_run_tree(self) -> None:
        # TODO: Figure out if old versdions get garbage collected via orphaning
        # grab current edit config
        self.run_config = self.trees['edit'].config_file

        # Do nothing if run tree exists and config has not changed
        if (self.trees['run'] and
                (self.trees['run'].config_file == self.run_config)):
            return

        # otherwise build new tree widget
        r_widget = RunTree.from_edit_tree(self.trees['edit'])

        self.layout.addWidget(r_widget)
        self.trees['run'] = r_widget
