"""
`atef config` opens up a graphical config file editor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Any

from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QTabWidget,
                            QTreeWidget, QTreeWidgetItem, QPushButton,
                            QVBoxLayout)
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
        self.action_new_file.triggered.connect(self.open_new_file)

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

    tree_widget = QTreeWidget

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
            widget_data=self.config_file,
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
    widget_data: Any
    widget_cached: Optional[QWidget]

    def __init__(
        self,
        *args,
        widget_class: type[QWidget],
        widget_data: Any,
        name: str,
        func_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.setText(0, name)
        if func_name is not None:
            self.setText(1, func_name)
        self.widget_class = widget_class
        self.widget_data = widget_data
        self.widget_cached = None

    def get_widget(self) -> QWidget:
        if self.widget_cached is None:
            self.widget_cached = self.widget_class(self.widget_data)
        return self.widget_cached


class Overview(AtefCfgDisplay, QWidget):
    filename = 'config_overview.ui'

    add_device_button: QPushButton
    add_pv_button: QPushButton
    config_layout: QVBoxLayout

    def __init__(self, config_file: ConfigurationFile, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_file = config_file
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
