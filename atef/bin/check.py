"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import asyncio
import dataclasses
import enum
import logging
import pathlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union, cast

import happi
import ophyd
import rich
import rich.console
import rich.tree

from ..cache import DataCache, _SignalCache, get_signal_cache
from ..check import Comparison, Result, Severity
from ..config import (AnyConfiguration, Configuration, ConfigurationFile,
                      ConfigurationGroup, PreparedComparison, PreparedFile,
                      PreparedGroup)
from ..util import get_maximum_severity, ophyd_cleanup

logger = logging.getLogger(__name__)

DESCRIPTION = __doc__


class VerbositySetting(enum.Flag):
    default = enum.auto()
    show_description = enum.auto()
    show_tags = enum.auto()
    show_passed_tests = enum.auto()


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

    argparser.add_argument(
        "-p", "--parallel",
        action="store_true",
        help="Acquire data for comparisons in parallel",
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


def get_comparison_text_for_tree(
    item: Union[PreparedComparison, Exception],
    *,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
    verbosity: VerbositySetting = VerbositySetting.default,
) -> Optional[str]:
    """
    Get a description for the rich Tree, given a comparison or a failed result.

    Parameters
    ----------
    item : Union[PreparedComparison, Exception]
        The item to add to the tree.
    severity_to_rich : Optional[Dict[Severity, str]], optional
        A mapping of severity values to rich colors.
    verbose : int, optional
        The verbosity level, where 0 is not verbose.

    Returns
    -------
    str or None
        Returns a description to add to the tree if applicable for the given
        verbosity level, or ``None`` if no message should be displayed.
    """
    severity_to_rich = severity_to_rich or default_severity_to_rich

    if isinstance(item, PreparedComparison):
        # A successfully prepared comparison
        result = item.result
        prepared = item
    elif isinstance(item, Exception):
        # An error that was transformed into a Result with a severity
        result = Result.from_exception(item)
        prepared = None
    else:
        raise ValueError(f"Unexpected item type: {item}")

    if result is None:
        return (
            f"{severity_to_rich[Severity.internal_error]}[default]: "
            f"comparison not run"
        )

    if result.severity > Severity.success:
        return (
            f"{severity_to_rich[result.severity]}[default]: {result.reason}"
        )

    if VerbositySetting.show_passed_tests and prepared is not None:
        if prepared.comparison is not None:
            description = prepared.comparison.describe()
        else:
            description = "no comparison configured"

        return (
            f"{severity_to_rich[result.severity]}[default]: "
            f"{prepared.identifier} {description}"
        )

    # According to the severity and verbosity settings, this message should
    # not be displayed.
    return None


def get_name_for_tree(
    obj: Union[Comparison, AnyConfiguration],
    verbosity: VerbositySetting
) -> str:
    """
    Get a combined name and description for a given item.

    Parameters
    ----------
    obj : Union[Comparison, AnyConfiguration]
        The comparison or configuration.

    Returns
    -------
    str
        The displayable name.
    """
    if VerbositySetting.show_description in verbosity:
        if obj.description:
            if obj.name:
                return f"{obj.name}: {obj.description}"
            return obj.description

    if obj.name:
        return obj.name
    return ""


def group_to_rich_tree(
    group: PreparedGroup,
    verbosity: VerbositySetting = VerbositySetting.default,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
):
    severity_to_rich = severity_to_rich or default_severity_to_rich

    severity_marker = severity_to_rich[group.result.severity]
    tree_name = get_name_for_tree(group.group, verbosity=verbosity)
    tree = rich.tree.Tree(f"{severity_marker} {tree_name}")
    print("tree", tree_name)
    for config in group.configs:
        print(type(config))
        if isinstance(config, PreparedGroup):
            tree.add(
                group_to_rich_tree(
                    config,
                    verbosity=verbosity,
                    severity_to_rich=severity_to_rich
                )
            )
        else:
            result = config.result
            severity = getattr(result, "severity", Severity.error)
            severity_marker = severity_to_rich[group.result.severity]

            subtree_name = get_name_for_tree(config.config, verbosity=verbosity)
            subtree = rich.tree.Tree(f"{severity_marker} {subtree_name}")
            if result is not None and severity == Severity.success:
                if VerbositySetting.show_passed_tests in verbosity:
                    tree.add(subtree)
            else:
                tree.add(subtree)

            for comparison in config.comparisons:
                subtree.add(
                    get_comparison_text_for_tree(comparison, verbosity=verbosity)
                )

            for comparison in config.prepare_failures:
                subtree.add(
                    get_comparison_text_for_tree(comparison, verbosity=verbosity)
                )

    return tree


async def check_and_log(
    config: ConfigurationFile,
    console: rich.console.Console,
    verbosity: VerbositySetting = VerbositySetting.default,
    client: Optional[happi.Client] = None,
    name_filter: Optional[Sequence[str]] = None,
    parallel: bool = True,
    cache: Optional[DataCache] = None,
    filename: Optional[str] = None,
):
    """
    Check a configuration and log the results.

    Parameters
    ----------
    config : ConfigurationFile
        The configuration to check.
    console : rich.console.Console
        The rich console to write output to.
    verbose : int, optional
        The verbosity level for the output.
    client : happi.Client, optional
        The happi client, if available.
    name_filter : Sequence[str], optional
        A filter for names.
    parallel : bool, optional
        Pre-fill cache in parallel when possible.
    cache : DataCache
        The data cache instance.
    """
    items = []
    name_filter = list(name_filter or [])
    severities = []

    if cache is None:
        cache = DataCache()

    prepared_file = PreparedFile.from_config(config, cache=cache, client=client)

    cache_fill_tasks = []
    if parallel:
        cache_fill_tasks = await prepared_file.fill_cache()

    await prepared_file.compare()

    root_tree = rich.tree.Tree(str(filename))
    tree = group_to_rich_tree(prepared_file.root, verbosity=verbosity)
    root_tree.add(tree)

    if filename is not None:
        console.print(root_tree)
    else:
        console.print(tree)

    # for prepared in all_prepared:
    #     if isinstance(prepared, PreparedComparison):
    #         if name_filter:
    #             device_name = getattr(prepared.device, "name", None)
    #             if device_name is not None:
    #                 if device_name not in name_filter:
    #                     logger.debug(
    #                         "Skipping device check at user's request: %s",
    #                         device_name,
    #                     )
    #                     continue
    #             elif prepared.identifier not in name_filter:
    #                 logger.debug(
    #                     "Skipping identifier at user's request: %s",
    #                     prepared.identifier
    #                 )
    #                 continue

    #         prepared.result = await prepared.compare()
    #         if prepared.result is not None:
    #             items.append(prepared)
    #             severities.append(prepared.result.severity)
    #     elif isinstance(prepared, Exception):
    #         ex = cast(Exception, prepared)
    #         items.append(ex)
    #         severities.append(Result.from_exception(ex).severity)
    #     else:
    #         logger.error(
    #             "Internal error: unexpected result from PreparedComparison: %s",
    #             type(prepared)
    #         )

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


async def main(
    filename: str,
    name_filter: Optional[Sequence[str]] = None,
    verbose: int = 0,
    parallel: bool = False,
    *,
    cleanup: bool = True,
    signal_cache: Optional[_SignalCache] = None,
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
    cache = DataCache(signals=signal_cache or get_signal_cache())
    try:
        with console.status("[bold green] Performing checks..."):
            await check_and_log(
                config_file,
                console=console,
                # verbose=verbose,
                client=client,
                name_filter=name_filter,
                parallel=parallel,
                cache=cache,
                filename=filename,
            )
    finally:
        if cleanup:
            ophyd_cleanup()
