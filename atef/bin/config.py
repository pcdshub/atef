"""
`atef config` opens up a graphical config file editor.
"""
import logging
import sys
from typing import List, Optional

from pydm import exception
from qtpy.QtWidgets import QApplication

from ..type_hints import AnyPath
from ..widgets.config.window import Window

logger = logging.getLogger(__name__)


def main(cache_size: int, filenames: Optional[List[AnyPath]] = None, **kwargs):
    app = QApplication(sys.argv)
    main_window = Window(cache_size=cache_size, show_welcome=not filenames)
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
