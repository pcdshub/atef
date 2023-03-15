"""
Widgets for config gui's active-checkout run mode.
Widgets should map onto edit widgets from atef.widgets.config.data_active
"""

import asyncio
import logging
import pathlib

from qtpy import QtWidgets

from atef.config import ConfigurationFile, PreparedFile, run_passive_step
from atef.procedure import DescriptionStep, PassiveStep
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

    def __init__(self, *args, data: PassiveStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        if not self.bridge.filepath.get():
            logger.warning('no passive step to run')
            return

        fp = pathlib.Path(self.bridge.filepath.get())
        if not fp.is_file():
            return
        self.config_file = ConfigurationFile.from_filename(fp)
        self.prepared_config = PreparedFile.from_config(self.config_file)

        self.setup_tree()

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
        self.tree_view.model().layoutAboutToBeChanged.emit()

        loop = asyncio.get_event_loop()
        coroutine = run_passive_step(self.prepared_config)
        loop.run_until_complete(coroutine)

        self.tree_view.model().layoutChanged.emit()


class DescriptionRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing description step
    """
    filename = 'description_step_run_widget.ui'

    title_label: QtWidgets.QLabel
    desc_label: QtWidgets.QLabel

    def __init__(self, *args, data: DescriptionStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.title_label.setText(self.bridge.name.get())
        self.desc_label.setText(self.bridge.description.get())
