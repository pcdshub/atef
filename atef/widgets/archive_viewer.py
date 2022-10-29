"""
Widget classes designed for PV archiver interaction
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, List, Optional

from pydm.widgets.archiver_time_plot import PyDMArchiverTimePlot
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QRegularExpression
from qtpy.QtGui import QRegularExpressionValidator
from qtpy.QtWidgets import QWidget

from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)
archive_viewer_singleton = None


def get_archive_viewer() -> ArchiverViewerWidget:
    """
    Only allow one viewer to be open at a time.
    Makes it unambiguous whether where to send PV's to.

    Returns
    -------
    ArchiveViewerWidget
        the widget instance
    """
    if archive_viewer_singleton:
        return archive_viewer_singleton
    else:
        return ArchiverViewerWidget()


class ArchiverViewerWidget(DesignerDisplay, QWidget):
    """
    Archiver time plot viewer
    """
    filename: ClassVar[str] = 'archive_viewer_widget.ui'

    time_plot: PyDMArchiverTimePlot
    button_month: QtWidgets.QPushButton
    button_week: QtWidgets.QPushButton
    button_day: QtWidgets.QPushButton
    input_field: QtWidgets.QLineEdit
    curve_list: QtWidgets.QTableView
    redraw_button: QtWidgets.QPushButton

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        pvs: List[str] = []
    ):
        super().__init__(parent=parent)

        # list_holder = ListHolder(
        #     some_list=list(self.)
        # )
        # self.pv_list = QDataclassList.of_type(str)(
        #     data=,
        #     attr='some_list'
        #     parent=self
        # )
        self._pv_list = pvs
        for pv in pvs:
            self.add_signal(pv)

        # - look for correct archiver url, take one that pings or look for env var
        # - connect curve_list to plot
        # - connect buttons on string lists?
        # - set up validator on QLineEdit
        self._setup_ui()
        self._setup_range_buttons()

    def _setup_ui(self):

        # set up table view
        self.model = PVModel()
        self.curve_list.setModel(self.model)

        # set up delegates
        # Color picker delegate
        self.colorDelegate = ColorDelegate()
        self.curve_list.setItemDelegateForColumn(1, self.colorDelegate)

        # symbol delegate
        self.symbolDelegate = SymbolDelegate()
        self.curve_list.setItemDelegateForColumn(2, self.symbolDelegate)

        # delete button in last column
        self.deleteDelegate = DeleteDelegate()
        # -1 doesn't work sadly
        self.curve_list.setItemDelegateForColumn(3, self.deleteDelegate)
        self.deleteDelegate.delete_request.connect(self.model.removeRow)

        for pv in (self._pv_list or []):
            self.add_signal(pv)

        # set up list selector
        self._setup_pv_selector()
        # pass

    def _setup_range_buttons(self):
        def _set_time_span_fn(s: float):
            """
            Set the time span of the plot.
            Currently only works if view-all has not been selected?...

            Parameters
            ----------
            s : float
                The time span in seconds
            """
            def fn():
                self.time_plot.setTimeSpan(s)
                self.time_plot.updateXAxis()
            return fn

        self.button_day.clicked.connect(_set_time_span_fn(24*60*60))
        self.button_week.clicked.connect(_set_time_span_fn(24*60*60*7))
        self.button_month.clicked.connect(_set_time_span_fn(24*60*60*7*30))

    def _setup_pv_selector(self):
        # inputs get made into list items
        # validate PV form, returnPressed unless valid
        regexp = QRegularExpression(r'^\w+(:\w+)+(\.\w+)*$')
        validator = QRegularExpressionValidator(regexp)
        self.input_field.setValidator(validator)

        def _add_item():
            """ slot for input_field submission """
            # grab and clear text
            pv = self.input_field.text()
            print(f'_add_item: {pv}')

            # TO-DO: Further validation?  Check if PV exists?

            # add item to list
            self.curve_list.model().appendCurve(pv)

        self.input_field.returnPressed.connect(_add_item)

        # self.curve_list.model().rowsInserted.connect(self._update_curves)
        # self.curve_list.model().rowsRemoved.connect(self._update_curves)
        # TO-DO: delete item from list ability
        # TO-DO: contex_menu_policy helper (add, delete, bring to forground?)

    def _update_curves(self):
        # grab all the list items
        pv_data = self.curve_list.model().pvs
        print(pv_data)

        self.time_plot.clearCurves()

        for pv in pv_data:
            self.time_plot.addYChannel(
                y_channel=f'ca://{pv[0]}',
                name=f'{pv[0]}',
                symbol=pv[1]['symbol'],
                color=pv[1]['color'],
                useArchiveData=True
            )

        self.time_plot.setShowLegend(True)

    # TO-DO: crosshair hover over plot
    # TO-DO: scaling / dealing with different scales

    def add_signal(self, pv: str) -> None:
        """
        Add a signal to the widget and update the plots.

        Parameters
        ----------
        pv : str
            the PV to add
        """
        # add item to list
        index_1 = self.model.createIndex(0, 0)
        index_2 = self.model.createIndex(0, 3)
        self.curve_list.model().insertRow(0, index_1)
        # TO-DO: Need to validate the pv's here, ouside of just the input fields
        self.model.pvs[0] = [pv, {'color': QtGui.QColor(255, 0, 0), 'symbol': 'o'}]
        self.model.dataChanged.emit(index_1, index_2)
        self._update_curves()


# class PVModel(QtGui.QStandardItemModel):
class PVModel(QtCore.QAbstractTableModel):
    def __init__(self, *args, pvs=[], **kwargs):
        # standard item model needs to be init with columns and rows
        # fill out here and feed into super
        super().__init__(*args, **kwargs)
        self.pvs: List[List[str, dict]] = pvs or []
        self.headers = ['PV Name', 'color', 'symbol', 'remove']
        # self.setHorizontalHeaderLabels(self.headers)

    def data(self, index, role):
        if index.column() == 0:
            # name column, no edit permissions
            if role == QtCore.Qt.DisplayRole:
                return self.pvs[index.row()][0]
        elif index.column() == 3:
            if role == QtCore.Qt.DisplayRole:
                return 'delete?'
        else:
            # data column.  Each column gets its own data delegate
            if role == QtCore.Qt.DisplayRole:
                name, data = self.pvs[index.row()]
                return data[self.headers[index.column()]]
            if role in (QtCore.Qt.EditRole, QtCore.Qt.BackgroundRole):
                return self.pvs[index.row()][1][self.headers[index.column()]]

    def rowCount(self, index): return len(self.pvs)

    def columnCount(self, index): return len(self.headers)

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int
    ) -> Any:
        if role != QtCore.Qt.DisplayRole:
            return None

        if orientation == QtCore.Qt.Horizontal:
            return self.headers
        else:
            return list(range(len(self.pvs)))

    def flags(self, index):
        if (index.column() != 0):
            return QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsEnabled
        else:
            return QtCore.Qt.ItemIsEnabled

    def removeRow(
        self,
        row: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> bool:
        """ augment existing implementation """
        self.beginRemoveRows(parent, row, row)
        del self.pvs[row]
        self.endRemoveRows()

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        """ Edit the data """
        self.pvs[index.row()][1][self.headers[index.column()]] = value
        # one index changed, so top_left == bottom_right
        self.dataChanged.emit(index, index)
        return True

    def appendCurve(
        self,
        pv_name: str,
        color: QtGui.QColor = QtGui.QColor(255, 0, 0),
        symbol: str = 'o'
    ):
        print(f'appendCurve({pv_name}')
        curr_len = len(self.pvs)
        print(curr_len)
        # index = self.createIndex(0,curr_len)
        index = QtCore.QModelIndex()
        self.beginInsertRows(index, 0, curr_len+1)
        self.pvs.append((pv_name, {'color': color, 'symbol': symbol}))
        # self.dataChanged.emit(index, index)
        self.endInsertRows()
        # self.layoutChanged.emit()

    def insertRow(
        self,
        row: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> bool:
        self.beginInsertRows(
            QtCore.QModelIndex(), row, row + self.rowCount(parent)
        )
        self.pvs.insert(
            row, ['name', {'color': QtGui.QColor(255, 0, 0), 'symbol': 's'}]
        )
        self.endInsertRows()
        return True


class ColorDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        picker = QtWidgets.QColorDialog()
        return picker

    def setModelData(
        self,
        editor: QtWidgets.QColorDialog,
        model: QtCore.QAbstractItemModel,
        index: QtCore.QModelIndex
    ) -> None:
        # no parent allows pop-out
        color = editor.currentColor()
        if color.isValid():
            model.setData(index, color, QtCore.Qt.EditRole)


class SymbolDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        combo = QtWidgets.QComboBox(parent=parent)
        combo.insertItems(0, ['', 'o', 's', 't', 'h'])
        return combo

    def setModelData(
        self,
        editor: QtWidgets.QComboBox,
        model: QtCore.QAbstractItemModel,
        index: QtCore.QModelIndex
    ) -> None:
        value = editor.currentText()
        model.setData(index, value, QtCore.Qt.EditRole)


class DeleteDelegate(QtWidgets.QStyledItemDelegate):
    delete_request = QtCore.Signal(int)

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        del_button = QtWidgets.QPushButton('delete', parent)
        del_button.clicked.connect(
            lambda _, row=index.row(): self.delete_request.emit(row)
        )
        return del_button

    def updateEditorGeometry(
        self,
        editor: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> None:
        editor.setGeometry(option.rect)
