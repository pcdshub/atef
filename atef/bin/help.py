"""
`atef help` will show detailed help information.
"""

import argparse

DESCRIPTION = __doc__


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        'argument_name',
        type=str,
        help='Get help on this.',
    )

    return argparser


def main(argument_name):
    print(f"This should show help for {argument_name!r}.")
