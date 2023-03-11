"""
Widgets for config gui's active-checkout run mode.
Widgets should map onto edit widgets from atef.widgets.config.data_active
"""

from qtpy import QtWidgets

from atef.procedure import DescriptionStep, PassiveStep
from atef.widgets.config.data_base import DataWidget
from atef.widgets.core import DesignerDisplay


class PassiveRunWidget(DesignerDisplay, DataWidget):
    """
    Widget for viewing run status of a passive checkout.
    Features a TreeView with icon status readouts, overall check status...
    """
    filename = 'passive_run_widget.ui'

    def __init__(self, *args, data: PassiveStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)

        # set up tree
        # connect results to tree column


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
