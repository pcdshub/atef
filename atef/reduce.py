from __future__ import annotations

import enum
from typing import Protocol, Sequence

import numpy as np
import ophyd

from .ophyd_helpers import acquire_async, acquire_blocking
from .type_hints import Number, PrimitiveType


class _ReduceMethodType(Protocol):
    def __call__(self, data: Sequence[PrimitiveType], *args, **kwargs) -> PrimitiveType:
        ...


class ReduceMethod(str, enum.Enum):
    average = "average"
    median = "median"
    sum = "sum"
    min = "min"
    max = "max"
    std = "std"

    @property
    def method(self) -> _ReduceMethodType:
        """Callable reduction method."""
        return {
            ReduceMethod.average: np.average,
            ReduceMethod.median: np.median,
            ReduceMethod.sum: np.sum,
            ReduceMethod.min: np.min,
            ReduceMethod.max: np.max,
            ReduceMethod.std: np.std,
        }[self]

    def reduce_values(self, values: Sequence[PrimitiveType]) -> PrimitiveType:
        """
        Reduce the given values according to the configured method.

        For example, if ``method`` is `ReduceMethod.average`, use `np.average`
        to reduce the provided values into a scalar result.
        """
        return self.method(np.asarray(values))

    def subscribe_and_reduce(
        self, signal: ophyd.Signal, duration: Number
    ) -> PrimitiveType:
        """
        Subscribe to the signal, acquire data over ``duration`` and reduce
        according to the reduce method.
        """
        data = acquire_blocking(signal, duration)
        return self.reduce_values(data)

    async def subscribe_and_reduce_async(
        self, signal: ophyd.Signal, duration: Number
    ) -> PrimitiveType:
        """
        Subscribe to the signal, acquire data over ``duration`` and reduce
        according to the reduce method.
        """
        data = await acquire_async(signal, duration)
        return self.reduce_values(data)
