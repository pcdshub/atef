"""
Widget classes designed for atef-to-happi interaction.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Dict, List, Optional, Union, cast

import happi
import ophyd
from happi.qt.model import (HappiDeviceListView, HappiDeviceTreeView,
                            HappiViewMixin)
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtWidgets import QTableView, QWidget

from ..qt_helpers import ThreadWorker, copy_to_clipboard
from .core import DesignerDisplay
from .ophyd import OphydDeviceTableWidget

logger = logging.getLogger(__name__)


class HappiSearchWidget(DesignerDisplay, QWidget):
    """
    Happi item (device) search widget.

    This widget includes a list view and a tree view for showing all items
    in happi.

    It provides the following signals:
    * ``happi_items_selected`` - one or more happi items were selected.
    * ``happi_items_chosen`` - one or more happi items were chosen by the user.

    To configure multi-item selection, external configuration of
    ``happi_list_view`` and ``happi_tree_view`` are currently required.

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
    happi_items_chosen: ClassVar[QtCore.Signal] = QtCore.Signal(list)  # List[str]

    _client: Optional[happi.client.Client]
    _last_selected: List[str]
    _search_thread: Optional[ThreadWorker]
    _tree_view_expanded: Optional[List[QtCore.QModelIndex]]
    _tree_current_category: str
    _tree_updated: bool
    button_choose: QtWidgets.QPushButton
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
        self._last_selected = []
        self._tree_current_category = "beamline"
        self._search_thread = None
        self._tree_has_data = False
        self._tree_view_expanded = None
        self._setup_ui()
        # Set the client at the end, as this may trigger an update:
        self.client = client

    def _setup_ui(self):
        """Configure UI elements at init time."""
        self._setup_tree_view()
        self._setup_list_view()

        def record_selected_items(items: List[str]):
            self._last_selected = items

        self.happi_items_selected.connect(record_selected_items)

        def items_chosen():
            self.happi_items_chosen.emit(list(self._last_selected))

        self.button_refresh.clicked.connect(self.refresh_happi)
        self.button_choose.clicked.connect(items_chosen)
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

        def item_double_clicked(index: QtCore.QModelIndex):
            self.happi_items_chosen.emit([index.data()])

        view.doubleClicked.connect(item_double_clicked)

        view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(
            self._list_view_context_menu
        )

        self.edit_filter.textEdited.connect(self._update_filter)

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

        def item_double_clicked(index: QtCore.QModelIndex):
            if index.parent().data() is None:
                return  # skip top-level items

            self.happi_items_chosen.emit([index.data()])

        view.doubleClicked.connect(item_double_clicked)

        view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(self._tree_view_context_menu)

        self.edit_filter.textEdited.connect(self._update_filter)
        view.proxy_model.setRecursiveFilteringEnabled(True)

    def _update_filter(self, text: Optional[str] = None) -> None:
        """Update the list/tree view filters based on the ``edit_filter`` text."""
        if text is None:
            text = self.edit_filter.text()

        text = text.strip()
        self.happi_list_view.proxy_model.setFilterRegExp(text)
        self.happi_tree_view.proxy_model.setFilterRegExp(text)

        # Saves/restores the pre-search expansion state of the root nodes.
        # All root categories are expanded during search for convenience
        if len(text) > 0:
            if self._tree_view_expanded is None:
                model = self.happi_tree_view.proxy_model
                self._tree_view_expanded = [
                    model.index(x, 0) for x in range(model.rowCount())
                    if self.happi_tree_view.isExpanded(model.index(x, 0))
                ]
            self.happi_tree_view.expandAll()
        elif self._tree_view_expanded is not None:
            self.happi_tree_view.collapseAll()
            for index in self._tree_view_expanded:
                self.happi_tree_view.expand(index)
            self._tree_view_expanded = None

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

        if index is not None and index.data() is not None:
            # Add action to add the selected device
            add_action = self.menu.addAction(f'&Add Device: {index.data()}')

            def items_added():
                self.happi_items_chosen.emit([index.data()])

            add_action.triggered.connect(items_added)

            # Add action to copy the text
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
            self._update_filter()

        def report_error(ex: Exception):
            logger.warning("Failed to update happi information: %s", ex, exc_info=ex)
            self.button_refresh.setEnabled(True)

        if self._tree_view_expanded is not None:
            self._tree_view_expanded = []

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

    def search_results_by_key(self, key: str) -> Dict[str, happi.SearchResult]:
        """Cached happi item search results by the provided key."""

        def get_entries():
            for result in self.happi_list_view.entries():
                result = cast(happi.client.SearchResult, result)
                try:
                    yield (result.metadata[key], result)
                except KeyError:
                    continue

        return dict(get_entries())


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

        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)

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

        def get_metadata():
            if self.client is None:
                return {}

            try:
                self.item = self.client[self.item_name]
            except KeyError:
                self.item = None
            return dict(self.item or {})

        self._worker = ThreadWorker(get_metadata)
        self._worker.returned.connect(self._got_metadata)
        self._worker.start()

    def _got_metadata(self, metadata: dict) -> None:
        """Got metadata from the background thread."""
        self._metadata = metadata
        self.updated_metadata.emit(self.item_name, metadata)
        self.model.clear()

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

    @metadata.setter
    def metadata(self, md: Dict[str, Any]) -> None:
        """The current happi item metadata, as a dictionary."""
        self._got_metadata(md)


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

    show_device_components : bool, optional
        Toggle the visibility of the device component tab.  This allows for
        reuse of the HappiDeviceComponentWidget when only the device search
        and happi metadata information are desirable. Defaults to True.
    """
    filename: ClassVar[str] = 'happi_device_component.ui'

    item_search_widget: HappiSearchWidget
    device_widget: OphydDeviceTableWidget
    metadata_widget: HappiItemMetadataView
    _client: Optional[happi.client.Client]
    _device_worker: Optional[ThreadWorker]
    _device_cache: Dict[str, ophyd.Device]
    components_tab: QtWidgets.QWidget
    device_tab_widget: QtWidgets.QTabWidget
    metadata_tab: QtWidgets.QWidget

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        client: Optional[happi.Client] = None,
        show_device_components: bool = True,
    ):
        super().__init__(parent=parent)
        self._client = None
        self._device_worker = None
        self._device_cache = {}
        self.client = client
        self.show_device_components = show_device_components
        self.item_search_widget.happi_items_selected.connect(
            self._new_item_selection
        )
        self.item_search_widget.button_choose.setVisible(False)
        if not self.show_device_components:
            self.device_tab_widget.removeTab(0)
            self.setWindowTitle("Happi Item Search with Metadata")

    @QtCore.Slot(list)
    def _new_item_selection(self, items: List[str]) -> None:
        """New item selected from the happi search."""
        client = self.client
        if client is None or not items:
            return

        def get_device() -> Optional[ophyd.Device]:
            """This happens in a QThread."""
            if client is None:  # static check fail
                return None

            if item in self._device_cache:
                return self._device_cache[item]

            device = search_result.get()
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

        try:
            by_name = self.item_search_widget.search_results_by_key("name")
            search_result = by_name[item]
        except Exception:
            logger.exception("Failed to retrieve happi metadata for %s", item)
            return

        try:
            self.metadata_widget.metadata = search_result.metadata
        except Exception:
            logger.exception("Failed to display happi metadata")

        if not self.show_device_components:
            # User can request to never instantiate a device this way
            return

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
