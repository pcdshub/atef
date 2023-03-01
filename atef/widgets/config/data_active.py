from atef.widgets.config.data_base import DataWidget
from atef.widgets.core import DesignerDisplay


class PassiveEditPage(DesignerDisplay, DataWidget):
    """
    Widget for selecting and previewing a passive checkout.
    Features readouts for number of checks to run, ... and more?
    """
    filename = 'passive_edit_page.ui'
    pass


class PlanEditPage(DesignerDisplay, DataWidget):
    """
    Widget for creating and editing a plan step
    Accesses the Bluesky RunEngine
    Should include some readout?
    """
    filename = ''
    pass
