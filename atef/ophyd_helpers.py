"""
ophyd helpers; may move to pcdsutils at some point.
"""

import asyncio
import contextlib
import logging
import time
from typing import Callable, List, Optional

import ophyd
from ophyd.ophydobj import OphydObject

from .type_hints import Number, PrimitiveType

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def subscription_context(
    *objects: OphydObject,
    callback: Callable,
    event_type: Optional[str] = None,
    run: bool = True
):
    """
    [Context manager] Subscribe to a specific event from all objects

    Unsubscribes all signals before exiting

    Parameters
    ----------
    *objects : ophyd.OphydObj
        Ophyd objects (signals) to monitor
    callback : callable
        Callback to run, with same signature as that of
        :meth:`ophyd.OphydObj.subscribe`.
    event_type : str, optional
        The event type to subscribe to
    run : bool, optional
        Run the previously cached subscription immediately
    """
    obj_to_cid = {}
    try:
        for obj in objects:
            try:
                obj_to_cid[obj] = obj.subscribe(
                    callback, event_type=event_type, run=run
                )
            except Exception:
                logger.exception("Failed to subscribe to object %s", obj.name)
        yield dict(obj_to_cid)
    finally:
        for obj, cid in obj_to_cid.items():
            try:
                obj.unsubscribe(cid)
            except KeyError:
                # It's possible that when the object is being torn down, or
                # destroyed that this has already been done.
                ...


@contextlib.contextmanager
def no_device_lazy_load():
    '''
    Context manager which disables the ophyd.device.Device
    `lazy_wait_for_connection` behavior and later restore its value.
    '''
    old_val = ophyd.Device.lazy_wait_for_connection
    try:
        ophyd.Device.lazy_wait_for_connection = False
        yield
    finally:
        ophyd.Device.lazy_wait_for_connection = old_val


FilterBy = Callable[[ophyd.device.ComponentWalk], bool]


def get_all_signals_from_device(
    device: ophyd.Device,
    include_lazy: bool = False,
    filter_by: Optional[FilterBy] = None,
):
    """
    Get all signals in a given device.

    Parameters
    ----------
    device : ophyd.Device
        ophyd Device to monitor
    include_lazy : bool, optional
        Include lazy signals as well
    filter_by : callable, optional
        Filter signals, with signature ``callable(ophyd.Device.ComponentWalk)``
    """
    def default_filter_by(*_) -> bool:
        return True

    filter_by = filter_by or default_filter_by

    def _get_signals():
        return [
            walk.item
            for walk in device.walk_signals(include_lazy=include_lazy)
            if filter_by(walk)
        ]

    if not include_lazy:
        return _get_signals()

    with no_device_lazy_load():
        return _get_signals()


class SubscribeCallback(Protocol):
    def __call__(self, **kwargs) -> None:
        ...


@contextlib.contextmanager
def subscription_context_device(
    device: ophyd.Device,
    callback: SubscribeCallback,
    event_type: Optional[str] = None,
    run: bool = True,
    *,
    include_lazy: bool = False,
    filter_by: Optional[FilterBy] = None
):
    """
    [Context manager] Subscribe to ``event_type`` from signals in ``device``.

    Unsubscribes all signals before exiting

    Parameters
    ----------
    device : ophyd.Device
        ophyd Device to monitor
    callback : callable
        Callback to run, with same signature as that of
        :meth:`ophyd.OphydObj.subscribe`
    event_type : str, optional
        The event type to subscribe to
    run : bool, optional
        Run the previously cached subscription immediately
    include_lazy : bool, optional
        Include lazy signals as well
    filter_by : callable, optional
        Filter signals, with signature ``callable(ophyd.Device.ComponentWalk)``
    """
    signals = get_all_signals_from_device(device, include_lazy=include_lazy)
    with subscription_context(
        *signals, callback=callback, event_type=event_type, run=run
    ) as obj_to_cid:
        yield obj_to_cid


@contextlib.contextmanager
def _acquire(signal: ophyd.Signal):
    """
    [Context manager] Subscribe to signal, acquire data until the block exits.

    Parameters
    ----------
    signal : ophyd.Signal
        Ophyd object to monitor.

    Returns
    -------
    data : List[PrimitiveType]
        The data acquired.  Guaranteed to have at least one item.
    """
    signal.wait_for_connection()
    data = []

    start_value = signal.get()

    def acquire(value, **_):
        data.append(value)

    with subscription_context(signal, callback=acquire):
        yield data

    if not data:
        data.extend([start_value, signal.get()])


def acquire_blocking(signal: ophyd.Signal, duration: Number) -> List[PrimitiveType]:
    """
    Subscribe to signal, acquire data for ``duration`` seconds.

    Parameters
    ----------
    signal : ophyd.Signal
        Ophyd object to monitor.

    duration : number
        Seconds to acquire for.

    Returns
    -------
    data : List[PrimitiveType]
        The data acquired.  Guaranteed to have at least one item.
    """
    with _acquire(signal) as data:
        time.sleep(duration)
    return data


async def acquire_async(signal: ophyd.Signal, duration: Number) -> List[PrimitiveType]:
    """
    Subscribe to signal, acquire data for ``duration`` seconds.

    Parameters
    ----------
    signal : ophyd.Signal
        Ophyd object to monitor.

    duration : number
        Seconds to acquire for.

    Returns
    -------
    data : List[PrimitiveType]
        The data acquired.  Guaranteed to have at least one item.
    """
    with _acquire(signal) as data:
        await asyncio.sleep(duration)
    return data
