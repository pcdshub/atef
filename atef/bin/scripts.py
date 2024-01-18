"""
`atef scripts` runs helper scripts.  Scripts may be added over time.
"""

import argparse
import importlib
import logging
from pkgutil import iter_modules
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)

DESCRIPTION = __doc__


def gather_scripts() -> Dict[str, Tuple[Callable, Callable]]:
    """Gather scripts, one main function from each submodule"""
    # similar to main's _build_commands
    global DESCRIPTION
    DESCRIPTION += "\nTry:\n"
    results = {}
    unavailable = []

    scripts_module = importlib.import_module("atef.scripts")
    for sub_module in iter_modules(scripts_module.__path__):
        module_name = sub_module.name
        try:
            module = importlib.import_module(f".{module_name}", "atef.scripts")
        except Exception as ex:
            unavailable.append((module_name, ex))
        else:
            results[module_name] = (module.build_arg_parser, module.main)
            DESCRIPTION += f'\n    $ atef scripts {module_name} --help'

    if unavailable:
        DESCRIPTION += '\n\n'

        for command, ex in unavailable:
            DESCRIPTION += (
                f'\nWARNING: "atef scripts {command}" is unavailable due to:'
                f'\n\t{ex.__class__.__name__}: {ex}'
            )

    return results


SCRIPTS = gather_scripts()


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = """
    Runs atef related scripts.  Pick a subcommand to run its script
    """

    sub_parsers = argparser.add_subparsers(help='available script subcommands')
    for script_name, (build_parser_func, script_main) in SCRIPTS.items():
        sub = sub_parsers.add_parser(script_name)
        build_parser_func(sub)
        sub.set_defaults(func=script_main)

    return argparser


def main():
    print(DESCRIPTION)
