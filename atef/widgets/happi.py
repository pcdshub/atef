"""
Widget classes designed for atef-to-happi interaction.
"""

from __future__ import annotations

from typing import Optional, Union

import happi
from happi.qt import HappiDeviceListView
from happi.qt.model import HappiDeviceTreeView
from qtpy import QtCore, QtWidgets
from qtpy.QtWidgets import QWidget

from .core import DesignerDisplay


class HappiSearchWidget(DesignerDisplay, QWidget):
    filename = 'happi_search_widget.ui'

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

    @property
    def selected_device_widget(self) -> Union[HappiDeviceListView, HappiDeviceTreeView]:
        """The selected device widget - either the list or tree view."""
        if self.radio_by_name.isChecked():
            return self.happi_list_view

        if self.happi_tree_view is None:
            self.happi_tree_view = HappiDeviceTreeView(client=self.client)
            # self.happi_tree_view.setSortingEnabled(True)
            self.happi_tree_view.search()
            self.happi_tree_view.groups = [
                self.combo_by_category.itemText(idx)
                for idx in range(self.combo_by_category.count())
            ]
            self._category_changed(self.combo_by_category.currentText())
            self.list_or_tree_frame.layout().insertWidget(0, self.happi_tree_view)

        return self.happi_tree_view

    @QtCore.Slot(str)
    def _category_changed(self, category: str):
        if self.happi_tree_view is None:
            return

        self.happi_tree_view.group_by(category)
        # Bugfix (?) otherwise this ends up in descending order
        self.happi_tree_view.proxy_model.sort(0, QtCore.Qt.AscendingOrder)

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
        self.selected_device_widget.search()
