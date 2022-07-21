from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Generator, List, Optional, Sequence, Tuple, Union

import apischema
import happi
import ophyd
import yaml
from ophyd.signal import ConnectionTimeoutError

from . import serialization, tools, util
from .cache import DataCache
from .check import Comparison, Result
from .enums import Severity
from .exceptions import PreparedComparisonException
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
    """
    A configuration that is built to check one or more devices.

    Identifiers are by default assumed to be attribute (component) names of the
    devices.  Identifiers may refer to components on the device
    (``"component"`` would mean to access each device's ``.component``) or may
    refer to any level of sub-device components (``"sub_device.component"``
    would mean to access each device's ``.sub_device`` and that sub-device's
    ``.a`` component).
    """
    #: Happi device names which give meaning to self.checklist[].ids.
    devices: List[str] = field(default_factory=list)


@dataclass
class PVConfiguration(Configuration):
    """
    A configuration that is built to check live EPICS PVs.

    Identifiers are by default assumed to be PV names.
    """
    ...


@dataclass
class ToolConfiguration(Configuration):
    """
    A configuration unrelated to PVs or Devices which verifies status via some
    tool.

    Comparisons can optionally be run on the tool's results.
    """
    tool: tools.Tool = field(default_factory=tools.Ping)


AnyConfiguration = Union[
    PVConfiguration,
    DeviceConfiguration,
    ToolConfiguration,
]
PathItem = Union[
    AnyConfiguration,
    IdentifierAndComparison,
    Comparison,
    str,
]


@dataclass
class ConfigurationFile:
    """
    A configuration file comprised of a number of devices/PV configurations.
    """

    #: configs: PVConfiguration, DeviceConfiguration, or ToolConfiguration.
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
    #: The data cache to use for the preparation step.
    cache: DataCache
    #: The identifier used for the comparison.
    identifier: str = ""
    #: The comparison itself.
    comparison: Comparison = field(default_factory=Comparison)
    #: The name of the associated configuration.
    name: Optional[str] = None
    #: The hierarhical path that led to this prepared comparison.
    path: List[PathItem] = field(default_factory=list)
    #: The last result of the comparison, if run.
    result: Optional[Result] = None

    async def compare(self) -> Result:
        """
        Run the comparison.

        To be immplemented in subclass.
        """
        raise NotImplementedError()

    @classmethod
    def from_config(
        cls,
        config: AnyConfiguration,
        *,
        client: Optional[happi.Client] = None,
        cache: Optional[DataCache] = None,
    ) -> Generator[Union[PreparedComparison, PreparedComparisonException], None, None]:
        """
        Create one or more PreparedComparison instances from a PVConfiguration
        or a DeviceConfiguration.

        If available, provide an instantiated happi Client and a data
        cache.  If unspecified, a configuration-derived happi Client will
        be instantiated and a global data cache will be utilized.

        It is recommended - but not required - to manage a data cache on a
        per-configuration basis.  Managing the global cache is up to the user.

        Parameters
        ----------
        config : PVConfiguration or DeviceConfiguration
            The configuration.
        client : happi.Client, optional
            A happi Client instance.
        cache : DataCache, optional
            The data cache to use for this and other similar comparisons.

        Yields
        ------
        item : PreparedComparisonException or PreparedComparison
            If an error occurs during preparation, a
            PreparedComparisonException will be yielded in place of the
            PreparedComparison.
        """
        if cache is None:
            cache = DataCache()

        if isinstance(config, PVConfiguration):
            yield from PreparedSignalComparison._from_pv_config(config, cache=cache)
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
                        path=[
                            config,
                            dev_name,
                        ],
                    )
                else:
                    yield from PreparedSignalComparison._from_device_config(
                        config=config,
                        device=device,
                        cache=cache,
                    )
        elif isinstance(config, ToolConfiguration):
            yield from PreparedToolComparison._from_tool_config(config)
        else:
            raise NotImplementedError(f"Configuration type unsupported: {type(config)}")


@dataclass
class PreparedSignalComparison(PreparedComparison):
    """
    A unified representation of comparisons for device signals and standalone
    PVs.

    Each PreparedSignalComparison has a single leaf in the configuration tree,
    comprised of:
    * A configuration
    * The signal specification.  This is comprised of the configuration and
        "IdentifierAndComparison"
        - DeviceConfiguration: Device and attribute (the "identifier")
        - PVConfiguration: PV name (the "identifier")
    * A comparison to run
        - Including data reduction settings
    """
    #: The device the comparison applies to, if applicable.
    device: Optional[ophyd.Device] = None
    #: The signal the comparison is to be run on.
    signal: Optional[ophyd.Signal] = None
    #: The value from the signal the comparison is to be run on.
    data: Optional[Any] = None

    async def get_data_async(self) -> Any:
        """
        Get the provided signal's data from the cache according to the
        reduction configuration.

        Caller must prepare the cache prior to calling this method.

        Returns
        -------
        data : Any
            The acquired data.

        Raises
        ------
        TimeoutError
            If unable to connect or retrieve data from the signal.
        """
        signal = self.signal
        if signal is None:
            raise ValueError("Signal instance unset")

        data = await self.cache.get_signal_data(
            signal,
            reduce_period=self.comparison.reduce_period,
            reduce_method=self.comparison.reduce_method,
            string=self.comparison.string or False,
        )

        self.data = data
        return data

    async def compare(self) -> Result:
        """
        Run the prepared comparison.

        Returns
        -------
        Result
            The result of the comparison.  This is also set in ``self.result``.
        """
        try:
            self.data = await self.get_data_async()
        except (TimeoutError, asyncio.TimeoutError, ConnectionTimeoutError):
            result = Result(
                severity=self.comparison.if_disconnected,
                reason=f"Signal not able to connect or read: {self.identifier}"
            )
        except Exception as ex:
            result = Result(
                severity=Severity.internal_error,
                reason=(
                    f"Getting data for signal {self.identifier!r} comparison "
                    f"{self.comparison} raised {ex.__class__.__name__}: {ex}"
                ),
            )

        try:
            result = self._compare()
        except Exception as ex:
            result = Result(
                severity=Severity.internal_error,
                reason=(
                    f"Failed to run {self.identifier!r} comparison "
                    f"{self.comparison} raised {ex.__class__.__name__}: {ex} "
                    f"with value {self.data}"
                ),
            )

        self.result = result
        return result

    def _compare(self) -> Result:
        """
        Run the comparison with the already-acquired data in ``self.data``.
        """
        if self.signal is None:
            return Result(
                severity=Severity.internal_error,
                reason="Signal not set"
            )

        data = self.data
        if data is None:
            # 'None' is likely incompatible with our comparisons and should
            # be raised for separately
            return Result(
                severity=self.comparison.if_disconnected,
                reason=(
                    f"No data available for signal {self.identifier!r} in "
                    f"comparison {self.comparison}"
                ),
            )

        return self.comparison.compare(
            data,
            identifier=self.identifier
        )

    @classmethod
    def from_device(
        cls,
        device: ophyd.Device,
        attr: str,
        comparison: Comparison,
        name: Optional[str] = None,
        path: Optional[List[PathItem]] = None,
        cache: Optional[DataCache] = None,
    ) -> PreparedSignalComparison:
        """Create a PreparedComparison from a device and comparison."""
        full_attr = f"{device.name}.{attr}"
        logger.debug("Checking %s.%s with comparison %s", full_attr, comparison)
        if cache is None:
            cache = DataCache()

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
            path=path or [],
            cache=cache,
        )

    @classmethod
    def from_pvname(
        cls,
        pvname: str,
        comparison: Comparison,
        name: Optional[str] = None,
        path: Optional[List[PathItem]] = None,
        cache: Optional[DataCache] = None,
    ) -> PreparedSignalComparison:
        """Create a PreparedComparison from a PV name and comparison."""
        if cache is None:
            cache = DataCache()

        return cls(
            identifier=pvname,
            device=None,
            signal=cache.signals[pvname],
            comparison=comparison,
            name=name,
            path=path or [],
            cache=cache,
        )

    @classmethod
    def _from_pv_config(
        cls,
        config: PVConfiguration,
        cache: DataCache,
    ) -> Generator[
        Union[PreparedComparisonException, PreparedSignalComparison], None, None
    ]:
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
                    path = [
                        config,
                        checklist_item,
                        comparison,
                        pvname,
                    ]
                    try:
                        yield cls.from_pvname(
                            pvname=pvname,
                            path=path,
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
                            path=path,
                        )

    @classmethod
    def _from_device_config(
        cls,
        device: ophyd.Device,
        config: DeviceConfiguration,
        cache: DataCache,
    ) -> Generator[
        Union[PreparedComparisonException, PreparedSignalComparison], None, None
    ]:
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
                    path = [
                        config,
                        checklist_item,
                        comparison,
                        attr,
                    ]
                    try:
                        yield cls.from_device(
                            device=device,
                            attr=attr,
                            comparison=comparison,
                            name=config.name or config.description,
                            path=path,
                            cache=cache,
                        )
                    except Exception as ex:
                        yield PreparedComparisonException(
                            exception=ex,
                            comparison=comparison,
                            name=config.name or config.description,
                            identifier=attr,
                            path=path,
                        )


@dataclass
class PreparedToolComparison(PreparedComparison):
    """
    A unified representation of comparisons for device signals and standalone PVs.

    Each PreparedToolComparison has a single leaf in the configuration tree,
    comprised of:
    * A configuration
    * The tool configuration (i.e., a :class:`tools.Tool` instance)
    * Identifiers to compare are dependent on the tool type
    * A comparison to run
        - For example, a :class:`tools.Ping` has keys described in
          :class:`tools.PingResult`.
    """
    #: The device the comparison applies to, if applicable.
    tool: tools.Tool = field(default_factory=lambda: tools.Ping(hosts=[]))

    async def compare(self) -> Result:
        """
        Run the prepared comparison.

        Returns
        -------
        Result
            The result of the comparison.  This is also set in ``self.result``.
        """
        try:
            result = await self.tool.run()
        except (asyncio.TimeoutError, TimeoutError):
            return Result(
                severity=self.comparison.if_disconnected,
                reason=(
                    f"Tool {self.tool} timed out {self.identifier!r} ({self.name}) "
                    f"for comparison {self.comparison}"
                ),
            )
        except Exception as ex:
            logger.debug("Internal error with tool %s", self, exc_info=True)
            # TODO: include some traceback information for debugging?
            # Could 'Result' have optional verbose error information?
            return Result(
                severity=Severity.internal_error,
                reason=(
                    f"Tool {self.tool} failed to run {self.identifier!r} ({self.name}) "
                    f"for comparison {self.comparison}: {ex.__class__.__name__} {ex}"
                ),
            )

        try:
            value = tools.get_result_value_by_key(result, self.identifier)
        except KeyError as ex:
            return Result(
                severity=self.comparison.severity_on_failure,
                reason=(
                    f"Provided key is invalid for tool result {self.tool} "
                    f"{self.identifier!r} ({self.name}): {ex} "
                    f"(in comparison {self.comparison})"
                ),
            )

        self.result = self.comparison.compare(
            value,
            identifier=self.identifier
        )
        return self.result

    @classmethod
    def from_tool(
        cls,
        tool: tools.Tool,
        result_key: str,
        comparison: Comparison,
        name: Optional[str] = None,
        path: Optional[List[PathItem]] = None,
        cache: Optional[DataCache] = None,
    ) -> PreparedToolComparison:
        """
        Prepare a tool-based comparison for execution.

        Parameters
        ----------
        tool : Tool
            The tool to run.
        result_key : str
            The key from the result dictionary to check after running the tool.
        comparison : Comparison
            The comparison to perform on the tool's results (looking at the
            specific result_key).
        name : Optional[str], optional
            The name of the comparison.
        path : Optional[List[PathItem]], optional
            The path that led us to this single comparison.
        cache : DataCache, optional
            The data cache to use for this and other similar comparisons.

        Returns
        -------
        PreparedToolComparison
        """
        if cache is None:
            cache = DataCache()
        tool.check_result_key(result_key)
        return cls(
            tool=tool,
            comparison=comparison,
            name=name,
            identifier=result_key,
            path=path or [],
            cache=cache,
        )

    @classmethod
    def _from_tool_config(
        cls,
        config: ToolConfiguration,
    ) -> Generator[Union[PreparedComparisonException, PreparedComparison], None, None]:
        """
        Create one or more PreparedComparison instances from a
        ToolConfiguration.

        Parameters
        ----------
        config : ToolConfiguration
            The configuration.

        Yields
        ------
        item : PreparedComparisonException or PreparedComparison
            If an error occurs during preparation, a
            PreparedComparisonException will be yielded in place of the
            PreparedComparison.
        """
        for checklist_item in config.checklist:
            for comparison in checklist_item.comparisons:
                for result_key in checklist_item.ids:
                    path = [
                        config,
                        checklist_item,
                        comparison,
                        result_key,
                    ]
                    try:
                        yield cls.from_tool(
                            tool=config.tool,
                            result_key=result_key,
                            comparison=comparison,
                            name=config.name or config.description,
                            path=path,
                        )
                    except Exception as ex:
                        yield PreparedComparisonException(
                            exception=ex,
                            comparison=comparison,
                            name=config.name or config.description,
                            identifier=result_key,
                            path=path,
                        )


async def check_device(
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
                    prepared = PreparedSignalComparison.from_device(
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
                    result = await prepared.compare()

                if result.severity > overall:
                    overall = result.severity
                results.append(result)

    return overall, results


async def check_pvs(
    checklist: Sequence[IdentifierAndComparison],
    cache: Optional[DataCache] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Check a PVConfiguration.

    Parameters
    ----------
    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple PVs may share the
        same checks.
    cache : DataCache, optional
        The data cache to use for this and other similar comparisons.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []
    if cache is None:
        cache = DataCache()

    def get_comparison_and_pvname():
        for checklist_item in checklist:
            for comparison in checklist_item.comparisons:
                for pvname in checklist_item.ids:
                    yield comparison, pvname

    prepared_comparisons = [
        PreparedSignalComparison.from_pvname(
            pvname=pvname, comparison=comparison, cache=cache
        )
        for comparison, pvname in get_comparison_and_pvname()
    ]

    cache_fill_tasks = []
    for prepared in prepared_comparisons:
        # Pre-fill the cache with PVs, connecting in the background
        cache_fill_tasks.append(
            asyncio.create_task(
                prepared.get_data_async()
            )
        )

    for prepared in prepared_comparisons:
        logger.debug(
            "Checking %r with comparison %s", prepared.identifier, prepared.comparison
        )

        result = await prepared.compare()

        if result.severity > overall:
            overall = result.severity
        results.append(result)

    return overall, results


async def check_tool(
    tool: tools.Tool,
    checklist: Sequence[IdentifierAndComparison],
    cache: Optional[DataCache] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Check a PVConfiguration.

    Parameters
    ----------
    tool : Tool
        The tool instance defining which tool to run and with what arguments.
    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple PVs may share the
        same checks.
    cache : DataCache, optional
        The data cache to use for this tool and other similar comparisons.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []

    if cache is None:
        cache = DataCache()

    def get_comparison_and_key():
        for checklist_item in checklist:
            for comparison in checklist_item.comparisons:
                for key in checklist_item.ids:
                    yield comparison, key

    for comparison, key in get_comparison_and_key():
        logger.debug("Checking %r with comparison %s", key, comparison)

        prepared = PreparedToolComparison.from_tool(
            tool, result_key=key, comparison=comparison, cache=cache,
        )
        result = await prepared.compare()

        if result.severity > overall:
            overall = result.severity
        results.append(result)

    return overall, results
