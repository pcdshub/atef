"""
sub-parsers for scripts files.  Separarted to allow inclusion in main cli
entrypoint without importing core functionality
"""
import argparse


def build_pmgr_arg_parser(argparser=None) -> argparse.ArgumentParser:
    """Create the argparser."""
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = """
    This script creates an atef check from a pmgr configuration.  The configuration will
    be converted into a PVConfiguration.  Note that default tolerances will be used for
    checks.

    An example invocation might be:
    python scripts/pmgr_check.py cxi test_pmgr_checkout.json --names "KB1 DS SLIT LEF" --prefix CXI:KB1:MMS:13
    """

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


def build_converter_arg_parser(argparser=None) -> argparse.ArgumentParser:
    """Create the argparser."""
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = """
    This script will convert a prototype atef configuration file to the latest
    supported (and numbered) version.
    """
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


SUBSCRIPTS = {
    "converter_v0": build_converter_arg_parser,
    "pmgr_check": build_pmgr_arg_parser,
}
