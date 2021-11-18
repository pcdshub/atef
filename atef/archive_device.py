from __future__ import annotations

import copy
import datetime
import functools
import inspect
import logging
import threading
from types import SimpleNamespace
from typing import ClassVar, Dict, Iterable, List, Optional, Type

import aa
import ophyd
from ophyd import Component as Cpt
from ophyd import Device
from ophyd import DynamicDeviceComponent as DDCpt
from ophyd.signal import EpicsSignalBase

from .pyepics_compat import PyepicsConnectionCallback, PyepicsPvCompatibility

logger = logging.getLogger(__name__)


ARCHIVE_CACHE_SIZE = 20_000


class ArchiverHelper:
    _instance_: ClassVar[ArchiverHelper] = None
    pv_to_appliance: Dict[str, aa.fetcher.Fetcher]
    appliances: List[aa.fetcher.Fetcher]

    def __init__(self):
        self.pv_to_appliance = {}
        self.appliances = []
        self.add_appliance("localhost", 17668)

    @functools.lru_cache(maxsize=ARCHIVE_CACHE_SIZE)
    def get_pv_at_time(
        self, pvname: str, dt: datetime.datetime
    ) -> aa.data.ArchiveEvent:
        dt = dt.astimezone()  # TODO: aapy raising without this
        if pvname not in self.pv_to_appliance:
            for fetcher in self.appliances:
                try:
                    event = fetcher.get_event_at(pvname, dt)
                except ValueError:
                    ...
                else:
                    self.pv_to_appliance[pvname] = fetcher
                    return event
            # raise ValueError(f"{pvname!r} not available in archiver(s)")
            return aa.data.ArchiveEvent(
                pv=pvname,
                value=None,
                timestamp=dt.timestamp(),
                severity=3,
                enum_options={},
            )

        fetcher = self.pv_to_appliance[pvname]
        return fetcher.get_event_at(pvname, dt)

    @staticmethod
    def instance() -> ArchiverHelper:
        if ArchiverHelper._instance_ is None:
            ArchiverHelper._instance_ = ArchiverHelper()
        return ArchiverHelper._instance_

    def add_fetcher(self, fetcher: aa.fetcher.Fetcher):
        self.appliances.append(fetcher)

    def add_appliance(
        self, host: str, port: int, method: str = "pb"
    ) -> aa.fetcher.Fetcher:
        if method == "pb":
            fetcher = aa.pb.PbFetcher(host, port)
        elif method == "pb_file":
            fetcher = aa.pb.PbFileFetcher(host, port)
        elif method == "json":
            fetcher = aa.json.JsonFetcher(host, port)
        else:
            raise ValueError(f"Unknown aapy fetcher method {method!r}")

        self.add_fetcher(fetcher)
        return fetcher


class ArchivedDevice:
    _date_and_time_: datetime.datetime = datetime.datetime.now()

    def time_slip(self, dt: datetime.datetime):
        self._date_and_time_ = dt
        result = {}
        for cpt_name in self.component_names:
            obj = getattr(self, cpt_name)
            if hasattr(obj, "time_slip") and callable(obj.time_slip):
                result[cpt_name] = obj.time_slip(dt)
        return result


class ArchiveControlLayer:
    thread_class = threading.Thread
    pv_form = "time"
    name = "archive"

    _pvs: Dict[str, List[ArchivedPV]]

    def __init__(self):
        self._pvs = {}

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
    ) -> ArchivedPV:
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

        pv = ArchivedPV(
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


def switch_control_layer(
    cls: Device,
    control_layer: SimpleNamespace,
    component_classes: Iterable[Type[Device]],
    *,
    cache: Dict[Type[Device], Type[Device]],
    class_prefix: str = "",
    new_bases: Optional[Iterable[type]] = None,
) -> Device:
    """
    Inspect cls and construct an archived device that has the same structure.

    Parameters
    ----------
    cls : type[Device]
        A real Device class to inspect and create an archived Device class
        from.

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
        if "cl" in cpt.kwargs or (
            inspect.isclass(cpt.cls) and issubclass(cpt.cls, component_classes)
        ):
            cpt.kwargs["cl"] = control_layer
            logger.debug("Control layer set on %s.%s", cls.__name__, cpt_name)

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


class ArchivedPV(PyepicsPvCompatibility):
    def _make_connection(self):
        _ = self.get_with_metadata()
        super()._mark_as_connected()

    def put(
        self,
        value,
        wait=False,
        timeout=30.0,
        use_complete=False,
        callback=None,
        callback_data=None,
    ):
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

    def get_timestamp_from_referrer(self):
        device: ArchivedDevice = self._referrer.parent
        return device._date_and_time_

    def get_with_metadata(self, as_string=None, **kwargs):
        as_string = as_string if as_string is not None else self.as_string
        helper = ArchiverHelper.instance()
        data = helper.get_pv_at_time(
            self.pvname, self.get_timestamp_from_referrer()
        )
        if data.value is None:
            value = "" if as_string else 0.0
            # self.connected = False
        else:
            value = data.value[0] if len(data.value) == 1 else data.value
        self._args.update(
            value=value,
            timestamp=data.timestamp,
            severity=data.severity,
        )
        if data.enum_options:
            self._args["enum_strs"] = list(data.enum_options.values())
        return self._args.copy()


_archived_device_cache = {}


def test():
    global at1k4
    global display
    import pcdsdevices  # noqa
    import pcdsdevices.tests.conftest  # noqa

    pcdsdevices.tests.conftest.find_all_device_classes()
    for cls in pcdsdevices.tests.conftest.find_all_device_classes():
        switch_control_layer(
            cls,
            control_layer=ArchiveControlLayer(),
            component_classes=(EpicsSignalBase,),
            cache=_archived_device_cache,
            class_prefix="Archived",
            new_bases=(ArchivedDevice,),
        )

    at1k4 = _archived_device_cache[pcdsdevices.attenuator.AT1K4](
        prefix="AT1K4:L2SI", calculator_prefix="AT1K4:CALC", name="at1k4"
    )

    at1k4.time_slip(datetime.datetime.now())

    # import PyQt5  # noqa
    # import typhos  # noqa

    # app = PyQt5.QtWidgets.QApplication([])
    # display = typhos.suite.TyphosDeviceDisplay.from_device(at1k4)
    # display.show()
    # app.exec_()


if __name__ == "__main__":
    test()
