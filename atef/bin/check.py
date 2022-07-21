"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import asyncio
import dataclasses
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
                      PathItem, PreparedComparison)
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


def description_for_tree(
    item: Union[PreparedComparison, Exception],
    *,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
    verbose: int = 0,
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

    if verbose > 0 and prepared is not None:
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


def _get_name_and_description(obj: Union[Comparison, AnyConfiguration]) -> str:
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
    if obj.name and obj.description:
        return f"{obj.name}: {obj.description}"
    if obj.name:
        return obj.name
    return obj.description or ""


def _get_display_name_for_item(item: PathItem) -> str:
    """
    Get the text to show in the rich Tree for the given item.

    Parameters
    ----------
    item : PathItem
        The item to get the display text for.

    Returns
    -------
    str
        Text to display.
    """
    if isinstance(item, (Comparison, Configuration)):
        return _get_name_and_description(item)
    return getattr(item, "name", str(item)) or ""


@dataclasses.dataclass
class _RichTreeHelper:
    """A helper for mapping to subtrees of a root ``rich.Tree``."""
    root: rich.tree.Tree
    path_to_tree: Dict[Tuple[str, ...], rich.tree.Tree] = dataclasses.field(
        default_factory=dict
    )
    path: List[str] = dataclasses.field(default_factory=list)

    def get_subtree(self, path: Iterable[PathItem]) -> rich.tree.Tree:
        """
        Get a subtree based on the traversed node names along the path.

        Parameters
        ----------
        path : Iterable[str]
            The path of names to the subtree.

        Returns
        -------
        rich.tree.Tree
            The subtree depending on the provided path.
        """
        displayed_path = [_get_display_name_for_item(item) for item in path]
        partial_path = ()
        node = self.root
        for part in displayed_path:
            partial_path = partial_path + (part, )
            if partial_path not in self.path_to_tree:
                subtree = rich.tree.Tree(partial_path[-1])
                self.path_to_tree[partial_path] = subtree
                node.add(subtree)

            node = self.path_to_tree[partial_path]

        return node


def log_results_rich(
    console: rich.console.Console,
    severity: Severity,
    config: AnyConfiguration,
    items: List[Union[Exception, PreparedComparison]],
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

    root = rich.tree.Tree(f"{label_prefix}{label_middle}{label_suffix}")
    tree_helper = _RichTreeHelper(root=root)
    for item in items:
        path = getattr(item, "path", [])

        # Remove the configuration name ``path[0]`` and the final attribute
        # in the tree ``path[-1]``
        path = path[1:-1]

        desc = description_for_tree(
            item, severity_to_rich=severity_to_rich, verbose=verbose
        )
        if desc:
            node = tree_helper.get_subtree(path)
            node.add(desc)

    console.print(root)


async def check_and_log(
    config: AnyConfiguration,
    console: rich.console.Console,
    verbose: int = 0,
    client: Optional[happi.Client] = None,
    name_filter: Optional[Sequence[str]] = None,
    parallel: bool = True,
    cache: Optional[DataCache] = None,
):
    """
    Check a configuration and log the results.

    Parameters
    ----------
    config : AnyConfiguration
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

    all_prepared = list(
        PreparedComparison.from_config(config, client=client, cache=cache)
    )

    cache_fill_tasks = []
    if parallel:
        for prepared in all_prepared:
            if isinstance(prepared, PreparedComparison):
                cache_fill_tasks.append(
                    asyncio.create_task(prepared.get_data_async())
                )

    for prepared in all_prepared:
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

            prepared.result = await prepared.compare()
            if prepared.result is not None:
                items.append(prepared)
                severities.append(prepared.result.severity)
        elif isinstance(prepared, Exception):
            ex = cast(Exception, prepared)
            items.append(ex)
            severities.append(Result.from_exception(ex).severity)
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
            for config in config_file.configs:
                await check_and_log(
                    config,
                    console=console,
                    verbose=verbose,
                    client=client,
                    name_filter=name_filter,
                    parallel=parallel,
                    cache=cache,
                )
    finally:
        if cleanup:
            ophyd_cleanup()
