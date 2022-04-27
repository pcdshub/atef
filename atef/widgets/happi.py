"""
Widget classes designed for atef-to-happi interaction.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Dict, List, Optional, Union

import happi
import ophyd
from happi.qt.model import (HappiDeviceListView, HappiDeviceTreeView,
                            HappiViewMixin)
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtWidgets import QWidget

from ..qt_helpers import ThreadWorker, copy_to_clipboard
from .core import DesignerDisplay
from .ophyd import OphydDeviceTableWidget

logger = logging.getLogger(__name__)


class HappiSearchWidget(DesignerDisplay, QWidget):
    """
    Happi item (device) search widget.

    This widget includes a list view and a tree view for showing all items
    in happi.  It provides a signal ``happi_items_selected`` such that external
    widgets can use this to monitor for the selection of one (or more) items.

    To configure multi-item selection, external configuration of
    happi_list_view and happi_tree_view are currently required.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget.

    client : happi.Client, optional
        Happi client instance.  May be supplied at initialization time or
        later.
    """
    filename: ClassVar[str] = 'happi_search_widget.ui'
    happi_items_selected: ClassVar[QtCore.Signal] = QtCore.Signal(list)  # List[str]

    _client: Optional[happi.client.Client]
    _search_thread: Optional[ThreadWorker]
    _tree_current_category: str
    _tree_updated: bool
    button_refresh: QtWidgets.QPushButton
    combo_by_category: QtWidgets.QComboBox
    device_selection_group: QtWidgets.QGroupBox
    edit_filter: QtWidgets.QLineEdit
    happi_list_view: HappiDeviceListView
    happi_tree_view: HappiDeviceTreeView
    label_filter: QtWidgets.QLabel
    layout_by_name: QtWidgets.QHBoxLayout
    list_or_tree_frame: QtWidgets.QFrame
    radio_by_category: QtWidgets.QRadioButton
    radio_by_name: QtWidgets.QRadioButton

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        client: Optional[happi.Client] = None,
    ):
        super().__init__(parent=parent)
        self._client = None
        self._tree_current_category = "beamline"
        self._search_thread = None
        self._tree_has_data = False
        self._setup_ui()
        # Set the client at the end, as this may trigger an update:
        self.client = client

    def _setup_ui(self):
        """Configure UI elements at init time."""
        self._setup_tree_view()
        self._setup_list_view()

        self.button_refresh.clicked.connect(self.refresh_happi)
        self.list_or_tree_frame.layout().insertWidget(0, self.happi_list_view)

        self.radio_by_name.clicked.connect(self._select_device_widget)
        self.radio_by_category.clicked.connect(self._select_device_widget)
        self.combo_by_category.currentTextChanged.connect(self._category_changed)
        self.button_refresh.clicked.emit()

    def _setup_list_view(self):
        """Set up the happi_list_view."""
        def list_selection_changed(
            selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection
        ):
            self.happi_items_selected.emit([idx.data() for idx in selected.indexes()])

        view = self.happi_list_view
        view.selectionModel().selectionChanged.connect(
            list_selection_changed
        )

        view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(
            self._list_view_context_menu
        )

        def set_filter(text):
            self.happi_list_view.proxy_model.setFilterRegExp(text)

        self.edit_filter.textEdited.connect(set_filter)

    def _setup_tree_view(self):
        """Set up the happi_tree_view."""
        view = self.happi_tree_view
        view.setVisible(False)
        view.groups = [
            self.combo_by_category.itemText(idx)
            for idx in range(self.combo_by_category.count())
        ]
        self.list_or_tree_frame.layout().insertWidget(0, view)

        def tree_selection_changed(
            selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection
        ):
            items = [
                idx.data() for idx in selected.indexes()
                if idx.parent().data() is not None  # skip top-level items
            ]
            self.happi_items_selected.emit(items)

        view.selectionModel().selectionChanged.connect(
            tree_selection_changed
        )

        view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(self._tree_view_context_menu)

        def set_filter(text):
            self.happi_tree_view.proxy_model.setFilterRegExp(text)

        self.edit_filter.textEdited.connect(set_filter)
        view.proxy_model.setRecursiveFilteringEnabled(True)

    def _tree_view_context_menu(self, pos: QtCore.QPoint) -> None:
        """Context menu for the happi tree view."""
        self.menu = QtWidgets.QMenu(self)
        index: QtCore.QModelIndex = self.happi_tree_view.indexAt(pos)
        if index is not None:
            def copy(*_):
                copy_to_clipboard(index.data())

            copy_action = self.menu.addAction(f"&Copy: {index.data()}")
            copy_action.triggered.connect(copy)

        self.menu.exec_(self.happi_tree_view.mapToGlobal(pos))

    def _list_view_context_menu(self, pos: QtCore.QPoint) -> None:
        """Context menu for the happi list view."""
        self.menu = QtWidgets.QMenu(self)
        index: QtCore.QModelIndex = self.happi_list_view.indexAt(pos)
        if index is not None:
            def copy(*_):
                copy_to_clipboard(index.data())

            copy_action = self.menu.addAction(f"&Copy: {index.data()}")
            copy_action.triggered.connect(copy)

        self.menu.exec_(self.happi_list_view.mapToGlobal(pos))

    @property
    def selected_device_widget(self) -> Union[HappiDeviceListView, HappiDeviceTreeView]:
        """The selected device widget - either the list or tree view."""
        if self.radio_by_name.isChecked():
            return self.happi_list_view

        return self.happi_tree_view

    @QtCore.Slot(str)
    def _category_changed(self, category: str):
        """By-category category has changed."""
        if self._tree_has_data and self._tree_current_category == category:
            return

        self._tree_current_category = category
        self.happi_tree_view.group_by(category)
        # Bugfix (?) otherwise this ends up in descending order
        self.happi_tree_view.model().sort(0, QtCore.Qt.AscendingOrder)
        self.radio_by_category.setChecked(True)
        self._select_device_widget()

    @QtCore.Slot()
    def _select_device_widget(self):
        """Switch between the list/table view."""
        selected = self.selected_device_widget
        for widget in (self.happi_tree_view, self.happi_list_view):
            widget.setVisible(selected is widget)

        if self.happi_tree_view.isVisible() and not self._tree_has_data:
            self._tree_has_data = True
            self.refresh_happi()

    @QtCore.Slot()
    def refresh_happi(self):
        """Search happi again and update the widgets."""
        def search():
            # TODO/upstream: this is coupled with 'search' in the view
            HappiViewMixin.search(self.selected_device_widget)

        def update_gui():
            # TODO/upstream: this is coupled with 'search' in the view
            self.selected_device_widget._update_data()
            self.button_refresh.setEnabled(True)

        def report_error(ex: Exception):
            logger.warning("Failed to update happi information: %s", ex, exc_info=ex)
            self.button_refresh.setEnabled(True)

        if self._client is None:
            return
        if self._search_thread is not None and self._search_thread.isRunning():
            return

        self.button_refresh.setEnabled(False)
        self._search_thread = ThreadWorker(search)
        self._search_thread.finished.connect(update_gui)
        self._search_thread.error_raised.connect(report_error)
        self._search_thread.start()

    @property
    def client(self) -> Optional[happi.Client]:
        """The client to use for search."""
        return self._client

    @client.setter
    def client(self, client: Optional[happi.Client]):
        self._client = client
        self.happi_tree_view.client = client
        self.happi_list_view.client = client
        self.refresh_happi()


class HappiItemMetadataView(DesignerDisplay, QtWidgets.QWidget):
    """
    Happi item (device) metadata information widget.

    This widget contains a table that displays key and value information
    as provided from the happi client.

    The default context menu allows for copying of keys or values.

    It emits an ``updated_metadata(item_name: str, md: dict)`` when the
    underlying model is updated.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget.

    client : happi.Client, optional
        Happi client instance.  May be supplied at initialization time or
        later.
    """
    filename: ClassVar[str] = 'happi_metadata_view.ui'
    updated_metadata: ClassVar[QtCore.Signal] = QtCore.Signal(str, object)

    _client: Optional[happi.client.Client]
    _item_name: Optional[str]
    item: Optional[happi.HappiItem]
    label_title: QtWidgets.QLabel
    model: QtGui.QStandardItemModel
    proxy_model: QtCore.QSortFilterProxyModel
    table_view: QtWidgets.QTableView
    _metadata: Dict[str, Any]

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        client: Optional[happi.Client] = None,
        item_name: Optional[str] = None,
    ):
        super().__init__(parent=parent)
        self._client = None
        self._item_name = None
        self._item = None
        self._setup_ui()
        # Set the client/item at the end, as this may trigger an update:
        self.client = client
        self.item_name = item_name

    def _setup_ui(self):
        """Configure UI elements at init time."""
        self.model = QtGui.QStandardItemModel()

        self.proxy_model = QtCore.QSortFilterProxyModel()
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setDynamicSortFilter(True)
        self.proxy_model.setSourceModel(self.model)
        self.table_view.setModel(self.proxy_model)

        self.table_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._table_context_menu)

    def _table_context_menu(self, pos: QtCore.QPoint) -> None:
        """Context menu when the key/value table is right-clicked."""
        self.menu = QtWidgets.QMenu(self)
        index: QtCore.QModelIndex = self.table_view.indexAt(pos)
        if index is not None:
            def copy(*_):
                copy_to_clipboard(index.data())

            copy_action = self.menu.addAction(f"&Copy: {index.data()}")
            copy_action.triggered.connect(copy)

        self.menu.exec_(self.table_view.mapToGlobal(pos))

    def _update_metadata(self):
        """Update the metadata based on ``self.item_name`` using the configured client."""
        if self.client is None or self.item_name is None:
            return

        try:
            self.item = self.client[self.item_name]
        except KeyError:
            self.item = None

        metadata = dict(self.item or {})
        self._metadata = metadata
        self.updated_metadata.emit(self.item_name, metadata)
        self.model.clear()
        if self.item is None:
            self.label_title.setText("")
            return

        self.label_title.setText(metadata["name"])
        self.model.setHorizontalHeaderLabels(["Key", "Value"])
        skip_keys = {"_id", "name"}
        for key, value in sorted(metadata.items()):
            if key in skip_keys:
                continue

            key_item = QtGui.QStandardItem(str(key))
            value_item = QtGui.QStandardItem(str(value))
            key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.model.appendRow([key_item, value_item])

    @property
    def client(self) -> Optional[happi.Client]:
        """The client to use for search."""
        return self._client

    @client.setter
    def client(self, client: Optional[happi.Client]):
        self._client = client
        self._update_metadata()

    @property
    def item_name(self) -> Optional[str]:
        """The item name to search for metadata."""
        return self._item_name

    @item_name.setter
    def item_name(self, item_name: Optional[str]):
        self._item_name = item_name
        self._update_metadata()

    @property
    def metadata(self) -> Dict[str, Any]:
        """The current happi item metadata, as a dictionary."""
        return dict(self._metadata)


class HappiDeviceComponentWidget(DesignerDisplay, QWidget):
    """
    Happi item (device) search widget + component view and selection.

    This is comprised of a :class:`HappiSearchWidget`, which allow for happi
    item searching and selection, a :class:`HappiItemMetadataView`, and an
    :class:`OphydDeviceTableWidget` which shows control system-connected
    information about the device and its components.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget.

    client : happi.Client, optional
        Happi client instance.  One will be created using ``from_config`` if
        not supplied.
    """
    filename: ClassVar[str] = 'happi_device_component.ui'

    item_search_widget: HappiSearchWidget
    device_widget: OphydDeviceTableWidget
    metadata_widget: HappiItemMetadataView
    _client: Optional[happi.client.Client]
    _device_worker: Optional[ThreadWorker]
    _device_cache: Dict[str, ophyd.Device]

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        client: Optional[happi.Client] = None,
    ):
        super().__init__(parent=parent)
        self._client = None
        self._device_worker = None
        self._device_cache = {}
        self.client = client
        # self._setup_ui()
        self.item_search_widget.happi_items_selected.connect(
            self._new_item_selection
        )

    @QtCore.Slot(list)
    def _new_item_selection(self, items: List[str]) -> None:
        """New item selected from the happi search."""
        client = self.client
        if not client or not items:
            return

        def get_device() -> Optional[ophyd.Device]:
            """This happens in a QThread."""
            if client is None:  # static check fail
                return None

            if item in self._device_cache:
                return self._device_cache[item]

            container = client[item]
            device = container.get()
            self._device_cache[item] = device
            return device

        def get_device_error(ex: Exception):
            """Handler for when ``get_device`` failed in the background thread."""
            logger.error("Failed to instantiate device %s: %s", item, ex)
            logger.debug("Failed to instantiate device %s: %s", item, ex, exc_info=ex)

        def set_device(device: Optional[ophyd.Device] = None):
            """Handler for when ``get_device`` succeeds in the background thread."""
            # Any GUI-related handling should happen here.
            self.device_widget.device = device

        if self._device_worker is not None and self._device_worker.isRunning():
            return

        item, *_ = items

        # Set metadata early, even if instantiation fails
        self.metadata_widget.item_name = item

        worker = ThreadWorker(get_device)
        self._device_worker = worker
        worker.returned.connect(set_device)
        worker.error_raised.connect(get_device_error)
        worker.start()

    @property
    def client(self) -> Optional[happi.Client]:
        """The client to use for search."""
        return self._client

    @client.setter
    def client(self, client: Optional[happi.Client]):
        self._client = client
        self.item_search_widget.client = client
        self.metadata_widget.client = client
