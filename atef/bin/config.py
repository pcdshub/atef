"""
`atef config` opens up a graphical config file editor.
"""
from __future__ import annotations

import argparse
import dataclasses
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, Union

from qtpy.QtCore import QEvent, QObject, QTimer
from qtpy.QtCore import Signal as QSignal
from qtpy.QtWidgets import (QApplication, QComboBox, QFormLayout, QHBoxLayout,
                            QLabel, QLayout, QLineEdit, QMainWindow,
                            QMessageBox, QPlainTextEdit, QPushButton,
                            QTabWidget, QTreeWidget, QTreeWidgetItem,
                            QVBoxLayout, QWidget)
from qtpy.uic import loadUiType

from ..check import (Comparison, ConfigurationFile, DeviceConfiguration,
                     IdentifierAndComparison, PVConfiguration, Severity)
from ..reduce import ReduceMethod


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

    bridge: QDataclassBridge
    tree_widget: QTreeWidget

    def __init__(self, *args, config_file: ConfigurationFile, **kwargs):
        super().__init__(*args, **kwargs)
        self.bridge = QDataclassBridge(config_file, parent=self)
        self.last_selection: Optional[AtefItem] = None
        self.built_widgets = set()
        self.assemble_tree()
        self.show_selected_display(self.overview_item)
        self.tree_widget.itemPressed.connect(self.show_selected_display)
        # TODO remove this or make it a debug option
        self.debug_timer = QTimer(parent=self)
        self.debug_timer.setInterval(1000*60)
        self.debug_timer.timeout.connect(self.debug_show_data)
        self.debug_timer.start()

    def debug_show_data(self, *args, **kwargs):
        print(self.bridge.data)

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
            widget_args=[self.bridge.configs, self.tree_widget],
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
        append_item_arg: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.setText(0, name)
        if func_name is not None:
            self.setText(1, func_name)
        self.widget_class = widget_class
        self.widget_args = widget_args or []
        if append_item_arg:
            self.widget_args.append(self)
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

    config_list: QDataclassList
    tree_ref: QTreeWidget
    row_count: int

    def __init__(
        self,
        config_list: QDataclassList,
        tree_ref: QTreeWidget,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.config_list = config_list
        self.tree_ref = tree_ref
        self.row_count = 0
        self.initialize_overview()
        self.add_device_button.clicked.connect(self.add_device_config)
        self.add_pv_button.clicked.connect(self.add_pv_config)

    def initialize_overview(self):
        """
        Read the configuration data and create the overview rows.
        """
        for config in self.config_list.get():
            if isinstance(config, DeviceConfiguration):
                self.add_device_config(config=config, update_data=False)
            elif isinstance(config, PVConfiguration):
                self.add_pv_config(config=config, update_data=False)
            else:
                raise RuntimeError(
                    f'{config} is not a valid config!'
                )

    def add_device_config(
        self,
        checked: Optional[bool] = None,
        config: Optional[DeviceConfiguration] = None,
        update_data: bool = True,
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
        update_data : bool, optional
            If True, the default, mutates the dataclass.
            Set to False during the initial reading of the file.
        """
        if config is None:
            config = DeviceConfiguration()
        self.add_config(config, update_data=update_data)

    def add_pv_config(
        self,
        checked: Optional[bool] = None,
        config: Optional[PVConfiguration] = None,
        update_data: bool = True,
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
        update_data : bool, optional
            If True, the default, mutates the dataclass.
            Set to False during the initial reading of the file.
        """
        if config is None:
            config = PVConfiguration()
        self.add_config(config, update_data=update_data)

    def add_config(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
        update_data: bool = True,
    ):
        """
        Add an existing config to the tree and to the overview.

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
        if isinstance(config, DeviceConfiguration):
            func_name = 'device config'
        else:
            func_name = 'pv config'
        row = OverviewRow(config)
        self.scroll_content.layout().insertWidget(
            self.row_count,
            row,
        )
        item = AtefItem(
            widget_class=Group,
            widget_args=[row.bridge],
            name=config.name or 'untitled',
            func_name=func_name,
            append_item_arg=True,
        )
        self.tree_ref.addTopLevelItem(item)
        self.row_count += 1

        # If either of the widgets change the name, update tree
        row.bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )
        # Note: this is the only place in the UI where
        # we add new config data
        if update_data:
            self.config_list.append(config)


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
        self.name_edit.setText(load_name)
        # Setup the name edit
        self.name_edit.textEdited.connect(self.update_saved_name)
        self.bridge.name.changed_value.connect(self.name_edit.setText)

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
        self.bridge.name.put(name)

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


class OverviewRow(ConfigTextMixin, AtefCfgDisplay, QWidget):
    """
    A single row in the overview widget.

    This displays and provides means to edit the name and description
    of a single configuration.

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

    bridge: QDataclassBridge

    name_edit: QLineEdit
    config_type: QLabel
    lock_button: QPushButton
    desc_edit: QPlainTextEdit

    def __init__(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.bridge = QDataclassBridge(config, parent=self)
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
        # Setup the lock button
        self.lock_button.toggled.connect(self.handle_locking)
        if self.name_edit.text():
            # Start locked if we are reading from file
            self.lock_button.toggle()

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


class Group(ConfigTextMixin, AtefCfgDisplay, QWidget):
    """
    The group of checklists and devices associated with a Configuration.

    From this widget we can edit name/description, add tags,
    add devices, and add checklists to the Configuration.
    """
    filename = 'config_group.ui'

    name_edit: QLineEdit
    desc_edit: QPlainTextEdit
    tags_content: QVBoxLayout
    add_tag_button: QPushButton
    devices_container: QWidget
    devices_content: QVBoxLayout
    add_devices_button: QPushButton
    checklists_container: QWidget
    checklists_content: QVBoxLayout
    add_checklist_button: QPushButton
    line_between_adds: QWidget

    def __init__(
        self,
        bridge: QDataclassBridge,
        tree_item: AtefItem,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.bridge = bridge
        self.tree_item = tree_item
        self.initialize_group()

    def initialize_group(self):
        self.initialize_config_text()
        tags_list = StrList(
            data_list=self.bridge.tags,
            layout=QHBoxLayout(),
        )
        self.tags_content.addWidget(tags_list)
        self.add_tag_button.clicked.connect(
            partial(tags_list.add_item, '')
        )
        if isinstance(self.bridge.data, PVConfiguration):
            self.devices_container.hide()
            self.line_between_adds.hide()
        else:
            devices_list = StrList(
                data_list=self.bridge.devices,
                layout=QVBoxLayout(),
            )
            self.devices_content.addWidget(devices_list)
            self.add_devices_button.clicked.connect(
                partial(devices_list.add_item, '')
            )
        self.checklist_list = NamedDataclassList(
            data_list=self.bridge.checklist,
            layout=QVBoxLayout(),
        )
        self.checklists_content.addWidget(self.checklist_list)
        for bridge in self.checklist_list.bridges:
            self.setup_checklist_item_bridge(bridge)
        self.add_checklist_button.clicked.connect(self.add_checklist)

    def setup_checklist_item_bridge(self, bridge: QDataclassBridge):
        item = AtefItem(
            widget_class=IdAndCompWidget,
            widget_args=[bridge, type(self.bridge.data)],
            name=bridge.name.get() or 'untitled',
            func_name='checklist',
            append_item_arg=True,
        )
        self.tree_item.addChild(item)
        bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )

    def add_checklist(
        self,
        checked: Optional[bool] = None,
        id_and_comp: Optional[IdentifierAndComparison] = None,
    ):
        if id_and_comp is None:
            id_and_comp = IdentifierAndComparison()
            self.bridge.checklist.append(id_and_comp)
        bridge = self.checklist_list.add_item(id_and_comp)
        self.setup_checklist_item_bridge(bridge)
        # TODO make the delete button work
        # new_row.del_button.clicked.connect


class StrList(QWidget):
    """
    A widget used to modify the str variant of QDataclassList.
    """
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
    ):
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

    def save_item_update(self, item: StrListElem, new_value: str):
        index = self.widgets.index(item)
        self.data_list.put_to_index(index, new_value)

    def remove_item(self, item: StrListElem, checked: bool):
        index = self.widgets.index(item)
        self.widgets.remove(item)
        self.data_list.remove_index(index)
        item.deleteLater()


class NamedDataclassList(StrList):
    """
    A widget used to modify a QDataclassList with named dataclass elements.

    A named dataclass is any dataclass element with a str "name" field.
    This widget will allow us to add elements to the list by name,
    display the names, modify the names, add blank entries, etc.
    """
    def __init__(self, *args, **kwargs):
        self.bridges = []
        super().__init__(*args, **kwargs)

    def add_item(
        self,
        starting_value: Any,
        checked: Optional[bool] = None,
        init: bool = False,
    ) -> QDataclassBridge:
        super().add_item(
            starting_value=starting_value.name,
            checked=checked,
            init=init,
        )
        bridge = QDataclassBridge(starting_value, parent=self)
        bridge.name.changed_value.connect(
            self.widgets[-1].line_edit.setText
        )
        self.bridges.append(bridge)
        return bridge

    def save_item_update(self, item: StrListElem, new_value: str):
        index = self.widgets.index(item)
        self.bridges[index].name.put(new_value)

    def remove_item(self, item: StrListElem, checked: bool):
        index = self.widgets.index(item)
        super().remove_item(item=item, checked=checked)
        bridge = self.bridges[index]
        bridge.deleteLater()
        del self.bridges[index]


class StrListElem(AtefCfgDisplay, QWidget):
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
    del_button: QPushButton

    def __init__(self, start_text: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.line_edit.setText(start_text)
        self.line_edit.setFrame(not start_text)
        edit_filter = FrameOnEditFilter(parent=self)
        self.line_edit.installEventFilter(edit_filter)
        self.on_text_changed(start_text)
        self.line_edit.textChanged.connect(self.on_text_changed)

    def on_text_changed(self, text: str):
        # Show or hide the del button as needed
        self.del_button.setVisible(not text)
        # Adjust the width to match the text
        font_metrics = self.line_edit.fontMetrics()
        width = font_metrics.boundingRect(text).width()
        self.line_edit.setFixedWidth(max(width + 10, 40))


class FrameOnEditFilter(QObject):
    def eventFilter(self, object: QLineEdit, event: QEvent):
        if event.type() == QEvent.FocusIn:
            object.setFrame(True)
            object.setReadOnly(False)
            return True
        if event.type() == QEvent.FocusOut:
            if object.text():
                object.setFrame(False)
            object.setReadOnly(True)
            return True
        return False


class IdAndCompWidget(ConfigTextMixin, AtefCfgDisplay, QWidget):
    filename = 'id_and_comp.ui'

    name_edit: QLineEdit
    id_label: QLabel
    id_content: QVBoxLayout
    add_id_button: QPushButton
    comp_label: QLabel
    comp_content: QVBoxLayout
    add_comp_button: QPushButton

    bridge: QDataclassBridge
    config_type: type

    def __init__(
        self,
        bridge: QDataclassBridge,
        config_type: type,
        tree_item: AtefItem,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.bridge = bridge
        self.config_type = config_type
        self.tree_item = tree_item
        self.initialize_idcomp()

    def initialize_idcomp(self):
        # Connect the name to the dataclass
        self.initialize_config_name()
        # Set up editing of the identifiers list
        identifiers_list = StrList(
            data_list=self.bridge.ids,
            layout=QVBoxLayout(),
        )
        self.id_content.addWidget(identifiers_list)
        self.add_id_button.clicked.connect(
            partial(identifiers_list.add_item, '')
        )
        # Adjust the identifier text appropriately for config type
        if issubclass(self.config_type, DeviceConfiguration):
            self.id_label.setText('Device Signals')
            self.add_id_button.setText('Add Signal')
        elif issubclass(self.config_type, PVConfiguration):
            self.id_label.setText('PV Names')
            self.add_id_button.setText('Add PV')
        self.comparison_list = NamedDataclassList(
            data_list=self.bridge.comparisons,
            layout=QVBoxLayout(),
        )
        self.comp_content.addWidget(self.comparison_list)
        for comparison in self.bridge.comparisons.get():
            self.add_comparison(comparison=comparison)
        self.add_comp_button.clicked.connect(self.add_comparison)

    def setup_comparison_item_bridge(self, bridge: QDataclassBridge):
        item = AtefItem(
            widget_class=CompView,
            widget_args=[bridge],
            name=bridge.name.get() or 'untitled',
            func_name='comparison',
        )
        self.tree_item.addChild(item)
        bridge.name.changed_value.connect(
            partial(item.setText, 0)
        )

    def add_comparison(
        self,
        checked: Optional[bool] = None,
        comparison: Optional[Comparison] = None,
    ):
        if comparison is None:
            # Empty default
            comparison = Comparison()
            self.bridge.comparisons.append(comparison)
        bridge = self.comparison_list.add_item(comparison)
        self.setup_comparison_item_bridge(bridge)
        # TODO make the delete button work
        # new_row.del_button.clicked.connect


class CompView(ConfigTextMixin, AtefCfgDisplay, QWidget):
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
        dataclass_type: type,
        widget_type: type,
    ) -> None:
        cls.specific_comparison_widgets[dataclass_type] = widget_type
        cls.data_types[dataclass_type.__name__] = dataclass_type

    def __init__(self, bridge: QDataclassBridge, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bridge = bridge
        self.comparison_setup_done = False
        self.initialize_comp_view()

    def initialize_comp_view(self):
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

    def _comp_type_from_combobox(self, type_name: str):
        return self.change_comparison_type(self.data_types[type_name])

    def change_comparison_type(self, new_type: type):
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
        - connect everything to the new bridge
        """
        if self.comparison_setup_done:
            # Clean up the previous widget
            self.specific_widget.deleteLater()
            # Clean up the previous bridge
            old_data = self.bridge.data
            self.bridge.deleteLater()
            # Create a new dataclass, transferring over any compatible data
            new_data = cast_dataclass(old_data, new_type)
            # Create a new bridge and assign it to self
            self.bridge = QDataclassBridge(new_data)
            # Replace our bridge in the parent as appropriate
            # TODO
            raise NotImplementedError()
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
        # Fill the generic combobox options
        if not self.comparison_setup_done:
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

    def new_invert_combo(self, index: int):
        self.bridge.invert.put(bool(index))

    def new_reduce_period_edit(self, value: str):
        try:
            value = int(value)
        except Exception:
            value = None
        self.bridge.reduce_period.put(value)

    def new_reduce_method_combo(self, value: str):
        self.bridge.reduce_method.put(ReduceMethod[value])

    def new_string_combo(self, index: int):
        self.bridge.string.put(bool(index))

    def new_sev_on_failure_combo(self, value: str):
        self.bridge.severity_on_failure.put(Severity[value])

    def new_if_disc_combo(self, value: str):
        self.bridge.if_disconnected.put(Severity[value])


def cast_dataclass(data: Any, new_type: type):
    new_fields = dataclasses.fields(new_type)
    new_kwargs = {
        key: value for key, value in dataclasses.asdict(data).items()
        if key in set(field.name for field in new_fields)
    }
    return new_type(**new_kwargs)


class CompMixin:
    data_type: ClassVar[type]

    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        CompView.register_comparison(cls.data_type, cls)


class ComparisonWidget(CompMixin, QLabel):
    data_type = Comparison

    def __init__(self, bridge: QDataclassBridge, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setText('Please select a comparison type above.')


def main():
    # TreeClass = create_ui_class('config_tree.ui', 'TreeClass')
    app = QApplication([])
    main_window = Window()
    main_window.show()
    app.exec()
