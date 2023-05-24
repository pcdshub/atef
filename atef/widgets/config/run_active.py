"""
Widgets for config gui's active-checkout run mode.
Widgets should map onto edit widgets from atef.widgets.config.data_active, and
take a subclass of PreparedProcedureStep as their ``data``.
"""

import asyncio
import logging
import pathlib

import qtawesome
from qtpy import QtWidgets

from atef.config import (ConfigurationFile, PreparedFile,
                         PreparedSignalComparison, run_passive_step)
from atef.procedure import (PreparedDescriptionStep, PreparedPassiveStep,
                            PreparedSetValueStep, PreparedValueToSignal)
from atef.widgets.config.data_base import DataWidget
from atef.widgets.config.run_base import ResultStatus, create_tree_items
from atef.widgets.config.utils import ConfigTreeModel, TreeItem
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import insert_widget

logger = logging.getLogger(__name__)


class PassiveRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing run status of a passive checkout.
    Features a TreeView with icon status readouts, overall check status

    This widget holds onto a different ``PreparedFile`` from the ``RunCheck``
    widget in ``RunStepPage``.  We assume they return the same result, and will
    be run at roughly the same time
    """
    filename = 'passive_run_widget.ui'

    tree_view: QtWidgets.QTreeView
    refresh_button: QtWidgets.QPushButton

    def __init__(self, *args, data: PreparedPassiveStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        if not self.bridge.origin.get().filepath:
            logger.warning('no passive step to run')
            return

        fp = pathlib.Path(self.bridge.origin.get().filepath)
        if not fp.is_file():
            return
        self.config_file = ConfigurationFile.from_filename(fp)
        self.prepared_config = PreparedFile.from_config(self.config_file)

        self.setup_tree()

        self.refresh_button.setIcon(qtawesome.icon('fa.refresh'))
        self.refresh_button.clicked.connect(self.run_config)

    def setup_tree(self):
        """ Sets up ConfigTreeModel with the data from the ConfigurationFile """
        root_item = TreeItem(
            data=self.config_file, prepared_data=self.prepared_config
        )
        create_tree_items(data=self.config_file.root, parent=root_item,
                          prepared_file=self.prepared_config)

        model = ConfigTreeModel(data=root_item)
        self.tree_view.setModel(model)

        # Customize the look of the table
        header = self.tree_view.header()
        header.setSectionResizeMode(header.ResizeToContents)
        self.tree_view.header().swapSections(0, 1)
        self.tree_view.expandAll()

    def run_config(self, *args, **kwargs) -> None:
        """ slot to be connected to RunCheck Button """
        try:
            self.tree_view.model().layoutAboutToBeChanged.emit()
        except AttributeError:
            # no model has been set, this method should be no-op
            return

        asyncio.run(run_passive_step(self.prepared_config))

        self.tree_view.model().layoutChanged.emit()


class DescriptionRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing description step
    """
    filename = 'description_step_run_widget.ui'

    title_label: QtWidgets.QLabel
    desc_label: QtWidgets.QLabel

    def __init__(self, *args, data: PreparedDescriptionStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.title_label.setText(self.bridge.origin.get().name)
        self.desc_label.setText(self.bridge.origin.get().description)


class SetValueRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing set value step.  Displays Results alongside their
    respective actions or checks
    """
    filename = 'set_value_run_widget.ui'

    actions_table: QtWidgets.QTableWidget
    checks_table: QtWidgets.QTableWidget

    def __init__(self, *args, data: PreparedSetValueStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        self._setup_ui()

    def _setup_ui(self) -> None:
        for table, field, cls in [
            (self.actions_table, self.data.prepared_actions, ActionRowRunWidget),
            (self.checks_table, self.data.prepared_criteria, CheckRowRunWidget)
        ]:
            for item in field:
                ins_ind = table.rowCount()
                action_row = cls(data=item)
                table.insertRow(ins_ind)
                table.setRowHeight(ins_ind, action_row.sizeHint().height())
                table.setCellWidget(ins_ind, 0, action_row)

    def update_statuses(self):
        """ slot to be connected to RunCheck button """
        for table in (self.actions_table, self.checks_table):
            for i in range(table.rowCount()):
                row_widget = table.cellWidget(i, 0)
                row_widget.status_label.update()


class ActionRowRunWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Simple widget displaying information stored in ``PreparedValueToSignal``
    Does not implement the SimpleRowWidget interface
    """
    filename = 'action_row_run_widget.ui'

    name_label: QtWidgets.QLabel
    target_label: QtWidgets.QLabel
    value_label: QtWidgets.QLabel
    status_label_placeholder: QtWidgets.QWidget

    def __init__(self, *args, data: PreparedValueToSignal, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_label = ResultStatus(data=data)
        insert_widget(self.status_label, self.status_label_placeholder)
        self.name_label.setText(data.name)
        self.target_label.setText(data.signal.name)
        enum_strs = getattr(data.signal, 'enum_strs')
        if enum_strs:
            try:
                self.value_label.setText(str(enum_strs[int(data.value)]))
            except IndexError:
                self.value_label.setText(str(data.value))
        else:
            self.value_label.setText(str(data.value))


class CheckRowRunWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Simple widget displaying information stored in ``PreparedSignalComparison``
    Does not implement the SimpleRowWidget interface
    """
    filename = 'check_row_run_widget.ui'

    child_button: QtWidgets.QPushButton
    name_label: QtWidgets.QLabel
    target_label: QtWidgets.QLabel
    check_summary_label: QtWidgets.QLabel
    status_label_placeholder: QtWidgets.QLabel

    def __init__(self, *args, data: PreparedSignalComparison, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_label = ResultStatus(data=data)
        insert_widget(self.status_label, self.status_label_placeholder)
        self.name_label.setText(data.name)
        self.target_label.setText(data.signal.name)
        self.check_summary_label.setText(data.comparison.describe())
