from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Generator, List, Mapping, Optional, Sequence, Tuple, Union

import apischema
import happi
import ophyd
import yaml

from . import serialization, util
from .cache import get_signal_cache
from .check import Comparison, Result
from .enums import Severity
from .exceptions import PreparedComparisonException
# from .tools import ToolArguments, SupportedTool
from .type_hints import AnyPath
from .yaml_support import init_yaml_support

logger = logging.getLogger(__name__)


@dataclass
class IdentifierAndComparison:
    """
    Set of identifiers (IDs) and comparisons to perform on those identifiers.
    """
    #: An optional identifier for this set.
    name: Optional[str] = None
    #: PV name, attribute name, or test-specific identifier.
    ids: List[str] = field(default_factory=list)
    #: The comparisons to perform for *each* of the ids.
    comparisons: List[Comparison] = field(default_factory=list)


@dataclass
@serialization.as_tagged_union
class Configuration:
    """
    Configuration base class for shared settings between all configurations.

    Subclasses of Comparison will be serialized as a tagged union.  This means
    that the subclass name will be used as an identifier for the generated
    serialized dictionary (and JSON object).
    """

    #: Name tied to this configuration.
    name: Optional[str] = None
    #: Description tied to this configuration.
    description: Optional[str] = None
    #: Tags tied to this configuration.
    tags: Optional[List[str]] = None
    #: Comparison checklist for this configuration.
    checklist: List[IdentifierAndComparison] = field(default_factory=list)


@dataclass
class DeviceConfiguration(Configuration):
    #: Happi device names which give meaning to self.checklist[].ids.
    devices: List[str] = field(default_factory=list)


@dataclass
class PVConfiguration(Configuration):
    ...


# @dataclass
# class ToolConfiguration(Configuration):
#     tool: SupportedTool = SupportedTool.ping
#     arguments: ToolArguments = field(default_factory=dict)


AnyConfiguration = Union[PVConfiguration, DeviceConfiguration]


@dataclass
class ConfigurationFile:
    """
    A configuration file comprised of a number of devices/PV configurations.
    """

    #: configs: either PVConfiguration or DeviceConfiguration.
    configs: List[Configuration]

    def get_by_device(self, name: str) -> Generator[DeviceConfiguration, None, None]:
        """Get all configurations that match the device name."""
        for config in self.configs:
            if isinstance(config, DeviceConfiguration):
                if name in config.devices:
                    yield config

    def get_by_pv(
        self, pvname: str
    ) -> Generator[Tuple[PVConfiguration, List[IdentifierAndComparison]], None, None]:
        """Get all configurations + IdentifierAndComparison that match the PV name."""
        for config in self.configs:
            if isinstance(config, PVConfiguration):
                checks = [check for check in config.checklist if pvname in check.ids]
                if checks:
                    yield config, checks

    def get_by_tag(self, *tags: str) -> Generator[Configuration, None, None]:
        """Get all configurations that match the tag name."""
        if not tags:
            return

        tag_set = set(tags)
        for config in self.configs:
            if tag_set.intersection(set(config.tags or [])):
                yield config

    @classmethod
    def from_json(cls, filename: AnyPath) -> ConfigurationFile:
        """Load a configuration file from JSON."""
        with open(filename) as fp:
            serialized_config = json.load(fp)
        return apischema.deserialize(cls, serialized_config)

    @classmethod
    def from_yaml(cls, filename: AnyPath) -> ConfigurationFile:
        """Load a configuration file from yaml."""
        with open(filename) as fp:
            serialized_config = yaml.safe_load(fp)
        return apischema.deserialize(cls, serialized_config)

    def to_json(self):
        """Dump this configuration file to a JSON-compatible dictionary."""
        return apischema.serialize(ConfigurationFile, self, exclude_defaults=True)

    def to_yaml(self):
        """Dump this configuration file to yaml."""
        init_yaml_support()
        return yaml.dump(self.to_json())


@dataclass
class PreparedComparison:
    """
    A unified representation of comparisons for device signals and standalone PVs.
    """
    #: The identifier used for the comparison.
    identifier: str = ""
    #: The comparison itself.
    comparison: Comparison = field(default_factory=Comparison)
    #: The device the comparison applies to, if applicable.
    device: Optional[ophyd.Device] = None
    #: The signal the comparison is to be run on.
    signal: Optional[ophyd.Signal] = None
    #: The name of the associated configuration.
    name: Optional[str] = None
    #: The last result of the comparison, if run.
    result: Optional[Result] = None

    def compare(self) -> Result:
        """
        Run the prepared comparison.

        Returns
        -------
        Result
            The result of the comparison.  This is also set in ``self.result``.
        """
        if self.signal is None:
            return Result(
                severity=Severity.internal_error,
                reason="Signal not set"
            )
        try:
            self.signal.wait_for_connection()
        except TimeoutError:
            return Result(
                severity=self.comparison.if_disconnected,
                reason=(
                    f"Unable to connect to {self.identifier!r} ({self.name}) "
                    f"for comparison {self.comparison}"
                ),
            )
        self.result = self.comparison.compare_signal(
            self.signal,
            identifier=self.identifier
        )
        return self.result

    @classmethod
    def from_device(
        cls,
        device: ophyd.Device,
        attr: str,
        comparison: Comparison,
        name: Optional[str] = None,
    ) -> PreparedComparison:
        """Create a PreparedComparison from a device and comparison."""
        full_attr = f"{device.name}.{attr}"
        logger.debug("Checking %s.%s with comparison %s", full_attr, comparison)
        signal = getattr(device, attr, None)
        if signal is None:
            raise AttributeError(
                f"Attribute {full_attr} does not exist on class "
                f"{type(device).__name__}"
            )

        return cls(
            name=name,
            device=device,
            identifier=full_attr,
            comparison=comparison,
            signal=signal,
        )

    @classmethod
    def from_pvname(
        cls,
        pvname: str,
        comparison: Comparison,
        name: Optional[str] = None,
        *,
        cache: Optional[Mapping[str, ophyd.Signal]] = None,
    ) -> PreparedComparison:
        """Create a PreparedComparison from a PV name and comparison."""
        if cache is None:
            cache = get_signal_cache()

        return cls(
            identifier=pvname,
            device=None,
            signal=cache[pvname],
            comparison=comparison,
            name=name,
        )

    @classmethod
    def _from_pv_config(
        cls,
        config: PVConfiguration,
        cache: Optional[Mapping[str, ophyd.Signal]] = None,
    ) -> Generator[Union[PreparedComparisonException, PreparedComparison], None, None]:
        """
        Create one or more PreparedComparison instances from a PVConfiguration.

        Parameters
        ----------
        config : PVConfiguration or DeviceConfiguration
            The configuration.

        cache : dict of str to type[Signal]
            The PV to signal cache.

        Yields
        ------
        item : PreparedComparisonException or PreparedComparison
            If an error occurs during preparation, a
            PreparedComparisonException will be yielded in place of the
            PreparedComparison.
        """
        for checklist_item in config.checklist:
            for comparison in checklist_item.comparisons:
                for pvname in checklist_item.ids:
                    try:
                        yield cls.from_pvname(
                            pvname=pvname,
                            comparison=comparison,
                            name=config.name or config.description,
                            cache=cache,
                        )
                    except Exception as ex:
                        yield PreparedComparisonException(
                            exception=ex,
                            comparison=comparison,
                            name=config.name or config.description,
                            identifier=pvname,
                        )

    @classmethod
    def _from_device_config(
        cls,
        device: ophyd.Device,
        config: DeviceConfiguration,
    ) -> Generator[Union[PreparedComparisonException, PreparedComparison], None, None]:
        """
        Create one or more PreparedComparison instances from a DeviceConfiguration.

        Parameters
        ----------
        config : PVConfiguration or DeviceConfiguration
            The configuration.

        client : happi.Client
            A happi Client instance.

        Yields
        ------
        item : PreparedComparisonException or PreparedComparison
            If an error occurs during preparation, a
            PreparedComparisonException will be yielded in place of the
            PreparedComparison.
        """
        for checklist_item in config.checklist:
            for comparison in checklist_item.comparisons:
                for attr in checklist_item.ids:
                    try:
                        yield cls.from_device(
                            device=device,
                            attr=attr,
                            comparison=comparison,
                            name=config.name or config.description,
                        )
                    except Exception as ex:
                        yield PreparedComparisonException(
                            exception=ex,
                            comparison=comparison,
                            name=config.name or config.description,
                            identifier=attr,
                        )

    @classmethod
    def from_config(
        cls,
        config: AnyConfiguration,
        *,
        client: Optional[happi.Client] = None,
        cache: Optional[Mapping[str, ophyd.Signal]] = None,
    ) -> Generator[Union[PreparedComparison, PreparedComparisonException], None, None]:
        """
        Create one or more PreparedComparison instances from a PVConfiguration
        or a DeviceConfiguration.

        If available, provide an instantiated happi Client and PV-to-Signal
        cache.  If unspecified, a configuration-derived happi Client will
        be instantiated and a global PV-to-Signal cache will be utilized.

        Parameters
        ----------
        config : PVConfiguration or DeviceConfiguration
            The configuration.

        client : happi.Client
            A happi Client instance.

        cache : dict of str to type[Signal]
            The PV to signal cache.

        Yields
        ------
        item : PreparedComparisonException or PreparedComparison
            If an error occurs during preparation, a
            PreparedComparisonException will be yielded in place of the
            PreparedComparison.
        """
        if isinstance(config, PVConfiguration):
            yield from cls._from_pv_config(config, cache=cache)
        elif isinstance(config, DeviceConfiguration):
            if client is None:
                client = happi.Client.from_config()
            for dev_name in config.devices:
                try:
                    device = util.get_happi_device_by_name(dev_name, client=client)
                except Exception as ex:
                    yield PreparedComparisonException(
                        exception=ex,
                        comparison=None,  # TODO
                        name=config.name or config.description,
                        identifier=dev_name,
                    )
                else:
                    yield from cls._from_device_config(
                        config=config,
                        device=device,
                    )


def check_device(
    device: ophyd.Device, checklist: Sequence[IdentifierAndComparison]
) -> Tuple[Severity, List[Result]]:
    """
    Check a given device using the list of comparisons.

    Parameters
    ----------
    device : ophyd.Device
        The device to check.

    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple attributes may
        share the same checks.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []
    for checklist_item in checklist:
        for comparison in checklist_item.comparisons:
            for attr in checklist_item.ids:
                full_attr = f"{device.name}.{attr}"
                logger.debug("Checking %s.%s with comparison %s", full_attr, comparison)
                try:
                    prepared = PreparedComparison.from_device(
                        device=device, attr=attr, comparison=comparison
                    )
                except AttributeError:
                    result = Result(
                        severity=Severity.internal_error,
                        reason=(
                            f"Attribute {full_attr} does not exist on class "
                            f"{type(device).__name__}"
                        ),
                    )
                else:
                    result = prepared.compare()

                if result.severity > overall:
                    overall = result.severity
                results.append(result)

    return overall, results


def check_pvs(
    checklist: Sequence[IdentifierAndComparison],
    *,
    cache: Optional[Mapping[str, ophyd.Signal]] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Check a PVConfiguration.

    Parameters
    ----------
    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple PVs may share the
        same checks.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []
    cache = cache or get_signal_cache()

    def get_comparison_and_pvname():
        for checklist_item in checklist:
            for comparison in checklist_item.comparisons:
                for pvname in checklist_item.ids:
                    yield comparison, pvname

    for comparison, pvname in get_comparison_and_pvname():
        # Pre-fill the cache with PVs, connecting in the background
        _ = cache[pvname]

    for comparison, pvname in get_comparison_and_pvname():
        logger.debug("Checking %s.%s with comparison %s", pvname, comparison)

        prepared = PreparedComparison.from_pvname(
            pvname=pvname, comparison=comparison, cache=cache
        )
        result = prepared.compare()

        if result.severity > overall:
            overall = result.severity
        results.append(result)

    return overall, results
