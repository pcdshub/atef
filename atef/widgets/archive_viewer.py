"""
Widget classes designed for PV archiver interaction
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Dict, List, Optional

from pydm.widgets.archiver_time_plot import PyDMArchiverTimePlot
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QRegularExpression
from qtpy.QtGui import QRegularExpressionValidator
from qtpy.QtWidgets import QWidget

from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)
archive_viewer_singleton = None
symbol_map = {'None': None, 'circle': 'o', 'square': 's',
              'cross': '+', 'star': 'star'}

style_map = {'solid': QtCore.Qt.SolidLine,
             'dash': QtCore.Qt.DashLine, 'dot': QtCore.Qt.DotLine}


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
            self.model.add_signal(pv)

        # - look for correct archiver url, take one that pings or look for env var
        # - connect curve_list to plot
        # - connect buttons on string lists?
        # - set up validator on QLineEdit
        self._setup_ui()
        self._setup_range_buttons()

    def _setup_ui(self):

        # set up table view for PV info
        self.model = PVModel(parent=self)
        self.curve_list.setModel(self.model)
        horiz_header = self.curve_list.horizontalHeader()
        horiz_header.setSectionResizeMode(horiz_header.ResizeToContents)

        # set up delegates
        # Color picker delegate
        self.colorDelegate = ColorDelegate()
        self.curve_list.setItemDelegateForColumn(1, self.colorDelegate)

        # symbol delegate
        self.symbolDelegate = EnumDelegate(enums=symbol_map)
        self.curve_list.setItemDelegateForColumn(2, self.symbolDelegate)

        # style delegate
        self.styleDelegate = EnumDelegate(enums=style_map)
        self.curve_list.setItemDelegateForColumn(3, self.styleDelegate)

        # delete button in last column
        self.deleteDelegate = DeleteDelegate()
        # -1 doesn't work sadly
        del_col = len(self.model.headers) - 1
        self.curve_list.setItemDelegateForColumn(del_col, self.deleteDelegate)
        self.deleteDelegate.delete_request.connect(self.model.removeRow)

        for pv in (self._pv_list or []):
            self.model.add_signal(pv)

        # set up list selector
        self._setup_pv_selector()

        self.redraw_button.clicked.connect(self._update_curves)
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
        self.button_month.clicked.connect(_set_time_span_fn(24*60*60*30))

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

            # TO-DO: Further validation?  Check if PV exists?

            # add item
            self.add_signal(pv)

        self.input_field.returnPressed.connect(_add_item)

    def _update_curves(self):
        # grab all the list items
        pv_data = self.curve_list.model().pvs

        self.time_plot.clearCurves()
        for pv in pv_data:
            self.time_plot.addYChannel(
                y_channel=f'ca://{pv[0]}',
                name=f'{pv[0]}',
                symbol=pv[1]['symbol'],
                color=pv[1]['color'],
                lineStyle=pv[1]['lineStyle'],
                lineWidth=pv[1]['lineWidth'],
                useArchiveData=True
            )

        try:
            self.time_plot.setLabel('left', text='')
        except Exception:
            # pyqtgraph raises a vanilla exception
            # if a better way to find the left axis name exists, use it
            logger.debug('left axis does not exist to rename')

        self.time_plot.setShowLegend(True)

    # TO-DO: crosshair hover over plot?
    #   -> time_plot.enableCrosshair
    # TO-DO: scaling / dealing with different scales
    # TO-DO: set the units in the label?

    def add_signal(self, pv: str) -> None:
        success = self.model.add_signal(pv)
        if success:
            self._update_curves()
            self.input_field.clear()


class PVModel(QtCore.QAbstractTableModel):
    def __init__(self, *args, pvs=[], **kwargs):
        # standard item model needs to be init with columns and rows
        # fill out here and feed into super
        super().__init__(*args, **kwargs)
        self.pvs: List[List[str, dict]] = pvs or []
        self.headers = ['PV Name', 'color', 'symbol', 'lineStyle',
                        'lineWidth', 'remove']

    def data(self, index, role):
        if index.column() == 0:
            # name column, no edit permissions
            if role == QtCore.Qt.DisplayRole:
                return self.pvs[index.row()][0]
        elif index.column() == (len(self.headers) - 1):
            if role == QtCore.Qt.DisplayRole:
                return 'delete?'
        else:
            # data column.  Each column gets its own data delegate
            if role == QtCore.Qt.DisplayRole:
                name, data = self.pvs[index.row()]
                return data[self.headers[index.column()]]
            if role in (QtCore.Qt.EditRole, QtCore.Qt.BackgroundRole):
                col_name = self.headers[index.column()]
                return self.pvs[index.row()][1][col_name]

    def rowCount(self, index): return len(self.pvs)

    def columnCount(self, index): return len(self.headers)

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int
    ) -> Any:
        if role != QtCore.Qt.DisplayRole:
            return

        if orientation == QtCore.Qt.Horizontal:
            return self.headers[section]

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

    def insertRow(
        self,
        row: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> bool:
        self.beginInsertRows(
            QtCore.QModelIndex(), row, row
        )
        self.pvs.insert(
            row, ['name', {'color': QtGui.QColor(255, 0, 0),
                           'symbol': 'o',
                           'lineWidth': 2,
                           'lineStyle': QtCore.Qt.SolidLine}]
        )
        self.endInsertRows()
        return True

    def add_signal(self, pv: str) -> bool:
        """
        Add a signal to the widget.

        Parameters
        ----------
        pv : str
            the PV to add

        Returns
        -------
        bool
            if pv was added to the model successfully
        """
        # don't insert if already present
        if pv in [row[0] for row in self.pvs]:
            logger.debug(f'{pv} already in model.  Skipping add')
            QtWidgets.QMessageBox.information(
                self.parent(),
                'Duplicate PV',
                'PV already exists in list, skipping add'
            )
            return False

        # add item to list
        index_1 = self.createIndex(0, 0)
        index_2 = self.createIndex(0, 3)
        self.insertRow(0, index_1)
        # TO-DO: Need to validate the pv's here, ouside of just the input fields
        self.pvs[0][0] = pv
        self.dataChanged.emit(index_1, index_2)
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


class EnumDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, *args, enums: Dict[str, Any], **kwargs) -> None:
        self.enums = enums
        self.enums_inv = {value: key for key, value in self.enums.items()}
        super().__init__(*args, **kwargs)

    def createEditor(
        self,
        parent: QtWidgets.QWidget,
        option,
        index: QtCore.QModelIndex
    ) -> QtWidgets.QWidget:
        combo = QtWidgets.QComboBox(parent=parent)
        combo.insertItems(0, list(self.enums.keys()))
        return combo

    def setModelData(
        self,
        editor: QtWidgets.QComboBox,
        model: QtCore.QAbstractItemModel,
        index: QtCore.QModelIndex
    ) -> None:
        value = editor.currentText()
        model.setData(index, self.enums[value], QtCore.Qt.EditRole)

    def displayText(self, value: Any, locale: QtCore.QLocale) -> str:
        return str(self.enums_inv[value])


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
