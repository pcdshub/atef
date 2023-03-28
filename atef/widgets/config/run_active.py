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

from atef.config import ConfigurationFile, PreparedFile, run_passive_step
from atef.procedure import PreparedDescriptionStep, PreparedPassiveStep
from atef.widgets.config.data_base import DataWidget
from atef.widgets.config.run_base import create_tree_items
from atef.widgets.config.utils import ConfigTreeModel, TreeItem
from atef.widgets.core import DesignerDisplay

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
