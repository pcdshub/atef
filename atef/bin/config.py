"""
`atef config` opens up a graphical config file editor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Any, Union

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QTabWidget,
                            QTreeWidget, QTreeWidgetItem, QPushButton,
                            QMessageBox, QLineEdit, QLabel, QPlainTextEdit)
from qtpy.uic import loadUiType

from ..check import ConfigurationFile, DeviceConfiguration, PVConfiguration


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()
    return argparser


class AtefCfgDisplay:
    """Helper class for loading the .ui files and adding logic"""
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
        self.tree_widget.setColumnCount(2)
        self.tree_widget.setHeaderLabels(['Node', 'Function'])
        self.overview_item = AtefItem(
            widget_class=Overview,
            widget_args=[self.config_file, self.tree_widget],
            name='Overview',
            func_name='Add New Configs'
        )
        self.tree_widget.addTopLevelItem(self.overview_item)

    def show_selected_display(self, item: AtefItem, *args, **kwargs):
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
        if self.widget_cached is None:
            self.widget_cached = self.widget_class(*self.widget_args)
        return self.widget_cached


class Overview(AtefCfgDisplay, QWidget):
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
        if config is None:
            config = DeviceConfiguration()
        self.add_config(config)

    def add_pv_config(
        self,
        checked: Optional[bool] = None,
        config: Optional[PVConfiguration] = None,
    ):
        if config is None:
            config = PVConfiguration()
        self.add_config(PVConfiguration)

    def add_config(
        self,
        config: Union[DeviceConfiguration, PVConfiguration],
    ):
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
