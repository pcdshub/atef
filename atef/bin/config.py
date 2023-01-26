"""
`atef config` opens up a graphical config file editor.
"""
import argparse
import logging
from typing import List, Optional

from pydm import exception
from qtpy.QtWidgets import QApplication

from ..type_hints import AnyPath
from ..widgets.config.window import Window

logger = logging.getLogger(__name__)


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.add_argument(
        "filenames",
        metavar="filename",
        type=str,
        nargs="*",
        help="Configuration filename",
    )

    return argparser


def main(filenames: Optional[List[AnyPath]] = None):
    app = QApplication([])
    main_window = Window(show_welcome=not filenames)
    main_window.show()
    exception.install()

    for filename in filenames or []:
        try:
            main_window.open_file(filename=filename)
        except FileNotFoundError:
            logger.error(
                "File specified on the command-line not found: %s", filename
            )

    app.exec()
