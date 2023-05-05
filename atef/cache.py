from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import logging
import typing
from dataclasses import dataclass, field
from typing import (Any, Dict, Hashable, Iterable, Mapping, Optional, Type,
                    TypeVar, cast)

import ophyd

from .reduce import ReduceMethod, get_data_for_signal_async
from .type_hints import Number

if typing.TYPE_CHECKING:
    from . import tools

_CacheSignalType = TypeVar("_CacheSignalType", bound=ophyd.Signal)


logger = logging.getLogger(__name__)
_signal_cache: Optional[_SignalCache[ophyd.EpicsSignal]] = None


def get_signal_cache() -> _SignalCache[ophyd.EpicsSignal]:
    """Get the global EpicsSignal cache."""
    global _signal_cache
    if _signal_cache is None:
        _signal_cache = _SignalCache[ophyd.EpicsSignal](ophyd.EpicsSignal)
    return _signal_cache


@dataclass
class _SignalCache(Mapping[str, _CacheSignalType]):
    signal_type_cls: Type[ophyd.EpicsSignal]
    pv_to_signal: Dict[str, _CacheSignalType] = field(default_factory=dict)

    def __getitem__(self, pv: str) -> _CacheSignalType:
        """Get a PV from the cache."""
        if pv not in self.pv_to_signal:
            signal = cast(
                _CacheSignalType,
                self.signal_type_cls(pv, name=pv)
            )
            self.pv_to_signal[pv] = signal
            return signal

        return self.pv_to_signal[pv]

    def __iter__(self):
        yield from self.pv_to_signal

    def __len__(self):
        return len(self.pv_to_signal)

    def clear(self) -> None:
        """Clear the signal cache."""
        for sig in self.pv_to_signal.values():
            try:
                sig.destroy()
            except Exception:
                logger.debug("Destroy failed for signal %s", sig.name)
        self.pv_to_signal.clear()


@dataclass(frozen=True, eq=True)
class DataKey:
    period: Optional[Number] = None
    method: ReduceMethod = ReduceMethod.average
    string: bool = False


@dataclass(frozen=True, eq=True)
class ToolKey:
    tool_cls: Type[tools.Tool]
    settings: Optional[Hashable]

    @classmethod
    def from_tool(cls, tool: tools.Tool) -> ToolKey:
        settings = cast(Hashable, _freeze(dataclasses.asdict(tool)))
        return cls(
            tool_cls=type(tool),
            settings=settings,
        )


def _freeze(data):
    """
    Freeze ``data`` such that it can be used as a hashable key.

    Parameters
    ----------
    data : Any
        The data to be frozen.

    Returns
    -------
    Any
        Hopefully hashable version of ``data``.
    """
    if isinstance(data, str):
        return data
    if isinstance(data, Mapping):
        return frozenset(
            (_freeze(key), _freeze(value))
            for key, value in data.items()
        )
    if isinstance(data, Iterable):
        return tuple(_freeze(part) for part in data)
    return data


@dataclass
class DataCache:
    signal_data: Dict[ophyd.Signal, Dict[DataKey, Any]] = field(default_factory=dict)
    signals: _SignalCache[ophyd.EpicsSignal] = field(
        default_factory=get_signal_cache
    )
    tool_data: Dict[ToolKey, Any] = field(
        default_factory=dict
    )

    def clear(self) -> None:
        """Clear the data cache."""
        for data in list(self.signal_data.values()):
            data.clear()
        self.tool_data.clear()

    async def get_pv_data(
        self,
        pv: str,
        reduce_period: Optional[Number] = None,
        reduce_method: ReduceMethod = ReduceMethod.average,
        string: bool = False,
        executor: Optional[concurrent.futures.Executor] = None,
    ) -> Optional[Any]:
        """
        Get EPICS PV data with the provided data reduction settings.

        Utilizes cached data if already available.  Multiple calls
        with the same cache key will be batched.

        Parameters
        ----------
        pv : str
            The PV name.
        reduce_period : float, optional
            Period over which the comparison will occur, where multiple samples may
            be acquired prior to a result being available.
        reduce_method : ReduceMethod, optional
            Reduce collected samples by this reduce method.  Ignored if
            reduce_period unset.
        string : bool, optional
            If applicable, request and compare string values rather than the
            default specified.
        executor : concurrent.futures.Executor, optional
            The executor to run the synchronous call in.  Defaults to
            the loop-defined default executor.

        Returns
        -------
        Any
            The acquired data.
        """
        return await self.get_signal_data(
            self.signals[pv],
            reduce_period=reduce_period,
            reduce_method=reduce_method,
            string=string,
            executor=executor,
        )

    async def get_signal_data(
        self,
        signal: ophyd.Signal,
        reduce_period: Optional[Number] = None,
        reduce_method: ReduceMethod = ReduceMethod.average,
        string: bool = False,
        executor: Optional[concurrent.futures.Executor] = None,
    ) -> Optional[Any]:
        """
        Get signal data with the provided data reduction settings.

        Utilizes cached data if already available.  Multiple calls
        with the same cache key will be batched.

        Parameters
        ----------
        signal : ophyd.Signal
            The signal to retrieve data from.
        reduce_period : float, optional
            Period over which the comparison will occur, where multiple samples may
            be acquired prior to a result being available.
        reduce_method : ReduceMethod, optional
            Reduce collected samples by this reduce method.  Ignored if
            reduce_period unset.
        string : bool, optional
            If applicable, request and compare string values rather than the
            default specified.
        executor : concurrent.futures.Executor, optional
            The executor to run the synchronous call in.  Defaults to
            the loop-defined default executor.

        Returns
        -------
        Any
            The acquired data.
        """
        key = DataKey(period=reduce_period, method=reduce_method, string=string)
        signal_data = self.signal_data.setdefault(signal, {})
        try:
            data = signal_data[key]
        except KeyError:
            data = asyncio.create_task(
                self._update_signal_data_by_key(signal, key, executor=executor)
            )
            signal_data[key] = data

        if isinstance(data, asyncio.Future):
            return await data
        return data

    async def _update_signal_data_by_key(
        self,
        signal: ophyd.Signal,
        key: DataKey,
        executor: Optional[concurrent.futures.Executor] = None,
    ) -> Any:
        """
        Update the signal data cache given the signal and the reduction key.

        Parameters
        ----------
        signal : ophyd.Signal
            The signal to update.
        key : DataKey
            The data key corresponding to the acquisition settings.
        executor : concurrent.futures.Executor, optional
            The executor to run the synchronous call in.  Defaults to
            the loop-defined default executor.

        Returns
        -------
        Any
            The acquired data.
        """
        signal_data = self.signal_data[signal]
        try:
            acquired = await asyncio.shield(
                get_data_for_signal_async(
                    signal,
                    reduce_period=key.period,
                    reduce_method=key.method,
                    string=key.string,
                    executor=executor,
                )
            )
        except TimeoutError:
            acquired = None

        signal_data[key] = acquired
        return acquired

    async def get_tool_data(
        self,
        tool: tools.Tool,
    ) -> Optional[Any]:
        """
        Get tool data.

        Utilizes cached data if already available.  Multiple calls
        with the same cache key will be batched.

        Parameters
        ----------
        tool : tools.Tool
            The tool to run.

        Returns
        -------
        Any
            The acquired data.
        """
        try:
            key = ToolKey.from_tool(tool)
        except Exception:
            # Unhashable for some reason: we need to fix `_freeze`. Re-run
            # the tool on demand and don't cache its results.
            logger.warning(
                "Internal issue with tool: %s.  Caching mechanism "
                "unavailable so performance may suffer.",
                tool,
            )
            logger.debug("Tool cache key failure: %s", tool, exc_info=True)
            return await tool.run()

        try:
            data = self.tool_data[key]
        except KeyError:
            data = asyncio.create_task(self._update_tool_by_key(tool, key))
            self.tool_data[key] = data

        if isinstance(data, asyncio.Future):
            return await data

        return data

    async def _update_tool_by_key(
        self,
        tool: tools.Tool,
        key: ToolKey,
        executor: Optional[concurrent.futures.Executor] = None,
    ) -> Any:
        """
        Update the tool cache given the signal and the reduction key.

        Parameters
        ----------
        tool : tools.Tool
            The tool to run.
        key : ToolKey
            The hashable key according to the tool's configuration.
        executor : concurrent.futures.Executor, optional
            The executor to run the synchronous call in.  Defaults to
            the loop-defined default executor.

        Returns
        -------
        Any
            The acquired data.
        """
        try:
            acquired = await tool.run()
        except Exception:
            acquired = None

        self.tool_data[key] = acquired
        return acquired
