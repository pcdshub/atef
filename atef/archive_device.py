from __future__ import annotations

import collections
import copy
import dataclasses
import datetime
import inspect
import logging
import math
import threading
import time
from types import SimpleNamespace
from typing import (Any, Callable, ClassVar, Deque, Dict, Generator, Iterable,
                    List, Optional, Tuple, Type)

import archapp
import ophyd
from ophyd import Component as Cpt
from ophyd import Device
from ophyd import DynamicDeviceComponent as DDCpt
from ophyd._dispatch import wrap_callback as _wrap_callback
from ophyd.ophydobj import OphydObject
from ophyd.signal import EpicsSignalBase

from .pyepics_compat import (PyepicsConnectionCallback, PyepicsPutCallback,
                             PyepicsPvCompatibility)

logger = logging.getLogger(__name__)


PER_PV_CACHE = 100


def get_dispatcher():
    """Get the ophyd-configured dispatcher (pyepics_shim, etc.)."""
    return ophyd.get_cl().get_dispatcher()


def wrap_callback(event_type: str, callback: Callable):
    """Wrap a callback to have it run in a specific ophyd dispatcher thread."""
    return _wrap_callback(get_dispatcher(), event_type, callback)


@dataclasses.dataclass(frozen=True)
class ArchivedValue:
    pvname: str
    value: Optional[Any]
    timestamp: datetime.datetime
    status: int
    severity: int
    appliance: Optional[archapp.EpicsArchive] = dataclasses.field(
        default=None, repr=False
    )
    enum_strs: Optional[Tuple[str, ...]] = None

    @classmethod
    def from_archapp(
        cls,
        pvname: str,
        appliance: archapp.EpicsArchive,
        /,
        val: Any = None,
        nanos: int = 0,
        secs: int = 0,
        **data,
    ) -> ArchivedValue:
        timestamp = datetime.datetime.fromtimestamp(secs + 1e-9 * nanos)
        kwargs = {
            kw: data[kw]
            for kw in ("status", "severity", "enum_strs")
            if kw in data
        }
        # TODO: archiver appliance API refactor will add more kwargs here
        # but they are undefined as of yet
        return cls(
            pvname=pvname,
            appliance=appliance,
            value=val,
            timestamp=timestamp,
            **kwargs
        )

    def to_archapp(self) -> Dict[str, Any]:
        """
        Testing helper to convert an ArchivedValue back into what the appliance
        API would respond with.
        """
        timestamp = self.timestamp.timestamp()
        seconds = int(math.floor(timestamp))
        nanoseconds = int((timestamp - seconds) * 1e9)
        return dict(
            # pvname=self.pvname,
            val=self.value,
            secs=seconds,
            nanos=nanoseconds,
            status=self.status,
            severity=self.severity,
            enum_strs=self.enum_strs,
        )

    @classmethod
    def from_missing_data(
        cls, pvname: str, timestamp: datetime.datetime
    ) -> ArchivedValue:
        return ArchivedValue(
            pvname=pvname,
            value=None,
            timestamp=timestamp,
            status=3,
            severity=3,
            enum_strs=None,
            appliance=None,
        )


@dataclasses.dataclass
class ArchivedValueStore:
    """
    Archived value cache entry.

    Attributes
    ----------
    pvname : str
        The PV name.

    appliance : archapp.EpicsArchive
        The appliance that sources the PV's data.

    data : deque[ArchivedValue]
        Length-limited deque of historical data.

    timestamp_aliases : dict[datetime, datetime]
        The requested timestamp is not always equal to that which the archiver
        responds with.  The archived values may not change as frequently as
        those in the control system due to PV configuration or archiver
        configuration, so many requested values may map onto a single archiver
        appliance data value.  This dictionary is implicitly kept up-to-date on
        access of ``by_timestamp``.
    """
    pvname: str
    appliance: archapp.EpicsArchive
    data: Deque[ArchivedValue] = dataclasses.field(
        default_factory=lambda: collections.deque(maxlen=PER_PV_CACHE)
    )
    timestamp_aliases: Dict[datetime.datetime, datetime.datetime] = dataclasses.field(
        default_factory=dict
    )

    @property
    def by_timestamp(self) -> Dict[datetime.datetime, ArchivedValue]:
        result = {
            data.timestamp: data
            for data in self.data
        }
        for from_, to in list(self.timestamp_aliases.items()):
            try:
                result[from_] = result[to]
            except KeyError:
                self.timestamp_aliases.pop(to)
        return result


ArchiverCallback = Callable[[ArchivedValue], None]
MatchPVsResult = Tuple[
    Dict[str, ArchivedValue],
    Dict[archapp.EpicsArchive, List[str]],
    List[str]
]


class ArchiverHelper:
    _instance_: ClassVar[ArchiverHelper]
    appliances: List[archapp.EpicsArchive]
    cache: Dict[str, ArchivedValueStore]
    search_loop_rate: ClassVar[float] = 0.2
    _pv_to_callbacks: Dict[datetime.datetime, Dict[str, List[ArchiverCallback]]]

    def __init__(self):
        self.appliances = []
        self.add_appliance("localhost")
        self.cache = {}
        self._callback_lock = threading.Lock()
        self._pv_to_callbacks = {}
        self.thread = threading.Thread(target=self._search_thread_loop, daemon=True)
        self.thread.start()

    def match_pvs_to_appliance(
        self,
        *pvnames: str,
        dt: datetime.datetime
    ) -> MatchPVsResult:
        """
        Match PVs to the Archiver Appliance that holds their data.

        Parameters
        ----------
        *pvnames : str
            The PV names to look for.

        dt : datetime.datetime
            The timestamp to be used for cache checks / the search query.

        Returns
        -------
        cached : dict[str, ArchivedValue]
            Cached entries by pvname for the given timestamp.  Because this is
            a cache hit, interacting with the archive appliance is not
            necessary after this.

        by_appliance : dict[str, archapp.EpicsArchive]
            Dictionary of PV name to archiver appliance instance.  Each of
            these items represents a cache miss, and further interaction with
            the archiver appliance will be required.

        unknown : list[str]
            PV names not found in any configured archivers.
        """
        if dt is None:
            dt = datetime.datetime.now()

        by_appliance = collections.defaultdict(list)
        cached = {}
        to_find = []
        for pvname in set(pvnames):
            cache_item = self.cache.get(pvname, None)
            if cache_item is None:
                to_find.append(pvname)
            else:
                try:
                    cached[pvname] = cache_item.by_timestamp[dt]
                except KeyError:
                    by_appliance[cache_item.appliance].append(pvname)

        to_find = list(sorted(to_find))
        for appliance in self.appliances:
            if not to_find:
                break

            try:
                event = appliance.get_snapshot(*to_find, at=dt)
            except ValueError:
                ...
            else:
                for pvname, data in event.items():
                    value = ArchivedValue.from_archapp(
                        pvname, appliance, **data
                    )
                    cached[pvname] = value
                    self.add_to_cache(pvname, value, dt)
                    to_find.remove(pvname)

        return cached, dict(by_appliance), to_find

    def add_to_cache(self, pvname: str, value: ArchivedValue, dt: datetime.datetime):
        """Add an ArchivedValue to the cache for the given pvname."""
        if pvname not in self.cache:
            self.cache[pvname] = ArchivedValueStore(
                pvname=pvname,
                appliance=value.appliance,
            )
        cache_item = self.cache[pvname]
        cache_item.data.append(value)
        if dt != value.timestamp:
            cache_item.timestamp_aliases[dt] = value.timestamp

    def get_pvs_at_time(
        self, *pvnames: str, dt: datetime.datetime
    ) -> Dict[str, ArchivedValue]:
        """
        Bulk request many PVs at the given timestamp.

        PVs are matched to the appropriate archiver appliance, if available.
        Missing PVs are initialized with stub values.

        Parameters
        ----------
        *pvs : str
            PV names.

        dt : datetime.datetime
            The timestamp at which to get a snapshot of the PVs.

        Returns
        -------
        archive_data : dict[str, ArchivedValue]
            PV name to ArchivedValue.
        """
        data, by_appliance, missing = self.match_pvs_to_appliance(*pvnames, dt=dt)
        for appliance, appliance_pvs in by_appliance.items():
            try:
                event = appliance.get_snapshot(*appliance_pvs, at=dt)
            except ValueError:
                ...
            else:
                missing.extend(list(set(appliance_pvs) - set(event)))
                for pvname, per_pv_data in event.items():
                    value = ArchivedValue.from_archapp(
                        pvname, appliance, **per_pv_data
                    )
                    data[pvname] = value
                    self.add_to_cache(pvname, value, dt)

        for pv in missing:
            data[pv] = ArchivedValue.from_missing_data(
                pvname=pv,
                timestamp=dt,
            )

        return data

    def get_pv_at_time(
        self, pvname: str, dt: datetime.datetime
    ) -> ArchivedValue:
        """
        Request PV data at the given timestamp.

        Convenience method wrapper around `get_pvs_at_time`.

        Parameters
        ----------
        pv : str
            PV name.

        dt : datetime.datetime
            The timestamp at which to get a snapshot of the PV.

        Returns
        -------
        archive_data : ArchivedValue
        """
        return self.get_pvs_at_time(pvname, dt=dt)[pvname]

    def _search_thread_loop(self):
        """
        Thread which searches for new PVs in bulk.
        """
        while True:
            time.sleep(self.search_loop_rate)
            if not self._pv_to_callbacks:
                continue

            with self._callback_lock:
                to_update = copy.deepcopy(self._pv_to_callbacks)
                self._pv_to_callbacks.clear()

            for dt, pv_to_callback in to_update.items():
                try:
                    data_by_pv = self.get_pvs_at_time(*pv_to_callback, dt=dt)
                except Exception:
                    logger.exception(
                        "Fatal error when retrieving PVs from archiver; "
                        "associated devices may not work. PVs: %s",
                        list(pv_to_callback)
                    )
                    continue
                for pvname, data in data_by_pv.items():
                    for callback in pv_to_callback[pvname]:
                        try:
                            callback(data)
                        except Exception:
                            logger.exception(
                                "Failed to run callback %s for PV %s with data %s",
                                callback,
                                pvname,
                                data,
                            )

    def queue_pv(
        self,
        pvname: str,
        dt: datetime.datetime,
        callback: ArchiverCallback,
        event_type: str = "metadata",
    ):
        """
        Search for pvname in the background search thread.
        """
        # ophyd ensures the callback won't be wrapped twice - just in case
        callback = wrap_callback(event_type, callback)
        try:
            cached_value = self.cache[pvname].by_timestamp[dt]
        except KeyError:
            ...
        else:
            # Queue the callback to be run in the appropriate thread
            callback(cached_value)
            return

        with self._callback_lock:
            if dt not in self._pv_to_callbacks:
                self._pv_to_callbacks[dt] = {}
            self._pv_to_callbacks[dt].setdefault(pvname, []).append(callback)

    @staticmethod
    def instance() -> ArchiverHelper:
        """Access the process-global ArchiveHelper singleton."""
        if not hasattr(ArchiverHelper, "_instance_"):
            ArchiverHelper._instance_ = ArchiverHelper()
        return ArchiverHelper._instance_

    def add_appliance(
        self,
        host: str,
        data_port: int = 17668,
        management_port: int = 17665
    ) -> archapp.EpicsArchive:
        """
        Add an archiver appliance to check.  Multiple may be added and will be
        checked in the order of addition.
        """
        archiver = archapp.EpicsArchive(
            hostname=host,
            data_port=data_port,
            mgmt_port=management_port,
        )
        self.appliances.append(archiver)


class ArchiverDevice(Device):
    # TODO: discuss if this mixin should exist; and if not, where the datetime
    # information should be stored
    component_names: List[str]
    archive_timestamp: datetime.datetime = datetime.datetime.now()

    def _find_archiver_pvs(self) -> Generator[Tuple[str, ArchiverPV], None, None]:
        for walk in self.walk_signals(include_lazy=True):
            if isinstance(walk.item, EpicsSignalBase):
                read_pv = getattr(walk.item, "_read_pv", None)
                write_pv = getattr(walk.item, "_write_pv", None)
                if read_pv is write_pv:
                    write_pv = None

                yield walk.dotted_name, read_pv
                if write_pv is not None:
                    yield walk.dotted_name, write_pv

    def time_slip(self, dt: datetime.datetime):
        pv_to_attr = collections.defaultdict(list)
        for attr, instance in self._find_archiver_pvs():
            instance.archive_timestamp = dt
            pv_to_attr[instance.pvname].append(attr)
        helper = ArchiverHelper.instance()
        self.archive_timestamp = dt
        return helper.get_pvs_at_time(*list(pv_to_attr), dt=dt)


class ArchiverControlLayer:
    thread_class = threading.Thread
    pv_form = "time"
    name = "archive"

    _instance_: ClassVar[ArchiverControlLayer]
    _pvs: Dict[str, List[ArchiverPV]]

    def __init__(self):
        self._pvs = {}

    @staticmethod
    def instance() -> ArchiverControlLayer:
        """Access the process-global ArchiverControlLayer singleton."""
        if not hasattr(ArchiverControlLayer, "_instance_"):
            ArchiverControlLayer._instance_ = ArchiverControlLayer()
        return ArchiverControlLayer._instance_

    def setup(self, logger):
        ...

    def caget(self, pvname, **kwargs):
        raise NotImplementedError()
        try:
            return self.get_pv(pvname).get(**kwargs)
        finally:
            self.release_pvs(pvname)

    def caput(self, *args, **kwargs):
        logger.warning("This is archived mode; no putting is allowed.")

    def get_pv(
        self,
        pvname: str,
        connection_callback: Optional[PyepicsConnectionCallback] = None,
        **kwargs
    ) -> ArchiverPV:
        if connection_callback is None:
            raise RuntimeError(
                "Only EpicsSignal supported for now (no connection cb?)"
            )

        # Yeah, so we're going to use the knowledge of how EpicsSignal uses
        # connection callback to find who's referring to this PV.  Add it to
        # the pile of bad stuff we do.
        try:
            referrer = connection_callback.__self__
        except AttributeError:
            raise RuntimeError(
                "Only EpicsSignal supported for now (no method?)"
            )

        pv = ArchiverPV(
            pvname,
            dispatcher=self.get_dispatcher(),
            connection_callback=connection_callback,
            referrer=referrer,
            **kwargs,
        )
        if pvname not in self._pvs:
            self._pvs[pvname] = []
        self._pvs[pvname].append(pv)
        return pv

    def release_pvs(self, *pvs: str):
        for pvname in pvs:
            try:
                _ = self._pvs[pvname]
            except KeyError:
                continue

    def get_dispatcher(self):
        return ophyd.get_cl().get_dispatcher()


def make_archived_device(cls: Type[Device]) -> Type[ArchiverDevice]:
    """
    Make an alternate device class that uses the ArchiveControlLayer.

    The new class will:
    1. Inherit from :class:`ArchiverDevice`
    2. Switch the control layer of all components that are subclasses of
       EpicsSignalBase.
    3. Have a name like f"Archiver{cls.__name__}".

    Converted classes will be cached in ``_archived_device_cache``.

    Parameters
    ----------
    cls : ophyd.Device subclass
        The class to convert.

    Returns
    -------
    cls : subclass of (cls, ArchiverDevice)
        The converted class.
    """
    return switch_control_layer(
        cls,
        control_layer=ArchiverControlLayer.instance(),
        component_classes=(EpicsSignalBase,),
        cache=_archived_device_cache,
        class_prefix="Archiver",
        new_bases=(ArchiverDevice,),
    )


def switch_control_layer(
    cls: Type[Device],
    control_layer: SimpleNamespace,
    component_classes: Iterable[Type[OphydObject]],
    *,
    cache: Dict[Type[Device], Type[Device]],
    class_prefix: str = "",
    new_bases: Optional[Iterable[type]] = None,
) -> Type[ArchiverDevice]:
    """
    Inspect cls and construct an archived device that has the same structure.

    Parameters
    ----------
    cls : type[Device]
        A real Device class to inspect and create an archived Device class
        from.

    control_layer : SimpleNamespace
        The control layer instance, with attributes such as ``get_pv`` and
        ``setup``.

    component_classes : iterable of OphydObject subclasses
        Adjust the control layer of components that are subclasses of these.
        That is, if ``EpicsSignalBase`` is specified as an option,
        any components with a ``cls`` of ``EpicsSignal`` or ``EpicsSignalRO``
        would be included.

    cache : dict
        Required dictionary of "actual device" to "modified device". Should be
        consistent between calls to this function.

    class_prefix : str, optional
        The prefix to prepend to the newly created Device class.

    new_bases : list of classes, optinoal
        Base classes to use when creating the new class.

    Returns
    -------
    archived_device : type[Device]
        The resulting archived Device class.
    """
    if cls in cache:
        return cache[cls]
    if not issubclass(cls, Device):
        cache[cls] = cls
        return cls

    component_classes = tuple(component_classes)
    new_bases = tuple(new_bases)

    clsdict = {}
    # Update all the components recursively
    for cpt_name in cls.component_names:
        cpt = getattr(cls, cpt_name)
        if not isinstance(cpt, DDCpt):
            replacement_cpt = copy.copy(cpt)
        else:
            # Make a regular Cpt out of the DDC, as it already has been
            # generated
            replacement_cpt = Cpt(
                cls=cpt.cls,
                suffix=cpt.suffix,
                lazy=cpt.lazy,
                trigger_value=cpt.trigger_value,
                kind=cpt.kind,
                add_prefix=cpt.add_prefix,
                doc=cpt.doc,
                **cpt.kwargs,
            )

        if "cl" in cpt.kwargs or (
            inspect.isclass(cpt.cls) and issubclass(cpt.cls, component_classes)
        ):
            replacement_cpt.kwargs["cl"] = control_layer
            logger.debug("Control layer set on %s.%s", cls.__name__, cpt_name)

        replacement_cpt.cls = switch_control_layer(
            cls=replacement_cpt.cls,
            control_layer=control_layer,
            component_classes=component_classes,
            cache=cache,
            class_prefix=class_prefix,
            new_bases=new_bases,
        )

        clsdict[cpt_name] = replacement_cpt

    new_class = type(
        f"{class_prefix}{cls.__name__}",
        (cls, *new_bases),
        clsdict
    )
    cache[cls] = new_class
    logger.debug("cache[%s] = %s", cls, new_class)
    return new_class


class ArchiverPV(PyepicsPvCompatibility):
    """
    An epics.PV-like interface to archiver appliance data.

    Notes
    -----
    There is a 1-to-1 correspondence of :class:`ArchiverPV` to Component,
    whereas normally :class:`epics.PV` can be shared.
    """

    # `_make_connection` below will be in the main thread
    _connect_in_thread: ClassVar[bool] = False

    archive_timestamp: datetime.datetime = datetime.datetime.now()

    def _make_connection(self):
        """PyepicsPvCompatibility hook at startup."""
        helper = ArchiverHelper.instance()
        helper.queue_pv(
            pvname=self.pvname,
            dt=self._referrer_timestamp,
            callback=self._archiver_initial_data,
        )

    def _archiver_initial_data(self, data: ArchivedValue):
        """ArchiverHelper.queue_pv callback."""
        # found_in_archiver = data.appliance is not None
        self._update_state_from_archiver(data)
        self._change_connection_status(connected=True)

    @property
    def _referrer_timestamp(self) -> datetime.datetime:
        """
        Timestamp from the referrer in the following order of precedence:
        1. Timestamp from the _root_ device in the signal's device hierarchy.
        2. The immediate parent device's timestamp.
        3. This PV instance's timestamp (final fallback; should not be hit)
        """
        try:
            return self._referrer.root.archive_timestamp
        except AttributeError:
            try:
                return self._referrer.parent.archive_timestamp
            except AttributeError:
                return self.archive_timestamp

    def put(
        self,
        value: Any,
        wait: bool = False,
        timeout: float = 30.0,
        use_complete: bool = False,
        callback: Optional[PyepicsPutCallback] = None,
        callback_data: Optional[Any] = None,
    ):
        """Stub for pv.put. Callback will be run, but no change will be made."""
        logger.warning(
            "This is archived mode; no puts are allowed. "
            "Attempted to change %r to %r",
            self.pvname,
            value,
        )
        return super().put(
            value,
            wait=wait,
            timeout=timeout,
            use_complete=use_complete,
            callback=callback,
            callback_data=callback_data,
        )

    def _update_state_from_archiver(
        self, data: ArchivedValue, as_string=None
    ) -> Dict[str, Any]:
        """Update _args and run callbacks based on the ArchivedValue."""
        as_string = as_string if as_string is not None else self.as_string
        if data.value is None:
            # Archiver has no data for this PV. What should we do?
            # Guess as to whether or not it's a string and use a default
            # value:
            value = "" if as_string else 0.0
            # Alternatively, we could consider setting the device as
            # disconnected, but this may annoyingly break devices more often
            # than not:
            # self.connected = False
        else:
            value = str(data.value) if as_string else data.value

        self._args.update(
            value=value,
            timestamp=data.timestamp.timestamp(),
            severity=data.severity,
        )
        if data.enum_strs:
            self._args["enum_strs"] = list(data.enum_strs)

        self.run_callbacks()
        return self._args

    def get_with_metadata(self, as_string=None, **kwargs) -> Dict[str, Any]:
        """Query the ArchiverHelper to get the value at the referrer's timestamp."""
        as_string = as_string if as_string is not None else self.as_string
        helper = ArchiverHelper.instance()
        data = helper.get_pv_at_time(self.pvname, self._referrer_timestamp)
        return self._update_state_from_archiver(data, as_string=as_string).copy()


_archived_device_cache = {}
