"""
`atef config` opens up a graphical config file editor.
"""
import argparse

from pydm import exception
from qtpy.QtWidgets import QApplication

from ..widgets.config import Window


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()
    return argparser


def main():
    app = QApplication([])
    main_window = Window()
    main_window.show()
    exception.install()
    app.exec()
