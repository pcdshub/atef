"""
Ophyd Device-related widgets.
"""
from __future__ import annotations

import dataclasses
import enum
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

import numpy as np
import ophyd
import ophyd.device
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Qt

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OphydAttributeData:
    attr: str
    description: Dict[str, Any]
    docstring: Optional[str]
    pvname: str
    read_only: bool
    readback: Any
    setpoint: Any
    signal: ophyd.Signal

    @classmethod
    def from_device_attribute(
        cls, device: ophyd.Device, attr: str
    ) -> OphydAttributeData:
        """Get attribute information given a device and dotted attribute name."""
        inst = getattr(device, attr)
        cpt = getattr(type(device), attr)
        read_only = isinstance(inst, (ophyd.EpicsSignalRO,))
        return cls(
            attr=attr,
            description={},
            docstring=cpt.doc,
            pvname=getattr(inst, "pvname", "(Python)"),
            read_only=read_only,
            readback=None,
            setpoint=None,
            signal=inst,
        )

    @classmethod
    def from_device(cls, device: ophyd.Device) -> Dict[str, OphydAttributeData]:
        """Create a data dictionary for a given device."""
        data = {
            attr: cls.from_device_attribute(device, attr)
            for attr in device.component_names
            if attr not in device._sub_devices
        }

        for sub_name in device._sub_devices:
            sub_dev = getattr(device, sub_name)
            sub_data = {
                f"{sub_name}.{key}": value
                for key, value in OphydAttributeData.from_device(sub_dev).items()
            }
            for key, value in sub_data.items():
                value.attr = key
            data.update(sub_data)

        return data


class DeviceColumn(enum.IntEnum):
    """Column information for the device model."""
    attribute = 0
    readback = 1
    setpoint = 2
    pvname = 3

    total_columns = 4


class _DevicePollThread(QtCore.QThread):
    """
    Polling thread
    """

    data_changed = QtCore.Signal(str)
    _attrs: Set[str]

    def __init__(
        self,
        device: ophyd.Device,
        data: Dict[str, OphydAttributeData],
        poll_rate: float,
        *,
        parent: Optional[QtWidgets.QWidget] = None
    ):
        super().__init__(parent=parent)
        self.device = device
        self.data = data
        self.poll_rate = poll_rate
        self._attrs = set()

    def _instantiate_device(self) -> Set[str]:
        """Instantiate the device and return the attrs to pay attention to."""
        attrs = set(self.data)
        # Instantiate all signals first
        with ophyd.device.do_not_wait_for_lazy_connection(self.device):
            for attr in list(attrs):
                try:
                    getattr(self.device, attr)
                except Exception:
                    logger.exception(
                        "Poll thread for %s.%s @ %.3f sec failure on initial access",
                        self.device.name,
                        attr,
                        self.poll_rate,
                    )
                    attrs.remove(attr)
        return attrs

    def _update_attr(self, attr: str):
        """Update an attribute of the device."""
        setpoint = None
        data = self.data[attr]
        try:
            sig = data.signal

            if not data.description:
                data.description = sig.describe()[sig.name] or {}

            if hasattr(sig, "get_setpoint"):
                setpoint = sig.get_setpoint()
            elif hasattr(sig, "setpoint"):
                setpoint = sig.setpoint
            readback = sig.get()
        except Exception:
            logger.exception(
                "Poll thread for %s.%s @ %.3f sec failure",
                self.device.name,
                attr,
                self.poll_rate,
            )
            self._attrs.remove(attr)
            return

        new_data = {}
        if readback is not None:
            units = data.description.get("units", "") or ""
            new_data["readback"] = f"{readback} {units}"
        if setpoint is not None:
            new_data["setpoint"] = setpoint

        for key, value in new_data.items():
            old_value = getattr(data, key)

            try:
                changed = np.any(old_value != value)
            except Exception:
                ...
            else:
                if changed or old_value is None:
                    for key, value in new_data.items():
                        setattr(data, key, value)
                    self.data_changed.emit(attr)
                    return

    def run(self):
        self.running = True
        attrs = self._instantiate_device()

        while self.running:
            t0 = time.monotonic()
            for attr in list(attrs):
                self._update_attr(attr)

            elapsed = time.monotonic() - t0
            time.sleep(max((0, self.poll_rate - elapsed)))


class PolledDeviceModel(QtCore.QAbstractTableModel):
    """A table model representing an ophyd Device with periodic data polling."""

    device: ophyd.Device
    poll_rate: float
    _polling: bool
    poll_thread: Optional[QtCore.QThread]
    read_only: bool
    _data: Dict[str, OphydAttributeData]
    _row_to_data: Dict[int, OphydAttributeData]
    horizontal_header: List[str]

    def __init__(
        self,
        device: ophyd.Device,
        *,
        poll_rate: float = 1.0,
        parent: Optional[QtWidgets.QWidget] = None,
        read_only: bool = True,
        **kwargs
    ):
        super().__init__(parent=parent, **kwargs)
        self.device = device
        self.poll_rate = float(poll_rate)
        self._polling = False
        self.poll_thread = None
        self.read_only = read_only

        self._data = OphydAttributeData.from_device(device)
        self._row_to_data = {
            row: data for row, (_, data) in enumerate(sorted(self._data.items()))
        }
        self.horizontal_header = [
            "Attribute",
            "Readback",
            "Setpoint",
            "PV Name",
        ]
        self.start()

    def start(self):
        "Start the polling thread"
        if self._polling:
            return

        self._polling = True
        self._poll_thread = _DevicePollThread(
            self.device, self._data, self.poll_rate, parent=self
        )
        self._poll_thread.data_changed.connect(self._data_changed)
        self._poll_thread.start()

    def _data_changed(self, attr):
        row = list(self._data).index(attr)
        self.dataChanged.emit(
            self.createIndex(row, 0), self.createIndex(row, self.columnCount(0))
        )

    def stop(self):
        thread = self._poll_thread
        if self._polling or not thread:
            return

        thread.running = False
        self._poll_thread = None
        self._polling = False

    def hasChildren(self, index):
        # TODO sub-devices?
        return False

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return self.horizontal_header[section]

    def setData(self, index, value, role=Qt.EditRole):
        row = index.row()
        column = index.column()
        info = self._row_to_data[row]

        if role != Qt.EditRole or column != DeviceColumn.setpoint:
            return False

        obj = info.signal

        def set_thread():
            try:
                logger.debug("Setting %s = %r", obj.name, value)
                obj.put(value, wait=False)
            except Exception:
                logger.exception("Failed to set %s to %r", obj.name, value)

        self._set_thread = threading.Thread(target=set_thread, daemon=True)
        self._set_thread.start()
        return True

    def flags(self, index):
        flags = super().flags(index)

        row = index.row()
        if index.column() == DeviceColumn.setpoint:
            info = self._row_to_data[row]
            if not info.read_only and not self.read_only:
                return flags | Qt.ItemIsEnabled | Qt.ItemIsEditable
        return flags

    def data(self, index, role):
        row = index.row()
        column = index.column()
        info = self._row_to_data[row]

        if role == Qt.EditRole and column == DeviceColumn.setpoint:
            return info.setpoint

        if role == Qt.DisplayRole:
            setpoint = info.setpoint
            if setpoint is None or np.size(setpoint) == 0:
                setpoint = ""
            columns = {
                0: info.attr,
                1: info.readback,
                2: setpoint,
                3: info.pvname,
            }
            return str(columns[column])

        if role == Qt.ToolTipRole:
            if column in (0,):
                return info.docstring
            if column in (DeviceColumn.readback, DeviceColumn.setpoint):
                enum_strings = info.description.get("enum_strs", None)
                if not enum_strings:
                    return
                return "\n".join(
                    f"{idx}: {item!r}" for idx, item in enumerate(enum_strings)
                )

    def columnCount(self, index) -> int:
        return DeviceColumn.total_columns

    def rowCount(self, index) -> int:
        return len(self._data)


class DeviceView(QtWidgets.QTableView):
    """A tabular view of an ophyd.Device."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        device: Optional[ophyd.Device] = None,
    ):
        super().__init__(parent=parent)
        self.proxy_model = QtCore.QSortFilterProxyModel()
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setDynamicSortFilter(True)
        self.setModel(self.proxy_model)

        self.models = {}
        self._device = None

        # Set the property last
        self.device = device

    def clear(self):
        for model in self.models.values():
            model.stop()
        self.models.clear()
        self._device = None

    @property
    def device(self) -> Optional[ophyd.Device]:
        return self._device

    @device.setter
    def device(self, device: Optional[ophyd.Device]):
        if device is self._device:
            return

        if self._device is not None:
            self.models[device].stop()

        self._device = device
        if device:
            try:
                model = self.models[device]
            except KeyError:
                model = PolledDeviceModel(device=device)
                self.models[device] = model

            model.start()

            self.proxy_model.setSourceModel(model)


class DeviceWidget(QtWidgets.QFrame):
    """A convenient frame with an embedded DeviceView."""
    closed = QtCore.Signal()

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        device: Optional[ophyd.Device] = None
    ):
        super().__init__(parent=parent)

        if device is not None:
            self.setWindowTitle(device.name)

        self.setMinimumSize(500, 400)

        self.filter_label = QtWidgets.QLabel("&Filter")
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_label.setBuddy(self.filter_edit)

        def set_filter(text):
            self.view.proxy_model.setFilterRegExp(text)

        self.filter_edit.textEdited.connect(set_filter)
        self.view = DeviceView(device=device)
        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        layout.addWidget(self.filter_label, 0, 0)
        layout.addWidget(self.filter_edit, 0, 1)
        layout.addWidget(self.view, 1, 0, 1, 2)

    def closeEvent(self, ev):
        super().closeEvent(ev)
        self.view.clear()
        self.closed.emit()

    @property
    def device(self) -> Optional[ophyd.Device]:
        return self.view.device

    @device.setter
    def device(self, device: Optional[ophyd.Device]):
        self.view.device = device
