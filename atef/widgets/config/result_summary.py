"""
Widgets for summarizing the results of a run.
"""
from __future__ import annotations

import csv
import dataclasses
from typing import Any, List, Optional, Set, Union

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt

from atef.config import PreparedFile
from atef.enums import Severity
from atef.procedure import PreparedProcedureFile
from atef.type_hints import AnyDataclass
from atef.walk import walk_config_file, walk_procedure_file
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import insert_widget


@dataclasses.dataclass
class ResultInfo:
    """Normalized, and slightly processed view of configs/steps with results"""
    status: Severity
    reason: str

    # An un-prepared dataclass, to match the tree views (will not hold the result)
    origin: AnyDataclass

    @property
    def type(self) -> str:
        return type(self.origin).__name__

    @property
    def name(self) -> str:
        return getattr(self.origin, 'name', '')


class ResultModel(QtCore.QAbstractTableModel):
    """
    Item model for results.  Read-Only.
    To be proxied for searching
    """
    result_info: List[ResultInfo]
    result_icon_map = {
        # check mark
        Severity.success: ('\u2713', QtGui.QColor(0, 128, 0, 255)),
        Severity.warning : ('?', QtGui.QColor(255, 165, 0, 255)),
        # x mark
        Severity.internal_error: ('\u2718', QtGui.QColor(255, 0, 0, 255)),
        Severity.error: ('\u2718', QtGui.QColor(255, 0, 0, 255)),
        'N/A': ('nothing', QtGui.QColor())
    }

    def __init__(
        self,
        *args,
        data: Optional[List[ResultInfo]] = None,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.result_info = data or []
        self.headers = ['Status', 'Type', 'Name', 'Reason']

    @classmethod
    def from_file(
        cls,
        file: Union[PreparedFile, PreparedProcedureFile]
    ) -> ResultModel:
        """ Build this model from a PreparedFile which contains results"""
        if isinstance(file, PreparedFile):
            datac = [cfg_tuple[0] for cfg_tuple in walk_config_file(file.root)]
            data = []
            for c in datac:
                origin = getattr(c, 'config', None) or getattr(c, 'comparison', None)
                if origin is None:
                    raise ValueError('could not find origin of passive component')
                info = ResultInfo(
                    status=c.result.severity,
                    reason=c.result.reason or '',
                    origin=origin
                )
                data.append(info)
        elif isinstance(file, PreparedProcedureFile):
            datac = [st_tuple[0] for st_tuple in walk_procedure_file(file.root)]
            data = []
            for s in datac:
                origin = getattr(s, 'origin', None) or getattr(s, 'comparison', None)
                if origin is None:
                    raise ValueError('could not find origin of active component')
                data.append(ResultInfo(
                    status=s.result.severity,
                    reason=s.result.reason or '',
                    origin=origin
                ))

        return cls(data=data)

    def rowCount(
        self,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> int:
        """
        Returns the number of rows in the model

        Parameters
        ----------
        parent : QtCore.QModelIndex
            The index of the parent to find rows under.  Invalid for tables.

        Returns
        -------
        int
            the number of rows in the model under ``parent``
        """
        # qt docs told me to
        if parent.isValid():
            return 0
        return len(self.result_info)

    def columnCount(
        self,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> int:
        """
        Returns the number of columns in the model.  Independent of parent.

        Parameters
        ----------
        parent : QtCore.QModelIndex
            The index of the parent to find columns for.  Invalid for tables.

        Returns
        -------
        int
            the number of rows in the model under ``parent``
        """
        if parent.isValid():
            return 0
        return 4

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = Qt.DisplayRole
    ) -> Any:
        """
        Return data from the model, depending on the provided role

        Parameters
        ----------
        index : QtCore.QModelIndex
            The index for the desired data
        role : int
            The data role

        Returns
        -------
        Any
        """
        if not index.isValid():
            return None

        if role == Qt.DisplayRole:
            if index.column() == 0:
                sev = self.result_info[index.row()].status
                return f'[{self.result_icon_map[sev][0]}] {sev.name}'
            elif index.column() == 1:
                return self.result_info[index.row()].type
            elif index.column() == 2:
                return self.result_info[index.row()].name
            elif index.column() == 3:
                return self.result_info[index.row()].reason

        if role == Qt.ForegroundRole:
            if index.column() == 0:
                brush = QtGui.QBrush()
                brush.setColor(
                    self.result_icon_map[self.result_info[index.row()].status][1]
                )
                return brush

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole
    ) -> Any:
        """
        Returns the data for the given ``role`` and ``section`` in the header with
        the specified ``orientation``.

        Parameters
        ----------
        section : int
            The column number for horizontal headers, or row number for vertical
        orientation : Qt.Orientation
            Qt.Horizontal | Qt.Vertical
        role : int
            the display role

        Returns
        -------
        Any
            the header data
        """
        if role != Qt.DisplayRole:
            return

        if orientation == Qt.Horizontal:
            return self.headers[section]

    def dclass_types(self) -> Set[str]:
        """Returns a set of the dataclass types stored in the model"""
        return set(res.type for res in self.result_info)


class CheckableComboBox(QtWidgets.QComboBox):
    """A QComboBox that allows for multiple selection via checkable items"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # make the text programmatically, but not manually editable
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.model().itemChanged.connect(self.update_text)

        # make the line edit look more like a button
        palette = QtWidgets.QApplication.palette()
        palette.setBrush(QtGui.QPalette.Base, palette.button())
        self.lineEdit().setPalette(palette)

        # manage click events on line edit
        self.lineEdit().installEventFilter(self)
        self.popup_is_open = False

        # Click events on item
        self.view().pressed.connect(self.item_pressed)

    def eventFilter(self, object, event):
        if object == self.lineEdit():
            if event.type() == QtCore.QEvent.MouseButtonRelease:
                if self.popup_is_open:
                    self.hidePopup()
                else:
                    self.showPopup()
                return True
            return False

        return False

    def showPopup(self):
        """Show popup if it's not open, and remember"""
        super().showPopup()
        self.popup_is_open = True

    def hidePopup(self):
        """Close popup and update the text"""
        super().hidePopup()
        # Used to prevent immediate reopening when clicking on the lineEdit
        self.startTimer(100)
        # Refresh the display text when closing
        self.update_text()
        self.popup_is_open = False

    def addItem(self, text: str, userData: Any = ...) -> None:
        """Adds an item with the given ``text`` and makes it checkable"""
        super(CheckableComboBox, self).addItem(text)
        item: QtGui.QStandardItem = self.model().item(self.count() - 1, 0)
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked)

    def item_pressed(self, index: QtCore.QModelIndex) -> None:
        """
        Handler for toggling check state when clicking on the item itself

        Parameters
        ----------
        index : QtCore.QModelIndex
            index of clicked item
        """
        item: QtGui.QStandardItem = self.model().item(index.row(), 0)
        if item.checkState() == Qt.Unchecked:
            item.setCheckState(Qt.Checked)
        else:
            item.setCheckState(Qt.Unchecked)

    def update_text(self) -> None:
        """Show a summary of selected items in the QComboBox LineEdit"""
        items = []
        for i in range(self.model().rowCount()):
            item = self.model().item(i, 0)
            if item.checkState() == Qt.Checked:
                items.append(item.text())

        if len(items) > 3 or len(items) == 0:
            self.lineEdit().setText(f'[{len(items)}] items shown')
        else:
            self.lineEdit().setText(', '.join(items))


class ResultFilterProxyModel(QtCore.QSortFilterProxyModel):
    """
    Filter proxy model specifically for ResultModel.
    Combines multiple filter conditions.
    """

    name_regexp: QtCore.QRegularExpression
    reason_regexp: QtCore.QRegularExpression

    allowed_types: List[str]
    allowed_statuses: List[str]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.name_regexp = QtCore.QRegularExpression()
        self.reason_regexp = QtCore.QRegularExpression()
        self.allowed_types = []
        self.allowed_statuses = []

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QtCore.QModelIndex
    ) -> bool:
        name_ok, reason_ok, type_ok, status_ok = True, True, False, False

        name_index = self.sourceModel().index(source_row, 2, source_parent)
        name = self.sourceModel().data(name_index, Qt.DisplayRole)
        name_ok = self.name_regexp.match(name).hasMatch()

        reason_index = self.sourceModel().index(source_row, 3, source_parent)
        reason = self.sourceModel().data(reason_index, Qt.DisplayRole)
        reason_ok = self.reason_regexp.match(reason).hasMatch()

        if self.allowed_types:
            type_index = self.sourceModel().index(source_row, 1, source_parent)
            type_data = self.sourceModel().data(type_index, Qt.DisplayRole)
            type_ok = type_data in self.allowed_types

        if self.allowed_statuses:
            status_index = self.sourceModel().index(source_row, 0, source_parent)
            status_data = self.sourceModel().data(status_index, Qt.DisplayRole)
            status_ok = any([allowed_stat in status_data
                             for allowed_stat in self.allowed_statuses])

        return name_ok and reason_ok and type_ok and status_ok


class ResultsSummaryWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Widget for showing results summary in table and text formats.
    Has options for filtering results by type, name, status, and reason.
    """
    filename = 'results_summary.ui'

    results_table: QtWidgets.QTableView
    results_text: QtWidgets.QTextEdit

    reason_edit: QtWidgets.QLineEdit
    name_edit: QtWidgets.QLineEdit
    type_combo_placeholder: QtWidgets.QWidget
    type_combo: CheckableComboBox

    success_check: QtWidgets.QCheckBox
    warning_check: QtWidgets.QCheckBox
    error_check: QtWidgets.QCheckBox

    refresh_button: QtWidgets.QToolButton
    save_text_button: QtWidgets.QPushButton
    clipboard_button: QtWidgets.QPushButton

    def __init__(self, *args, file: Any, **kwargs):
        super().__init__(*args, **kwargs)
        self.file = file
        self.status_map = {'success': self.success_check,
                           'warning': self.warning_check,
                           'error': self.error_check,
                           'internal_error': self.error_check}
        self.setup_ui()
        self.filters_changed()

    def setup_ui(self) -> None:
        """Set up slots and do initial ui setup"""
        self.model = ResultModel.from_file(self.file)
        self.proxy_model = ResultFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.results_table.setModel(self.proxy_model)
        self.results_table.setSortingEnabled(True)
        self.results_table.horizontalHeader().setStretchLastSection(True)

        # setup type combo box
        self.type_combo = CheckableComboBox()
        insert_widget(self.type_combo, self.type_combo_placeholder)

        # connect filter widgets
        self.name_edit.textChanged.connect(self.filters_changed)
        self.reason_edit.textChanged.connect(self.filters_changed)
        self.type_combo.model().itemChanged.connect(self.filters_changed)
        self.success_check.stateChanged.connect(self.filters_changed)
        self.warning_check.stateChanged.connect(self.filters_changed)
        self.error_check.stateChanged.connect(self.filters_changed)

        # setup refresh button
        refresh_icon = self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload)
        self.refresh_button.setIcon(refresh_icon)
        self.refresh_button.clicked.connect(self.refresh_results)

        # setup save buttons
        self.save_text_button.clicked.connect(self.save_text)
        self.clipboard_button.clicked.connect(self.copy_to_clipboard)

        self.refresh_results()

    def filters_changed(self, *args, **kwargs) -> None:
        """Update all the filters on the proxy model"""
        self.proxy_model.name_regexp.setPattern(self.name_edit.text())
        self.proxy_model.reason_regexp.setPattern(self.reason_edit.text())
        self.proxy_model.allowed_statuses = self.get_allowed_statuses()
        self.proxy_model.allowed_types = self.get_allowed_types()
        # signal that the model has been updated
        self.proxy_model.invalidateFilter()
        self.update_plain_text()

    def get_allowed_statuses(self) -> List[str]:
        """Gather allowed status types from the status checkboxes"""
        return [status_key for status_key, status_widget
                in self.status_map.items()
                if status_widget.isChecked()]

    def get_allowed_types(self) -> List[str]:
        """Gather the allowed types from the checkable combo box"""
        allowed_types = []
        for i in range(self.type_combo.model().rowCount()):
            item = self.type_combo.model().item(i)
            if item.checkState() == Qt.Checked:
                allowed_types.append(item.text())

        return allowed_types

    def get_plain_text(self) -> str:
        """Generate plain text version of ResultsSummary Table"""
        text = 'status, type, name, reason'
        for i in range(self.proxy_model.rowCount()):
            text += '\n'
            row = []
            for j in range(self.proxy_model.columnCount()):
                index = self.proxy_model.index(i, j)
                data = self.proxy_model.data(index, Qt.DisplayRole) or ''
                if ',' in data:
                    data = f'"{data}"'
                row.append(data)
            if row[0].startswith('['):
                row[0] = row[0][4:]  # strip out icon from plain text
            text += ','.join(row)
        return text

    def update_plain_text(self) -> None:
        """Slot for updating plain text tab"""
        text = self.get_plain_text()
        self.results_text.setText(text)

    def refresh_results(self) -> None:
        """
        Re-initialize the model with the file.  Also refreshes the type combo
        box for good measure, despite this getting destroyed on RunTree refresh
        """
        self.model = ResultModel.from_file(self.file)
        self.proxy_model = ResultFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.results_table.setModel(self.proxy_model)

        # reset all the settings/filters
        self.name_edit.setText('')
        self.reason_edit.setText('')

        self.success_check.setChecked(False)
        self.warning_check.setChecked(True)
        self.error_check.setChecked(True)

        # re-setup type combo box
        self.type_combo.clear()
        # Fill combo box with types
        for dclass_type in self.model.dclass_types():
            self.type_combo.addItem(dclass_type)

    def save_text(self) -> None:
        """Save the csv representation of the filtered results table"""
        text = self.get_plain_text().split('\n')
        if not text:
            return

        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption='Select a filename',
            filter='CSV Files (*.csv)',
        )
        if not filename.endswith('.csv'):
            filename += '.csv'

        with open(filename, 'w') as fd:
            writer = csv.writer(fd)
            for row in text:
                writer.writerow(row.split(',', 3))

    def copy_to_clipboard(self) -> None:
        """Copy the plain-text results table to the clipboard"""
        text = self.get_plain_text()
        if not text:
            return

        QtGui.QGuiApplication.clipboard().setText(text)

        self._msg = QtWidgets.QMessageBox(parent=self)
        self._msg.setText(
            'Plain-text results copied to clipboard.',
        )
        self._msg.setWindowTitle('Results Copied!')
        self._msg.exec()
