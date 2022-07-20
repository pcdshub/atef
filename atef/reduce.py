from __future__ import annotations

import asyncio
import concurrent.futures
import enum
from typing import Any, Optional, Protocol, Sequence

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


def get_data_for_signal(
    signal: ophyd.Signal,
    reduce_period: Optional[Number] = None,
    reduce_method: ReduceMethod = ReduceMethod.average,
    string: bool = False,
) -> Any:
    """
    Get data for the given signal, according to the string and data reduction
    settings.

    Parameters
    ----------
    signal : ophyd.Signal
        The signal.
    reduce_period : float, optional
        Period over which the comparison will occur, where multiple samples may
        be acquired prior to a result being available.
    reduce_method : ReduceMethod, optional
        Reduce collected samples by this reduce method.  Ignored if
        reduce_period unset.
    string : bool, optional
        If applicable, request and compare string values rather than the
        default specified.

    Returns
    -------
    Any
        The acquired data.

    Raises
    ------
    TimeoutError
        If the get operation times out.
    """
    if reduce_period is not None and reduce_period > 0:
        return reduce_method.subscribe_and_reduce(
            signal, reduce_period
        )

    if string:
        return signal.get(as_string=True)

    return signal.get()


async def get_data_for_signal_async(
    signal: ophyd.Signal,
    reduce_period: Optional[Number] = None,
    reduce_method: ReduceMethod = ReduceMethod.average,
    string: bool = False,
    *,
    executor: Optional[concurrent.futures.Executor] = None
) -> Any:
    """
    Get data for the given signal, according to the string and data
    reduction settings.

    Parameters
    ----------
    signal : ophyd.Signal
        The signal.
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

    Raises
    ------
    TimeoutError
        If the get operation times out.
    """
    if reduce_period is not None and reduce_period > 0:
        return await reduce_method.subscribe_and_reduce_async(
            signal, reduce_period
        )

    def inner_sync_get():
        if string:
            return signal.get(as_string=True)

        return signal.get()

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, inner_sync_get)
