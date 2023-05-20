from unittest import mock

import happi
import pytest
from pytestqt.qtbot import QtBot
from qtpy import QtWidgets

from atef.widgets.config.data_active import ActionRowWidget
from atef.widgets.ophyd import OphydAttributeData


def test_ophyd_attribute_data(happi_client):
    """ Pass if OphydAttributeData creation is successful """
    dev = happi_client.search()[0].get()
    OphydAttributeData.from_device_attribute(dev, 'setpoint')
    OphydAttributeData.from_device_attribute(dev, 'acceleration')


# TODO: Figure out how to get sim Enum.  SynGauss has one but requires a device
# as input ... how to do that in happi?
@pytest.mark.parametrize('dev_name, attr, widget_type, data_type', [
    ['motor1', 'setpoint', QtWidgets.QLineEdit, int],
])
@mock.patch('atef.util.get_happi_client')
def test_action_target_set(
    mock_from_config,
    qtbot: QtBot,
    happi_client: happi.Client,
    dev_name: str,
    attr: str,
    widget_type: QtWidgets.QWidget,
    data_type: type
):
    """
    Pass if we can set attribute data for an ActionRow and verify the type of
    resulting input widget
    """
    mock_from_config.return_value = happi_client
    action_row = ActionRowWidget()
    dev = happi_client.search(name=dev_name)[0].get()
    attr_data = OphydAttributeData.from_device_attribute(dev, attr)

    action_row.target_entry_widget.set_signal([attr_data])

    action_row.target_entry_widget.data_updated.emit()
    qtbot.wait_until(lambda: not action_row.value_button_box.isHidden())
    assert isinstance(action_row.edit_widget, widget_type)
    assert action_row._dtype is data_type
