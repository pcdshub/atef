import asyncio
import concurrent.futures
import functools
import logging
import pathlib
from typing import Callable, Optional, Sequence

import happi
import ophyd

from .enums import Severity
from .exceptions import (HappiLoadError, HappiUnavailableError,
                         MissingHappiDeviceError)

logger = logging.getLogger(__name__)

ATEF_SOURCE_PATH = pathlib.Path(__file__).parent


def ophyd_cleanup():
    """Clean up ophyd - avoid teardown errors by stopping callbacks."""
    dispatcher = ophyd.cl.get_dispatcher()
    if dispatcher is not None:
        dispatcher.stop()


@functools.lru_cache(None)
def get_happi_client() -> Optional[happi.Client]:
    """
    Get the atef-configured happi client or the one as-configured by happi,
    if available.

    If misconfigured, this will warn once and return ``None`` for future calls.
    """
    try:
        return happi.Client.from_config()
    except Exception as ex:
        logger.warning(
            "Unable to load happi Client from configuration (%s): %s",
            ex.__class__.__name__,
            ex,
        )
        return None


def get_happi_device_by_name(
    name: str,
    *,
    client: Optional[happi.Client] = None,
) -> ophyd.Device:
    """
    Get an instantiated device from the happi database by name.

    Parameters
    ----------
    name : str
        The device name.

    client : happi.Client, optional
        The happi Client instance, if available.  Defaults to instantiating
        a temporary client with the environment configuration.
    """
    if client is None:
        client = get_happi_client()

    if client is None:
        ex = HappiUnavailableError(
            f"The happi database is misconfigured or otherwise unavailable; "
            f"unable to load device {name!r}"
        )
        ex.dev_name = name
        ex.dev_config = None
        raise ex

    try:
        search_result = client[name]
    except KeyError:
        ex = MissingHappiDeviceError(
            f"Device {name} not in happi database; skipping"
        )
        ex.dev_name = name
        ex.dev_config = None
        raise ex

    try:
        return search_result.get()
    except Exception as ex:
        logger.debug(
            "Failed to instantiate device %r",
            name,
            exc_info=True,
        )
        load_ex = HappiLoadError(
            f"Device {name} invalid in happi database; "
            f"{ex.__class__.__name__}: {ex}"
        )
        load_ex.dev_name = name
        load_ex.dev_config = None
        raise load_ex from ex


def get_maximum_severity(severities: Sequence[Severity]) -> Severity:
    """Get the maximum severity defined from the sequence of severities."""
    return Severity(
        max(severity.value for severity in tuple(severities) + (Severity.success, ))
    )


def get_minimum_severity(severities: Sequence[Severity]) -> Severity:
    """Get the minimum severity defined from the sequence of severities."""
    severities = tuple(severities)
    if not severities:
        return Severity.success
    return Severity(min(severity.value for severity in severities))


def regex_for_devices(names: Optional[Sequence[str]]) -> str:
    """Get a regular expression that matches all the given device names."""
    names = list(names or [])
    return "|".join(f"^{name}$" for name in names)


async def run_in_executor(
    executor: Optional[concurrent.futures.Executor],
    func: Callable,
    *args, **kwargs
):
    """
    Using the provided executor, run the function and return its value.

    Parameters
    ----------
    executor : concurrent.futures.Executor or None
        The executor to use.  Defaults to the one from the running loop.
    func : Callable
        The function to run.
    *args :
        Arguments to pass.
    **kwargs :
        Keyword arguments to pass.

    Returns
    -------
    Any
        The value returned from func().
    """
    @functools.wraps(func)
    def wrapped():
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, wrapped)
