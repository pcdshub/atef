"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import logging
import pathlib
from typing import Dict, List, Optional, Sequence, Union, cast

import happi
import ophyd
import rich
import rich.console
import rich.tree

from ..check import Result, Severity
from ..config import AnyConfiguration, ConfigurationFile, PreparedComparison
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
        "--filter",
        type=str,
        nargs="*",
        dest="name_filter",
        help="Limit checkout to the named device(s) or identifiers",
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
    items: List[Union[Result, PreparedComparison]],
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
    for item in items:
        if isinstance(item, PreparedComparison):
            # A successfully prepared comparison
            result = item.result
            prepared = item
        else:
            # An error that was transformed into a Result with a severity
            result = item
            prepared = None

        if result is None:
            tree.add(
                f"{severity_to_rich[Severity.internal_error]}[default]: "
                f"comparison not run"
            )
        elif result.severity > Severity.success:
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: {result.reason}"
            )
        elif verbose > 0 and prepared is not None:
            if prepared.comparison is not None:
                description = prepared.comparison.describe()
            else:
                description = "no comparison configured"

            tree.add(
                f"{severity_to_rich[result.severity]}[default]: "
                f"{prepared.identifier} {description}"
            )

    console.print(tree)


def check_and_log(
    config: AnyConfiguration,
    console: rich.console.Console,
    verbose: int = 0,
    client: Optional[happi.Client] = None,
    name_filter: Optional[Sequence[str]] = None,
):
    """Check a configuration and log the results."""
    items = []
    name_filter = list(name_filter or [])
    severities = []
    for prepared in PreparedComparison.from_config(config, client=client):
        if isinstance(prepared, PreparedComparison):
            if name_filter:
                device_name = getattr(prepared.device, "name", None)
                if device_name is not None:
                    if device_name not in name_filter:
                        logger.debug(
                            "Skipping device check at user's request: %s",
                            device_name,
                        )
                        continue
                elif prepared.identifier not in name_filter:
                    logger.debug(
                        "Skipping identifier at user's request: %s",
                        prepared.identifier
                    )
                    continue

            prepared.result = prepared.compare()
            if prepared.result is not None:
                items.append(prepared)
                severities.append(prepared.result.severity)
        elif isinstance(prepared, Exception):
            ex = cast(Exception, prepared)
            result = Result.from_exception(ex)
            items.append(result)
            severities.append(result.severity)
        else:
            logger.error(
                "Internal error: unexpected result from PreparedComparison: %s",
                type(prepared)
            )

    if not items:
        # Nothing to report; all filtered out
        return

    log_results_rich(
        console,
        config=config,
        severity=get_maximum_severity(severities),
        items=items,
        verbose=verbose,
    )


def main(
    filename: str,
    name_filter: Optional[Sequence[str]] = None,
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
                check_and_log(
                    config,
                    console=console,
                    verbose=verbose,
                    client=client,
                    name_filter=name_filter,
                )
    finally:
        if cleanup:
            ophyd_cleanup()
