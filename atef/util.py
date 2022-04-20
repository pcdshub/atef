import logging
from typing import Optional

import happi
import ophyd

from .exceptions import HappiLoadError, MissingHappiDeviceError

logger = logging.getLogger(__name__)


def ophyd_cleanup():
    """Clean up ophyd - avoid teardown errors by stopping callbacks."""
    dispatcher = ophyd.cl.get_dispatcher()
    if dispatcher is not None:
        dispatcher.stop()


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
        client = happi.Client.from_config()

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
