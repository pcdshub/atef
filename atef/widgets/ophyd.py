"""
Ophyd Device-related widgets.
"""
from __future__ import annotations

import dataclasses
import enum
import logging
import threading
import time
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set

import numpy as np
import ophyd
import ophyd.device
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt

from ..qt_helpers import copy_to_clipboard
from .archive_viewer import get_archive_viewer
from .core import DesignerDisplay

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OphydAttributeDataSummary:
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    average: Optional[Any] = None

    @classmethod
    def from_attr_data(cls, *items: OphydAttributeData) -> OphydAttributeDataSummary:
        try:
            values = set(
                item.readback
                for item in items
                if item.readback is not None
            )
        except TypeError:
            # Unhashable readback values
            values = set()

        if not values:
            return OphydAttributeDataSummary()

        def ignore_type_errors(func: Callable) -> Optional[Any]:
            try:
                return func(values)
            except Exception:
                return None

        sum_ = ignore_type_errors(sum)
        if isinstance(sum_, (int, float)):
            average = sum_ / len(values)
        else:
            average = None

        return OphydAttributeDataSummary(
            minimum=ignore_type_errors(min),
            maximum=ignore_type_errors(max),
            average=average
        )


@dataclasses.dataclass
class OphydAttributeData:
    attr: str
    description: Dict[str, Any]
    docstring: Optional[str]
    pvname: str
    read_only: bool
    readback: Any
    setpoint: Any
    units: Optional[str]
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
            units=None,
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
    read_pvname = 3
    set_pvname = 4

    total_columns = 5


class _DevicePollThread(QtCore.QThread):
    """
    Polling thread for updating a PolledDeviceModel.

    Emits ``data_changed(attr: str)`` when an attribute has new data.

    Parameters
    ----------
    device : ophyd.Device
        The ophyd device to poll.

    poll_rate : float
        The poll rate in seconds. A zero or negative poll rate will indicate
        single-shot mode.  In "single shot" mode, the data is queried exactly
        once and then the thread exits.

    data : dict of attr to OphydAttributeData
        Per-attribute OphydAttributeData, potentially generated previously.

    parent : QWidget, optional, keyword-only
        The parent widget.
    """

    data_ready: ClassVar[QtCore.Signal] = QtCore.Signal()
    data_changed: ClassVar[QtCore.Signal] = QtCore.Signal(str)
    running: bool
    device: ophyd.Device
    data: Dict[str, OphydAttributeData]
    poll_rate: float
    _attrs: Set[str]

    def __init__(
        self,
        device: ophyd.Device,
        poll_rate: float,
        data: Dict[str, OphydAttributeData],
        *,
        parent: Optional[QtWidgets.QWidget] = None
    ):
        super().__init__(parent=parent)
        self.device = device
        self.data = data
        self.poll_rate = poll_rate
        self.running = False
        self._attrs = set()

    def stop(self) -> None:
        """Stop the polling thread."""
        self.running = False

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

                if not self.running:
                    # ``stop()`` may have been requested in the meantime
                    break

        return attrs

    def _update_attr(self, attr: str):
        """Update an attribute of the device."""
        setpoint = None
        data = self.data[attr]

        try:
            if not data.signal.connected:
                return
        except TimeoutError:
            return

        if not data.description:
            try:
                data.description = data.signal.describe()[data.signal.name] or {}
            except Exception:
                data.description = {
                    "units": data.signal.metadata.get("units", ""),
                }

        try:
            get_setpoint = getattr(data.signal, "get_setpoint", None)
            if callable(get_setpoint):
                setpoint = get_setpoint()
            else:
                setpoint = getattr(data.signal, "setpoint", None)
            readback = data.signal.get()
        except TimeoutError as ex:
            # Don't spam on failure to connect
            logger.debug("Failed to connect to %s.%s (%s)", self.device.name, attr, ex)
            return
        except Exception:
            logger.exception(
                "Poll thread for %s.%s @ %.3f sec failure",
                self.device.name,
                attr,
                self.poll_rate,
            )
            self._attrs.remove(attr)
            return

        new_data: Dict[str, Any] = {}
        new_data["units"] = data.description.get("units", "") or ""
        if readback is not None:
            new_data["readback"] = readback
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
        """The thread polling loop."""
        self.running = True
        self._attrs = self._instantiate_device()

        # We may have already created the dictionary; only do it if necessary:
        if not self.data:
            self.data.update(**OphydAttributeData.from_device(self.device))

        self.data_ready.emit()

        while self.running:
            t0 = time.monotonic()
            for attr in list(self._attrs):
                self._update_attr(attr)
                if not self.running:
                    break
                time.sleep(0)

            if self.poll_rate <= 0.0:
                # A zero or below means "single shot" updates.
                break

            elapsed = time.monotonic() - t0
            time.sleep(max((0, self.poll_rate - elapsed)))


class PolledDeviceModel(QtCore.QAbstractTableModel):
    """
    A table model representing an ophyd Device with periodic data polling.

    Emits ``data_updates_started`` when polling begins.
    Emits ``data_updates_finished`` when the polling thread stops.

    Parameters
    ----------
    device : ophyd.Device
        The ophyd device to poll.

    poll_rate : float, optional, keyword-only
        The poll rate in seconds.

    read_only : bool, optional
        Defaults to read-only or ``True``. Allow for puts to the control system
        via ophyd if ``read_only`` is ``False``.

    parent : QWidget, optional, keyword-only
        The parent widget.
    """

    _data: Dict[str, OphydAttributeData]
    _poll_thread: Optional[_DevicePollThread]
    _polling: bool
    _row_to_data: Dict[int, OphydAttributeData]
    device: ophyd.Device
    horizontal_header: List[str]
    read_only: bool
    data_updates_started: ClassVar[QtCore.Signal] = QtCore.Signal()
    data_updates_finished: ClassVar[QtCore.Signal] = QtCore.Signal()

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
        self._polling = False
        self.device = device
        self._poll_rate = float(poll_rate)
        self.poll_thread = None
        self.read_only = read_only

        self._data = {}
        self._row_to_data = {}
        self.horizontal_header = [
            "Attribute",
            "Readback",
            "Setpoint",
            "Read PV Name",
            "Setpoint PV Name"
        ]
        self.start()

    def start(self) -> None:
        "Start the polling thread"
        if self._polling:
            return

        self._polling = True
        self._poll_thread = _DevicePollThread(
            device=self.device,
            data=self._data,
            poll_rate=self.poll_rate,
            parent=self,
        )
        self._data = self._poll_thread.data  # A shared reference
        self._poll_thread.data_ready.connect(self._data_ready)
        self._poll_thread.finished.connect(self._poll_thread_finished)
        self.data_updates_started.emit()
        self._poll_thread.start()

    def stop(self) -> None:
        """Stop the polling thread for the model."""
        thread = self._poll_thread
        if not self._polling or not thread:
            return

        thread.stop()
        self._poll_thread = None
        self._polling = False

    @QtCore.Slot()
    def _poll_thread_finished(self):
        """Slot: poll thread finished and returned."""
        self.data_updates_finished.emit()
        if self._poll_thread is None:
            return

        self._poll_thread.data_ready.disconnect(self._data_ready)
        self._poll_thread.finished.disconnect(self._poll_thread_finished)
        self._polling = False

    @QtCore.Slot()
    def _data_ready(self) -> None:
        """
        Slot: initial indication from _DevicePollThread that the data dictionary is ready.
        """
        self.beginResetModel()
        self._row_to_data = {
            row: data for row, (_, data) in enumerate(sorted(self._data.items()))
        }
        self.endResetModel()
        if self._poll_thread is not None:
            self._poll_thread.data_changed.connect(self._data_changed)

    @QtCore.Slot(str)
    def _data_changed(self, attr: str) -> None:
        """Slot: data changed for the given attribute in the thread."""
        try:
            row = list(self._data).index(attr)
        except IndexError:
            ...
        else:
            self.dataChanged.emit(
                self.createIndex(row, DeviceColumn.readback),
                self.createIndex(row, DeviceColumn.set_pvname),
            )

    def get_data_for_row(self, row: int) -> Optional[OphydAttributeData]:
        """Get the OphydAttributeData for the provided row."""
        try:
            return self._row_to_data[row]
        except KeyError:
            return None

    @property
    def poll_rate(self) -> float:
        """The poll rate for the underlying thread."""
        return self._poll_rate

    @poll_rate.setter
    def poll_rate(self, rate: float) -> None:
        self._poll_rate = rate
        if self._poll_thread is not None and self._polling:
            self._poll_thread.poll_rate = rate

    def hasChildren(self, index: QtCore.QModelIndex) -> bool:
        """Qt hook: does the index have children?"""
        # TODO sub-devices?
        return False

    def headerData(
        self, section: int, orientation: Qt.Orientation, role=Qt.DisplayRole
    ) -> Optional[str]:
        """Qt hook: header information."""
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.horizontal_header[section]
        return None

    def setData(
        self, index: QtCore.QModelIndex, value: Any, role: int = Qt.EditRole
    ) -> bool:
        """Qt hook: request to set ``index`` to ``value``."""
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

    def flags(self, index: QtCore.QModelIndex) -> Qt.ItemFlags:
        """Qt hook: flags for the provided index."""
        flags = super().flags(index)

        row = index.row()
        if index.column() == DeviceColumn.setpoint:
            info = self._row_to_data[row]
            if not info.read_only and not self.read_only:
                return flags | Qt.ItemIsEnabled | Qt.ItemIsEditable
        return flags

    def data(self, index: QtCore.QModelIndex, role: int) -> Optional[str]:
        """Qt hook: get data for the provided index and role."""
        row = index.row()
        column = index.column()
        try:
            info = self._row_to_data[row]
        except KeyError:
            return

        if role == Qt.EditRole and column == DeviceColumn.setpoint:
            return info.setpoint

        if role == Qt.DisplayRole:
            setpoint = info.setpoint
            if setpoint is None or np.size(setpoint) == 0:
                setpoint = ""
            if column == DeviceColumn.attribute:
                return info.attr
            if column == DeviceColumn.readback:
                units = info.units or ""
                return f"{info.readback} {units}"
            if column == DeviceColumn.setpoint:
                return f"{info.setpoint}"
            if column == DeviceColumn.read_pvname:
                return info.pvname
            if column == DeviceColumn.set_pvname:
                return getattr(info.signal, 'setpoint_pvname', 'None')
            return ""

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

        return None

    def columnCount(self, index: Optional[QtCore.QModelIndex] = None) -> int:
        """Qt hook: column count for the given index."""
        return DeviceColumn.total_columns

    def rowCount(self, index: Optional[QtCore.QModelIndex] = None) -> int:
        """Qt hook: row count for the given index."""
        if not self._row_to_data:
            return 0
        return max(self._row_to_data) + 1


class OphydDeviceTableView(QtWidgets.QTableView):
    """
    A tabular view of an ophyd.Device, its components, and signal values.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget.

    device : ophyd.Device, optional
        The ophyd device to look at.  May be set later.
    """

    #: The default poll rate for the model update thread.
    poll_rate: float = 0.0
    #: Signal indicating the model's poll thread has started executing.
    data_updates_started: ClassVar[QtCore.Signal] = QtCore.Signal()
    #: Signal indicating the model's poll thread has finished executing.
    data_updates_finished: ClassVar[QtCore.Signal] = QtCore.Signal()
    #: Signal indicating the attributes have been selected by the user.
    attributes_selected: ClassVar[QtCore.Signal] = QtCore.Signal(
        list  # List[OphydAttributeData]
    )
    context_menu_helper: Callable[[], QtWidgets.QMenu]

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

        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._table_context_menu)

        self.models = {}
        self._device = None

        self.sortByColumn(0, Qt.AscendingOrder)

        self.setSelectionBehavior(self.SelectionBehavior.SelectRows)

        # Set the property last
        self.device = device

    def stop(self) -> None:
        """Stop the underlying model from updating."""
        model = self.current_model
        if model is None:
            return

        model.stop()
        try:
            model.data_updates_started.disconnect(self.data_updates_started.emit)
        except TypeError:
            ...

        try:
            model.data_updates_finished.disconnect(self.data_updates_finished.emit)
        except TypeError:
            ...

    def clear(self):
        """Clear all models and reset the device."""
        self.stop()
        for model in self.models.values():
            model.stop()

        self.models.clear()
        self._device = None

    def _table_context_menu(self, pos: QtCore.QPoint) -> None:
        """Context menu for the table."""
        if self.context_menu_helper:
            self.menu = self.context_menu_helper()
        else:
            self.menu = QtWidgets.QMenu(self)
        index: QtCore.QModelIndex = self.indexAt(pos)
        if index is not None:
            def copy(*_):
                copy_to_clipboard(index.data())

            def select_attr(*_):
                self.attributes_selected.emit([row_data])

            standard_actions = []

            if index.data() is not None:
                copy_action = QtWidgets.QAction(f'&Copy: {index.data()}')
                standard_actions.append(copy_action)
                copy_action.triggered.connect(copy)

            row_data = self.get_data_from_proxy_index(index)
            if row_data is not None:
                select_action = QtWidgets.QAction(
                    f"&Select attribute: {row_data.attr}"
                )
                standard_actions.append(select_action)
                select_action.triggered.connect(select_attr)

            # Insert the standard actions above the custom ones
            if not self.menu.actions():
                self.menu.insertActions(None, standard_actions)
            else:
                self.menu.insertActions(self.menu.actions()[0], standard_actions)

        self.menu.exec_(self.mapToGlobal(pos))

    def get_data_from_proxy_index(
        self, index: QtCore.QModelIndex
    ) -> Optional[OphydAttributeData]:
        """Proxy model index -> source model index -> OphydAttributeData."""
        model = self.current_model
        if model is None:
            return None

        return model.get_data_for_row(self.proxy_model.mapToSource(index).row())

    @property
    def current_model(self) -> Optional[PolledDeviceModel]:
        """The current device model."""
        try:
            return self.models[self._device]
        except KeyError:
            return None

    @property
    def device(self) -> Optional[ophyd.Device]:
        """The currently-configured ophyd Device."""
        return self._device

    @device.setter
    def device(self, device: Optional[ophyd.Device]):
        if device is self._device:
            return

        self.stop()

        self._device = device
        if not device:
            return

        try:
            model = self.models[device]
        except KeyError:
            model = PolledDeviceModel(device=device, poll_rate=self.poll_rate)
            self.models[device] = model
            new_model = True
        else:
            new_model = False

        model.poll_rate = self.poll_rate
        model.data_updates_started.connect(self.data_updates_started.emit)
        model.data_updates_finished.connect(self.data_updates_finished.emit)
        self.proxy_model.setSourceModel(model)

        if new_model:
            # Only start an update if we haven't previously gotten information
            # about the device
            model.start()

    @property
    def selected_attribute_data(self) -> List[OphydAttributeData]:
        """The OphydAttributeData items that correspond to the selection."""
        unique_indexes = {ind.row(): ind for ind in self.selectedIndexes()}
        data = [
            self.get_data_from_proxy_index(index)
            for index in unique_indexes.values()
        ]
        return [datum for datum in data if datum is not None]


CustomMenuHelper = Callable[[List[OphydAttributeData]], QtWidgets.QMenu]


class OphydDeviceTableWidget(DesignerDisplay, QtWidgets.QFrame):
    """
    A convenient frame with an embedded OphydDeviceTableView.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget.

    custom_menu_helper : callable, optional
        A callable which creates a drop-down menu for selection of attributes.
        Signature is ``callable(List[OphydAttributeData]) -> QMenu``.

    device : ophyd.Device, optional
        The ophyd device to look at.  May be set later.
    """
    filename = "ophyd_device_tree_widget.ui"

    closed: ClassVar[QtCore.Signal] = QtCore.Signal()
    attributes_selected: ClassVar[QtCore.Signal] = QtCore.Signal(
        list  # List[OphydAttributeData]
    )

    _custom_menu: Optional[QtWidgets.QMenu]
    custom_menu_helper: Optional[CustomMenuHelper]
    label_filter: QtWidgets.QLabel
    edit_filter: QtWidgets.QLineEdit
    device_table_view: OphydDeviceTableView
    button_update_data: QtWidgets.QPushButton
    button_select_attrs: QtWidgets.QPushButton
    button_archive_view: QtWidgets.QPushButton

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        custom_menu_helper: Optional[CustomMenuHelper] = None,
        device: Optional[ophyd.Device] = None
    ):
        super().__init__(parent=parent)

        self._setup_ui()
        self.device = device
        self.custom_menu_helper = custom_menu_helper
        self._custom_menu = None

    def _setup_ui(self):
        """Configure UI elements at init time."""

        def set_filter(text):
            self.device_table_view.proxy_model.setFilterRegExp(text)

        def update_data():
            model = self.device_table_view.current_model
            if model is not None:
                model.start()

        self.device_table_view.context_menu_helper = self._create_context_menu

        self.edit_filter.textEdited.connect(set_filter)
        self.button_update_data.clicked.connect(update_data)
        # For now, we are disabling polling for device tables.  Update at the
        # request of the user.
        self.device_table_view.poll_rate = 0.0

        def disable_button():
            self.button_update_data.setEnabled(False)

        self.device_table_view.data_updates_started.connect(disable_button)

        def enable_button():
            self.button_update_data.setEnabled(True)

        self.device_table_view.data_updates_finished.connect(enable_button)

        def table_selection_changed(
            selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection
        ):
            self.button_select_attrs.setEnabled(bool(len(selected.indexes())))

        self.button_select_attrs.clicked.connect(self._select_attrs_clicked)

        def select_single_attr(index: QtCore.QModelIndex) -> None:
            data = self.device_table_view.get_data_from_proxy_index(index)
            if data is not None:
                self.attributes_selected.emit([data])

        self.device_table_view.doubleClicked.connect(select_single_attr)
        self.device_table_view.attributes_selected.connect(
            self.attributes_selected.emit
        )

        self.button_archive_view.clicked.connect(self._open_archive_viewer)

    def _create_context_menu(self):
        """Handler for when the device table view is right clicked."""
        attrs = self.device_table_view.selected_attribute_data
        if not attrs or not self.custom_menu_helper:
            return QtWidgets.QMenu()

        self._custom_menu = self.custom_menu_helper(attrs)
        return self._custom_menu

    def _select_attrs_clicked(self):
        """Handler for when attributes are selected."""
        attrs = self.device_table_view.selected_attribute_data
        if not attrs:
            return

        if self.custom_menu_helper:
            top_left = self.button_select_attrs.mapToGlobal(QtCore.QPoint(0, 0))
            self._custom_menu = self.custom_menu_helper(attrs)
            self._custom_menu.exec_(top_left)
        else:
            self.attributes_selected.emit(attrs)

    def _open_archive_viewer(self):
        """ Handler for opening Archive Viewer Widget """
        data = self.device_table_view.selected_attribute_data

        arch_widget = get_archive_viewer()
        for datum in data:
            dev_attr = '.'.join((datum.signal.parent.name, datum.attr))
            arch_widget.add_signal(datum.pvname, dev_attr=dev_attr)
        arch_widget.show()

    def closeEvent(self, ev: QtGui.QCloseEvent):
        super().closeEvent(ev)
        self.device_table_view.clear()
        self.closed.emit()

    @property
    def device(self) -> Optional[ophyd.Device]:
        """The currently-configured ophyd Device."""
        return self.device_table_view.device

    @device.setter
    def device(self, device: Optional[ophyd.Device]):
        self.device_table_view.device = device
        if device is not None:
            self.setWindowTitle(device.name)
