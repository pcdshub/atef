"""
Widgets for config gui's active-checkout run mode.
Widgets should mamp onto edit widgets from atef.widgets.config.data_active
"""

from atef.widgets.config.run_base import RunPage


class PassiveRunPage(RunPage):
    """
    Widget for viewing run status of a passive checkout.
    Features a TreeView with icon status readouts, overall check status...
    """
    filename = 'passive_run_page.ui'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
