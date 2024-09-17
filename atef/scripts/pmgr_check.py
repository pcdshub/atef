"""
This script creates an atef check from a pmgr configuration.  The configuration will
be converted into a PVConfiguration.  Note that default tolerances will be used for
checks.

An example invocation might be:
python scripts/pmgr_check.py cxi test_pmgr_checkout.json --names "KB1 DS SLIT LEF" --prefix CXI:KB1:MMS:13
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
        "--names",
        "-n",
        dest="pmgr_names",
        type=str,
        nargs="+",
        help="a list of stored pmgr configuration names, case and whitespace sensitive. "
             "e.g. 'KB1 DS SLIT LEF'.  Length must match --prefixes",
    )

    argparser.add_argument(
        "--prefixes",
        "-p",
        dest="prefixes",
        type=str,
        nargs="+",
        help="a list of EPICS PV prefixes, e.g. 'CXI:KB1:MMS:13'.  Length must match --names",
    )

    argparser.add_argument(
        "--table",
        "-t",
        dest="table_name",
        default="ims_motor",
        type=str,
        help="Table type, by default 'ims_motor'",
    )

    argparser.add_argument(
        dest="hutch",
        type=str,
        help="name of hutch, e.g. 'cxi'",
    )

    argparser.add_argument(
        "filename",
        type=str,
        help="Output filepath",
    )

    return argparser


def main(*args, **kwargs):
    from atef.scripts.pmgr_check_main import main
    main(*args, **kwargs)


def main_script(args=None) -> None:
    """Get pmgr data and contruct checkout."""
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
