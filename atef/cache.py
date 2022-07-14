from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Mapping, TypeVar

import ophyd

_CacheSignalType = TypeVar("_CacheSignalType")


logger = logging.getLogger(__name__)


@dataclass
class _SignalCache(Mapping[str, _CacheSignalType]):
    signal_type_cls: _CacheSignalType
    pv_to_signal: Dict[str, _CacheSignalType] = field(default_factory=dict)

    def __getitem__(self, pv: str) -> _CacheSignalType:
        """Get a PV from the cache."""
        if pv not in self.pv_to_signal:
            self.pv_to_signal[pv] = self.signal_type_cls(pv, name=pv)

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


_signal_cache = None


def get_signal_cache() -> _SignalCache[ophyd.EpicsSignalRO]:
    """Get the global EpicsSignal cache."""
    global _signal_cache
    if _signal_cache is None:
        _signal_cache = _SignalCache(ophyd.EpicsSignalRO)
    return _signal_cache
