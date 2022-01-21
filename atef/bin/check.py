"""
`atef check` runs passive checkouts of devices given a configuration file.
"""

import argparse
import logging
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Sequence, Union, cast

import apischema
import happi
import ophyd
import rich
import rich.console
import rich.tree
import yaml

from ..check import (ConfigurationFile, DeviceConfiguration, PVConfiguration,
                     Result, Severity, check_device,
                     pv_config_to_device_config)
from ..util import ophyd_cleanup

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

AnyConfiguration = Union[PVConfiguration, DeviceConfiguration]


@dataclass
class DeviceAndConfig:
    """Device and configuration(s) from a ConfigurationFile."""
    device: ophyd.Device
    dev_config: DeviceConfiguration
    pv_config: Optional[PVConfiguration] = None


class ConfigFileLoadError(Exception):
    """Generic configuration file loading failure."""
    ...


class ConfigFileHappiError(ConfigFileLoadError):
    """Config file load error relating to a happi device."""
    dev_name: str
    dev_config: DeviceConfiguration


class MissingHappiDeviceError(ConfigFileHappiError):
    """Config file load error: the happi device doesn't exist."""
    ...


class HappiLoadError(ConfigFileHappiError):
    """Config file load error: the happi device couldn't be instantiated."""
    ...


def get_configurations_from_file(
    config: ConfigurationFile,
    filtered_devices: Optional[Sequence[str]] = None
) -> Generator[Union[DeviceAndConfig, Exception], None, None]:
    """
    Get all devices and configurations from the given configuration file.

    Yields
    ------
    config : DeviceAndConfig or Exception
        The configuration entry, if valid, or an exception detailing what went
        wrong with the entry.
    """
    for pv_config in config.pvs:
        if filtered_devices and pv_config.name not in filtered_devices:
            logger.debug(
                "Skipping filtered-out PV-only configuration %s", pv_config.name
            )
            continue

        dev_cls, dev_config = pv_config_to_device_config(pv_config)
        logger.debug("PV-only configuration %s -> %s", pv_config.name, dev_cls.__name__)

        dev = dev_cls(name=pv_config.name or "PVConfig")
        yield DeviceAndConfig(dev, dev_config, pv_config)

    if not config.devices:
        return

    client = happi.Client.from_config()
    for dev_name, dev_config in config.devices.items():
        if filtered_devices and dev_name not in filtered_devices:
            logger.debug("Skipping filtered-out device %s", dev_name)
            continue

        try:
            search_result = client[dev_name]
        except KeyError:
            ex = MissingHappiDeviceError(
                f"Device {dev_name} not in happi database; skipping"
            )
            ex.dev_name = dev_name
            ex.dev_config = cast(DeviceConfiguration, dev_config)
            yield ex
            continue

        try:
            dev = search_result.get()
        except Exception as ex:
            logger.debug(
                "Failed to instantiate device %r",
                dev_name,
                exc_info=True,
            )
            load_ex = HappiLoadError(
                f"Device {dev_name} invalid in happi database; "
                f"{ex.__class__.__name__}: {ex}"
            )
            load_ex.dev_name = dev_name
            load_ex.dev_config = cast(DeviceConfiguration, dev_config)
            yield load_ex
            continue

        yield DeviceAndConfig(dev, dev_config)


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
    device: ophyd.Device,
    severity: Severity,
    config: Union[PVConfiguration, DeviceConfiguration],
    results: List[Result],
    *,
    severity_to_rich: Optional[Dict[Severity, str]] = None,
    verbose: int = 0,
):
    """Log check results to the module logger."""
    severity_to_rich = severity_to_rich or default_severity_to_rich

    desc = f" ({config.description}) " if config.description else ""

    tree = rich.tree.Tree(f"{severity_to_rich[severity]} [default]{device.name}{desc}")
    for result in results:
        if result.severity > Severity.success or verbose > 0:
            tree.add(
                f"{severity_to_rich[result.severity]}[default]: {result.reason}"
            )
    console.print(tree)


def main(
    filename: str,
    filtered_devices: Optional[Sequence[str]] = None,
    verbose: int = 0,
    *,
    cleanup: bool = True
):
    serialized_config = yaml.safe_load(open(filename))
    config = apischema.deserialize(ConfigurationFile, serialized_config)

    console = rich.console.Console()
    try:
        with console.status("[bold green] Performing checks..."):
            for info in get_configurations_from_file(
                config, filtered_devices=filtered_devices
            ):
                if isinstance(info, ConfigFileHappiError):
                    console.print("Failed to load", info.dev_name)
                    continue
                if isinstance(info, Exception):
                    console.print("Failed to load", info)
                    continue

                severity, results = check_device(info.device, info.dev_config.checks)
                log_results_rich(
                    console,
                    device=info.device,
                    config=info.dev_config,
                    severity=severity,
                    results=results,
                    verbose=verbose,
                )
    finally:
        if cleanup:
            ophyd_cleanup()
