"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import logging
import pathlib
from typing import Dict, List, Optional, Sequence, Union

import happi
import ophyd
import rich
import rich.console
import rich.tree

from ..check import (ConfigurationFile, DeviceConfiguration,
                     PreparedComparison, PVConfiguration, Result, Severity)
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


def log_results(
    device: ophyd.Device,
    severity: Severity,
    config: Union[PVConfiguration, DeviceConfiguration],
    results: List[Result],
    *,
    severity_to_log_level: Optional[Dict[Severity, int]] = None,
):
    """Log check results to the module logger."""
    severity_to_log_level = severity_to_log_level or default_severity_to_log_level

    passed_or_failed = "passed" if severity <= Severity.warning else "FAILED"
    logger.info(
        "Device %s (%s) %s with severity %s",
        device.name,
        config.description or "no description",
        passed_or_failed,
        severity.name,
    )
    for result in results:
        log_level = severity_to_log_level[result.severity]
        if not logger.isEnabledFor(log_level):
            continue
        logger.log(log_level, result.reason)


def log_results_rich(
    console: rich.console.Console,
    severity: Severity,
    config: Union[PVConfiguration, DeviceConfiguration],
    results: List[Result],
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
    for result in results:
        if result.severity > Severity.success:
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: {result.reason}"
            )
        elif verbose > 0:
            # TODO
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: {result.reason} (TODO)"
            )
            # comparison_info = result.comparison.describe() if result.comparison else ""
            # tree.add(
            #     f"{severity_to_rich[result.severity]}[default]: "
            #     f"{comparison_info} "
            #     f"{result.reason or ''}"
            # )

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

    client = happi.Client.from_config()
    console = rich.console.Console()
    try:
        with console.status("[bold green] Performing checks..."):
            for config in config_file.configs:
                results = []
                for prepared in PreparedComparison.from_config(config, client=client):
                    if isinstance(prepared, ConfigFileHappiError):
                        console.print("Failed to load", prepared.dev_name)
                        continue
                    elif isinstance(prepared, Exception):
                        console.print("Failed to load", prepared)
                        continue

                    results.append(prepared.compare())

                severity = get_maximum_severity([result.severity for result in results])

                log_results_rich(
                    console,
                    config=config,
                    severity=severity,
                    results=results,
                    device=prepared.device,
                    verbose=verbose,
                )
    finally:
        if cleanup:
            ophyd_cleanup()
