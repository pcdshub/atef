"""
Widget classes designed for PV archiver interaction
"""

from __future__ import annotations

import logging
from typing import ClassVar, List, Optional

from pydm.widgets.archiver_time_plot import PyDMArchiverTimePlot
from qtpy import QtWidgets
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
    string_list: QtWidgets.QListWidget

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        pvs: Optional[List[str]] = None
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

        # - look for correct archiver url, take one that pings or look for env var
        # - connect string_list to plot
        # - connect buttons to TimePlot
        # - connect buttons on string lists?
        # - set up validator on QLineEdit
        self._setup_ui()
        self._setup_range_buttons()

    def _setup_ui(self):
        # for pv in self._pv_list:
        self.time_plot.addYChannel(
            y_channel='ca://LM1K4:HRM_BHC:HUMID',
            name='pv1',
            useArchiveData=True
        )
        self._setup_pv_selector()
        # pass

    def _setup_range_buttons(self):
        def _set_time_span_fn(s: float):
            """_summary_

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
        regexp = QRegularExpression(r'^\w+(:\w+)+$')
        validator = QRegularExpressionValidator(regexp)
        self.input_field.setValidator(validator)

        def _add_item():
            # grab and clear text
            pv = self.input_field.text()
            print(f'_add_item: {pv}')

            # TO-DO: Further validation?  Check if PV exists?

            # add item to list
            QtWidgets.QListWidgetItem(pv, parent=self.string_list)

        self.input_field.returnPressed.connect(_add_item)

        def _update_curves():
            # grab all the list items
            pvs = [self.string_list.item(i).text()
                   for i in range(self.string_list.count())]
            print(pvs)

            self.time_plot.clearCurves()

            for pv in pvs:
                self.time_plot.addYChannel(
                    y_channel=f'ca://{pv}',
                    name=f'{pv}',
                    useArchiveData=True
                )

            # set up legend
            for curve_item in self.time_plot._curves:
                self.time_plot.addLegendItem(curve_item, curve_item.name())
            self.time_plot.setShowLegend(True)

        self.string_list.model().rowsInserted.connect(_update_curves)
        self.string_list.model().rowsRemoved.connect(_update_curves)
        # TO-DO: delete item from list ability
        # TO-DO: contex_menu_policy helper (add, delete, bring to forground?)

    # TO-DO: crosshair hover over plot
    # TO-DO: scaling / dealing with different scales
