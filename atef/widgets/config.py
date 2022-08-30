"""
Widget classes designed for atef configuration.

TODO: delete these comments later
I'm going to copy some design goals from various gh/slack pages
- Some of the existing ui screens will want some extensions, e.g. comparisons
  will want to be able to show you which PVs/signals they'll be run on with
  the introduction of by_attr vs shared, among other things. The top-level
  object being itself a group config leaves a natural path for setting all of
  this up without a huge shakeup- just add some fields/modifications to the
  top-level screen and re-use it for all group layers.
- Old structure (high level):
  - ConfigurationFile > Configuration (Device, PV) >
    > IdentifierAndComparison > Comparison (many kinds)
- New structure (high level):
  - ConfigurationFile > ConfigurationGroup >
    > Configuration (Device, PV, Tool, Group)
    > Comparison (many kinds)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os.path
from functools import partial
from itertools import zip_longest
from pprint import pprint
from typing import (Any, Callable, ClassVar, Dict, List, Optional, Tuple, Type,
                    Union, cast)

from apischema import deserialize, serialize
from pydm.widgets.drawing import PyDMDrawingLine
from qtpy import QtWidgets
from qtpy.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from qtpy.QtCore import Signal as QSignal
from qtpy.QtGui import QClipboard, QColor, QGuiApplication, QPalette
from qtpy.QtWidgets import (QAction, QCheckBox, QComboBox, QFileDialog,
                            QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                            QLayout, QLineEdit, QMainWindow, QMenu,
                            QMessageBox, QPlainTextEdit, QPushButton, QStyle,
                            QTableWidget, QTabWidget, QToolButton, QTreeWidget,
                            QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import util
from ..check import (Comparison, Equals, Greater, GreaterOrEqual, Less,
                     LessOrEqual, NotEquals, Range)
from ..config import (Configuration, ConfigurationFile, ConfigurationGroup,
                      DeviceConfiguration, PVConfiguration, ToolConfiguration)
from ..enums import Severity
from ..qt_helpers import QDataclassBridge, QDataclassList, QDataclassValue
from ..reduce import ReduceMethod
from ..type_hints import PrimitiveType
from .core import DesignerDisplay
from .happi import HappiDeviceComponentWidget
from .ophyd import OphydAttributeData, OphydAttributeDataSummary

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

    def __init__(self, *args, show_welcome: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('atef config')
        self.action_new_file.triggered.connect(self.new_file)
        self.action_open_file.triggered.connect(self.open_file)
        self.action_save.triggered.connect(self.save)
        self.action_save_as.triggered.connect(self.save_as)
        self.action_print_dataclass.triggered.connect(self.print_dataclass)
        self.action_print_serialized.triggered.connect(self.print_serialized)
        if show_welcome:
            QTimer.singleShot(0, self.welcome_user)

    def welcome_user(self):
        """
        On open, ask the user what they'd like to do (new config? load?)

        TODO: only show when we don't get a file cli argument to start.
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

    def get_current_tree(self) -> Tree:
        """
        Return the widget of the current open tab.
        """
        return self.tab_widget.currentWidget()

    def new_file(self, *args, **kwargs):
        """
        Create and populate a new edit tab.

        The parameters are open as to accept inputs from any signal.
        """
        widget = Tree(config_file=ConfigurationFile(configs=[]))
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
        widget = Tree(config_file=data, full_path=filename)
        self.tab_widget.addTab(widget, self.get_tab_name(filename))
        self.tab_widget.setCurrentIndex(self.tab_widget.count()-1)

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
        except OSError:
            logger.exception(f'Error saving file {filename}')
        else:
            self.set_current_tab_name(filename)
            current_tree.full_path = filename

    def serialize_tree(self, tree: Tree) -> dict:
        """
        Return the serialized data from a Tree widget.
        """
        try:
            return serialize(
                ConfigurationFile,
                tree.bridge.data,
            )
        except Exception:
            logger.exception('Error serializing file')

    def print_dataclass(self, *args, **kwargs):
        """
        Print the dataclass of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.get_current_tree().bridge.data)

    def print_serialized(self, *args, **kwargs):
        """
        Print the serialized data structure of the current tab.

        The parameters are open as to accept inputs from any signal.
        """
        pprint(self.serialize_tree(self.get_current_tree()))


class Tree(DesignerDisplay, QWidget):
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

    bridge: QDataclassBridge
    tree_widget: QTreeWidget
    splitter: QtWidgets.QSplitter

    full_path: str

    def __init__(
        self,
        *args,
        config_file: ConfigurationFile,
        full_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.bridge = QDataclassBridge(config_file, parent=self)
        self.full_path = full_path
        self.last_selection: Optional[AtefItem] = None
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
        self.root_item = AtefItem(
            tree_parent=self.tree_widget,
            name='root',
            func_name=ConfigurationGroupWidget.func_names[ConfigurationGroup]
        )
        root = ConfigurationGroupWidget(
            QDataclassBridge(self.bridge.root.get())
        )
        link_page(item=self.root_item, widget=root)

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


class AtefItem(QTreeWidgetItem):
    """
    A QTreeWidget item with some convenience methods.

    Parameters
    ----------
    name : str
        The text on the left column of the tree view.
    func_name : str
        The text on the right column of the tree view.
    """
    widget: Optional[PageWidget]
    parent_tree_item: QTreeWidgetItem
    full_tree: QTreeWidget

    def __init__(
        self,
        tree_parent: Union[AtefItem, QTreeWidget],
        name: str,
        func_name: Optional[str] = None,
    ):
        super().__init__()
        self.widget = None
        self.setText(0, name)
        if func_name is not None:
            self.setText(1, func_name)
        if isinstance(tree_parent, QTreeWidget):
            self.parent_tree_item = tree_parent.invisibleRootItem()
            self.full_tree = tree_parent
        else:
            self.parent_tree_item = tree_parent
            self.full_tree = tree_parent.full_tree
        self.parent_tree_item.addChild(self)

    def assign_widget(self, widget: PageWidget) -> None:
        """
        Updates this tree item with a reference to the corresponding page.

        Parameters
        ----------
        widget : PageWidget
            The page to show when this tree item is selected.
        """
        self.widget = widget

    def find_ancestor_by_widget(self, cls: Type[QtWidgets.QWidget]) -> Optional[AtefItem]:
        """Find an ancestor widget of the given type."""
        ancestor = self.parent_tree_item
        while hasattr(ancestor, "parent_tree_item"):
            widget = getattr(ancestor, "widget", None)
            if isinstance(widget, cls):
                return ancestor
            ancestor = ancestor.parent_tree_item

        return None

    def find_ancestor_by_item(self, cls: Type[AtefItem]) -> Optional[AtefItem]:
        """Find an ancestor widget of the given type."""
        ancestor = self.parent_tree_item
        while hasattr(ancestor, "parent_tree_item"):
            if isinstance(ancestor, cls):
                return ancestor
            ancestor = ancestor.parent_tree_item

        return None


class PageWidget(DesignerDisplay, QWidget):
    """
    Interface for widgets that correspond to a tree node.

    The core thing this enforces is that all PageWidget instances
    will have their main dataclass manipulated through the
    bridge attribute.

    Once linked to a tree item using the ``link_page`` function,
    these widgets will have knowledge over their place in the tree
    and will be able to use that in various ways.

    These displays should have a QToolButton named "parent_button"
    to help navigate up to the parent node in the tree.

    Parameters
    ----------
    bridge : QDataclassBridge
        The interface that lets us manipulate the data structure
    parent : QWidget
        Standard qt parent argument
    """
    bridge: QDataclassBridge
    tree_item: AtefItem
    parent_tree_item: AtefItem
    full_tree: QTreeWidget

    parent_button: QToolButton

    # Placeholder for DesignerDisplay's __init_subclass__
    # Must be overriden properly in PageWidget subclasses
    filename = ''

    def __init__(
        self,
        bridge: QDataclassBridge,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent=parent)
        self.bridge = bridge
        try:
            parent_button = self.parent_button
        except AttributeError:
            pass
        else:
            parent_button.clicked.connect(self.navigate_to_parent)
            icon = self.style().standardIcon(QStyle.SP_FileDialogToParent)
            parent_button.setIcon(icon)

    def assign_tree_item(self, item: AtefItem):
        """
        Updates this page with references to the tree.

        Parameters
        ----------
        item : AtefItem
            The item that should be showing this page.
        """
        self.tree_item = item
        self.parent_tree_item = item.parent_tree_item
        self.full_tree = item.full_tree
        self.full_tree.itemChanged.connect(
            self._update_parent_tooltip_from_tree,
        )
        self.update_parent_tooltip()

    def _update_parent_tooltip_from_tree(
        self,
        item: QTreeWidgetItem,
        column: int,
    ):
        """
        Update the parent tooltip if our parent's name changes.
        """
        if item is self.parent_tree_item:
            self.update_parent_tooltip()

    def update_parent_tooltip(self):
        """
        Ensure that the to-parent tooltip is updated, accurate, and helpful.
        """
        try:
            parent_button = self.parent_button
        except AttributeError:
            pass
        else:
            nav_parent = self.get_nav_parent()
            parent_button.setToolTip(
                "Navigate to parent item "
                f"{nav_parent.text(0)} "
                f"({nav_parent.text(1)})"
            )

    def navigate_to(self, item: AtefItem, *args, **kwargs):
        """
        Make the tree switch to a specific item.

        This can be used to navigate to child items, for example.

        Parameters
        ----------
        item : AtefItem
            The tree node to navigate to.
        """
        self.full_tree.setCurrentItem(item)

    def navigate_to_parent(self, *args, **kwargs):
        """
        Make the tree switch to this widget's parent in the tree.
        """
        self.navigate_to(self.get_nav_parent())

    def get_nav_parent(self) -> AtefItem:
        """
        Get the navigation parent target item.

        This is self.parent_tree_item normally except when we are
        a top-level item, in which case the target should be the
        overview widget because otherwise there isn't any parent
        to navigate to.
        """
        if isinstance(self.parent_tree_item, AtefItem):
            return self.parent_tree_item
        else:
            return self.full_tree.topLevelItem(0)

    def setup_child_nav_button(
        self,
        button: QToolButton,
        item: AtefItem,
    ) -> None:
        button.clicked.connect(partial(self.navigate_to, item))
        icon = self.style().standardIcon(QStyle.SP_ArrowRight)
        button.setIcon(icon)
        button.show()
        button.setToolTip(
            f"Navigate to child {item.text(1)}"
        )

    def get_configuration(self) -> Optional[Union[DeviceConfiguration, PVConfiguration]]:
        """Get an applicable `Configuration`, if available."""
        if isinstance(self, Group):
            item = self
        else:
            item = self.tree_item.find_ancestor_by_widget(Group)

        if item is None or item.widget is None:
            return None

        group = cast(Group, item.widget)
        if isinstance(group.bridge.data, (PVConfiguration, DeviceConfiguration)):
            return group.bridge.data
        return None


def link_page(item: AtefItem, widget: PageWidget):
    """
    Link a page widget to an atef tree item.

    All linkage calls should go through here to remove ambiguity
    about ordering, etc. and so each object only has to worry about
    how to update itself.

    Parameters
    ----------
    item : AtefItem
        The tree item to link.
    widget : PageWidget
        The widget to link.
    """
    item.assign_widget(widget)
    widget.assign_tree_item(item)


class ConfigTextMixin:
    """
    A mix-in class for proper name and desc widget handling.

    Does the following:
    - sets up self.bridge to take updates from and send
      updates to self.name_edit and self.desc_edit
    - makes self.desc_edit expand/contract to match the
      available text
    """
    bridge: QDataclassBridge
    name_edit: QLineEdit
    desc_edit: QPlainTextEdit

    def initialize_config_text(self):
        """
        Call this in the mixed-in class to establish the config text.

        Requires self.bridge, self.name_edit, and self.desc_edit
        to be instantiated and available.
        """
        self.initialize_config_name()
        self.initialize_config_desc()

    def initialize_config_name(self):
        """
        Call this in the mixed-in class to establish the config name only.

        Requires self.bridge and self.name_edit
        to be instantiated and available.
        """
        # Load starting text
        load_name = self.bridge.name.get() or ''
        self.last_name = load_name
        self.name_edit.setText(load_name)
        # Setup the name edit
        self.name_edit.textEdited.connect(self.update_saved_name)
        self.bridge.name.changed_value.connect(self.apply_new_name)

    def initialize_config_desc(self):
        """
        Call this in the mixed-in class to establish the config desc only.

        Requires self.bridge and self.desc_edit
        to be instantiated and available.
        """
        # Load starting text
        load_desc = self.bridge.description.get() or ''
        self.last_desc = load_desc
        self.desc_edit.setPlainText(load_desc)

        # Setup the desc edit
        self.desc_edit.textChanged.connect(self.update_saved_desc)
        self.bridge.description.changed_value.connect(self.apply_new_desc)
        self.update_text_height()
        self.desc_edit.textChanged.connect(self.update_text_height)

    def update_saved_name(self, name: str):
        """
        When the user edits the name, write to the config.
        """
        self.last_name = self.name_edit.text()
        self.bridge.name.put(name)

    def apply_new_name(self, text: str):
        """
        If the text changed in the data, update the widget.

        Only run if needed to avoid annoyance with cursor repositioning.
        """
        if text != self.last_name:
            self.name_edit.setText(text)

    def update_saved_desc(self):
        """
        When the user edits the desc, write to the config.
        """
        self.last_desc = self.desc_edit.toPlainText()
        self.bridge.description.put(self.last_desc)

    def apply_new_desc(self, desc: str):
        """
        When some other widget updates the description, update it here.
        """
        if desc != self.last_desc:
            self.desc_edit.setPlainText(desc)

    def update_text_height(self):
        """
        When the user edits the desc, make the text box the correct height.
        """
        line_count = max(self.desc_edit.document().size().toSize().height(), 1)
        self.desc_edit.setFixedHeight(line_count * 13 + 12)


class ConfigurationGroupWidget(ConfigTextMixin, PageWidget):
    """
    A widget that represents a ``ConfigurationGroup`` dataclass.

    ``ConfigurationGroup`` is a configuration that holds other configurations.
    There is a single ``ConfigurationGroup`` called "root" at the top of the
    tree. Users can add additional groups under other groups in order to
    organize their configurations in a way that makes the most sense to them.

    All configurations have "name", "description", and "tags" fields.
    ``ConfigurationGroup`` instances also have a list of ``Configuration``
    instances and two special fields: "values" and "mode".

    "values" allows us to define global constants that can be used or
    overridden in sub-groups. TODO in a future version these will be useable
    as comparison targets.

    "mode" allows us to choose how to interpret the overall result from
    all of the component configurations, and can be set to "all_" or "any_"
    depending on if we need all the subconfigurations to pass or just one of
    them- in case we have different running modes, for example.

    This widget was formally named ``Overview`` in older versions of this
    application.

    TODO support drag and drop to rearrange the configs in the group,
    must persist through loads
    """
    filename = 'configuration_group_widget.ui'

    name_edit: QLineEdit
    desc_edit: QPlainTextEdit
    tags_content: QVBoxLayout
    add_tag_button: QToolButton

    add_config_button: QPushButton
    select_type_combo: QComboBox

    group_content_table: QTableWidget

    row_count: int
    row_mapping: Dict[OverviewRow, Tuple[Configuration, AtefItem]]

    func_names: ClassVar[Dict[type, str]] = {
        DeviceConfiguration: "device config",
        PVConfiguration: "pv config",
        ToolConfiguration: "tool config",
        ConfigurationGroup: "group config"
    }
    func_names_rev: ClassVar[Dict[str, type]] = {
        value: key for key, value in func_names.items()
    }
    next_type: type[Configuration]

    def __init__(
        self,
        bridge: QDataclassBridge,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(bridge, parent=parent)
        self.row_count = 0
        self.row_mapping = {}
        self.initialize_config_text()
        self.add_config_button.connect(self.add_config_from_gui)
        for text in self.func_names.values():
            self.select_type_combo.addItem(text)
        self.next_type = next(self.func_names)
        self.select_type_combo.activated.connect(self.new_add_type)

    def assign_tree_item(self, item: AtefItem):
        super().assign_tree_item(item)
        self.initialize_configuration_group()

    def initialize_configuration_group(self):
        """
        Read the configuration data and create the group rows.
        """
        for config in self.bridge.get():
            self.add_config(config=config, update_data=False)

    def add_config_from_gui(self, *args, **kwargs):
        """
        Add a new config row using the gui's button.

        The type will depend on the selection in the combo box.
        """
        self.add_config(config=self.next_type())

    def set_next_type(self, text: str, *args, **kwargs):
        """
        Cache the type that corresponds with the combo box selection.
        """
        self.next_type = self.func_names_rev[text]

    def add_config(
        self,
        config: Configuration,
        update_data: bool = True,
    ):
        """
        Add an existing config to the tree and to the group.

        This is the core method that modifies the tree and adds the row
        widget.

        Parameters
        ----------
        config : Configuration
            A single configuration object.
        update_data : bool, optional
            If True, the default, mutates the dataclass.
            Set to False during the initial reading of the file.
        """
        # Create enclosed widget
        row = OverviewRow(config, self)
        # Add widget to the end of the table
        self.group_content_table.insertRow(self.row_count)
        self.group_content_table.setCellWidget(self.row_count, 0, row)
        self.group_content_table.setRowHeight(self.row_count, row.height())
        # Add to the tree
        func_name = self.func_names[type(config)]
        item = AtefItem(
            tree_parent=self.full_tree,
            name=config.name or 'untitled',
            func_name=func_name,
        )
        page = Group(row.bridge)
        link_page(item, page)
        # Add link to the new child page
        self.setup_child_nav_button(row.child_button, item)
        # Keep track of the row wigets
        self.row_count += 1
        self.row_mapping[row] = (config, item)

        # If either of the widgets change the name, update tree
        row.bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )
        # Extend the dataclass
        if update_data:
            self.bridge.append(config)

    def delete_row(self, row: OverviewRow) -> None:
        """
        Delete a row and the corresponding data from the file.

        This will remove the config data structure and the
        tree node, and leave us in a state where adding a new
        config will work as expected.

        Parameters
        ----------
        row : OverviewRow
            The row that we want to remove from the display.
            This row has an associated tree item and config
            dataclass.
        """
        config, tree_item = self.row_mapping[row]
        # Remove from data structure
        self.bridge.remove_value(config)
        # Remove from tree
        self.tree_item.removeChild(tree_item)
        # Remove from table
        for row_index in range(self.group_content_table.rowCount()):
            if self.group_content_table.cellWidget(row_index, 0) is row:
                self.group_content_table.removeRow(row_index)
                break
        # Adjust counter
        self.row_count -= 1
        # Remove from tracker
        del self.row_mapping[row]
        # Deallocate the widget
        row.deleteLater()


class OverviewRow(ConfigTextMixin, DesignerDisplay, QWidget):
    """
    A single row in the overview widget.

    This displays and provides means to edit the name and description
    of a single configuration.

    Parameters
    ----------
    config : Configuration
        The full configuration associated with this row, so that we can
        read and edit the name and description.
    """
    filename = 'config_overview_row.ui'

    bridge: QDataclassBridge

    name_edit: QLineEdit
    config_type: QLabel
    lock_button: QPushButton
    desc_edit: QPlainTextEdit
    delete_button: QToolButton
    child_button: QToolButton

    def __init__(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
        overview: ConfigurationGroupWidget,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.bridge = QDataclassBridge(config, parent=self)
        self.overview = overview
        self.initialize_row()

    def initialize_row(self):
        """
        Set up all the logic and starting state of the row widget.
        """
        self.initialize_config_text()
        if isinstance(self.bridge.data, DeviceConfiguration):
            self.config_type.setText('Device Config')
        else:
            self.config_type.setText('PV Config')
        # Setup the lock and delete buttons
        self.lock_button.toggled.connect(self.handle_locking)
        self.name_edit.textChanged.connect(
            self.on_name_changed,
        )
        self.delete_button.clicked.connect(self.delete_this_config)
        if self.name_edit.text():
            # Start locked if we are reading from file
            self.lock_button.toggle()
        icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        self.delete_button.setIcon(icon)

    def lock_editing(self, locked: bool):
        """
        Set the checked state of the "locked" button as the user would.

        Parameters
        ----------
        locked : bool
            True if locking, False if unlocking
        """
        self.lock_button.setChecked(locked)

    def handle_locking(self, locked: bool):
        """
        When the checked state of the "locked" button changes, make it so.

        When locked, the boxes will be read only and have an indicated visual change.
        When unlocked, the boxes will be writable and have the default look and feel.

        It is expected that the user won't edit these a lot, and that it is easier
        to browse through the rows with the non-edit style.

        Parameters
        ----------
        locked : bool
            True if locking, False if unlocking
        """
        self.name_edit.setReadOnly(locked)
        self.name_edit.setFrame(not locked)
        self.desc_edit.setReadOnly(locked)
        if locked:
            self.desc_edit.setFrameShape(self.desc_edit.NoFrame)
            self.setStyleSheet(
                "QLineEdit, QPlainTextEdit { background: transparent }"
            )
            self.delete_button.setEnabled(False)
        else:
            self.desc_edit.setFrameShape(self.desc_edit.StyledPanel)
            color = self.palette().color(QPalette.ColorRole.Base)
            self.setStyleSheet(
                f"QLineEdit, QPlainTextEdit {{ background: rgba({color.red()},"
                f"{color.green()}, {color.blue()}, {color.alpha()}) }}"
            )
            if not self.name_edit.text():
                self.delete_button.setEnabled(True)

    def on_name_changed(self, name: str) -> None:
        """
        Actions to perform when the name field changes.

        This will disable the delete button as appropriate.
        Only enable the delete button in an unlocked state with an
        empty name. This is to help prevent someone from
        accidentally nuking their entire config tree.

        Parameters
        ----------
        name : str
            The updated configuration name.
        """
        if not name and not self.lock_button.isChecked():
            self.delete_button.setEnabled(True)
        else:
            self.delete_button.setEnabled(False)

    def delete_this_config(self, checked: Optional[bool] = None) -> None:
        """
        Helper function to facilitate the removal of this row.

        Parameters
        ----------
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        """
        self.overview.delete_row(self)


class Group(ConfigTextMixin, PageWidget):
    """
    The group of checklists and devices associated with a Configuration.

    From this widget we can edit name/description, add tags,
    add devices, and add checklists to the Configuration.

    Parameters
    ----------
    bridge : QDataclassBridge
        A dataclass bridge to an atef.check.Configuration dataclass.
        This will be used to update the dataclass and to listen for
        dataclass updates.
    """
    filename = 'config_group.ui'

    name_edit: QLineEdit
    desc_edit: QPlainTextEdit
    tags_content: QVBoxLayout
    add_tag_button: QToolButton
    devices_container: QWidget
    devices_content: QVBoxLayout
    checklists_container: QWidget
    checklists_content: QVBoxLayout
    add_checklist_button: QPushButton
    line_between_adds: QWidget

    bridge_item_map: Dict[QDataclassBridge, AtefItem]

    def __init__(
        self,
        bridge: QDataclassBridge,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(bridge, parent=parent)
        self.bridge_item_map = {}

    def assign_tree_item(self, item: AtefItem):
        super().assign_tree_item(item)
        self.initialize_group()

    def initialize_group(self) -> None:
        """
        Perform first-time setup for the widget.

        - Set up the name and desc text using the standard behavior
        - Set up the tag widget and make it functional
        - Hide the devices layout for PVConfiguration
        - Set up the devices widget for DeviceConfiguration
        - Set up the list of checklists widget
        - Set the sizing rules for the two columns
        """
        self.initialize_config_text()
        tags_list = StrList(
            data_list=self.bridge.tags,
            layout=QHBoxLayout(),
        )
        self.tags_content.addWidget(tags_list)

        def add_tag():
            if tags_list.widgets and not tags_list.widgets[-1].line_edit.text().strip():
                # Don't add another tag if we haven't filled out the last one
                return

            elem = tags_list.add_item('')
            elem.line_edit.setFocus()

        self.add_tag_button.clicked.connect(add_tag)

        if isinstance(self.bridge.data, PVConfiguration):
            self.devices_container.hide()
            self.line_between_adds.hide()
        else:
            devices_list = DeviceListWidget(
                data_list=self.bridge.devices,
            )
            self.devices_content.addWidget(devices_list)
        self.checklist_list = NamedDataclassList(
            data_list=self.bridge.checklist,
            layout=QVBoxLayout(),
        )
        self.checklist_list.bridge_item_removed.connect(
            self._cleanup_bridge_node,
        )
        self.checklists_content.addWidget(self.checklist_list)
        for bridge, widget in zip(
            self.checklist_list.bridges,
            self.checklist_list.widgets,
        ):
            item = self.setup_checklist_item_bridge(bridge)
            self.setup_child_nav_button(widget.child_button, item)
        self.add_checklist_button.clicked.connect(self.add_checklist)
        self.resize_columns()

    def resize_columns(self) -> None:
        """
        Set the column widths to be equal and less than half the full width.

        This only needs to be done for the device configuration. For PV
        configuration we only have one column.
        """
        if isinstance(self.bridge.data, DeviceConfiguration):
            full_width = self.width()
            col_width = int(full_width*0.45)
            self.checklists_container.setFixedWidth(col_width)
            self.devices_container.setFixedWidth(col_width)

    def resizeEvent(self, *args, **kwargs) -> None:
        """
        Override resizeEvent to update the column widths when we resize.
        """
        self.resize_columns()
        return super().resizeEvent(*args, **kwargs)

    def setup_checklist_item_bridge(
        self,
        bridge: QDataclassBridge,
    ) -> AtefItem:
        """
        Set up a single checklist item with a dataclass bridge.

        Parameters
        ----------
        bridge : QDataclassBridge
            A dataclass bridge to an instance of
            atef.check.IdentifierAndComparison
        """
        item = AtefItem(
            tree_parent=self.tree_item,
            name=bridge.name.get() or 'untitled',
            func_name='checklist',
        )
        page = IdAndCompWidget(bridge, type(self.bridge.data))
        link_page(item, page)
        self.bridge_item_map[bridge] = item
        bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )
        return item

    def add_checklist(
        self,
        checked: Optional[bool] = None,
        # TODO this doesn't exist
        # id_and_comp: Optional[IdentifierAndComparison] = None,
    ) -> None:
        """
        Add a new or existing checklist to the list of checklists.

        Parameters
        ----------
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        id_and_comp : IdentifierAndComparison, optional
            The checklist to add. If omitted, we'll create a blank checklist.
        """
        # TODO this doesn't exist
        # if id_and_comp is None:
        #     id_and_comp = IdentifierAndComparison()
        # widget, bridge = self.checklist_list.add_item(id_and_comp)
        # item = self.setup_checklist_item_bridge(bridge)
        # self.setup_child_nav_button(widget.child_button, item)

    def _cleanup_bridge_node(
        self,
        bridge: QDataclassBridge,
    ) -> None:
        """
        Remove the tree item and delete the bridge when we remove a checklist.

        Parameters
        ----------
        bridge: QDataclassBridge
            A dataclass bridge to an instance of
            atef.check.IdentifierAndComparison
        """
        item = self.bridge_item_map[bridge]
        self.tree_item.removeChild(item)
        del self.bridge_item_map[bridge]
        bridge.deleteLater()


class StrList(QWidget):
    """
    A widget used to modify the str variant of QDataclassList.

    Parameters
    ----------
    data_list : QDataclassList
        The dataclass list to edit using this widget.
    layout : QLayout
        The layout to use to arrange our labels. This should be an
        instantiated but not placed layout. This lets us have some
        flexibility in whether we arrange things horizontally,
        vertically, etc.
    """
    widgets: List[StrListElem]

    def __init__(
        self,
        data_list: QDataclassList,
        layout: QLayout,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.data_list = data_list
        self.setLayout(layout)
        self.widgets = []
        starting_list = data_list.get()
        if starting_list is not None:
            for starting_value in starting_list:
                self.add_item(starting_value, init=True)

    def add_item(
        self,
        starting_value: str,
        checked: Optional[bool] = None,
        init: bool = False,
    ) -> StrListElem:
        """
        Create and add new editable widget element to this widget's layout.

        This can either be an existing string on the dataclass list to keep
        track of, or it can be used to add a new string to the dataclass list.

        This method will also set up the signals and slots for the new widget.

        Parameters
        ----------
        starting_value : str
            The starting text value for the new widget element.
            This should match the text exactly for tracking existing
            strings.
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        init : bool, optional
            Whether or not this is the initial initialization of this widget.
            This will be set to True in __init__ so that we don't mutate
            the underlying dataclass. False, the default, means that we're
            adding a new string to the dataclass, which means we should
            definitely append it.

        Returns
        -------
        strlistelem : StrListElem
            The widget created by this function call.
        """
        new_widget = StrListElem(starting_value, parent=self)
        self.widgets.append(new_widget)
        if not init:
            self.data_list.append(starting_value)
        self.layout().addWidget(new_widget)
        new_widget.line_edit.textEdited.connect(
            partial(self.save_item_update, new_widget)
        )
        new_widget.del_button.clicked.connect(
            partial(self.remove_item, new_widget)
        )
        return new_widget

    def save_item_update(self, item: StrListElem, new_value: str) -> None:
        """
        Update the dataclass as appropriate when the user submits a new value.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has edited.
        new_value : str
            The value that the user has submitted.
        """
        index = self.widgets.index(item)
        self.data_list.put_to_index(index, new_value)

    def remove_item(self, item: StrListElem, checked: bool) -> None:
        """
        Update the dataclass as appropriate when the user removes a value.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has clicked the delete button for.
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        """
        index = self.widgets.index(item)
        self.widgets.remove(item)
        self.data_list.remove_index(index)
        item.deleteLater()


class StringListWithDialog(DesignerDisplay, QWidget):
    """
    A widget used to modify the str variant of QDataclassList, tied to a
    specific dialog that helps with selection of strings.

    The ``item_add_request`` signal must be hooked into with the
    caller-specific dialog tool.  This class may be subclassed to add this
    functionality.

    Parameters
    ----------
    data_list : QDataclassList
        The dataclass list to edit using this widget.

    allow_duplicates : bool, optional
        Allow duplicate entries in the list.  Defaults to False.
    """
    filename: ClassVar[str] = "string_list_with_dialog.ui"
    item_add_request: ClassVar[QSignal] = QSignal()
    item_edit_request: ClassVar[QSignal] = QSignal(list)  # List[str]

    button_add: QtWidgets.QToolButton
    button_layout: QtWidgets.QVBoxLayout
    button_remove: QtWidgets.QToolButton
    list_strings: QtWidgets.QListWidget

    def __init__(
        self,
        data_list: QDataclassList,
        allow_duplicates: bool = False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.data_list = data_list
        self.allow_duplicates = allow_duplicates
        self._setup_ui()

    def _setup_ui(self):
        starting_list = self.data_list.get()
        for starting_value in starting_list or []:
            self._add_item(starting_value, init=True)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # def test():
        #     text, success = QtWidgets.QInputDialog.getText(
        #         self, "Device name", "Device name?"
        #     )
        #     if success:
        #         self.add_items([item for item in text.strip().split() if item])

        self.button_add.clicked.connect(self.item_add_request.emit)
        self.button_remove.clicked.connect(self._remove_item_request)

        def _edit_item_request():
            self.item_edit_request.emit(self.selected_items_text)

        self.list_strings.doubleClicked.connect(_edit_item_request)

    def _add_item(self, item: str, *, init: bool = False):
        """
        Add an item to the QListWidget and the bridge (if init is not set).

        Parameters
        ----------
        item : str
            The item to add.

        init : bool, optional
            Whether or not this is the initial initialization of this widget.
            This will be set to True in __init__ so that we don't mutate
            the underlying dataclass. False, the default, means that we're
            adding a new dataclass to the list, which means we should
            definitely append it.
        """
        if not init:
            if not self.allow_duplicates and item in self.data_list.get():
                return

            self.data_list.append(item)

        self.list_strings.addItem(QtWidgets.QListWidgetItem(item))

    def add_items(self, items: List[str]) -> None:
        """
        Add one or more strings to the QListWidget and the bridge.

        Parameters
        ----------
        item : list of str
            The item(s) to add.
        """
        for item in items:
            self._add_item(item)

    @property
    def selected_items_text(self) -> List[str]:
        """
        The text of item(s) currently selected in the QListWidget.

        Returns
        -------
        selected : list of str
        """
        return [item.text() for item in list(self.list_strings.selectedItems())]

    def _remove_item_request(self):
        """Qt hook: user requested item removal."""
        for item in self.list_strings.selectedItems():
            self.data_list.remove_value(item.text())
            self.list_strings.takeItem(self.list_strings.row(item))

    def _remove_item(self, item: str) -> None:
        """
        Remove an item from the QListWidget and the bridge.

        Parameters
        ----------
        items : str
            The item to remove.
        """
        self.data_list.remove_value(item)
        for row in range(self.list_strings.count()):
            if self.list_strings.item(row).text() == item:
                self.list_strings.takeItem(row)
                return

    def remove_items(self, items: List[str]) -> None:
        """
        Remove items from the QListWidget and the bridge.

        Parameters
        ----------
        items : list of str
            The items to remove.
        """
        for item in items:
            self._remove_item(item)

    def _edit_item(self, old: str, new: str) -> None:
        """
        Edit an item in place in the QListWidget and the bridge.

        If we don't allow duplicates and new already exists, we
        need to remove old instead.

        Parameters
        ----------
        old : str
            The original item to replace
        new : str
            The new item to replace it with
        """
        if old == new:
            return
        if not self.allow_duplicates and new in self.data_list.get():
            return self._remove_item(old)
        self.data_list.put_to_index(
            index=self.data_list.get().index(old),
            new_value=new,
        )
        for row in range(self.list_strings.count()):
            if self.list_strings.item(row).text() == old:
                self.list_strings.item(row).setText(new)
                return

    def edit_items(self, old_items: List[str], new_items: List[str]) -> None:
        """
        Best-effort edit of items in place in the QListWidget and the bridge.

        The goal is to replace each instance of old with each instance of
        new, in order.
        """
        # Ignore items that exist in both lists
        old_uniques = [item for item in old_items if item not in new_items]
        new_uniques = [item for item in new_items if item not in old_items]
        # Remove items from new if duplicates aren't allowed and they exist
        if not self.allow_duplicates:
            new_uniques = [
                item for item in new_uniques if item not in self.data_list.get()
            ]
        # Add, remove, edit in place as necessary
        # This will edit everything in place if the lists are equal length
        # If old_uniques is longer, we'll remove when we exhaust new_uniques
        # If new_uniques is longer, we'll add when we exhaust old_uniques
        # TODO find a way to add these at the selected index
        for old, new in zip_longest(old_uniques, new_uniques, fillvalue=None):
            if old is None:
                self._add_item(new)
            elif new is None:
                self._remove_item(old)
            else:
                self._edit_item(old, new)

    def _show_context_menu(self, pos: QPoint):
        """
        Displays a context menu that provides copy & remove actions
        to the user

        Parameters
        ----------
        pos : QPoint
            Position to display the menu at
        """
        if len(self.list_strings.selectedItems()) <= 0:
            return

        menu = QMenu(self)

        def copy_selected():
            items = self.list_strings.selectedItems()
            text = '\n'.join([x.text() for x in items])
            if len(text) > 0:
                QGuiApplication.clipboard().setText(text, QClipboard.Mode.Clipboard)

        copy = menu.addAction('&Copy')
        copy.triggered.connect(copy_selected)

        remove = menu.addAction('&Remove')
        remove.triggered.connect(self._remove_item_request)

        menu.exec(self.mapToGlobal(pos))


class DeviceListWidget(StringListWithDialog):
    """
    Device list widget, with ``HappiSearchWidget`` for adding new devices.
    """

    _search_widget: Optional[HappiDeviceComponentWidget] = None

    def _setup_ui(self):
        super()._setup_ui()
        self.item_add_request.connect(self._open_device_chooser)
        self.item_edit_request.connect(self._open_device_chooser)

    def _open_device_chooser(self, to_select: Optional[List[str]] = None):
        """
        Hook: User requested adding/editing an existing device.

        Parameters
        ----------
        to_select : list of str, optional
            If provided, the device chooser will filter for these items.
        """
        self._search_widget = HappiDeviceComponentWidget(
            client=util.get_happi_client(),
            show_device_components=False,
        )
        self._search_widget.item_search_widget.happi_items_chosen.connect(
            self.add_items
        )
        self._search_widget.show()
        self._search_widget.activateWindow()
        self._search_widget.item_search_widget.edit_filter.setText(
            util.regex_for_devices(to_select)
        )


class ComponentListWidget(StringListWithDialog):
    """
    Component list widget using a ``HappiDeviceComponentWidget``.
    """

    _search_widget: Optional[HappiDeviceComponentWidget] = None
    suggest_comparison: QSignal = QSignal(Comparison)
    get_device_list: Optional[Callable[[], List[str]]]

    def __init__(
        self,
        data_list: QDataclassList,
        get_device_list: Optional[Callable[[], List[str]]] = None,
        allow_duplicates: bool = False,
        **kwargs,
    ):
        self.get_device_list = get_device_list
        super().__init__(data_list=data_list, allow_duplicates=allow_duplicates, **kwargs)

    def _setup_ui(self):
        super()._setup_ui()
        self.item_add_request.connect(self._open_component_chooser)
        self.item_edit_request.connect(self._open_component_chooser)

    def _open_component_chooser(self, to_select: Optional[List[str]] = None):
        """
        Hook: User requested adding/editing a componen.

        Parameters
        ----------
        to_select : list of str, optional
            If provided, the device chooser will filter for these items.
        """

        widget = HappiDeviceComponentWidget(
            client=util.get_happi_client()
        )
        widget.device_widget.custom_menu_helper = self._attr_menu_helper
        self._search_widget = widget
        # widget.item_search_widget.happi_items_chosen.connect(
        #    self.add_items
        # )
        widget.show()
        widget.activateWindow()

        if self.get_device_list is not None:
            try:
                device_list = self.get_device_list()
            except Exception as ex:
                device_list = []
                logger.debug("Failed to get device list", exc_info=ex)

            widget.item_search_widget.edit_filter.setText(
                util.regex_for_devices(device_list)
            )

    def _attr_menu_helper(self, data: List[OphydAttributeData]) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu()

        summary = OphydAttributeDataSummary.from_attr_data(*data)
        short_attrs = [datum.attr.split(".")[-1] for datum in data]

        def add_attrs():
            for datum in data:
                self._add_item(datum.attr)

        def add_without():
            add_attrs()

        def add_with_equals():
            add_attrs()
            comparison = Equals(
                name=f'{"_".join(short_attrs)}_auto',
                description=f'Comparison from: {", ".join(short_attrs)}',
                value=summary.average,
            )
            self.suggest_comparison.emit(comparison)

        def add_with_range():
            add_attrs()
            comparison = Range(
                name=f'{"_".join(short_attrs)}_auto',
                description=f'Comparison from: {", ".join(short_attrs)}',
                low=summary.minimum,
                high=summary.maximum,
            )
            self.suggest_comparison.emit(comparison)

        menu.addSection("Add all selected")
        add_without_action = menu.addAction("Add selected without comparison")
        add_without_action.triggered.connect(add_without)

        if summary.average is not None:
            add_with_equals_action = menu.addAction(
                f"Add selected with Equals comparison (={summary.average})"
            )
            add_with_equals_action.triggered.connect(add_with_equals)

        if summary.minimum is not None:
            add_with_range_action = menu.addAction(
                f"Add selected with Range comparison "
                f"[{summary.minimum}, {summary.maximum}]"
            )
            add_with_range_action.triggered.connect(add_with_range)

        menu.addSection("Add single attribute")
        for attr in data:
            def add_single_attr(*, attr_name: str = attr.attr):
                self._add_item(attr_name)

            action = menu.addAction(f"Add {attr.attr}")
            action.triggered.connect(add_single_attr)

        return menu


class BulkListWidget(StringListWithDialog):
    """
    String list widget that uses a multi-line text box for entry and edit.
    """

    def _setup_ui(self):
        super()._setup_ui()
        self.item_add_request.connect(self._open_multiline)
        self.item_edit_request.connect(self._open_multiline)

    def _open_multiline(self, to_select: Optional[List[str]] = None):
        """
        User requested adding new strings or editing existing ones.

        Parameters
        ----------
        to_select : list of str, optional
            For editing, this will contain the string items that are
            selected so that we can pre-populate the edit box
            appropriately.
        """
        to_select = to_select or []
        if to_select:
            title = 'Edit PVs Dialog'
            label = 'Add to or edit these PVs as appropriate:'
            text = '\n'.join(to_select)
        else:
            title = 'Add PVs Dialog'
            label = 'Which PVs should be included?'
            text = ''
        user_input, ok = QInputDialog.getMultiLineText(
            self, title, label, text
        )
        if not ok:
            return
        new_pvs = [pv.strip() for pv in user_input.splitlines() if pv.strip()]
        self.edit_items(to_select, new_pvs)


class NamedDataclassList(StrList):
    """
    A widget used to modify a QDataclassList with named dataclass elements.

    A named dataclass is any dataclass element with a str "name" field.
    This widget will allow us to add elements to the list by name,
    display the names, modify the names, add blank entries, etc.

    Parameters
    ----------
    data_list : QDataclassList
        The dataclass list to edit using this widget.
    layout : QLayout
        The layout to use to arrange our labels. This should be an
        instantiated but not placed layout. This lets us have some
        flexibility in whether we arrange things horizontally,
        vertically, etc.
    """
    bridge_item_removed = QSignal(QDataclassBridge)
    bridges: List[QDataclassBridge]

    def __init__(self, *args, **kwargs):
        self.bridges = []
        super().__init__(*args, **kwargs)

    def add_item(
        self,
        starting_value: Any,
        checked: Optional[bool] = None,
        init: bool = False,
    ) -> Tuple[QWidget, QDataclassBridge]:
        """
        Create and add new editable widget element to this widget's layout.

        This can either be an existing dataclass for the list to keep
        track of, or it can be used to add a new dataclass to the list.

        This method will also set up the signals and slots for the new widget.

        Unlike the parent class, this will set up and return a
        QDataclassBridge that can be used to manage edits and updates to the
        dataclass. This bridge will be configured to link edits to the
        text widget with edits to the name field.

        Parameters
        ----------
        starting_value : Any dataclass
            The starting dataclass for the new widget element.
            This should be the actual dataclass for tracking existing
            dataclasses.
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        init : bool, optional
            Whether or not this is the initial initialization of this widget.
            This will be set to True in __init__ so that we don't mutate
            the underlying dataclass. False, the default, means that we're
            adding a new dataclass to the list, which means we should
            definitely append it.

        Returns
        -------
        strlistelem : StrListElem
            The widget created by this function call.
        """
        if not init:
            self.data_list.append(starting_value)
        new_widget = super().add_item(
            starting_value=starting_value.name,
            checked=checked,
            init=True,
        )
        bridge = QDataclassBridge(starting_value, parent=self)
        self._setup_bridge_signals(bridge, new_widget)
        self.bridges.append(bridge)
        return new_widget, bridge

    def _setup_bridge_signals(
        self,
        bridge: QDataclassBridge,
        widget: StrListElem,
    ) -> None:
        """
        Set up all the signals needed for a widget element and its bridge.

        Parameters
        ----------
        bridge : QDataclassBridge
            A bridge to the dataclass associated with the widget element.
        widget : StrListElem
            The widget element to link.
        """
        bridge.name.changed_value.connect(widget.on_data_changed)

    def save_item_update(self, item: StrListElem, new_value: str) -> None:
        """
        Update the dataclass as appropriate when the user submits a new value.

        Unlike the parent class, this will update the name field rather than
        replace the entire string object.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has edited.
        new_value : str
            The value that the user has submitted.
        """
        index = self.widgets.index(item)
        self.bridges[index].name.put(new_value)

    def remove_item(self, item: StrListElem, checked: bool) -> None:
        """
        Update the dataclass as appropriate when the user removes a value.

        Parameters
        ----------
        item : StrListElem
            The widget that the user has clicked the delete button for.
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        """
        index = self.widgets.index(item)
        super().remove_item(item=item, checked=checked)
        bridge = self.bridges[index]
        self.bridge_item_removed.emit(bridge)
        del self.bridges[index]

    def update_item_bridge(
        self,
        old_bridge: QDataclassBridge,
        new_bridge: QDataclassBridge,
    ) -> None:
        """
        Replace an existing bridge with a new bridge.

        This can be useful if you need to change out an entire dataclass,
        as may need to be done when the user requests a change of
        Comparison type.

        Internally, this handles any widget-specific setup of the new bridge
        and as much teardown as we can do to the old bridge.

        Parameters
        ----------
        old_bridge : QDataclassBridge
            The existing bridge that we'd like to replace.
        new_bridge : QDataclassBridge
            The new bridge that we'd like to replace it with.
        """
        index = self.bridges.index(old_bridge)
        self.bridges[index] = new_bridge
        new_bridge.setParent(self)
        self._setup_bridge_signals(
            new_bridge,
            self.widgets[index],
        )
        old_bridge.deleteLater()
        self.data_list.put_to_index(index, new_bridge.data)


class StrListElem(DesignerDisplay, QWidget):
    """
    A single element for the StrList widget.

    Has a QLineEdit for changing the text and a delete button.
    Changes its style to no frame when it has text and is out of focus.
    Only shows the delete button when the text is empty.

    The StrList widget is responsible for connecting this widget
    to the dataclass bridge.
    """
    filename = 'str_list_elem.ui'

    line_edit: QLineEdit
    del_button: QToolButton
    child_button: QToolButton

    def __init__(self, start_text: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.line_edit.setText(start_text)
        edit_filter = FrameOnEditFilter(parent=self)
        edit_filter.set_no_edit_style(self.line_edit)
        self.line_edit.installEventFilter(edit_filter)
        self.on_text_changed(start_text)
        self.line_edit.textChanged.connect(self.on_text_changed)
        self.child_button.hide()
        icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        self.del_button.setIcon(icon)

    def on_text_changed(self, text: str) -> None:
        """
        Edit our various visual elements when the user edits the text field.

        This will do all of the following:
        - make the delete button show only when the text field is empty
        - adjust the size of the text field to be roughly the size of the
          string we've inputted
        """
        # Show or hide the del button as needed
        self.del_button.setVisible(not text)
        # Adjust the width to match the text
        match_line_edit_text_width(self.line_edit, text=text)

    def on_data_changed(self, data: str) -> None:
        """
        Change the text displayed here using new data, if needed.
        """
        if self.line_edit.text() != data:
            self.line_edit.setText(data)


def match_line_edit_text_width(
    line_edit: QLineEdit,
    text: Optional[str] = None,
    minimum: int = 40,
    buffer: int = 10,
) -> None:
    """
    Set the width of a line edit to match the text length.

    You can use this in a slot and connect it to the line edit's
    "textChanged" signal. This creates an effect where the line
    edit will get longer when the user types text into it and
    shorter when the user deletes text from it.

    Parameters
    ----------
    line_edit : QLineEdit
        The line edit whose width you'd like to adjust.
    text : str, optional
        The text to use as the basis for our size metrics.
        In a slot you could pass in the text we get from the
        signal update. If omitted, we'll use the current text
        in the widget.
    minimum : int, optional
        The minimum width of the line edit, even when we have no
        text. If omitted, we'll use a default value.
    buffer : int, optional
        The buffer we have on the right side of the rightmost
        character in the line_edit before the edge of the widget.
        If omitted, we'll use a default value.
    """
    font_metrics = line_edit.fontMetrics()
    if text is None:
        text = line_edit.text()
    width = font_metrics.boundingRect(text).width()
    line_edit.setFixedWidth(max(width + buffer, minimum))


class FrameOnEditFilter(QObject):
    """
    A QLineEdit event filter for editing vs not editing style handling.

    This will make the QLineEdit look like a QLabel when the user is
    not editing it.
    """
    def eventFilter(self, object: QLineEdit, event: QEvent) -> bool:
        # Even if we install only on line edits, this can be passed a generic
        # QWidget when we remove and clean up the line edit widget.
        if not isinstance(object, QLineEdit):
            return False
        if event.type() == QEvent.FocusIn:
            self.set_edit_style(object)
            return True
        if event.type() == QEvent.FocusOut:
            self.set_no_edit_style(object)
            return True
        return False

    @staticmethod
    def set_edit_style(object: QLineEdit):
        """
        Set a QLineEdit to the look and feel we want for editing.

        Parameters
        ----------
        object : QLineEdit
            Any line edit widget.
        """
        object.setFrame(True)
        color = object.palette().color(QPalette.ColorRole.Base)
        object.setStyleSheet(
            f"QLineEdit {{ background: rgba({color.red()},"
            f"{color.green()}, {color.blue()}, {color.alpha()})}}"
        )
        object.setReadOnly(False)

    @staticmethod
    def set_no_edit_style(object: QLineEdit):
        """
        Set a QLineEdit to the look and feel we want for not editing.

        Parameters
        ----------
        object : QLineEdit
            Any line edit widget.
        """
        if object.text():
            object.setFrame(False)
            object.setStyleSheet(
                "QLineEdit { background: transparent }"
            )
        object.setReadOnly(True)


class IdAndCompWidget(ConfigTextMixin, PageWidget):
    """
    A widget to manage the ids and comparisons associated with a checklist.

    Parameters
    ----------
    bridge : QDataclassBridge
        A dataclass bridge to an atef.check.IdentifierAndComparison instance.
    config_type : DeviceConfiguration or PVConfiguration
        The type associated with this configuration. There are two types of
        checklists: those that reference ophyd objects, and those that
        reference PVs.
    """
    filename = 'id_and_comp.ui'

    name_edit: QLineEdit
    id_label: QLabel
    id_container: QWidget
    id_content: QVBoxLayout
    comp_label: QLabel
    comp_container: QWidget
    comp_content: QVBoxLayout
    add_comp_button: QPushButton

    bridge: QDataclassBridge
    config_type: Type[Configuration]
    bridge_item_map: Dict[QDataclassBridge, AtefItem]

    def __init__(
        self,
        bridge: QDataclassBridge,
        config_type: Type[Configuration],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(bridge, parent=parent)
        self.config_type = config_type
        self.bridge_item_map = {}

    def assign_tree_item(self, item: AtefItem):
        super().assign_tree_item(item)
        self.initialize_idcomp()

    def _add_suggested_comparison(self, comparison: Comparison):
        self.add_comparison(comparison=comparison)

    def get_device_list(self) -> List[str]:
        """Get the device list, if applicable."""
        config = self.get_configuration()
        if isinstance(config, DeviceConfiguration):
            return config.devices

        return []

    def initialize_idcomp(self) -> None:
        """
        Perform first-time setup of this widget.

        Does the following:
        - Connects the name field with the dataclass
        - Sets up the list of PVs or Devices and adjusts the label
        - Sets up the list of comparisons
        """
        # Connect the name to the dataclass
        self.initialize_config_name()
        # Set up editing of the identifiers list
        if issubclass(self.config_type, DeviceConfiguration):
            self.id_label.setText("Device Signals")
            identifiers_list = ComponentListWidget(
                get_device_list=self.get_device_list,
                data_list=self.bridge.ids,
            )
            identifiers_list.suggest_comparison.connect(self._add_suggested_comparison)
        elif issubclass(self.config_type, PVConfiguration):
            self.id_label.setText("PV Names")
            identifiers_list = BulkListWidget(
                data_list=self.bridge.ids,
            )

        self.id_content.addWidget(identifiers_list)
        self.comparison_list = NamedDataclassList(
            data_list=self.bridge.comparisons,
            layout=QVBoxLayout(),
        )
        self.comparison_list.bridge_item_removed.connect(
            self._cleanup_bridge_node,
        )
        self.comp_content.addWidget(self.comparison_list)
        for bridge, widget in zip(
            self.comparison_list.bridges,
            self.comparison_list.widgets,
        ):
            item = self.setup_comparison_item_bridge(bridge)
            self.setup_child_nav_button(widget.child_button, item)
        self.add_comp_button.clicked.connect(self.add_comparison)
        self.resize_columns()

    def resize_columns(self) -> None:
        """
        Set the column widths to be equal and less than half the full width.
        """
        full_width = self.width()
        col_width = int(full_width*0.45)
        self.id_container.setFixedWidth(col_width)
        self.comp_container.setFixedWidth(col_width)

    def resizeEvent(self, *args, **kwargs) -> None:
        """
        Override resizeEvent to update the column widths when we resize.
        """
        self.resize_columns()
        return super().resizeEvent(*args, **kwargs)

    def setup_comparison_item_bridge(
        self,
        bridge: QDataclassBridge,
    ) -> AtefItem:
        """
        Create the AtefItem associated with a bridge and set it up.

        These items handle the tree entry and loading of the subscreen.

        Parameters
        ----------
        bridge : QDataclassBridge
            A dataclass bridge to an instance of
            atef.check.IdentifierAndComparison
        """
        item = AtefItem(
            tree_parent=self.tree_item,
            name=bridge.name.get() or 'untitled',
            func_name='comparison',
        )
        page = CompView(bridge, self)
        link_page(item, page)
        self.bridge_item_map[bridge] = item
        self._setup_bridge_signals(bridge)
        return item

    def _setup_bridge_signals(self, bridge: QDataclassBridge) -> None:
        """
        Set up all the relevant signals for a QDataclassBridge.

        Currently, this just makes it so that when you edit the
        name field, the tree entry updates its text.

        Parameters
        ----------
        bridge : QDataclassBridge

        """
        item = self.bridge_item_map[bridge]
        bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )

    def update_comparison_bridge(
        self,
        old_bridge: QDataclassBridge,
        new_bridge: QDataclassBridge,
    ) -> None:
        """
        Swap out an underlying QDataclassBridge.

        This is used when the user wants to change a comparison's class.

        Parameters
        ----------
        old_bridge : QDataclassBridge
            The previous existing bridge.
        new_bridge : QDataclassBridge
            The new bridge to replace it with.
        """
        self.comparison_list.update_item_bridge(old_bridge, new_bridge)
        item = self.bridge_item_map[old_bridge]
        self.bridge_item_map[new_bridge] = item
        self._setup_bridge_signals(new_bridge)

    def add_comparison(
        self,
        checked: Optional[bool] = None,
        comparison: Optional[Comparison] = None,
    ) -> None:
        """
        Add a new or existing comparison to the list.

        Parameters
        ----------
        checked : bool, optional
            This argument is unused, but it will be sent by various button
            widgets via the "clicked" signal so it must be present.
        comparison : Comparison subclass, optional
            The specific comparison instance to add.
            If omitted, we'll create a blank atef.check.Equals instance
            as a default.
        """
        if comparison is None:
            # Empty default
            comparison = Equals()
        widget, bridge = self.comparison_list.add_item(comparison)
        item = self.setup_comparison_item_bridge(bridge)
        self.setup_child_nav_button(widget.child_button, item)

    def _cleanup_bridge_node(
        self,
        bridge: QDataclassBridge,
    ) -> None:
        """
        Remove the tree item and delete the bridge when we remove comparisons.

        Parameters
        ----------
        bridge: QDataclassBridge
            A dataclass bridge to an instance of
            atef.check.IdentifierAndComparison
        """
        item = self.bridge_item_map[bridge]
        self.tree_item.removeChild(item)
        del self.bridge_item_map[bridge]
        bridge.deleteLater()


class CompView(ConfigTextMixin, PageWidget):
    """
    Widget to view and edit a single Comparison subclass.

    This contains some generic fields common to all Comparison
    subclasses, and then a placeholder for Comparison-specific
    widgets to be loaded into.

    Comparison subclasses can be registered for use here by
    calling the register_comparison classmethod, which is
    called automatically in the CompMixin helper class.

    Parameters
    ----------
    bridge : QDataclassBridge
        A dataclass bridge that points to a subclass of Comparison.
    id_and_comp : IdAndCompWidget
        The widget that created and owns this widget.
        This is used in place of parent to be more robust to
        structural changes for us to access the checklist when we
        need to change the data type.
    """
    filename = 'comp_view.ui'

    name_edit: QLineEdit
    desc_edit: QPlainTextEdit
    comp_type_combo: QComboBox
    specific_content: QVBoxLayout
    generic_content: QFormLayout
    invert_combo: QComboBox
    reduce_period_edit: QLineEdit
    reduce_method_combo: QComboBox
    string_combo: QComboBox
    sev_on_failure_combo: QComboBox
    if_disc_combo: QComboBox

    specific_comparison_widgets: ClassVar[dict[type: type]] = {}
    data_types: ClassVar[dict[str: type]] = {}

    bool_choices = ('False', 'True')
    severity_choices = tuple(sev.name for sev in Severity)
    reduce_choices = tuple(red.name for red in ReduceMethod)

    invert_combo_items = bool_choices
    reduce_method_combo_items = reduce_choices
    string_combo_items = bool_choices
    sev_on_failure_combo_items = severity_choices
    if_disc_combo_items = severity_choices

    @classmethod
    def register_comparison(
        cls,
        dataclass_type: Type[Comparison],
        widget_type: Type[QWidget],
    ) -> None:
        """
        Register a comparison to be added to the combobox options.

        Parameters
        ----------
        dataclass_type : any Comparison subclass
            The comparison type to register.
        widget_type : QWidget
            The widget to load for that comparison type. Must accept
            a QDataclassBridge to the comparison instance as its
            first positional argument.
        """
        cls.specific_comparison_widgets[dataclass_type] = widget_type
        cls.data_types[dataclass_type.__name__] = dataclass_type

    def __init__(
        self,
        bridge: QDataclassBridge,
        id_and_comp: IdAndCompWidget,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(bridge, parent=parent)
        self.id_and_comp = id_and_comp
        self.comparison_setup_done = False

    def assign_tree_item(self, item: AtefItem):
        super().assign_tree_item(item)
        self.initialize_comp_view()

    def initialize_comp_view(self) -> None:
        """
        First time setup for the widget.

        - Populates the combo box with options
        - Switches the combobox to match the loaded type
        - Calls change_comparison_type with the initial type
        - Sets up the combobox signals and slots
        """
        last_added_index = 0
        for type_name, data_type in self.data_types.items():
            self.comp_type_combo.addItem(type_name)
            if isinstance(self.bridge.data, data_type):
                self.comp_type_combo.setCurrentIndex(last_added_index)
            last_added_index += 1
        self.change_comparison_type(type(self.bridge.data))
        self.comp_type_combo.currentTextChanged.connect(
            self._comp_type_from_combobox,
        )

    def _comp_type_from_combobox(self, type_name: str) -> None:
        """
        Call change_comparison_type as a combobox slot.

        Changes the argument to the type name as a string rather than as
        the type itself.

        Parameters
        ----------
        type_name : str
            The string name of the comparison type.
        """
        return self.change_comparison_type(self.data_types[type_name])

    def change_comparison_type(self, new_type: Type[Comparison]) -> None:
        """
        Switch the comparison from one type to another.

        On the first pass, this just needs to connect the generic
        widgets to our dataclass and setup the type-specific edit
        widgets by delegating to the appropriate widget class.

        On subsequent calls, this also needs to do the following:
        - create new dataclass
        - create new bridge
        - transfer over any matching fields
        - update the parent bridge about our new dataclass
        - clean up the old widget
        - swap out the edit widgets for the appropriate version
        - connect everything to the new bridge'

        Parameters
        ----------
        new_type : Comparison subclass
            The class to switch our comparison type to.
        """
        if self.comparison_setup_done:
            # Clean up the previous widget
            self.specific_widget.deleteLater()
            # Clean up the previous bridge
            old_bridge = self.bridge
            old_data = self.bridge.data
            # Create a new dataclass, transferring over any compatible data
            new_data = cast_dataclass(old_data, new_type)
            # Create a new bridge, seeded with the new dataclass
            new_bridge = QDataclassBridge(new_data)
            self.bridge = new_bridge
            # Replace our bridge in the parent as appropriate
            self.id_and_comp.update_comparison_bridge(old_bridge, new_bridge)
        # Redo the text setup with the new bridge (or maybe the first time)
        self.initialize_config_text()
        # Set up the widget specific items
        try:
            widget_class = self.specific_comparison_widgets[new_type]
        except KeyError:
            raise TypeError(
                f'{new_type} is not a registered type for CompView. '
                'Currently the registered types are '
                f'{tuple(self.specific_comparison_widgets)}'
            )
        self.specific_widget = widget_class(self.bridge)
        self.specific_content.addWidget(self.specific_widget)

        if not self.comparison_setup_done:
            # Fill the generic combobox options
            for text in self.invert_combo_items:
                self.invert_combo.addItem(text)
            for text in self.reduce_method_combo_items:
                self.reduce_method_combo.addItem(text)
            for text in self.string_combo_items:
                self.string_combo.addItem(text)
            for text in self.sev_on_failure_combo_items:
                self.sev_on_failure_combo.addItem(text)
            for text in self.if_disc_combo_items:
                self.if_disc_combo.addItem(text)
            # Set up starting values based on the dataclass values
            self.invert_combo.setCurrentIndex(int(self.bridge.invert.get()))
            reduce_period = self.bridge.reduce_period.get()
            if reduce_period is not None:
                self.reduce_period_edit.setText(str(reduce_period))
            self.reduce_method_combo.setCurrentIndex(
                self.reduce_method_combo_items.index(
                    self.bridge.reduce_method.get().name
                )
            )
            string_opt = self.bridge.string.get() or False
            self.string_combo.setCurrentIndex(int(string_opt))
            self.sev_on_failure_combo.setCurrentIndex(
                self.sev_on_failure_combo_items.index(
                    self.bridge.severity_on_failure.get().name
                )
            )
            self.if_disc_combo.setCurrentIndex(
                self.if_disc_combo_items.index(
                    self.bridge.if_disconnected.get().name
                )
            )
            # Set up the generic item signals in order from top to bottom
            self.invert_combo.currentIndexChanged.connect(
                self.new_invert_combo
            )
            self.reduce_period_edit.textEdited.connect(
                self.new_reduce_period_edit
            )
            self.reduce_method_combo.currentTextChanged.connect(
                self.new_reduce_method_combo
            )
            self.string_combo.currentIndexChanged.connect(
                self.new_string_combo
            )
            self.sev_on_failure_combo.currentTextChanged.connect(
                self.new_sev_on_failure_combo
            )
            self.if_disc_combo.currentTextChanged.connect(
                self.new_if_disc_combo
            )
            self.comparison_setup_done = True

    def new_invert_combo(self, index: int) -> None:
        """
        Slot to handle user input in the generic "Invert" combo box.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        index : int
            The index the user selects in the combo box.
        """
        self.bridge.invert.put(bool(index))

    def new_reduce_period_edit(self, value: str) -> None:
        """
        Slot to handle user intput in the generic "Reduce Period" line edit.

        Tries to interpet user input as a float. If this is not possible,
        the period will be stored as zero.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        value : str
            The string contents of the line edit.
        """
        try:
            value = float(value)
        except Exception:
            value = 0
        self.bridge.reduce_period.put(value)

    def new_reduce_method_combo(self, value: str) -> None:
        """
        Slot to handle user input in the generic "Reduce Method" combo box.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        value : str
            The string contents of the combo box.
        """
        self.bridge.reduce_method.put(ReduceMethod[value])

    def new_string_combo(self, index: int) -> None:
        """
        Slot to handle user input in the generic "String" combo box.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        index : int
            The integer index of the combo box.
        """
        self.bridge.string.put(bool(index))

    def new_sev_on_failure_combo(self, value: str) -> None:
        """
        Slot to handle user input in the "Severity on Failure" combo box.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        value : str
            The string contents of the combo box.
        """
        self.bridge.severity_on_failure.put(Severity[value])

    def new_if_disc_combo(self, value: str):
        """
        Slot to handle user input in the "If Disconnected" combo box.

        Uses the current bridge to mutate the stored dataclass.

        Parameters
        ----------
        value : str
            The string contents of the combo box.
        """
        self.bridge.if_disconnected.put(Severity[value])


def cast_dataclass(data: Any, new_type: Type) -> Any:
    """
    Convert one dataclass to another, keeping values in any same-named fields.

    Parameters
    ----------
    data : Any dataclass instance
        The dataclass instance that we'd like to convert.
    new_type : Any dataclass
        The dataclass type that we'd like to convert.

    Returns
    -------
    casted_data : instance of new_type
        The new dataclass instance.
    """
    new_fields = dataclasses.fields(new_type)
    field_names = set(field.name for field in new_fields)
    new_kwargs = {
        key: value for key, value in dataclasses.asdict(data).items()
        if key in field_names
    }
    return new_type(**new_kwargs)


class CompMixin:
    """
    Helper class for creating comparison widgets.

    Include as one of the parent classes and define the data_type classvar
    to ensure the widget is included as an option in CompView.
    """
    data_type: ClassVar[type]

    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        CompView.register_comparison(cls.data_type, cls)


def user_string_to_bool(text: str) -> bool:
    """
    Interpret a user's input as a boolean value.

    Strings like "true" should evaluate to True, strings
    like "fa" should evaluate to False, numeric inputs like
    1 or 2 should evaluate to True, numeric inputs like 0 or
    0.0 should evaluate to False, etc.

    Parameters
    ----------
    text : str
        The user's text input as a string. This is usually
        the value directly from a line edit widget.
    """
    if not text:
        return False
    try:
        if text[0].lower() in ('n', 'f', '0'):
            return False
    except (IndexError, AttributeError):
        # Not a string, let's be slightly helpful
        return bool(text)
    return True


def setup_line_edit_data(
    line_edit: QLineEdit,
    value_obj: QDataclassValue,
    from_str: Callable[[str], Any],
    to_str: Callable[[Any], str],
) -> None:
    """
    Setup a line edit for bilateral data exchange with a bridge.

    Parameters
    ----------
    line_edit : QLineEdit
        The line edit to set up.
    value_obj : QDataclassValue
        The bridge member that has the value we care about.
    from_str : callable
        A callable from str to the dataclass value. This is used
        to interpret the contents of the line edit.
    to_str : callable
        A callable from the dataclass value to str. This is used
        to fill the line edit when the dataclass updates.
    """
    def update_dataclass(text: str) -> None:
        try:
            value = from_str(text)
        except ValueError:
            return
        value_obj.put(value)

    def update_widget(value: Any) -> None:
        if not line_edit.hasFocus():
            try:
                text = to_str(value)
            except ValueError:
                return
            line_edit.setText(text)

    starting_value = value_obj.get()
    starting_text = to_str(starting_value)
    line_edit.setText(starting_text)
    line_edit.textEdited.connect(update_dataclass)
    value_obj.changed_value.connect(update_widget)


def setup_line_edit_sizing(
    line_edit: QLineEdit,
    minimum: int,
    buffer: int,
) -> None:
    """
    Setup a line edit for dynamic resizing as the text changes.

    Parameters
    ----------
    line_edit : QLineEdit,
        The line edit to setup.
    minimum : int
        Minimum width of the line edit in pixels.
    buffer : int
        Extra width beyond the exact size of the text in pixels.
    """
    def update_sizing(text):
        match_line_edit_text_width(
            line_edit=line_edit,
            text=text,
            minimum=minimum,
            buffer=buffer,
        )

    line_edit.textChanged.connect(update_sizing)
    update_sizing(line_edit.text())


def setup_line_edit_all(
    line_edit: QLineEdit,
    value_obj: QDataclassValue,
    from_str: Callable[[str], Any],
    to_str: Callable[[Any], str],
    minimum: int,
    buffer: int,
) -> None:
    """Combination of setup_line_edit_data and setup_line_edit_sizing."""
    setup_line_edit_data(
        line_edit=line_edit,
        value_obj=value_obj,
        from_str=from_str,
        to_str=to_str,
    )
    setup_line_edit_sizing(
        line_edit=line_edit,
        minimum=minimum,
        buffer=buffer,
    )


class BasicSymbolMixin:
    """
    Mix-in class for very basic comparisons.

    These are comparisons that can be summarized by a single
    symbol and follow some uniform conventions for the
    main value widget.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to a compatible dataclass.
        This widget is expecting a "value" attribute on
        the dataclass.
    """
    symbol: str
    value_edit: QLineEdit
    comp_symbol_label: QLabel

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_symbol_comparison()

    def _setup_symbol_comparison(self):
        """
        Basic setup for these simple symbol comparisons.

        - Puts the correct symbol into the GUI
        - Sets up the "value_edit" line edit to resize
          and be linked to the dataclass's value.
        """
        self.comp_symbol_label.setText(self.symbol)
        setup_line_edit_all(
            line_edit=self.value_edit,
            value_obj=self.bridge.value,
            from_str=float,
            to_str=str,
            minimum=30,
            buffer=15,
        )


class EqualsWidget(CompMixin, BasicSymbolMixin, PageWidget):
    """
    Widget to handle the fields unique to the "Equals" Comparison.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Equals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    filename = 'comp_equals.ui'
    data_type = Equals
    symbol = '='
    label_to_type: Dict[str, type] = {
        'float': float,
        'integer': int,
        'bool': bool,
        'string': str,
    }
    type_to_label: Dict[type, str] = {
        value: key for key, value in label_to_type.items()
    }
    cast_from_user_str: Dict[type, Callable[[str], bool]] = {
        tp: tp for tp in type_to_label
    }
    cast_from_user_str[bool] = user_string_to_bool

    range_label: QLabel
    atol_label: QLabel
    atol_edit: QLineEdit
    rtol_label: QLabel
    rtol_edit: QLineEdit
    data_type_label: QLabel
    data_type_combo: QComboBox

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_equals_widget()

    def setup_equals_widget(self) -> None:
        """
        Do all the setup needed to make this widget functional.

        Things handled here:
        - Set up the data type selection to know whether or not
          atol/rtol/range means anything and so that we can allow
          things like numeric strings. Use this selection to cast
          the input from the value text box.
        - Fill in the starting values for atol and rtol.
        - Connect the various edit widgets to their correspoinding
          data fields
        - Set up the range_label for a summary of the allowed range
        """
        for option in self.label_to_type:
            self.data_type_combo.addItem(option)
        setup_line_edit_data(
            line_edit=self.atol_edit,
            value_obj=self.bridge.atol,
            from_str=float,
            to_str=str,
        )
        setup_line_edit_data(
            line_edit=self.rtol_edit,
            value_obj=self.bridge.rtol,
            from_str=float,
            to_str=str,
        )
        starting_value = self.bridge.value.get()
        self.data_type_combo.setCurrentText(
            self.type_to_label[type(starting_value)]
        )
        self.update_range_label(starting_value)
        self.data_type_combo.currentTextChanged.connect(self.new_gui_type)
        self.bridge.value.changed_value.connect(self.update_range_label)
        self.bridge.atol.changed_value.connect(self.update_range_label)
        self.bridge.rtol.changed_value.connect(self.update_range_label)

    def update_range_label(self, *args, **kwargs) -> None:
        """
        Update the range label as appropriate.

        If our value is an int or float, this will do calculations
        using the atol and rtol to report the tolerance
        of the range to the user.

        If our value is a bool, this will summarize whether our
        value is being interpretted as True or False.
        """
        value = self.bridge.value.get()
        if not isinstance(value, (int, float, bool)):
            return
        if isinstance(value, bool):
            text = f' ({value})'
        else:
            atol = self.bridge.atol.get() or 0
            rtol = self.bridge.rtol.get() or 0

            diff = atol + abs(rtol * value)
            text = f'± {diff:.3g}'
        self.range_label.setText(text)

    def value_from_str(
        self,
        value: Optional[str] = None,
        gui_type_str: Optional[str] = None,
    ) -> PrimitiveType:
        """
        Convert our line edit value into a string based on the combobox.

        Parameters
        ----------
        value : str, optional
            The text contents of our line edit.
        gui_type_str : str, optional
            The text contents of our combobox.

        Returns
        -------
        converted : Any
            The casted datatype.
        """
        if value is None:
            value = self.value_edit.text()
        if gui_type_str is None:
            gui_type_str = self.data_type_combo.currentText()
        type_cast = self.cast_from_user_str[self.label_to_type[gui_type_str]]
        return type_cast(value)

    def new_gui_type(self, gui_type_str: str) -> None:
        """
        Slot for when the user changes the GUI data type.

        Re-interprets our value as the selected type. This will
        update the current value in the bridge as appropriate.

        If we have a numeric type, we'll enable the range and
        tolerance widgets. Otherwise, we'll disable them.

        Parameters
        ----------
        gui_type_str : str
            The user's text input from the data type combobox.
        """
        gui_type = self.label_to_type[gui_type_str]
        # Try the gui value first
        try:
            new_value = self.value_from_str(gui_type_str=gui_type_str)
        except ValueError:
            # Try the bridge value second, or give up
            try:
                new_value = gui_type(self.bridge.value.get())
            except ValueError:
                new_value = None
        if new_value is not None:
            self.bridge.value.put(new_value)
        self.range_label.setVisible(gui_type in (int, float, bool))
        tol_vis = gui_type in (int, float)
        self.atol_label.setVisible(tol_vis)
        self.atol_edit.setVisible(tol_vis)
        self.rtol_label.setVisible(tol_vis)
        self.rtol_edit.setVisible(tol_vis)


class NotEqualsWidget(EqualsWidget):
    """
    Variant of the "EqualsWidget" for the "NotEquals" case.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to a "NotEquals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    data_type = NotEquals
    symbol = '≠'


class GtLtBaseWidget(BasicSymbolMixin, PageWidget):
    """
    Base widget for comparisons like greater, less, etc.

    This class cannot be used on its own to manipulate the
    configuration. It must be subclassed to define "data_type"
    and "symbol".

    These comparisons have the following properties in common:
    - The only unique field is "value"
    - The comparison can be represented by a single symbol
    """
    filename = 'comp_float_gtlt.ui'


class GreaterWidget(CompMixin, GtLtBaseWidget):
    """
    Widget to handle the "Greater" comparison.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Equals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    data_type = Greater
    symbol = '>'


class GreaterOrEqualWidget(CompMixin, GtLtBaseWidget):
    """
    Widget to handle the "GreaterOrEqual" comparison.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Equals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    data_type = GreaterOrEqual
    symbol = '≥'


class LessWidget(CompMixin, GtLtBaseWidget):
    """
    Widget to handle the "Less" comparison.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Equals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    data_type = Less
    symbol = '<'


class LessOrEqualWidget(CompMixin, GtLtBaseWidget):
    """
    Widget to handle the "LessOrEqual" comparison.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Equals" object. This widget will
        read from and write to the "value", "atol", and "rtol"
        fields.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    data_type = LessOrEqual
    symbol = '≤'


class RangeWidget(CompMixin, PageWidget):
    """
    Widget to handle the "Range" comparison.

    Contains graphical representations of what the
    range means, since it might not always be clear
    to the user what a warning range means.

    Parameters
    ----------
    bridge : QDataclassBridge
        Dataclass bridge to an "Range" object.
    parent : QObject, keyword-only
        The normal qt parent argument
    """
    filename = 'comp_range.ui'
    data_type = Range

    _intensity = 200
    red = QColor.fromRgb(_intensity, 0, 0)
    yellow = QColor.fromRgb(_intensity, _intensity, 0)
    green = QColor.fromRgb(0, _intensity, 0)

    bridge: QDataclassBridge

    # Core
    low_edit: QLineEdit
    high_edit: QLineEdit
    warn_low_edit: QLineEdit
    warn_high_edit: QLineEdit
    inclusive_check: QCheckBox

    # Symbols
    comp_symbol_label_1: QLabel
    comp_symbol_label_2: QLabel
    comp_symbol_label_3: QLabel
    comp_symbol_label_4: QLabel

    # Graphical
    low_label: QLabel
    high_label: QLabel
    warn_low_label: QLabel
    warn_high_label: QLabel
    left_red_line: PyDMDrawingLine
    left_yellow_line: PyDMDrawingLine
    green_line: PyDMDrawingLine
    right_yellow_line: PyDMDrawingLine
    right_red_line: PyDMDrawingLine
    vertical_line_1: PyDMDrawingLine
    vertical_line_2: PyDMDrawingLine
    vertical_line_3: PyDMDrawingLine
    vertical_line_4: PyDMDrawingLine

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_range_widget()

    def setup_range_widget(self) -> None:
        """
        Do all the setup required for a range widget.

        - Connect the text entry fields and set the dynamic expand/contract
        - Set up the inclusive checkbox
        - Set up the symbols based on the inclusive checkbox
        - Set up the dynamic behavior of the visualization
        """
        # Line edits and visualization
        for ident in ('low', 'high', 'warn_low', 'warn_high'):
            line_edit = getattr(self, f'{ident}_edit')
            value_obj = getattr(self.bridge, ident)
            # Copy all changes to the visualization labels
            label = getattr(self, f'{ident}_label')
            line_edit.textChanged.connect(label.setText)
            # Trigger the visualization update on any update
            value_obj.changed_value.connect(self.update_visualization)
            # Standard setup and initialization
            setup_line_edit_all(
                line_edit=line_edit,
                value_obj=value_obj,
                from_str=float,
                to_str=str,
                minimum=100,
                buffer=15
            )
        # Checkbox
        self.bridge.inclusive.changed_value.connect(
            self.inclusive_check.setChecked
        )
        self.bridge.inclusive.changed_value.connect(
            self.update_visualization
        )
        self.inclusive_check.clicked.connect(self.bridge.inclusive.put)
        self.inclusive_check.setChecked(self.bridge.inclusive.get())
        # Symbols
        self.bridge.inclusive.changed_value.connect(self.update_symbols)
        self.update_symbols(self.bridge.inclusive.get())
        # One additional visual update on inversion
        self.bridge.invert.changed_value.connect(self.update_visualization)
        # Make sure this was called at least once
        self.update_visualization()

    def update_symbols(self, inclusive: bool) -> None:
        """
        Pick the symbol type based on range inclusiveness.

        Use the less than symbol if not inclusive, and the the
        less than or equals symbol if inclusive.

        Parameters
        ----------
        inclusive : bool
            True if the range should be inclusive and False otherwise.
        """
        if inclusive:
            symbol = '≤'
        else:
            symbol = '<'
        for index in range(1, 5):
            label = getattr(self, f'comp_symbol_label_{index}')
            label.setText(symbol)

    def resizeEvent(self, *args, **kwargs) -> None:
        """
        Override resizeEvent to update the visualization when we resize.
        """
        self.update_visualization()
        return super().resizeEvent(*args, **kwargs)

    def update_visualization(self, *args, **kwargs):
        """
        Make the visualization match the current data state.
        """
        # Cute trick: swap red and green if we're inverted
        if self.bridge.invert.get():
            green = self.red
            red = self.green
        else:
            green = self.green
            red = self.red
        yellow = self.yellow
        self.left_red_line.penColor = red
        self.left_yellow_line.penColor = yellow
        self.green_line.penColor = green
        self.right_yellow_line.penColor = yellow
        self.right_red_line.penColor = red
        # The boundary lines should be colored to indicate inclusive/not
        if self.bridge.inclusive.get():
            # boundaries are the same as the inner
            self.vertical_line_1.penColor = yellow
            self.vertical_line_2.penColor = green
            self.vertical_line_3.penColor = green
            self.vertical_line_4.penColor = yellow
        else:
            # boundaries are the same as the outer
            self.vertical_line_1.penColor = red
            self.vertical_line_2.penColor = yellow
            self.vertical_line_3.penColor = yellow
            self.vertical_line_4.penColor = red

        # Get static variables to work with for the resize
        low_mark = self.bridge.low.get()
        warn_low_mark = self.bridge.warn_low.get()
        warn_high_mark = self.bridge.warn_high.get()
        high_mark = self.bridge.high.get()
        # Make sure the ranges make sense
        # Nonsense ranges or no warning set: hide the warnings and skip rest
        try:
            ordered = low_mark < warn_low_mark < warn_high_mark < high_mark
        except TypeError:
            # Something is still None
            ordered = False
        real_space = self.width() * 0.7

        if not ordered or self.bridge.invert.get():
            # No warning bounds, something is nonphysical, or we are inverted
            # Note: inversion implies a nonsensical "fail and warn" region
            # that should be ignored.
            # Hide warnings, scale green, set bound colors, and end
            self.left_yellow_line.hide()
            self.right_yellow_line.hide()
            self.vertical_line_2.hide()
            self.vertical_line_3.hide()
            self.warn_low_label.hide()
            self.warn_high_label.hide()
            self.green_line.setFixedWidth(int(real_space))
            # Only red and green are available in this case
            # So we need to do the full check again
            if self.bridge.inclusive.get():
                # boundaries are the same as the inner
                self.vertical_line_1.penColor = green
                self.vertical_line_4.penColor = green
            else:
                # boundaries are the same as the outer
                self.vertical_line_1.penColor = red
                self.vertical_line_4.penColor = red
            return
        else:
            # Looks OK, show everything
            self.left_yellow_line.show()
            self.right_yellow_line.show()
            self.vertical_line_2.show()
            self.vertical_line_3.show()
            self.warn_low_label.show()
            self.warn_high_label.show()
        # The yellow and green lines should be sized relative to each other
        total_range = high_mark - low_mark
        left_range = warn_low_mark - low_mark
        mid_range = warn_high_mark - warn_low_mark
        right_range = high_mark - warn_high_mark
        self.left_yellow_line.setFixedWidth(int(
            real_space * left_range/total_range
        ))
        self.green_line.setFixedWidth(int(
            real_space * mid_range/total_range
        ))
        self.right_yellow_line.setFixedWidth(int(
            real_space * right_range/total_range
        ))
