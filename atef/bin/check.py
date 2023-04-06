"""
`atef check` runs passive checkouts of devices given a configuration file.
"""
from __future__ import annotations

import argparse
import asyncio
import enum
import itertools
import logging
from typing import Dict, Optional, Sequence, Tuple, Union

import happi
import rich
import rich.console
import rich.tree

from ..cache import DataCache, _SignalCache, get_signal_cache
from ..check import Comparison, Severity
from ..config import (AnyConfiguration, AnyPreparedConfiguration,
                      ConfigurationFile, FailedConfiguration,
                      PreparedComparison, PreparedFile, PreparedGroup)
from ..result import Result
from ..util import ophyd_cleanup

logger = logging.getLogger(__name__)

DESCRIPTION = __doc__


class VerbositySetting(enum.Flag):
    show_severity_emoji = enum.auto()
    show_severity_description = enum.auto()
    show_config_description = enum.auto()
    show_tags = enum.auto()
    show_passed_tests = enum.auto()
    default = show_severity_emoji | show_severity_description

    @classmethod
    def from_kwargs(
        cls, start: Optional[VerbositySetting] = None, **kwargs
    ) -> VerbositySetting:
        """
        Get a VerbositySetting from the provided kwargs.

        Parameters
        ----------
        start : VerbositySetting, optional
             The starting VerbositySetting.

        **kwargs : str to bool
            Keyword arguments that match VerbositySetting flags, with the
            value set to False (clear) or True (set).

        Returns
        -------
        VerbositySetting
            The adjusted VerbositySetting.
        """
        def set_or_clear(verbosity: cls, name: str, value: bool) -> cls:
            flag = getattr(cls, name)
            if value:
                return verbosity | flag
            return verbosity & ~flag

        if start is None:
            verbosity = cls.default
        else:
            verbosity = start

        for setting in cls:
            if setting.name is None:
                continue

            setting_value = kwargs.get(setting.name, None)
            if setting_value is not None:
                verbosity = set_or_clear(verbosity, setting.name, setting_value)
        return verbosity


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

    for setting in VerbositySetting:
        flag_name = setting.name.replace("_", "-")
        if setting == VerbositySetting.default:
            continue

        help_text = setting.name.replace("_", " ").capitalize()

        argparser.add_argument(
            f"--{flag_name}",
            dest=setting.name,
            help=help_text,
            action="store_true",
            default=setting in VerbositySetting.default,
        )

        if flag_name.startswith("show-"):
            hide_flag_name = flag_name.replace("show-", "hide-")
            help_text = help_text.replace("Show ", "Hide ")
            argparser.add_argument(
                f"--{hide_flag_name}",
                dest=setting.name,
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

    return argparser


default_severity_to_rich = {
    Severity.success: "[bold green]:heavy_check_mark:",
    Severity.warning: "[bold yellow]:heavy_check_mark:",
    Severity.error: "[bold red]:x:",
    Severity.internal_error: "[bold red]:x:",
}

default_severity_to_log_level = {
    Severity.success: logging.DEBUG,
    Severity.warning: logging.WARNING,
    Severity.error: logging.ERROR,
    Severity.internal_error: logging.ERROR,
}


def get_result_from_comparison(
    item: Union[PreparedComparison, Exception, FailedConfiguration, None]
) -> Tuple[Optional[PreparedComparison], Result]:
    """
    Get a Result, if available, from the provided arguments.

    In the case of an exception (or None/internal error), create one.

    Parameters
    ----------
    item : Union[PreparedComparison, Exception, None]
        The item to grab a result from.

    Returns
    -------
    PreparedComparison or None
        The prepared comparison, if available
    Result
        The result instance.
    """
    if item is None:
        return None, Result(
            severity=Severity.internal_error,
            reason="no result available (comparison not run?)"
        )
    if isinstance(item, Exception):
        # An error that was transformed into a Result with a severity
        return None, Result.from_exception(item)
    if isinstance(item, FailedConfiguration):
        # An error that was transformed into a Result with a severity
        return None, item.result

    if item.result is None:
        return item, Result(
            severity=Severity.internal_error,
            reason="no result available (comparison not run?)"
        )

    return item, item.result


def get_comparison_text_for_tree(
    item: Union[PreparedComparison, Exception],
    *,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
    verbosity: VerbositySetting = VerbositySetting.default,
) -> str:
    """
    Get a description for the rich Tree, given a comparison or a failed result.

    Parameters
    ----------
    item : Union[PreparedComparison, Exception]
        The item to add to the tree.
    severity_to_rich : Dict[Severity, str], optional
        A mapping of severity values to rich colors.
    verbosity : VerbositySetting, optional
        The verbosity settings.

    Returns
    -------
    str or None
        Returns a description to add to the tree.
    """
    severity_to_rich = severity_to_rich or default_severity_to_rich

    prepared, result = get_result_from_comparison(item)
    if result.severity > Severity.success:
        return (
            f"{severity_to_rich[result.severity]}[default]: {result.reason}"
        )

    if VerbositySetting.show_passed_tests in verbosity and prepared is not None:
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
    if VerbositySetting.show_config_description in verbosity:
        if obj.description:
            if obj.name:
                return f"{obj.name}: {obj.description}"
            return obj.description

    if obj.name:
        return obj.name
    return ""


def get_tree_heading(
    obj: AnyPreparedConfiguration,
    verbosity: VerbositySetting,
    severity_to_rich: Dict[Severity, str],
) -> str:
    """
    Get severity, name, and description (per verbosity settings) for a tree.

    Parameters
    ----------
    obj : Comparison or AnyConfiguration
        The comparison or configuration.

    Returns
    -------
    str
        The displayable name.
    """
    severity: Severity = getattr(obj.result, "severity", Severity.error)

    severity_text = []
    if VerbositySetting.show_severity_emoji in verbosity:
        severity_text.append(severity_to_rich[severity])
    if VerbositySetting.show_severity_description in verbosity:
        severity_text.append(severity.name.replace("_", " ").capitalize())
        severity_text.append(": ")

    severity_text = "".join(severity_text)
    name_and_desc = get_name_for_tree(obj.config, verbosity)
    return f"{severity_text}{name_and_desc}"


def should_show_in_tree(
    item: Union[PreparedComparison, Exception, FailedConfiguration, None],
    verbosity: VerbositySetting = VerbositySetting.default
) -> bool:
    """
    Should ``item`` be shown in the tree, based on the verbosity settings?

    Parameters
    ----------
    item : Union[PreparedComparison, Exception, FailedConfiguration, None]
        The item to check.
    verbosity : VerbositySetting, optional
        The verbosity settings.

    Returns
    -------
    bool
        True to show it in the tree, False to not show it.
    """
    _, result = get_result_from_comparison(item)
    if result is None:
        # Error - always show it
        return True

    if result.severity == Severity.success:
        return VerbositySetting.show_passed_tests in verbosity
    return True


def group_to_rich_tree(
    group: PreparedGroup,
    verbosity: VerbositySetting = VerbositySetting.default,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
) -> rich.tree.Tree:
    """
    Convert a `PreparedGroup` into a `rich.tree.Tree`.

    Parameters
    ----------
    group : PreparedGroup
        The group to convert.  Comparisons must be complete to generate the
        tree effectively.
    verbosity : VerbositySetting, optional
        The verbosity settings.
    severity_to_rich : Dict[Severity, str], optional
        A mapping of severity values to rich colors.

    Returns
    -------
    rich.tree.Tree
    """
    severity_to_rich = severity_to_rich or default_severity_to_rich

    tree = rich.tree.Tree(
        get_tree_heading(group, severity_to_rich=severity_to_rich, verbosity=verbosity)
    )
    for failure in group.prepare_failures:
        if should_show_in_tree(failure, verbosity):
            tree.add(
                get_comparison_text_for_tree(failure, verbosity=verbosity)
            )

    for config in group.configs:
        if isinstance(config, PreparedGroup):
            tree.add(
                group_to_rich_tree(
                    config,
                    verbosity=verbosity,
                    severity_to_rich=severity_to_rich
                )
            )
        else:
            subtree = rich.tree.Tree(
                get_tree_heading(
                    config, severity_to_rich=severity_to_rich, verbosity=verbosity
                )
            )
            severity = getattr(config.result, "severity", Severity.error)
            if config.result is not None and severity == Severity.success:
                if VerbositySetting.show_passed_tests in verbosity:
                    tree.add(subtree)
            else:
                tree.add(subtree)

            for comparison in itertools.chain(
                config.comparisons, config.prepare_failures
            ):
                if should_show_in_tree(comparison, verbosity):
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
    verbosity : VerbositySetting, optional
        The verbosity settings.
    client : happi.Client, optional
        The happi client, if available.
    name_filter : Sequence[str], optional
        A filter for names.
    parallel : bool, optional
        Pre-fill cache in parallel when possible.
    cache : DataCache
        The data cache instance.
    """
    name_filter = list(name_filter or [])

    if cache is None:
        cache = DataCache()

    prepared_file = PreparedFile.from_config(config, cache=cache, client=client)

    cache_fill_tasks = []
    if parallel:
        try:
            cache_fill_tasks = await prepared_file.fill_cache()
        except asyncio.CancelledError:
            console.print("Tests interrupted; no results available.")
            return

    try:
        await prepared_file.compare()
    except asyncio.CancelledError:
        console.print("Tests interrupted; showing partial results.")
        for task in cache_fill_tasks or []:
            task.cancel()

    root_tree = rich.tree.Tree(str(filename))
    tree = group_to_rich_tree(prepared_file.root, verbosity=verbosity)
    root_tree.add(tree)

    if filename is not None:
        console.print(root_tree)
    else:
        console.print(tree)


async def main(
    filename: str,
    name_filter: Optional[Sequence[str]] = None,
    parallel: bool = False,
    *,
    cleanup: bool = True,
    signal_cache: Optional[_SignalCache] = None,
    show_severity_emoji: bool = True,
    show_severity_description: bool = True,
    show_config_description: bool = False,
    show_tags: bool = False,
    show_passed_tests: bool = False,
):

    verbosity = VerbositySetting.from_kwargs(
        show_severity_emoji=show_severity_emoji,
        show_severity_description=show_severity_description,
        show_config_description=show_config_description,
        show_tags=show_tags,
        show_passed_tests=show_passed_tests,
    )

    config_file = ConfigurationFile.from_filename(filename)

    console = rich.console.Console()
    cache = DataCache(signals=signal_cache or get_signal_cache())
    try:
        with console.status("[bold green] Performing checks..."):
            await check_and_log(
                config_file,
                console=console,
                name_filter=name_filter,
                parallel=parallel,
                cache=cache,
                filename=filename,
                verbosity=verbosity,
            )
    finally:
        if cleanup:
            ophyd_cleanup()
