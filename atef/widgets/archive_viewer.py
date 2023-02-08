"""
Widget classes designed for PV archiver interaction
"""

from __future__ import annotations

import datetime
import functools
import itertools
import logging
import os
import re
import urllib
from typing import Any, ClassVar, Dict, List, Optional

from archapp.interactive import EpicsArchive
from pydm.widgets.archiver_time_plot import PyDMArchiverTimePlot
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QRegularExpression, Qt
from qtpy.QtGui import QRegularExpressionValidator
from qtpy.QtWidgets import QStyle, QWidget

from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)
archive_viewer_singleton = None
ARCHIVER_URLS = ['http://pscaa01.slac.stanford.edu',
                 'http://pscaa02.slac.stanford.edu']
symbol_map = {'None': None, 'circle': 'o', 'square': 's',
              'cross': '+', 'star': 'star'}
style_map = {'solid': Qt.SolidLine,
             'dash': Qt.DashLine, 'dot': Qt.DotLine}
color_cycle = itertools.cycle(
    [QtGui.QColor('red'), QtGui.QColor('blue'),
     QtGui.QColor('green'), QtGui.QColor('white')]
)


def get_archive_viewer() -> ArchiverViewerWidget:
    """
    Only allow one viewer to be open at a time.
    Makes it unambiguous where to send PV's to.

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
    err on the side of caution

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
                return url


def _success_decorator(method):
    """
    Decorator for qt methods that want to return True if successful and
    False otherwise.  Wraps the method in a simple try-except.
    """
    def _dec(self, *args, **kwargs):
        try:
            method(self, *args, **kwargs)
            return True
        except Exception as e:
            logger.debug(e)
            return False
    return _dec


class ArchiverError(Exception):
    """ Archiver related exceptions """
    ...


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
    clear_button: QtWidgets.QPushButton

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        pvs: List[str] = []
    ) -> None:
        super().__init__(parent=parent)

        # set the PYDM_ARCHIVER_URL if not already set
        if not os.environ.get('PYDM_ARCHIVER_URL'):
            archiver_url = get_reachable_url(ARCHIVER_URLS)
            if archiver_url is None:
                raise ArchiverError('Cannot reach any archiver urls')
            # need to set environment variable for archiver data plugin
            logger.debug(f'setting archiver url to: {archiver_url}')
            # pydm requires the port to be added
            os.environ['PYDM_ARCHIVER_URL'] = archiver_url + ':17668'
        else:
            archiver_url = os.environ['PYDM_ARCHIVER_URL']
            # ensure the port has been added for pydm
            # this handling needs work, but should suffice for now
            if not archiver_url.endswith(':17668'):
                port_re = re.search(r'(:\d+)$', archiver_url)
                if port_re and (port_re[0] != ':17668'):
                    logger.warning(f'PYDM_ARCHIVER_URL ({archiver_url}) does '
                                   'not end with port 17668, replacing port')
                    archiver_url = archiver_url.replace(port_re[0], ':17668')
                else:
                    archiver_url += ':17668'
                os.environ['PYDM_ARCHIVER_URL'] = archiver_url

        # EpicsArchive wants a stripped down url
        url_core = archiver_url.removeprefix('http://').removesuffix(':17668')
        self.archapp = EpicsArchive(url_core)

        self._pv_list = pvs
        for pv in pvs:
            self.model.add_signal(pv)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """
        Initial set up for this widget.
        - initializes PVModel
        - assigns delegates to columns for data handling
        - wire up various buttons
        - adds pvs if provided at init
        """
        self.setWindowTitle('Epics PV Archive Viewer')

        # set up table view for PV info
        self.model = PVModel(parent=self)
        self.curve_list.setModel(self.model)
        horiz_header = self.curve_list.horizontalHeader()
        horiz_header.setSectionResizeMode(horiz_header.ResizeToContents)

        # set up delegates
        # Color picker delegate
        self.colorDelegate = ColorDelegate()
        self.curve_list.setItemDelegateForColumn(2, self.colorDelegate)

        # line symbol delegate
        self.symbolDelegate = EnumDelegate(enums=symbol_map)
        self.curve_list.setItemDelegateForColumn(3, self.symbolDelegate)

        # line style delegate
        self.styleDelegate = EnumDelegate(enums=style_map)
        self.curve_list.setItemDelegateForColumn(4, self.styleDelegate)

        # delete button in last column
        self.deleteDelegate = DeleteDelegate()
        del_col = len(self.model.headers) - 1
        self.curve_list.setItemDelegateForColumn(del_col, self.deleteDelegate)
        self.deleteDelegate.delete_request.connect(self.model.removeRow)

        # set up list selector
        self._setup_pv_selector()

        self.redraw_button.clicked.connect(self.update_curves)
        self.clear_button.clicked.connect(self.clear_curves)

        ricon = self.style().standardIcon(QStyle.SP_BrowserReload)
        self.redraw_button.setIcon(ricon)
        cicon = self.style().standardIcon(QStyle.SP_DialogDiscardButton)
        self.clear_button.setIcon(cicon)
        # set up time range buttons
        self._setup_range_buttons()

        for pv in (self._pv_list or []):
            self.model.add_signal(pv)

    def _setup_range_buttons(self) -> None:
        def _set_time_span_fn(s: float):
            """
            Set the time span of the plot.

            Parameters
            ----------
            s : float
                The time span in seconds
            """
            def fn():
                now = datetime.datetime.today()
                prev = now - datetime.timedelta(seconds=s)
                self.time_plot.setXRange(prev.timestamp(), now.timestamp())
                self.time_plot.updateXAxis()
            return fn

        self.button_day.clicked.connect(_set_time_span_fn(24*60*60))
        self.button_week.clicked.connect(_set_time_span_fn(24*60*60*7))
        self.button_month.clicked.connect(_set_time_span_fn(24*60*60*30))

    def _setup_pv_selector(self) -> None:
        """
        Set up pv validation on input field line edit.
        Attempts to pass pv name to ``ArchiveViewerWidget.add_signal()``
        """
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

    def update_curves(self):
        """ Clears the timeplot and adds any PV's present in the PVModel """
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

    def clear_curves(self):
        """ Clears the curves in the model and updates the plot """
        while len(self.model.pvs) > 0:
            self.model.removeRow(0)

        self.update_curves()

    def add_signal(
        self,
        pv: str,
        dev_attr: Optional[str] = None,
        update_curves: bool = True
    ) -> None:
        """
        Adds a PV to the ArchiverViewerWidget's PVModel
        Ensures PV's have at least 3 data points in the archiver before
        adding a PV to the model

        Updates the time plot widget and clears the input field if
        successful

        Parameters
        ----------
        pv : str
            the PV to be added (eg. MR2L0:RTD:1:TEMP)
        dev_attr : Optional[str], optional
            the ophyd attribute name corresponding to the ``pv``,
            by default None
        """
        # check if data exists
        data = self.get_pv_data_snippet(pv)
        if data and len(data['data']) < 3:
            logger.warning(
                'Fewer than 3 datapoints from last two days found in '
                f'archiver app for pv ({pv})'
            )

        success = self.model.add_signal(pv, dev_attr=dev_attr)

        if success and update_curves:
            self.update_curves()
            self.input_field.clear()

    @functools.lru_cache()
    def get_pv_data_snippet(self, pv: str) -> Dict[str, Any]:
        """
        Queries archapp.EpicsArchive for a small amount of data for use
        in verifying the PV

        Parameters
        ----------
        pv : str
            the pv to get data for

        Returns
        -------
        Dict[str, Any]
            data dictionary, with keys ['data', 'meta']
        """
        # use get_raw for json metadata
        # also sidesteps an issue where some PV's aren't found using
        # the normal EpicsArchive.get()
        today = datetime.datetime.today()
        prev = today - datetime.timedelta(days=2)
        data = self.archapp._data.get_raw(pv, prev, today)
        return data


class PVModel(QtCore.QAbstractTableModel):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pvs: List[List[str, dict]] = []
        self.headers = ['PV Name', 'component', 'color', 'symbol',
                        'lineStyle', 'lineWidth', 'remove']

    def data(self, index: QtCore.QModelIndex, role: int) -> Any:
        """
        Returns the data stored under the given role for the item
        referred to by the index.

        Parameters
        ----------
        index : QtCore.QModelIndex
            An index referring to a cell of the TableView
        role : int
            The requested data role.

        Returns
        -------
        Any
            the requested data
        """
        if index.column() == 0:
            # name column, no edit permissions
            if role == Qt.DisplayRole:
                return self.pvs[index.row()][0]
        elif index.column() == (len(self.headers) - 1):
            if role == Qt.DisplayRole:
                return 'delete?'
        else:
            # data column.  Each column gets its own data delegate
            if role in (Qt.DisplayRole, Qt.EditRole,
                        Qt.BackgroundRole):
                _, data = self.pvs[index.row()]
                col_name = self.headers[index.column()]
                return data[col_name]

        # if nothing is found, return invalid QVariant
        return QtCore.QVariant()

    def rowCount(self, index):
        return len(self.pvs)

    def columnCount(self, index):
        return len(self.headers)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int
    ) -> Any:
        """
        Returns the header data for the model.
        Currently only displays horizontal header data
        """
        if role != Qt.DisplayRole:
            return

        if orientation == Qt.Horizontal:
            return self.headers[section]

    def flags(self, index: QtCore.QModelIndex) -> Qt.ItemFlag:
        """
        Returns the item flags for the given ``index``.  The returned
        item flag controls what behaviors the item supports.

        Parameters
        ----------
        index : QtCore.QModelIndex
            the index referring to a cell of the TableView

        Returns
        -------
        QtCore.Qt.ItemFlag
            the ItemFlag corresponding to the cell
        """
        if (index.column() > 1):
            return Qt.ItemIsEditable | Qt.ItemIsEnabled
        else:
            return Qt.ItemIsEnabled

    @_success_decorator
    def removeRow(
        self,
        row: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> bool:
        """
        Removes a row from child items of parent.
        Overrides existing implementation

        Parameters
        ----------
        row : int
            index of row to remove
        parent : QtCore.QModelIndex, optional
            the parent index, by default QtCore.QModelIndex()

        Returns
        -------
        bool
            True if removal is successful
        """
        self.beginRemoveRows(parent, row, row)
        del self.pvs[row]
        self.endRemoveRows()

    @_success_decorator
    def setData(
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = Qt.EditRole
    ) -> bool:
        """
        Set the ``role`` data at the given ``index`` to ``value``

        Parameters
        ----------
        index : QtCore.QModelIndex
            index to set data at
        value : Any
            data to set at index
        role : int, optional
            role enum, by default QtCore.Qt.EditRole

        Returns
        -------
        bool
            True if data successfully set
        """
        self.pvs[index.row()][1][self.headers[index.column()]] = value
        # one index changed, so top_left == bottom_right
        self.dataChanged.emit(index, index)

    @_success_decorator
    def insertRow(
        self,
        row: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> bool:
        """
        Inserts a row into the model, initializing data fields where possible

        Parameters
        ----------
        row : int
            location to insert row at
        parent : QtCore.QModelIndex, optional
            the parent index, by default QtCore.QModelIndex()

        Returns
        -------
        bool
            True if successful, False otherwise
        """
        self.beginInsertRows(
            QtCore.QModelIndex(), row, row
        )
        self.pvs.insert(
            row, ['name', {'color': next(color_cycle),
                           'component': 'N/A',
                           'symbol': 'o',
                           'lineWidth': 2,
                           'lineStyle': Qt.SolidLine}]
        )
        self.endInsertRows()

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
            return False

        # add item to list
        index_1 = self.createIndex(0, 0)
        index_2 = self.createIndex(0, len(self.headers) - 1)
        self.insertRow(0, index_1)
        self.pvs[0][0] = pv
        if dev_attr:
            self.pvs[0][1]['component'] = dev_attr
        self.dataChanged.emit(index_1, index_2)
        return True


class ColorDelegate(QtWidgets.QStyledItemDelegate):
    """
    Delegate for selecting a color.  Creates a QColorDialog for selection
    """
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
            model.setData(index, color, Qt.EditRole)


class EnumDelegate(QtWidgets.QStyledItemDelegate):
    """
    Delegate for selecting from a list of options.  Takes a dictionary
    at init that maps option names to their values.
    """
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
        model.setData(index, self.enums[value], Qt.EditRole)

    def displayText(self, value: Any, locale: QtCore.QLocale) -> str:
        return str(self.enums_inv[value])


class DeleteDelegate(QtWidgets.QStyledItemDelegate):
    """
    Delegate for creating a delete button.  Provides a ``delete_request``
    signal that emits if the button is pressed
    """
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
