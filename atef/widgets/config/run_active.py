"""
Widgets for config gui's active-checkout run mode.
Widgets should map onto edit widgets from atef.widgets.config.data_active, and
take a subclass of PreparedProcedureStep as their ``data``.
"""

import asyncio
import logging
import pathlib
from typing import Optional, Union

import qtawesome
from pcdsutils.qt.callbacks import WeakPartialMethodSlot
from qtpy import QtCore, QtWidgets

from atef.config import (ConfigurationFile, PreparedFile,
                         PreparedSignalComparison,
                         PreparedTemplateConfiguration, run_passive_step)
from atef.find_replace import FindReplaceAction
from atef.procedure import (PreparedDescriptionStep, PreparedPassiveStep,
                            PreparedProcedureFile, PreparedSetValueStep,
                            PreparedTemplateStep, PreparedValueToSignal,
                            ProcedureFile)
from atef.widgets.config.data_base import DataWidget
from atef.widgets.config.find_replace import FindReplaceRow
from atef.widgets.config.run_base import ResultStatus, create_tree_from_file
from atef.widgets.config.utils import ConfigTreeModel, walk_tree_items
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
        """Sets up ConfigTreeModel with the data from the ConfigurationFile"""

        root_item = create_tree_from_file(
            data=self.config_file,
            prepared_file=self.prepared_config
        )

        model = ConfigTreeModel(data=root_item)
        self.tree_view.setModel(model)

        self.tree_view.header().swapSections(0, 1)
        self.tree_view.expandAll()

    def run_config(self, *args, **kwargs) -> None:
        """slot to be connected to RunCheck Button"""
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
        """slot to be connected to RunCheck button"""
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


class TemplateRunWidget(DesignerDisplay, DataWidget):
    """Widget for viewing TemplateConfigurations, either passive or active"""
    filename = 'template_run_widget.ui'

    refresh_button: QtWidgets.QPushButton
    tree_view: QtWidgets.QTreeView
    edits_list: QtWidgets.QListWidget

    def __init__(
        self,
        *args,
        data: Union[PreparedTemplateConfiguration, PreparedTemplateStep],
        **kwargs
    ):
        super().__init__(*args, data=data, **kwargs)
        self._partial_slots: list[WeakPartialMethodSlot] = []
        self.orig_step = getattr(data, 'config', None) or getattr(data, 'origin', None)
        if not self.orig_step.filename:
            logger.warning('no passive step to run')
            return

        self.prepared_file = self.bridge.file.get()
        self.orig_file = self.prepared_file.file

        fp = pathlib.Path(self.orig_step.filename)
        if not fp.is_file():
            return

        if isinstance(self.orig_file, ConfigurationFile):
            self.unedited_file = ConfigurationFile.from_filename(fp)
        elif isinstance(self.orig_file, ProcedureFile):
            self.unedited_file = ProcedureFile.from_filename(fp)

        self.setup_tree()
        self.setup_edits_list()

        self.refresh_button.setIcon(qtawesome.icon('fa.refresh'))
        self.refresh_button.clicked.connect(self.run_config)

    def setup_tree(self):
        """Sets up ConfigTreeModel with the data from the ConfigurationFile"""

        root_item = create_tree_from_file(
            data=self.orig_file,
            prepared_file=self.prepared_file
        )

        model = ConfigTreeModel(data=root_item)
        self.tree_view.setModel(model)

        self.tree_view.header().swapSections(0, 1)
        self.tree_view.expandAll()

    def setup_edits_list(self):
        """Populate edit_list with edits for display.  Links """
        target = self.unedited_file
        if target is not None:
            for regexFR in self.orig_step.edits:
                action = regexFR.to_action(target=target)
                l_item = QtWidgets.QListWidgetItem()
                row_widget = FindReplaceRow(data=action)
                row_widget.button_box.hide()
                l_item.setSizeHint(QtCore.QSize(row_widget.width(), row_widget.height()))
                self.edits_list.addItem(l_item)
                self.edits_list.setItemWidget(l_item, row_widget)

                # reveal tree when details selected
                reveal_slot = WeakPartialMethodSlot(
                    row_widget, row_widget.details_button.pressed,
                    self.reveal_tree_item, self.edits_list, action=row_widget.data
                )
                self._partial_slots.append(reveal_slot)

        reveal_staged_slot = WeakPartialMethodSlot(
            self.edits_list, self.edits_list.itemSelectionChanged,
            self.reveal_tree_item, self.edits_list,
        )
        self._partial_slots.append(reveal_staged_slot)

    def reveal_tree_item(
        self,
        this_list: QtWidgets.QListWidget,
        action: Optional[FindReplaceAction] = None
    ) -> None:
        """Reveal and highlight the tree-item referenced by ``action``"""
        if not action:
            curr_widget = this_list.itemWidget(this_list.currentItem())
            if curr_widget is None:  # selection has likely been removed
                return

            action: FindReplaceAction = curr_widget.data

        model: ConfigTreeModel = self.tree_view.model()

        closest_index = None
        # Gather objects in path, ignoring steps that jump into lists etc
        path_objs = [part[0] for part in action.path if not isinstance(part[0], str)]
        for tree_item in walk_tree_items(model.root_item):
            if tree_item.orig_data in path_objs:
                closest_index = model.index_from_item(tree_item)

        if closest_index:
            self.tree_view.setCurrentIndex(closest_index)
            self.tree_view.scrollTo(closest_index)

    def run_config(self, *args, **kwargs) -> None:
        """slot to be connected to RunCheck Button"""
        try:
            self.tree_view.model().layoutAboutToBeChanged.emit()
        except AttributeError:
            # no model has been set, this method should be no-op
            return

        if isinstance(self.prepared_file, PreparedFile):
            asyncio.run(run_passive_step(self.prepared_file))
        elif isinstance(self.prepared_file, PreparedProcedureFile):
            # ensure
            asyncio.run(self.prepared_file.run())

        self.tree_view.model().layoutChanged.emit()
