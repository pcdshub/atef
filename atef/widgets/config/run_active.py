"""
Widgets for config gui's active-checkout run mode.
Widgets should map onto edit widgets from atef.widgets.config.data_active
"""

import logging

from qtpy import QtWidgets

from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import DescriptionStep, PassiveStep
from atef.widgets.config.data_base import DataWidget
from atef.widgets.config.run_base import create_tree_items
from atef.widgets.config.utils import ConfigTreeModel, TreeItem
from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)


class PassiveRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing run status of a passive checkout.
    Features a TreeView with icon status readouts, overall check status...
    """
    filename = 'passive_run_widget.ui'

    tree_view: QtWidgets.QTreeView

    def __init__(self, *args, data: PassiveStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)
        if not self.bridge.filepath.get():
            logger.warning('no passive step to run')
            return

        fp = self.bridge.filepath.get()
        self.config_file = ConfigurationFile.from_filename(fp)
        self.prepared_file = PreparedFile.from_config(self.config_file)
        self.setup_tree()

        # need to setup RunCheck widget

    def setup_tree(self):
        # tree data
        root_item = TreeItem(
            data=self.config_file, prepared_data=self.prepared_file
        )
        create_tree_items(data=self.config_file.root, parent=root_item,
                          prepared_file=self.prepared_file)

        model = ConfigTreeModel(data=root_item)

        self.tree_view.setModel(model)
        header = self.tree_view.header()
        header.setSectionResizeMode(header.ResizeToContents)
        self.tree_view.header().swapSections(0, 1)
        self.tree_view.expandAll()


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
