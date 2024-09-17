import argparse

from qtpy.QtWidgets import QStyleFactory


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    # Arguments that need to be passed through to Qt
    qt_args = {
        '--qmljsdebugger': 1,
        '--reverse': '?',
        '--stylesheet': 1,
        '--widgetcount': '?',
        '--platform': 1,
        '--platformpluginpath': 1,
        '--platformtheme': 1,
        '--plugin': 1,
        '--qwindowgeometry': 1,
        '--qwindowicon': 1,
        '--qwindowtitle': 1,
        '--session': 1,
        '--display': 1,
        '--geometry': 1
    }

    for name in qt_args:
        argparser.add_argument(
            name,
            type=str,
            nargs=qt_args[name]
        )

    argparser.add_argument(
        '--style',
        type=str,
        choices=QStyleFactory.keys(),
        default='fusion',
        help='Qt style to use for the application'
    )

    argparser.description = """
    Runs the atef configuration GUI, optionally with an existing configuration.
    Qt arguments are also supported. For a full list, see the Qt docs:
    https://doc.qt.io/qt-5/qapplication.html#QApplication
    https://doc.qt.io/qt-5/qguiapplication.html#supported-command-line-options
    """
    argparser.add_argument(
        "--cache-size",
        metavar="cache_size",
        type=int,
        default=5,
        help="Page widget cache size",
    )

    argparser.add_argument(
        "filenames",
        metavar="filename",
        type=str,
        nargs="*",
        help="Configuration filename",
    )

    return argparser


def main(*args, **kwargs):
    from atef.bin.config_main import main
    main(*args, **kwargs)
