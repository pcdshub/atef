"""
Widget classes designed for PV archiver interaction
"""

from __future__ import annotations

import datetime
import itertools
import logging
import os
import urllib
from typing import Any, ClassVar, Dict, List, Optional

from archapp.interactive import EpicsArchive
from pydm.widgets.archiver_time_plot import PyDMArchiverTimePlot
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QRegularExpression
from qtpy.QtGui import QRegularExpressionValidator
from qtpy.QtWidgets import QWidget

from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)
archive_viewer_singleton = None
ARCHIVER_URLS = ['http://pscaa01.slac.stanford.edu',
                 'http://pscaa02.slac.stanford.edu']
symbol_map = {'None': None, 'circle': 'o', 'square': 's',
              'cross': '+', 'star': 'star'}
style_map = {'solid': QtCore.Qt.SolidLine,
             'dash': QtCore.Qt.DashLine, 'dot': QtCore.Qt.DotLine}
color_cycle = itertools.cycle(
    [QtGui.QColor('red'), QtGui.QColor('blue'),
     QtGui.QColor('green'), QtGui.QColor('white')]
)


def get_archive_viewer() -> ArchiverViewerWidget:
    """
    Only allow one viewer to be open at a time.
    Makes it unambiguous whether where to send PV's to.

    Returns
    -------
    ArchiveViewerWidget
        the widget instance
    """
    global archive_viewer_singleton
    if archive_viewer_singleton is None:
        archive_viewer_singleton = ArchiverViewerWidget()
    return archive_viewer_singleton


def get_reachable_url(urls: List[str]) -> str:
    """
    Get valid archiver URLS from the urls
    Looks only for an response code below 400, as a proxy ping test.
    Ideally the urls provided by the env var work, but for now we
    err on the side fo caution

    Returns
    -------
    str
        a responsive url
    """
    for url in urls:
        try:
            _ = urllib.request.urlopen(url, timeout=1)
        except urllib.error.URLError as e:
            if isinstance(e, urllib.error.HTTPError):
                # HTTPError subclasses URLError, separates code
                errno = e.code
            elif e.reason.errno:
                errno = e.reason.errno
            else:
                errno = -1

            if 0 < errno < 400:
                # timeouts give socket.timeout and have no errno
                # here we only care if the server exists
                # connection refused is ok
                return url + ':17668'


class ArchiverError(Exception):
    """ Archiver related exceptions """
    ...


# TO-DO: crosshair hover over plot?
#   -> time_plot.enableCrosshair
# TO-DO: scaling / dealing with different scales between curves
# TO-DO: Turn off autoscale.  Set to 1 day automatically
# TO-DO: make buttons work after manual zoom
# TO-DO: Make redraw retain scale.
# TO-DO: set tooltips
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

        # set the PYDM_ARCHIVER_URL if not already set
        if not os.environ.get('PYDM_ARCHIVER_URL'):
            archiver_url = get_reachable_url(ARCHIVER_URLS)
            if archiver_url is None:
                raise ArchiverError('Cannot reach any archiver urls')
            # need to set environment variable for archiver data plugin
            logger.debug(f'setting archiver url to: {archiver_url}')
            os.environ['PYDM_ARCHIVER_URL'] = archiver_url
        else:
            archiver_url = os.environ['PYDM_ARCHIVER_URL']

        url_core = archiver_url.removeprefix('http://').split('.', 1)[0]
        self.archapp = EpicsArchive(url_core)

        self._pv_list = pvs
        for pv in pvs:
            self.model.add_signal(pv)

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
        self.curve_list.setItemDelegateForColumn(2, self.colorDelegate)

        # symbol delegate
        self.symbolDelegate = EnumDelegate(enums=symbol_map)
        self.curve_list.setItemDelegateForColumn(3, self.symbolDelegate)

        # style delegate
        self.styleDelegate = EnumDelegate(enums=style_map)
        self.curve_list.setItemDelegateForColumn(4, self.styleDelegate)

        # delete button in last column
        self.deleteDelegate = DeleteDelegate()
        del_col = len(self.model.headers) - 1
        self.curve_list.setItemDelegateForColumn(del_col, self.deleteDelegate)
        self.deleteDelegate.delete_request.connect(self.model.removeRow)

        for pv in (self._pv_list or []):
            self.model.add_signal(pv)

        # set up list selector
        self._setup_pv_selector()

        self.redraw_button.clicked.connect(self._update_curves)

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
                now = datetime.datetime.today()
                prev = now - datetime.timedelta(seconds=s)
                self.time_plot.setMaxXRange(now.timestamp())
                self.time_plot.setMinXRange(prev.timestamp())
                self.time_plot.updateXAxis()
            return fn

        self.button_day.clicked.connect(_set_time_span_fn(24*60*60))
        self.button_week.clicked.connect(_set_time_span_fn(24*60*60*7))
        self.button_month.clicked.connect(_set_time_span_fn(24*60*60*30))

    def _setup_pv_selector(self):
        # validate PV form, returnPressed unless valid
        regexp = QRegularExpression(r'^\w+(:\w+)+(\.\w+)*$')
        validator = QRegularExpressionValidator(regexp)
        self.input_field.setValidator(validator)

        def _add_item():
            """ slot for input_field submission """
            # grab and clear text
            pv = self.input_field.text()

            # add item
            self.add_signal(pv)

        self.input_field.returnPressed.connect(_add_item)

    def _update_curves(self):
        # grab all the list items
        pv_data = self.curve_list.model().pvs

        # Re-add curves
        self.time_plot.clearCurves()
        for pv in pv_data:

            data = self.get_pv_data_snippet(pv[0])

            self.time_plot.addYChannel(
                y_channel=f'ca://{pv[0]}',
                name=f'{pv[0]} ({data["meta"].get("EGU","")})',
                symbol=pv[1]['symbol'],
                color=pv[1]['color'],
                lineStyle=pv[1]['lineStyle'],
                lineWidth=pv[1]['lineWidth'],
                useArchiveData=True,
                yAxisName='yAxis'
            )

        try:
            self.time_plot.setLabel('yAxis', text='')
        except Exception:
            # pyqtgraph raises a vanilla exception
            # if a better way to find the left axis name exists, use it
            logger.debug('left axis does not exist to rename')

        self.time_plot.setShowLegend(True)

    def add_signal(self, pv: str, dev_attr: Optional[str] = None) -> None:
        # check if data exists
        data = self.get_pv_data_snippet(pv)
        if data and len(data['data']) < 3:
            QtWidgets.QMessageBox.information(
                self,
                'Invalid PV',
                'Fewer than 3 datapoints from last two days found in '
                'archiver app, skipping add'
            )
            return

        success = self.model.add_signal(pv, dev_attr=dev_attr)

        if success:
            self._update_curves()
            self.input_field.clear()

    def get_pv_data_snippet(self, pv: str) -> Dict[str, Any]:
        # use raw get for json metadata
        # also sidesteps an issue where some PV's aren't found using
        # the normal EpicsArchive.get()
        today = datetime.datetime.today()
        prev = today - datetime.timedelta(days=2)
        data = self.archapp._data.get_raw(pv, prev, today)
        return data


class PVModel(QtCore.QAbstractTableModel):
    def __init__(self, *args, pvs=[], **kwargs):
        # standard item model needs to be init with columns and rows
        # fill out here and feed into super
        super().__init__(*args, **kwargs)
        self.pvs: List[List[str, dict]] = pvs or []
        self.headers = ['PV Name', 'component', 'color', 'symbol',
                        'lineStyle', 'lineWidth', 'remove']

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
            if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole,
                        QtCore.Qt.BackgroundRole):
                _, data = self.pvs[index.row()]
                col_name = self.headers[index.column()]
                return data[col_name]

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
            row, ['name', {'color': next(color_cycle),
                           'component': 'N/A',
                           'symbol': 'o',
                           'lineWidth': 2,
                           'lineStyle': QtCore.Qt.SolidLine}]
        )
        self.endInsertRows()
        return True

    def add_signal(self, pv: str, dev_attr: Optional[str] = None) -> bool:
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
        self.pvs[0][0] = pv
        if dev_attr:
            self.pvs[0][1]['component'] = dev_attr
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
