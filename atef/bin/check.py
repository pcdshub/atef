"""
`atef check` runs passive checkouts of devices given a configuration file.
"""
import argparse

DESCRIPTION = __doc__

_VERBOSITY_SETTINGS = {
    "show-severity-emoji": True,
    "show-severity-description": True,
    "show-config-description": False,
    "show-tags": False,
    "show-passed-tests": False,
}


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "filename",
        type=str,
        help="Configuration filename",
    )

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


async def main(*args, **kwargs):
    from atef.bin.check_main import main

    await main(*args, **kwargs)
