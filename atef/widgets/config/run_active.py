"""
Widgets for config gui's active-checkout run mode.
Widgets should mamp onto edit widgets from atef.widgets.config.data_active
"""

from qtpy import QtWidgets

from atef.procedure import DescriptionStep
from atef.widgets.config.data_base import DataWidget
from atef.widgets.core import DesignerDisplay


class PassiveRunPage(DesignerDisplay, DataWidget):
    """
    Widget for viewing run status of a passive checkout.
    Features a TreeView with icon status readouts, overall check status...
    """
    filename = 'passive_run_page.ui'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


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
        print(self.bridge.data)
        print(self.bridge)
        self.title_label.setText(self.bridge.name.get())
        self.desc_label.setText(self.bridge.description.get())
