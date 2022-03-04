"""
`atef config` opens up a graphical config file editor.
"""
import argparse
from pathlib import Path

from qtpy.QtWidgets import QApplication, QMainWindow, QWidget
from qtpy.uic import loadUiType


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


class Tree(AtefCfgDisplay, QWidget):
    filename = 'config_tree.ui'


class Settings(AtefCfgDisplay, QWidget):
    filename = 'config_settings.ui'


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
