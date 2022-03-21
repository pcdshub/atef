"""
`atef config` opens up a graphical config file editor.
"""
from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
from typing import Optional, Any, List, Union, ClassVar, Dict, Type

from qtpy.QtCore import QTimer, QObject, pyqtSignal
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QTabWidget,
                            QTreeWidget, QTreeWidgetItem, QPushButton,
                            QMessageBox, QLineEdit, QLabel, QPlainTextEdit)
from qtpy.uic import loadUiType

from ..check import ConfigurationFile, DeviceConfiguration, PVConfiguration


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()
    return argparser


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
    def __init__(self, data: Any, parent: Optional[QObject] = None):
        super().__init__(self, parent=parent)
        for field in dataclasses.fields(data):
            # Need to figure out which category this is:
            # 1. Primitive value -> make a QDataclassValue
            # 2. Another dataclass -> make a QDataclassValue (object)
            # 3. A list of values -> make a QDataclassList
            # 4. A list of dataclasses -> QDataclassList (object)
            use_type = field.type
            NestedClass = QDataclassValue
            # Resolve "optional" fields
            if isinstance(use_type, Optional):
                # Assume we may have it at some point
                use_type = use_type.__args__[0]
            # Extract the base type of the list
            if isinstance(use_type, List):
                NestedClass = QDataClassList
                use_type = use_type.__args__[0]
            # Identify dataclasses
            try:
                dataclasses.fields(use_type)
            except TypeError:
                # non-dataclass, use previous use_type
                # expected to be a primitive type
                ...
            else:
                # a dataclass, we need a generic object qsignal
                use_type = object
            setattr(
                self,
                field.name,
                NestedClass.of_type(use_type)(
                    data,
                    field.name,
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
    updated: pyqtSignal
    _registry: ClassVar[Dict[str, type]]

    def __init__(
        self,
        data: Any,
        attr: str,
        parent: Optional[QObject] = None,
    ):
        super().__init__(self, parent=parent)
        self.data = data
        self.attr = attr


class QDataclassValue(QDataclassElem):
    """
    A single value in the QDataclassBridge.
    """
    changed_value: pyqtSignal

    _registry = {}

    @classmethod
    def of_type(cls, data_type: type) -> Type[QDataclassValue]:
        """
        Create a QDataclass with a specific pyqtSignal

        Parameters
        ----------
        data_type : any primitive type
        """
        try:
            return cls._registry[data_type]
        except KeyError:
            ...
        new_class = type(
            f'QDataclass{data_type}',
            (cls, QObject),
            {
                'updated': pyqtSignal(),
                'changed_value': pyqtSignal(data_type),
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


class QDataClassList(QDataclassElem):
    """
    A list of values in the QDataclassBridge.
    """
    added_value: pyqtSignal
    added_index: pyqtSignal
    removed_value: pyqtSignal
    removed_index: pyqtSignal
    changed_value: pyqtSignal
    changed_index: pyqtSignal

    _registry = {}

    @classmethod
    def of_type(cls, data_type: type) -> Type[QDataClassList]:
        """
        Create a QDataclass with a specific pyqtSignal

        Parameters
        ----------
        data_type : any primitive type
        """
        try:
            return cls._registry[data_type]
        except KeyError:
            ...
        new_class = type(
            f'QDataclass{data_type}',
            (cls, QObject),
            {
                'updated': pyqtSignal(),
                'added_value': pyqtSignal(data_type),
                'added_index': pyqtSignal(int),
                'removed_value': pyqtSignal(data_type),
                'removed_index': pyqtSignal(int),
                'changed_value': pyqtSignal(data_type),
                'changed_index': pyqtSignal(int),
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
        self.get().append(new_value)
        self.added_value.emit(new_value)
        self.added_index.emit(len(self.get()) - 1)
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


class AtefCfgDisplay:
    """Helper class for loading the .ui files and adding logic."""
    filename: str

    def __init_subclass__(cls):
        """Read the file when the class is created"""
        super().__init_subclass__()
        cls.ui_form, _ = loadUiType(
            str(Path(__file__).parent.parent / 'ui' / cls.filename)
        )

    def __init__(self, *args, **kwargs):
        """Apply the file to this widget when the instance is created"""
        super().__init__(*args, **kwargs)
        self.ui_form.setupUi(self, self)

    def retranslateUi(self, *args, **kwargs):
        """Required function for setupUi to work in __init__"""
        self.ui_form.retranslateUi(self, *args, **kwargs)


class Window(AtefCfgDisplay, QMainWindow):
    """
    Main atef config window

    Has a tab widget for editing multiple files at once, and contains
    the menu bar for facilitating saving/loading.
    """
    filename = 'config_window.ui'
    user_default_filename = 'untitled'
    user_filename_ext = 'yaml'

    tab_widget: QTabWidget

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trees = {}
        self.setWindowTitle('atef config')
        self.action_new_file.triggered.connect(self.open_new_file)
        QTimer.singleShot(0, self.welcome_user)

    def welcome_user(self):
        """
        On open, ask the user what they'd like to do (new config? load?)

        TODO: implement loading
        TODO: only show when we don't get a file cli argument to start.
        """
        welcome_box = QMessageBox()
        welcome_box.setIcon(QMessageBox.Question)
        welcome_box.setWindowTitle('Welcome')
        welcome_box.setText('Welcome to atef config!')
        welcome_box.setInformativeText('Please select a startup action')
        welcome_box.addButton(QMessageBox.Open)
        new_button = welcome_box.addButton('New', QMessageBox.AcceptRole)
        welcome_box.addButton(QMessageBox.Close)
        new_button.clicked.connect(self.open_new_file)
        welcome_box.exec()

    def open_new_file(self, *args, **kwargs):
        """
        Create and populate a new edit tab.

        The parameters are open as to accept inputs from any signal.
        """
        name = self.user_default_filename
        index = 0
        while name in self.trees:
            index += 1
            name = f'{self.user_default_filename}{index}'
        widget = Tree(config_file=ConfigurationFile(configs=[]))
        self.trees[name] = widget
        self.tab_widget.addTab(
            widget,
            '.'.join((name, self.user_filename_ext))
        )


class Tree(AtefCfgDisplay, QWidget):
    """
    The main per-file widget as a "native" view into the file.

    Consists of a tree visualization on the left that can be selected through
    to choose which part of the tree to edit in the widget space on the right.

    Parameters
    ----------
    config_file : ConfigurationFile
        The config file object to use to build the tree.
    """
    filename = 'config_tree.ui'

    tree_widget: QTreeWidget

    def __init__(self, *args, config_file: ConfigurationFile, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_file = config_file
        self.last_selection: Optional[AtefItem] = None
        self.built_widgets = set()
        self.assemble_tree()
        self.show_selected_display(self.overview_item)
        self.tree_widget.itemPressed.connect(self.show_selected_display)

    def assemble_tree(self):
        """
        On startup, create the full tree.

        TODO: properly fill from the config_file, currently creates only the
        base tree with a single "overview" item.
        """
        self.tree_widget.setColumnCount(2)
        self.tree_widget.setHeaderLabels(['Node', 'Type'])
        self.overview_item = AtefItem(
            widget_class=Overview,
            widget_args=[self.config_file, self.tree_widget],
            name='Overview',
            func_name='overview'
        )
        self.tree_widget.addTopLevelItem(self.overview_item)

    def show_selected_display(self, item: AtefItem, *args, **kwargs):
        """
        Show the proper widget on the right when a tree row is selected.

        This works by hiding the previous widget and showing the new
        selection, creating the widget object if needed.

        TODO: make sure the widget we set visible is fully updated with
        the latest config file information.

        Parameters
        ----------
        item : AtefItem
            The selected item in the tree. This contains information like
            the textual annotation, cached widget references, and
            arguments for creating a new widget if needed.
        """
        if item is self.last_selection:
            return
        if self.last_selection is not None:
            self.last_selection.get_widget().setVisible(False)
        widget = item.get_widget()
        if widget not in self.built_widgets:
            self.layout().addWidget(widget)
            self.built_widgets.add(widget)
        widget.setVisible(True)
        self.last_selection = item


class AtefItem(QTreeWidgetItem):
    """
    A QTreeWidget item with some convenience methods.

    Facilitates the widget creation/caching mechanisms.
    """
    widget_class: type[QWidget]
    widget_args: list[Any]
    widget_cached: Optional[QWidget]

    def __init__(
        self,
        *args,
        widget_class: type[QWidget],
        widget_args: Optional[list[Any]],
        name: str,
        func_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.setText(0, name)
        if func_name is not None:
            self.setText(1, func_name)
        self.widget_class = widget_class
        self.widget_args = widget_args or []
        self.widget_cached = None

    def get_widget(self) -> QWidget:
        """
        Return the edit widget associated with this tree node.

        On the first call, the widget is created. On subsequent calls
        we use the cached widget.
        """
        if self.widget_cached is None:
            self.widget_cached = self.widget_class(*self.widget_args)
        return self.widget_cached


class Overview(AtefCfgDisplay, QWidget):
    """
    A view of all the top-level "Configuration" objects.

    This widget allows us to browse our config names, classes, and
    descriptions, as well as add new configs.

    TODO: add a way to delete configs.

    Parameters
    ----------
    config_file : ConfigurationFile
        A reference to the full config file dataclass to read from
        and update to as we do edits.
    tree_ref : QTreeWidget
        A reference to the entire tree widget so we can update the
        top-level names in the tree as they are edited here.
    """
    filename = 'config_overview.ui'

    add_device_button: QPushButton
    add_pv_button: QPushButton
    scroll_content: QWidget

    config_file: ConfigurationFile
    tree_ref: QTreeWidget
    row_count: int

    def __init__(
        self,
        config_file: ConfigurationFile,
        tree_ref: QTreeWidget,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.config_file = config_file
        self.tree_ref = tree_ref
        self.row_count = 0
        self.initialize_overview()
        self.add_device_button.clicked.connect(self.add_device_config)
        self.add_pv_button.clicked.connect(self.add_pv_config)

    def initialize_overview(self):
        """
        Read the configuration data and create the overview rows.
        """
        for config in self.config_file.configs:
            if isinstance(config, DeviceConfiguration):
                self.add_device_config(config=config)
            elif isinstance(config, PVConfiguration):
                self.add_pv_config(config=config)
            else:
                raise RuntimeError(
                    f'{config} is not a valid config!'
                )

    def add_device_config(
        self,
        checked: Optional[bool] = None,
        config: Optional[DeviceConfiguration] = None,
    ):
        """
        Add a device config row to the tree and to the overview.

        This method exists so that we can make the "add_device_button" work.

        Parameters
        ----------
        checked : bool
            Expected argument from a qPushButton, unused
        config : DeviceConfiguration, optional
            The device configuration to add. If omitted, we'll create
            a blank config.
        """
        if config is None:
            config = DeviceConfiguration()
        self.add_config(config)

    def add_pv_config(
        self,
        checked: Optional[bool] = None,
        config: Optional[PVConfiguration] = None,
    ):
        """
        Add a pv config row to the tree and to the overview.

        This method exists so that we can make the "add_pv_button" work.

        Parameters
        ----------
        checked : bool
            Expected argument from a qPushButton, unused
        config : PVConfiguration, optional
            The PV configuration to add. If omitted, we'll create
            a blank config.
        """
        if config is None:
            config = PVConfiguration()
        self.add_config(PVConfiguration)

    def add_config(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
    ):
        """
        Add an existing config to the tree and to the overview.

        This is the core method that modifies the tree and adds the row
        widget.

        Parameters
        ----------
        config : Configuration
            A single configuration object.
        """
        if isinstance(config, DeviceConfiguration):
            func_name = 'device config'
        else:
            func_name = 'pv config'
        item = AtefItem(
            widget_class=Group,
            widget_args=[],
            name=config.name or 'untitled',
            func_name=func_name,
        )
        self.tree_ref.addTopLevelItem(item)
        self.scroll_content.layout().insertWidget(
            self.row_count,
            OverviewRow(config, item),
        )
        self.row_count += 1


class OverviewRow(AtefCfgDisplay, QWidget):
    """
    A single row in the overview widget.

    This displays and provides means to edit the name and description
    of a single configuration.

    TODO: add a way to re-read the configuration if it is edited elsewhere

    Parameters
    ----------
    config : Configuration
        The full configuration associated with this row, so that we can
        read and edit the name and description.
    item : AtefItem
        The single item in the tree associated with this config, so that we
        can write to the text in the tree as we edit the name.
    """
    filename = 'config_overview_row.ui'

    name_edit: QLineEdit
    config_type: QLabel
    lock_button: QPushButton
    desc_edit: QPlainTextEdit

    def __init__(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
        item: AtefItem,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.config = config
        self.item = item
        self.initialize_row()

    def initialize_row(self):
        """
        Set up all the logic and starting state of the row widget.
        """
        self.name_edit.textEdited.connect(self.update_saved_name)
        self.desc_edit.textChanged.connect(self.update_saved_desc)
        self.update_text_height()
        self.desc_edit.textChanged.connect(self.update_text_height)
        self.lock_button.toggled.connect(self.handle_locking)
        if isinstance(self.config, DeviceConfiguration):
            self.config_type.setText('Device Config')
        else:
            self.config_type.setText('PV Config')

    def update_saved_name(self, name: str):
        """
        When the user edits the name, write to the tree and the config.
        """
        self.config.name = name
        self.item.setText(0, name)

    def update_saved_desc(self):
        """
        When the user edits the desc, write to the config.
        """
        self.config.description = self.desc_edit.toPlainText()

    def update_text_height(self):
        """
        When the user edits the desc, make the text box the correct height.
        """
        line_count = max(self.desc_edit.document().size().toSize().height(), 1)
        self.desc_edit.setFixedHeight(line_count * 13 + 12)

    def lock_editing(self, locked: bool):
        """
        Set the checked state of the "locked" button as the user would.
        """
        self.lock_button.setChecked(locked)

    def handle_locking(self, locked: bool):
        """
        When the checked state of the "locked" button changes, make it so.

        When locked, the boxes will be read only and have an indicated visual change.
        When unlocked, the boxes will be writable and have the default look and feel.

        It is expected that the user won't edit these a lot, and that it is easier
        to browse through the rows with the non-edit style.
        """
        self.name_edit.setReadOnly(locked)
        self.name_edit.setFrame(not locked)
        self.desc_edit.setReadOnly(locked)
        if locked:
            self.desc_edit.setFrameShape(self.desc_edit.NoFrame)
            self.setStyleSheet(
                "QLineEdit, QPlainTextEdit { background: transparent }"
            )
        else:
            self.desc_edit.setFrameShape(self.desc_edit.StyledPanel)
            self.setStyleSheet(
                "QLineEdit, QPlainTextEdit { background: white }"
            )


class NamedRow(AtefCfgDisplay, QWidget):
    filename = 'config_named_row.ui'

    rename_button: QPushButton
    confirm_button: QPushButton


class Group(AtefCfgDisplay, QWidget):
    filename = 'config_group.ui'


class Checklist(AtefCfgDisplay, QWidget):
    filename = 'checklist.ui'


def main():
    # TreeClass = create_ui_class('config_tree.ui', 'TreeClass')
    app = QApplication([])
    main_window = Window()
    main_window.show()
    app.exec()
