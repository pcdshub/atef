"""
This script will convert a prototype atef configuration file to the latest
supported (and numbered) version.
"""
import argparse
import logging

logger = logging.getLogger(__name__)

DESCRIPTION = __doc__


def build_arg_parser(argparser=None) -> argparse.ArgumentParser:
    """Create the argparser."""
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "--log",
        "-l",
        dest="log_level",
        default="INFO",
        type=str,
        help="Python logging level (e.g. DEBUG, INFO, WARNING)",
    )

    argparser.add_argument(
        "filename",
        type=str,
        nargs="+",
        help="File(s) to convert",
    )

    argparser.add_argument(
        "--write",
        action="store_true",
        help="Convert and overwrite the files in-place",
    )

    return argparser


def main(*args, **kwargs):
    from atef.scripts.converter_v0_main import main
    main(*args, **kwargs)


def main_script(args=None) -> None:
    """Run the conversion tool."""
    parser = build_arg_parser()

    # Add log_level if running file alone
    parser.add_argument(
        "--log",
        "-l",
        dest="log_level",
        default="INFO",
        type=str,
        help="Python logging level (e.g. DEBUG, INFO, WARNING), by default INFO",
    )
    args = parser.parse_args()
    kwargs = vars(args)
    logger.setLevel(args.log_level)
    kwargs.pop('log_level')
    logging.basicConfig()

    main(**kwargs)


if __name__ == "__main__":
    main_script()
