"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import logging
import pathlib
from typing import Dict, List, Optional, Sequence

import happi
import ophyd
import rich
import rich.console
import rich.tree

from ..check import (AnyConfiguration, ConfigurationFile, PreparedComparison,
                     PreparedComparisonException, Result, Severity)
from ..exceptions import ConfigFileHappiError
from ..util import get_maximum_severity, ophyd_cleanup

logger = logging.getLogger(__name__)

DESCRIPTION = __doc__


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

    argparser.add_argument(
        "-v", "--verbose",
        action="count",
        help="Increase output verbosity",
        default=0,
    )

    argparser.add_argument(
        "--device",
        type=str,
        nargs="*",
        dest="filtered_devices",
        help="Limit checkout to the named device(s)",
    )

    return argparser


default_severity_to_rich = {
    Severity.success: "[bold green]:heavy_check_mark: Success",
    Severity.warning: "[bold yellow]:heavy_check_mark: Warning",
    Severity.error: "[bold red]:x: Error",
    Severity.internal_error: "[bold red]:x: Internal error",
}

default_severity_to_log_level = {
    Severity.success: logging.DEBUG,
    Severity.warning: logging.WARNING,
    Severity.error: logging.ERROR,
    Severity.internal_error: logging.ERROR,
}


def log_results_rich(
    console: rich.console.Console,
    severity: Severity,
    config: AnyConfiguration,
    results: List[PreparedComparison],
    errors: List[Result],
    *,
    device: Optional[ophyd.Device] = None,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
    verbose: int = 0,
):
    """Log check results to the module logger."""
    severity_to_rich = severity_to_rich or default_severity_to_rich

    desc = f" ({config.description}) " if config.description else ""

    # Not sure about this just yet:
    label_prefix = f"{severity_to_rich[severity]} [default]"
    label_suffix = desc
    if config.name:
        label_middle = config.name
    elif device is not None:
        label_middle = device.name
    else:
        label_middle = ""

    tree = rich.tree.Tree(f"{label_prefix}{label_middle}{label_suffix}")
    for error in errors:
        tree.add(
            tree.add(
                f"{severity_to_rich[error.severity]}[default]: {error.reason}"
            )
        )

    for prepared in results:
        result = prepared.result
        if result is None:
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: comparison not run"
            )
            continue

        if result.severity > Severity.success:
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: {result.reason}"
            )
        elif verbose > 0:
            if prepared.comparison is not None:
                description = prepared.comparison.describe()
            else:
                description = "no comparison configured"

            tree.add(
                f"{severity_to_rich[result.severity]}[default]: "
                f"{prepared.identifier} {description}"
            )

    console.print(tree)


def main(
    filename: str,
    filtered_devices: Optional[Sequence[str]] = None,
    verbose: int = 0,
    *,
    cleanup: bool = True
):
    path = pathlib.Path(filename)
    if path.suffix.lower() == ".json":
        config_file = ConfigurationFile.from_json(filename)
    else:
        config_file = ConfigurationFile.from_yaml(filename)

    try:
        client = happi.Client.from_config()
    except Exception:
        # happi isn't necessarily required; fail later if we try to use it.
        # Without a proper config, it may raise OSError or something strange.
        client = None

    console = rich.console.Console()
    try:
        with console.status("[bold green] Performing checks..."):
            for config in config_file.configs:
                items = []
                errors = []
                for prepared in PreparedComparison.from_config(config, client=client):
                    if isinstance(prepared, PreparedComparison):
                        prepared.result = prepared.compare()
                        if prepared.result is not None:
                            items.append(prepared)
                    else:
                        if isinstance(prepared, ConfigFileHappiError):
                            console.print("Failed to load", prepared.dev_name)
                            severity = Severity.internal_error
                        elif isinstance(prepared, PreparedComparisonException):
                            console.print("Failed to prepare comparison", prepared)
                            if prepared.comparison is not None:
                                severity = prepared.comparison.severity_on_failure
                            else:
                                severity = Severity.internal_error
                        else:
                            severity = Severity.internal_error
                            console.print("Failed to load", prepared)

                        errors.append(
                            Result(
                                severity=severity,
                                reason=str(prepared)
                            )
                        )

                severity = get_maximum_severity(
                    [item.result.severity for item in items] +
                    [error.severity for error in errors]
                )

                log_results_rich(
                    console,
                    config=config,
                    errors=errors,
                    severity=severity,
                    results=items,
                    verbose=verbose,
                )
    finally:
        if cleanup:
            ophyd_cleanup()
