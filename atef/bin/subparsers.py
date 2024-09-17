"""
A collection of subparser helpers.  Separate from the sub-command submodules to
isolate imports until absolutely necessary.  Avoid importing any atef core
library utilities, wherever possible.
"""

import argparse
import importlib
from functools import partial
from typing import Callable

from qtpy.QtWidgets import QStyleFactory

from atef.scripts.scripts_subparsers import SUBSCRIPTS

# Sub-sub parsers too difficult to isolate here.  Leave as submodule import
# from atef.bin.scripts import build_arg_parser as build_arg_parser_scripts


def get_main(submodule_name: str, base_module: str) -> Callable:
    """Grab the `main` function from atef.bin.{submodule_name}"""
    module = importlib.import_module(f".{submodule_name}", base_module)

    return module.main


# `atef check`
_VERBOSITY_SETTINGS = {
    "show-severity-emoji": True,
    "show-severity-description": True,
    "show-config-description": False,
    "show-tags": False,
    "show-passed-tests": False,
}


def build_arg_parser_check(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = """
    `atef check` runs passive checkouts of devices given a configuration file.
    """
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "filename",
        type=str,
        help="Configuration filename",
    )

    # Hard code VerbositySetting to extricate from atef.bin.check
    for flag_name, default in _VERBOSITY_SETTINGS.items():

        help_text = flag_name.replace("-", " ").capitalize()

        argparser.add_argument(
            f"--{flag_name}",
            dest=flag_name.replace("-", "_"),
            help=help_text,
            action="store_true",
            default=default,
        )

        if flag_name.startswith("show-"):
            hide_flag_name = flag_name.replace("show-", "hide-")
            help_text = help_text.replace("Show ", "Hide ")
            argparser.add_argument(
                f"--{hide_flag_name}",
                dest=flag_name.replace("-", "_"),
                help=help_text,
                action="store_false",
            )

    # argparser.add_argument(
    #     "--filter",
    #     type=str,
    #     nargs="*",
    #     dest="name_filter",
    #     help="Limit checkout to the named device(s) or identifiers",
    # )

    argparser.add_argument(
        "-p", "--parallel",
        action="store_true",
        help="Acquire data for comparisons in parallel",
    )

    argparser.add_argument(
        "-r", "--report-path",
        help="Path to the report save path, if provided"
    )

    return argparser


# `atef config`
def build_arg_parser_config(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.formatter_class = argparse.RawTextHelpFormatter
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


# `atef scripts`
def build_arg_parser_scripts(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.formatter_class = argparse.RawTextHelpFormatter
    description = """
    Runs atef related scripts.  Pick a subcommand to run its script.

    Try:
    """

    sub_parsers = argparser.add_subparsers(help='available script subcommands')
    for script_name, build_parser_func in SUBSCRIPTS.items():
        description += f"\n    $ atef scripts {script_name} --help"
        sub = sub_parsers.add_parser(script_name)
        build_parser_func(sub)
        sub.set_defaults(func=partial(get_main, script_name, "atef.scripts"))

    argparser.description = description
    return argparser


SUBCOMMANDS = {
    "check": (build_arg_parser_check, partial(get_main, "check", "atef.bin")),
    "config": (build_arg_parser_config, partial(get_main, "config", "atef.bin")),
    "scripts": (build_arg_parser_scripts, partial(get_main, "scripts", "atef.bin")),
}
