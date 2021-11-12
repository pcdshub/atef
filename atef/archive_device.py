from __future__ import annotations

import copy
import datetime
import functools
import inspect
import itertools
import logging
import numbers
import time
from typing import Any, ClassVar, Dict, List, Optional

import aa
import ophyd
import pcdsdevices
# from ophyd import FormattedComponent as FCpt
from ophyd import Component as Cpt
from ophyd import Device
from ophyd import DynamicDeviceComponent as DDCpt
from ophyd import EpicsSignal, EpicsSignalRO, EpicsSignalWithRBV, Signal
from ophyd.sim import SynSignal, SynSignalRO
from ophyd.utils import LimitError
from pcdsdevices.signal import (EpicsSignalEditMD, EpicsSignalROEditMD,
                                PytmcSignal, PytmcSignalRO, PytmcSignalRW,
                                SignalEditMD)

logger = logging.getLogger(__name__)


ARCHIVE_CACHE_SIZE = 20_000


class ArchiverHelper:
    _instance_: Optional[ClassVar[ArchiverHelper]] = None
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
    def instance():
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
    def time_slip(self, dt: datetime.datetime):
        result = {}
        for cpt_name in self.component_names:
            obj = getattr(self, cpt_name)
            if hasattr(obj, "time_slip") and callable(obj.time_slip):
                result[cpt_name] = obj.time_slip(dt)
        return result


def make_archived_device(cls):
    """
    Inspect cls and construct an archived device that has the same structure.

    This works by replacing EpicsSignal with ArchivedEpicsSignal and
    EpicsSignalRO with ArchivedEpicsSignalRO. Then archived class will be a
    subclass of the real class.

    This assumes that EPICS connections are done entirely in EpicsSignal and
    EpicsSignalRO subcomponents. If this is not true, this will fail silently
    on class construction and loudly when manipulating an object.

    Parameters
    ----------
    cls : type[Device]
        A real Device class to inspect and create an archived Device class
        from.

    Returns
    -------
    archived_device : type[Device]
        The resulting archived Device class
    """
    if cls not in archived_device_cache:
        if not issubclass(cls, Device):
            # Ignore non-devices and non-epics-signals
            logger.debug("Ignore cls=%s, bases are %s", cls, cls.__bases__)
            archived_device_cache[cls] = cls
            return cls
        archived_dict = {}
        # Update all the components recursively
        for cpt_name in cls.component_names:
            cpt = getattr(cls, cpt_name)
            if isinstance(cpt, DDCpt):
                # Make a regular Cpt out of the DDC, as it already has
                # been generated
                archived_cpt = Cpt(
                    cls=cpt.cls,
                    suffix=cpt.suffix,
                    lazy=cpt.lazy,
                    trigger_value=cpt.trigger_value,
                    kind=cpt.kind,
                    add_prefix=cpt.add_prefix,
                    doc=cpt.doc,
                    **cpt.kwargs,
                )
            else:
                archived_cpt = copy.copy(cpt)

            archived_cpt.cls = make_archived_device(cpt.cls)
            logger.debug("switch cpt_name=%s to cls=%s", cpt_name, archived_cpt.cls)

            archived_dict[cpt_name] = archived_cpt
        archived_class = type(
            "Archived{}".format(cls.__name__), (cls, ArchivedDevice), archived_dict
        )
        archived_device_cache[cls] = archived_class
        logger.debug("archived_device_cache[%s] = %s", cls, archived_class)
    return archived_device_cache[cls]


def clear_archived_device(
    dev: Device, *, default_value=0, default_string_value="", ignore_exceptions=False
):
    """
    Clear an archived device by setting all signals to a specific value.

    Parameters
    ----------
    dev : Device
        The archived device
    default_value : any, optional
        The value to put to non-string components
    default_string_value : any, optional
        The value to put to components determined to be strings
    ignore_exceptions : bool, optional
        Ignore any exceptions raised by `sim_put`

    Returns
    -------
    all_values : list
        List of all (signal_instance, value) that were set
    """

    all_values = []
    for walk in dev.walk_signals(include_lazy=True):
        sig = walk.item
        if not hasattr(sig, "sim_put"):
            continue

        try:
            string = getattr(sig, "as_string", False)
            value = default_string_value if string else default_value
            sig.sim_put(value)
        except Exception:
            if not ignore_exceptions:
                raise
        else:
            all_values.append((sig, value))

    return all_values


def instantiate_archived_device(
    dev_cls, *, name=None, prefix="_prefix", **specified_kw
):
    """
    Instantiate an archived device, optionally specifying some initializer
    kwargs

    If unspecified, all initializer keyword arguments will default to the
    string f"_{argument_name}_".

    Parameters
    ----------
    dev_cls : class
        The device class to instantiate. This is allowed to be a regular
        device, as `make_archived_device` will be called on it first.
    name : str, optional
        The instantiated device name
    prefix : str, optional
        The instantiated device prefix
    **specified_kw :
        Keyword arguments to override with a specific value

    Returns
    -------
    dev : dev_cls instance
        The instantiated fake device
    """
    dev_cls = make_archived_device(dev_cls)
    sig = inspect.signature(dev_cls)
    ignore_kw = {
        "kind",
        "read_attrs",
        "configuration_attrs",
        "parent",
        "args",
        "name",
        "prefix",
    }

    def get_kwarg(name, param):
        default = param.default
        if default == param.empty:
            # NOTE: could check param.annotation here
            default = "_{}_".format(param.name)
        return specified_kw.get(name, default)

    kwargs = {
        name: get_kwarg(name, param)
        for name, param in sig.parameters.items()
        if param.kind != param.VAR_KEYWORD and name not in ignore_kw
    }
    kwargs["name"] = name if name is not None else dev_cls.__name__
    kwargs["prefix"] = prefix
    return dev_cls(**kwargs)


class _PVStandin:
    def __init__(self, pvname):
        self.pvname = pvname
        self._ctrlvars = {}

    def get_ctrlvars(self):
        return self._ctrlvars

    def get_timevars(self):
        return {}


class ArchivedEpicsSignal(SynSignal):
    """
    Archived version of EpicsSignal that's really just a SynSignal.

    Wheras SynSignal is generally used to test plans, ArchivedEpicsSignal is
    generally used in conjunction with make_archived_device to test any logic
    inside of a Device subclass.

    Unlike in SynSignal, this class is generally instantiated inside of a
    subcomponent generated automatically by make_archived_device. This means we
    need extra hooks for modifying the signal's properties after the class
    instantiates.

    We can emulate EpicsSignal features here. We currently emulate the put
    limits and some enum handling.
    """

    _metadata_keys = EpicsSignal._metadata_keys

    def __init__(
        self,
        read_pv,
        write_pv=None,
        *,
        put_complete=False,
        string=False,
        limits=False,
        auto_monitor=False,
        name=None,
        **kwargs,
    ):
        self.as_string = string
        self._enum_strs = None
        super().__init__(name=name, **kwargs)
        self._use_limits = limits
        self._put_func = None
        self._limits = None
        self._metadata.update(
            connected=False,
        )
        self.pvname = read_pv
        self.setpoint_pvname = write_pv or read_pv

        self._read_pv = _PVStandin(read_pv)
        self._write_pv = _PVStandin(write_pv)
        self._metadata_key_map = {read_pv: EpicsSignal._read_pv_metadata_key_map}
        if read_pv != write_pv:
            self._metadata_key_map = {
                write_pv: EpicsSignal._write_pv_metadata_key_map,
                read_pv: {
                    key: value
                    for key, value in self._metadata_key_map[read_pv].items()
                    if key not in ("lower_ctrl_limit", "upper_ctrl_limit")
                },
            }

    def _get_metadata_from_kwargs(
        self, pvname, cl_metadata, *, require_timestamp=False
    ):
        "Metadata from the control layer -> metadata for this Signal"

        def fix_value(fixer_function, value):
            return (
                fixer_function(value)
                if fixer_function is not None and value is not None
                else value
            )

        metadata = {
            md_key: fix_value(fixer_function, cl_metadata[cl_key])
            for cl_key, (md_key, fixer_function) in self._metadata_key_map[
                pvname
            ].items()
            if cl_metadata.get(cl_key, None) is not None
        }

        if require_timestamp and metadata.get("timestamp", None) is None:
            metadata["timestamp"] = time.time()
        return metadata

    def _metadata_changed(
        self, pvname, cl_metadata, *, from_monitor, update, require_timestamp=False
    ):
        metadata = self._get_metadata_from_kwargs(
            pvname, cl_metadata, require_timestamp=require_timestamp
        )
        if update:
            self._metadata.update(**metadata)
        return metadata

    def time_slip(self, dt: datetime.datetime) -> Any:
        helper = ArchiverHelper.instance()
        readback = helper.get_pv_at_time(self.pvname, dt)

        if self.pvname != self.setpoint_pvname:
            setpoint = helper.get_pv_at_time(self.setpoint_pvname, dt)
        else:
            setpoint = readback
        if not isinstance(self, ArchivedEpicsSignalRO):
            ...

        if readback.value is None:
            value = "" if self.as_string else 0.0
        else:
            value = readback.value[0] if len(readback.value) == 1 else readback.value
        if readback.has_enum_options:
            # if self.as_string:
            #     value = readback.enum_string
            self.sim_set_enum_strs(tuple(readback.enum_options.values()))

        for standin in (self._read_pv, self._write_pv):
            standin._ctrlvars.update(
                timestamp=readback.timestamp,
                severity=readback.severity,
                value=value,
            )

        self.sim_put(
            value=value,
            timestamp=readback.timestamp,
            severity=readback.severity,
            status=0,
        )
        if self.pvname != self.setpoint_pvname:
            return readback, setpoint
        return readback

    def describe(self):
        desc = super().describe()
        if self._enum_strs is not None:
            desc[self.name]["enum_strs"] = self.enum_strs
        return desc

    def sim_set_putter(self, putter):
        """
        Define arbirary behavior on signal put.

        This can be used to emulate basic IOC behavior.
        """
        self._put_func = putter

    def get(self, *, as_string=None, connection_timeout=1.0, **kwargs):
        """
        Implement getting as enum strings
        """
        if as_string is None:
            as_string = self.as_string

        value = super().get()

        if as_string:
            if self.enum_strs is not None and isinstance(value, int):
                return self.enum_strs[value]
            elif value is not None:
                return str(value)
        return value

    def put(
        self,
        value,
        *args,
        connection_timeout=0.0,
        callback=None,
        use_complete=None,
        timeout=0.0,
        wait=True,
        **kwargs,
    ):
        """
        Implement putting as enum strings and put functions

        Notes
        -----
        ArchivedEpicsSignal varies in subtle ways from the real class.

        * put-completion callback will _not_ be called.
        * connection_timeout, use_complete, wait, and timeout are ignored.
        """
        if self.enum_strs is not None:
            if value in self.enum_strs:
                value = self.enum_strs.index(value)
            elif isinstance(value, str):
                err = "{} not in enum strs {}".format(value, self.enum_strs)
                raise ValueError(err)
        if self._put_func is not None:
            return self._put_func(value, *args, **kwargs)
        return super().put(value, *args, **kwargs)

    def sim_put(self, *args, **kwargs):
        """
        Update the read-only signal's value.

        Implement here instead of ArchivedEpicsSignalRO so you can call it with
        every fake signal.
        """
        force = kwargs.pop("force", True)
        self._metadata["connected"] = True
        # The following will emit SUB_VALUE:
        ret = Signal.put(self, *args, force=force, **kwargs)
        # Also, ensure that SUB_META has been emitted:
        self._run_subs(sub_type=self.SUB_META, **self._metadata)
        return ret

    @property
    def enum_strs(self):
        """
        Simulated enum strings.

        Use sim_set_enum_strs during setup to set the enum strs.
        """
        return self._enum_strs

    def sim_set_enum_strs(self, enums):
        """
        Set the enum_strs for a fake device

        Parameters
        ----------
        enums: list or tuple of str
            The enums will be accessed by array index, e.g. the first item in
            enums will be 0, the next will be 1, etc.
        """
        self._enum_strs = tuple(enums)
        self._metadata["enum_strs"] = tuple(enums)
        self._run_subs(sub_type=self.SUB_META, **self._metadata)

    @property
    def limits(self):
        return self._limits

    def sim_set_limits(self, limits):
        """
        Set the fake signal's limits.
        """
        self._limits = limits

    def check_value(self, value):
        """
        Implement some of the checks from EpicsSignal
        """
        super().check_value(value)
        if value is None:
            raise ValueError("Cannot write None to EPICS PVs")
        if self._use_limits and self._limits:
            if not self.limits[0] <= value <= self.limits[1]:
                raise LimitError(f"value={value} not within limits {self.limits}")


class ArchivedEpicsSignalRO(SynSignalRO, ArchivedEpicsSignal):
    """
    Read-only ArchivedEpicsSignal
    """

    _metadata_keys = EpicsSignalRO._metadata_keys


class ArchivedEpicsSignalWithRBV(ArchivedEpicsSignal):
    """
    ArchivedEpicsSignal with PV and PV_RBV; used in the AreaDetector PV naming
    scheme
    """

    _metadata_keys = EpicsSignalWithRBV._metadata_keys

    def __init__(self, prefix, **kwargs):
        super().__init__(prefix + "_RBV", write_pv=prefix, **kwargs)


class ArchivedPytmcSignal(ArchivedEpicsSignal):
    """A suitable fake class for PytmcSignal."""

    def __new__(cls, prefix, io=None, **kwargs):
        new_cls = pcdsdevices.signal.select_pytmc_class(
            io=io,
            prefix=prefix,
            write_cls=ArchivedPytmcSignalRW,
            read_only_cls=ArchivedPytmcSignalRO,
        )
        return super().__new__(new_cls)

    def __init__(self, prefix, io=None, **kwargs):
        super().__init__(prefix + "_RBV", **kwargs)


class ArchivedPytmcSignalRW(ArchivedPytmcSignal, ArchivedEpicsSignal):
    def __init__(self, prefix, **kwargs):
        super().__init__(prefix, write_pv=prefix, **kwargs)


class ArchivedPytmcSignalRO(ArchivedPytmcSignal, ArchivedEpicsSignalRO):
    pass


class ArchivedEpicsSignalEditMD(ArchivedEpicsSignal, SignalEditMD):
    """
    EpicsSignal variant which allows for user correction of various metadata.

    Parameters
    ----------
    enum_strings : list of str, optional
        List of enum strings to replace the EPICS originals.  May not be
        used in conjunction with the dynamic ``enum_attrs``.

    enum_attrs : list of str, optional
        List of signal attribute names, relative to the parent device.  That is
        to say a given attribute is assumed to be a sibling of this signal
        instance.  Attribute names may be ``None`` in the case where the
        original enum string should be passed through.

    See Also
    ---------
    `ophyd.signal.EpicsSignal` for further parameter information.
    """

    _enum_attrs: list[Optional[str]]
    _enum_count: int
    _enum_strings: list[str]
    _original_enum_strings: list[str]
    _enum_signals: list[Optional[ophyd.ophydobj.OphydObject]]
    _enum_string_override: bool
    _enum_subscriptions: dict[ophyd.ophydobj.OphydObject, int]
    _pending_signals: set[ophyd.ophydobj.OphydObject]

    def __init__(
        self,
        *args,
        enum_attrs: Optional[list[Optional[str]]] = None,
        enum_strs: Optional[list[str]] = None,
        **kwargs,
    ):
        self._enum_attrs = list(enum_attrs or [])
        self._pending_signals = set()
        self._original_enum_strings = []
        self._enum_signals = []
        self._enum_subscriptions = {}
        self._enum_count = 0
        self._metadata_override = {}

        super().__init__(*args, **kwargs)

        if enum_attrs and enum_strs:
            raise ValueError("enum_attrs OR enum_strs may be set, but not both")

        self._enum_string_override = bool(enum_attrs or enum_strs)
        if self._enum_string_override:
            # We need to control 'connected' status based on other signals
            self._metadata_override["connected"] = False

        if enum_attrs:
            # Override by way of other signals
            self._enum_strings = [""] * len(self.enum_attrs)
            # The following magic is provided by EpicsSignalBaseEditMD.
            # The end result is:
            # -> self.metadata["enum_strs"] => self._enum_strings
            self._metadata_override["enum_strs"] = self._enum_strings
            if self.parent is None:
                raise RuntimeError(
                    "This signal {self.name!r} must be used in a "
                    "Device/Component hierarchy."
                )

            self._subscribe_enum_attrs()

        elif enum_strs:
            # Override with strings
            self._enum_strings = list(enum_strs)
            self._metadata_override["enum_strs"] = self._enum_strings

    def destroy(self):
        super().destroy()
        for sig, sub in self._enum_subscriptions.items():
            if sig is not None:
                sig.unsubscribe(sub)
        self._enum_subscriptions.clear()

    def _subscribe_enum_attrs(self):
        """Subscribe to enum signals by attribute name."""
        for attr in self.enum_attrs:
            if attr is None:
                # Opt-out for a specific signal
                self._enum_signals.append(None)
                continue

            try:
                obj = getattr(self.parent, attr)
            except AttributeError as ex:
                raise RuntimeError(
                    f"Attribute {attr!r} specified in enum list appears to be "
                    f"invalid for the device {self.parent.name}."
                ) from ex

            if obj is self:
                raise RuntimeError(
                    f"Recursively specified {self.name!r} in the enum_attrs "
                    "list.  Don't do that."
                )
            self._enum_signals.append(obj)
            self._pending_signals.add(obj)
            self._enum_subscriptions[obj] = obj.subscribe(
                self._enum_string_updated, run=True
            )

    # Switch out _metadata for metadata where appropriate
    @property
    def enum_strs(self) -> list[str]:
        """
        List of enum strings.

        For an EpicsSignalEditMD, this could be one of:

        1. The original enum strings from the PV
        2. The strings found from the respective signals referenced by
            ``enum_attrs``.
        3. The user-provided strings in ``enum_strs``.
        """
        if self._enum_string_override:
            return list(self._enum_strings)[: self._enum_count]
        return self.metadata["enum_strs"]

    @property
    def precision(self):
        """The PV precision as reported by EPICS (or EpicsSignalEditMD)."""
        return self.metadata["precision"]

    @precision.setter
    def precision(self, value):
        # TODO: archive-specific for synsignal
        self._metadata_override["precision"] = value

    @property
    def limits(self) -> tuple[numbers.Real, numbers.Real]:
        """The PV limits as reported by EPICS (or EpicsSignalEditMD)."""
        return (self.metadata["lower_ctrl_limit"], self.metadata["upper_ctrl_limit"])

    def describe(self):
        """
        Return the signal description as a dictionary.

        Units, limits, precision, and enum strings may be overridden.

        Returns
        -------
        dict
            Dictionary of name and formatted description string
        """
        desc = super().describe()
        desc[self.name]["units"] = self.metadata["units"]
        return desc

    @property
    def enum_attrs(self) -> list[str]:
        """Enum attribute names - the source of each enum string."""
        return list(self._enum_attrs)

    def _enum_string_updated(
        self, value: str, obj: ophyd.ophydobj.OphydObject, **kwargs
    ):
        """
        A single Signal from ``enum_signals`` updated its value.

        This is a ``SUB_VALUE`` subscription callback from that signal.

        Parameters
        ----------
        value : str
            The value of that enum index.

        obj : ophyd.ophydobj.OphydObject
            The ophyd object with the value.

        **kwargs :
            Additional metadata from ``self._metadata``.
        """
        if value is None:
            # The callback may run before it's connected
            return

        try:
            idx = self._enum_signals.index(obj)
        except IndexError:
            return

        self._enum_strings[idx] = str(value)
        self.log.debug(
            "Got enum %s [%d] = %s from %s",
            self.name,
            idx,
            value,
            getattr(obj, "pvname", "(no pvname)"),
        )
        try:
            self._pending_signals.remove(obj)
        except KeyError:
            ...

        if not self._pending_signals:
            # We're probably connected!
            self._run_metadata_callbacks()

    @property
    def connected(self) -> bool:
        """Is the signal connected and ready to use?"""
        return (
            self._metadata["connected"]
            and not self._destroyed
            and not len(self._pending_signals)
        )

    def _check_signal_metadata(self):
        """Check the original enum strings to compare the attributes."""
        self._original_enum_strings = self._metadata.get("enum_strs", None) or []
        if not self._original_enum_strings:
            self.log.error(
                "No enum strings on %r; was %r used inappropriately?",
                self.pvname,
                type(self).__name__,
            )
            return

        if self._enum_count == 0:
            self._enum_count = len(self._original_enum_strings)

            # Only update ones that have yet to be populated;  this can
            # be a race for who connects first:
            updated_enums = [
                existing or original
                for existing, original in itertools.zip_longest(
                    self._enum_strings, self._original_enum_strings, fillvalue=""
                )
            ]
            self._enum_strings[:] = updated_enums

    def _run_metadata_callbacks(self):
        """Hook for metadata callbacks, mostly run by superclasses."""
        self._metadata_override["connected"] = self.connected
        if self._metadata["connected"]:
            # The underlying PV has connected - check its enum_strs:
            self._check_signal_metadata()
        super()._run_metadata_callbacks()


class ArchivedEpicsSignalROEditMD(ArchivedEpicsSignalEditMD):
    def __init__(self, prefix, **kwargs):
        super().__init__(prefix, write_pv=prefix, **kwargs)


archived_device_cache = {
    EpicsSignal: ArchivedEpicsSignal,
    EpicsSignalRO: ArchivedEpicsSignalRO,
    EpicsSignalWithRBV: ArchivedEpicsSignalWithRBV,
    EpicsSignalEditMD: ArchivedEpicsSignalEditMD,
    EpicsSignalROEditMD: ArchivedEpicsSignalROEditMD,
    PytmcSignal: ArchivedPytmcSignal,
    PytmcSignalRO: ArchivedPytmcSignalRO,
    PytmcSignalRW: ArchivedPytmcSignalRW,
}


def test():
    global at1k4
    global display
    import pcdsdevices.tests.conftest  # noqa

    pcdsdevices.tests.conftest.find_all_device_classes()
    for cls in pcdsdevices.tests.conftest.find_all_device_classes():
        make_archived_device(cls)

    helper = ArchiverHelper.instance()
    at1k4 = archived_device_cache[pcdsdevices.attenuator.AT1K4](
        prefix="AT1K4:L2SI", calculator_prefix="AT1K4:CALC", name="at1k4"
    )

    at1k4.time_slip(datetime.datetime.now())

    import PyQt5  # noqa
    import typhos  # noqa

    app = PyQt5.QtWidgets.QApplication([])
    display = typhos.suite.TyphosDeviceDisplay.from_device(at1k4)
    display.show()
    app.exec_()


if __name__ == "__main__":
    test()
