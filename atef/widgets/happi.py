"""
Widget classes designed for atef-to-happi interaction.
"""

from __future__ import annotations

from typing import ClassVar, Optional, Union

import happi
from happi.qt import HappiDeviceListView
from happi.qt.model import HappiDeviceTreeView
from qtpy import QtCore, QtWidgets
from qtpy.QtWidgets import QWidget

from ..qt_helpers import ThreadPoolWorker
from .core import DesignerDisplay


class HappiSearchWidget(DesignerDisplay, QWidget):
    """
    Happi item (device) search widget.

    Parameters
    ----------
    client : happi.Client, optional
        Happi client instance.  One will be created using ``from_config`` if
        not supplied.

    parent : QWidget, optional
        The parent widget.
    """
    filename: ClassVar[str] = 'happi_search_widget.ui'
    happi_items_selected: ClassVar[QtCore.Signal] = QtCore.Signal(list)

    client: happi.client.Client
    combo_by_category: QtWidgets.QComboBox
    device_selection_group: QtWidgets.QGroupBox
    layout_by_name: QtWidgets.QHBoxLayout
    radio_by_category: QtWidgets.QRadioButton
    radio_by_name: QtWidgets.QRadioButton
    list_or_tree_frame: QtWidgets.QFrame
    button_refresh: QtWidgets.QPushButton
    happi_tree_view: Optional[HappiDeviceTreeView]
    happi_list_view: HappiDeviceListView

    def __init__(
        self,
        client: Optional[happi.Client] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent=parent)
        if client is None:
            client = happi.Client.from_config()
        self.client = client
        self._setup_ui()

    def _setup_ui(self):
        self.happi_list_view = HappiDeviceListView(client=self.client)
        self.happi_tree_view = None

        self.button_refresh.clicked.connect(self.refresh_happi)
        self.list_or_tree_frame.layout().insertWidget(0, self.happi_list_view)

        self.radio_by_name.clicked.connect(self._select_device_widget)
        self.radio_by_category.clicked.connect(self._select_device_widget)
        self.combo_by_category.currentTextChanged.connect(self._category_changed)
        self.button_refresh.clicked.emit()

        def list_selection_changed(
            selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection
        ):
            self.happi_items_selected.emit([idx.data() for idx in selected.indexes()])

        self.happi_list_view.selectionModel().selectionChanged.connect(
            list_selection_changed
        )

    def _setup_tree_view(self) -> HappiDeviceTreeView:
        """Set up the happi_tree_view if not already configured."""
        if self.happi_tree_view is not None:
            return self.happi_tree_view

        view = HappiDeviceTreeView(client=self.client)
        view.search()
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

        self.happi_tree_view = view
        return view

    @property
    def selected_device_widget(self) -> Union[HappiDeviceListView, HappiDeviceTreeView]:
        """The selected device widget - either the list or tree view."""
        if self.radio_by_name.isChecked():
            return self.happi_list_view

        if self.happi_tree_view is None:
            view = self._setup_tree_view()
            self._category_changed(self.combo_by_category.currentText())
            return view

        return self.happi_tree_view

    @QtCore.Slot(str)
    def _category_changed(self, category: str):
        """By-category category has changed."""
        if self.happi_tree_view is None:
            return

        self.happi_tree_view.group_by(category)
        # Bugfix (?) otherwise this ends up in descending order
        self.happi_tree_view.model().sort(0, QtCore.Qt.AscendingOrder)

    @QtCore.Slot()
    def _select_device_widget(self):
        """Switch between the list/table view."""
        selected = self.selected_device_widget
        for widget in (self.happi_tree_view, self.happi_list_view):
            if widget is not None:
                widget.setVisible(selected is widget)

    @QtCore.Slot()
    def refresh_happi(self):
        """Search happi again and update the widgets."""
        if False:
            # To be threaded we need to decouple search and GUI updates;
            # this will require some happi.qt.model changes.
            def search():
                self.selected_device_widget.search()

            ThreadPoolWorker.new_thread(search)
        self.selected_device_widget.search()
