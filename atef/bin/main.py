"""
`atef` is the top-level command for accessing various subcommands.

Try:

"""

import argparse
import asyncio
import logging
from inspect import iscoroutinefunction

import atef
from atef.bin.subparsers import SUBCOMMANDS

DESCRIPTION = __doc__


def main():
    """
    Create the top-level parser for atef.  Gathers subparsers from
    atef.bin.subparsers, which have been separated to avoid pre-mature imports

    Expects SUBCOMMANDS to be a dictionary mapping subcommand name to a tuple of:
    - sub-parser builder function: Callable[[], argparse.ArgumentParser]
    - function returning the main function for the sub command:
      Callable[[], Callable[**subcommand_kwargs]]

    Have fun "parsing" this ;D
    """
    top_parser = argparse.ArgumentParser(
        prog='atef',
        formatter_class=argparse.RawTextHelpFormatter
    )

    desc = DESCRIPTION

    top_parser.add_argument(
        '--version', '-V',
        action='version',
        version=atef.__version__,
        help="Show the atef version number and exit."
    )

    top_parser.add_argument(
        '--log', '-l', dest='log_level',
        default='INFO',
        type=str,
        help='Python logging level (e.g. DEBUG, INFO, WARNING)'
    )

    subparsers = top_parser.add_subparsers(help='Possible subcommands')
    for command_name, (build_func, main) in SUBCOMMANDS.items():
        desc += f'\n    $ atef {command_name} --help'
        sub = subparsers.add_parser(command_name)
        build_func(sub)
        sub.set_defaults(func=main)

    top_parser.description = desc

    args = top_parser.parse_args()
    kwargs = vars(args)
    log_level = kwargs.pop('log_level')

    logger = logging.getLogger('atef')
    logger.setLevel(log_level)
    logging.basicConfig()

    if hasattr(args, 'func'):
        func = kwargs.pop('func')
        logger.debug('main(**%r)', kwargs)
        main_fn = func()
        if iscoroutinefunction(main_fn):
            asyncio.run(main_fn(**kwargs))
        else:
            main_fn(**kwargs)
    else:
        top_parser.print_help()


if __name__ == '__main__':
    main()
