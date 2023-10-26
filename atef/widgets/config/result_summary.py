"""
Widgets for summarizing the results of a run.
"""
from __future__ import annotations

import dataclasses
from typing import Any, List

from PyQt5.QtCore import QModelIndex
from qtpy import QtCore, QtGui, QtWidgets

from atef.config import PreparedFile
from atef.enums import Severity
from atef.procedure import PreparedProcedureFile
from atef.type_hints import AnyDataclass
from atef.walk import walk_config_file, walk_procedure_file
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import insert_widget


@dataclasses.dataclass
class ResultInfo:
    """ Normalized, and slightly processed view of configs/steps with results """
    status: Severity
    name: str
    reason: str
    origin: AnyDataclass

    @property
    def type(self):
        return type(self.origin).__name__


class ResultModel(QtCore.QAbstractTableModel):
    """
    Item model for results.  Read-Only.
    To be proxied for searching
    """
    result_info: List[ResultInfo]

    def __init__(self, *args, data: List[ResultInfo] = [], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.result_info = data
        self.headers = ['Status', 'Type', 'Name', 'Reason']

    @classmethod
    def from_file(cls, file) -> ResultModel:
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
                    reason=c.result.reason,
                    name=origin.name,
                    origin=origin
                )
                data.append(info)
        elif isinstance(file, PreparedProcedureFile):
            datac = [st_tuple[0] for st_tuple in walk_procedure_file(file.root)]
            data = []
            for s in datac:
                data.append(ResultInfo(
                    status=s.result.severity,
                    reason=s.result.reason,
                    name=s.origin.name,
                    origin=s.origin
                ))

        return cls(data=data)

    def rowCount(self, parent: QtCore.QModelIndex) -> int:
        return len(self.result_info)

    def columnCount(self, parent: QtCore.QModelIndex) -> int:
        return 4

    def data(self, index: QtCore.QModelIndex, role: int) -> Any:
        if not index.isValid():
            return None

        if role == QtCore.Qt.DisplayRole:
            if index.column() == 0:
                return self.result_info[index.row()].status.name
            elif index.column() == 1:
                return self.result_info[index.row()].type
            elif index.column() == 2:
                return self.result_info[index.row()].name
            elif index.column() == 3:
                return self.result_info[index.row()].reason

    def headerData(
        self, section: int, orientation: QtCore.Qt.Orientation, role: int
    ) -> Any:
        if role != QtCore.Qt.DisplayRole:
            return

        if orientation == QtCore.Qt.Horizontal:
            return self.headers[section]

    def dclass_types(self) -> List[str]:
        return set(res.type for res in self.result_info)


class CheckableComboBox(QtWidgets.QComboBox):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)

        # make the line edit look more like a button
        palette = QtWidgets.QApplication.palette()
        palette.setBrush(QtGui.QPalette.Base, palette.button())
        self.lineEdit().setPalette(palette)

        # manage click events on line edit
        self.lineEdit().installEventFilter(self)
        self.close_on_edit_click = False

        self.model().itemChanged.connect(self.update_text)

    def eventFilter(self, object, event):

        if object == self.lineEdit():
            if event.type() == QtCore.QEvent.MouseButtonRelease:
                if self.close_on_edit_click:
                    self.hidePopup()
                else:
                    self.showPopup()
                return True
            return False

        return False

    def showPopup(self):
        super().showPopup()
        # When the popup is displayed, a click on the lineedit should close it
        self.close_on_edit_click = True

    def hidePopup(self):
        super().hidePopup()
        # Used to prevent immediate reopening when clicking on the lineEdit
        self.startTimer(100)
        # Refresh the display text when closing
        self.update_text()
        self.close_on_edit_click = False

    def addItem(self, item):
        super(CheckableComboBox, self).addItem(item)
        item = self.model().item(self.count() - 1, 0)
        item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        item.setCheckState(QtCore.Qt.Checked)

    def itemChecked(self, index):
        item = self.model().item(index, 0)
        return item.checkState() == QtCore.Qt.Checked

    def update_text(self) -> None:
        """ if we have items, make text show all of them """
        items = []
        for i in range(self.model().rowCount()):
            item = self.model().item(i, 0)
            if item.checkState() == QtCore.Qt.Checked:
                items.append(item.text())

        if len(items) > 3:
            self.lineEdit().setText(f'[{len(items)}] types shown')
        else:
            self.lineEdit().setText(', '.join(items))


class ResultFilterProxyModel(QtCore.QSortFilterProxyModel):
    """
    Filter proxy model specifically for ResultModel.
    Combines multiple filter conditions
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

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        name_ok, reason_ok, type_ok, status_ok = True, True, True, True

        name_index = self.sourceModel().index(source_row, 2, source_parent)
        name = self.sourceModel().data(name_index, QtCore.Qt.DisplayRole)
        name_ok = self.name_regexp.match(name).hasMatch()

        reason_index = self.sourceModel().index(source_row, 3, source_parent)
        reason = self.sourceModel().data(reason_index, QtCore.Qt.DisplayRole)
        reason_ok = self.reason_regexp.match(reason).hasMatch()

        if self.allowed_types:
            type_index = self.sourceModel().index(source_row, 1, source_parent)
            type_data = self.sourceModel().data(type_index, QtCore.Qt.DisplayRole)
            type_ok = type_data in self.allowed_types

        if self.allowed_statuses:
            status_index = self.sourceModel().index(source_row, 0, source_parent)
            status_data = self.sourceModel().data(status_index, QtCore.Qt.DisplayRole)
            status_ok = status_data in self.allowed_statuses

        return name_ok and reason_ok and type_ok and status_ok


class ResultsSummaryWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Widget for showing results summary
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

    def __init__(self, *args, file: Any, **kwargs):
        super().__init__(*args, **kwargs)
        self.file = file
        self.setup_ui()
        self.status_map = {'success': self.success_check,
                           'warning': self.warning_check,
                           'error': self.error_check,
                           'internal_error': self.error_check}

    def setup_ui(self) -> None:
        self.model = ResultModel.from_file(self.file)
        self.proxy_model = ResultFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.results_table.setModel(self.proxy_model)
        self.results_table.setSortingEnabled(True)
        self.results_table.horizontalHeader().setStretchLastSection(True)

        self.success_check.setChecked(True)
        self.warning_check.setChecked(True)
        self.error_check.setChecked(True)

        # setup type combo box
        self.type_combo = CheckableComboBox()
        insert_widget(self.type_combo, self.type_combo_placeholder)
        # Fill combo box with types
        for dclass_type in self.model.dclass_types():
            self.type_combo.addItem(dclass_type)

        # connect filter widgets
        self.name_edit.textChanged.connect(self.filters_changed)
        self.reason_edit.textChanged.connect(self.filters_changed)
        self.type_combo.model().itemChanged.connect(self.filters_changed)
        self.success_check.stateChanged.connect(self.filters_changed)
        self.warning_check.stateChanged.connect(self.filters_changed)
        self.error_check.stateChanged.connect(self.filters_changed)

    def filters_changed(self, *args, **kwargs) -> None:
        """ Update all the filters on the proxy model """
        self.proxy_model.name_regexp.setPattern(self.name_edit.text())
        self.proxy_model.reason_regexp.setPattern(self.reason_edit.text())
        self.proxy_model.allowed_statuses = self.get_allowed_statuses()
        self.proxy_model.allowed_types = self.get_allowed_types()
        self.proxy_model.invalidateFilter()
        self.update_plain_text()

    def get_allowed_statuses(self) -> List[str]:
        return [status_key for status_key, status_widget
                in self.status_map.items()
                if status_widget.isChecked()]

    def get_allowed_types(self) -> List[str]:
        allowed_types = []
        for i in range(self.type_combo.model().rowCount()):
            item = self.type_combo.model().item(i)
            if item.checkState() == QtCore.Qt.Checked:
                allowed_types.append(item.text())

        return allowed_types

    def update_plain_text(self) -> None:
        text = 'status, type, name, reason\n'
        for i in range(self.proxy_model.rowCount()):
            row = []
            for j in range(self.proxy_model.columnCount()):
                index = self.proxy_model.index(i, j)
                row.append(self.proxy_model.data(index, QtCore.Qt.DisplayRole))

            text += ', '.join(row)
            text += '\n'

        self.results_text.setText(text)
