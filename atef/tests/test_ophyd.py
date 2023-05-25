import happi
import pytest
from pytestqt.qtbot import QtBot
from qtpy import QtWidgets

from atef.widgets.config.data_active import ActionRowWidget
from atef.widgets.ophyd import (OphydAttributeData, OphydAttributeDataSummary,
                                PolledDeviceModel)


def test_ophyd_attribute_data(happi_client: happi.Client):
    """ Pass if OphydAttributeData/Summary creation is successful """
    dev = happi_client.search()[0].get()
    OphydAttributeData.from_device_attribute(dev, 'setpoint')
    odata = OphydAttributeData.from_device_attribute(dev, 'acceleration')

    OphydAttributeDataSummary.from_attr_data(odata)


# TODO: Figure out how to get sim Enum.  SynGauss has one but requires a device
# as input ... how to do that in happi?
@pytest.mark.parametrize('dev_name, attr, widget_type, data_type', [
    ['motor1', 'setpoint', QtWidgets.QLineEdit, int],
    ['enum1', 'enum', QtWidgets.QComboBox, int],
])
def test_action_target_set(
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
    action_row = ActionRowWidget()
    dev = happi_client.search(name=dev_name)[0].get()
    attr_data = OphydAttributeData.from_device_attribute(dev, attr)

    action_row.target_entry_widget.set_signal([attr_data])

    action_row.target_entry_widget.data_updated.emit()
    qtbot.wait_until(lambda: not action_row.value_button_box.isHidden())
    assert isinstance(action_row.edit_widget, widget_type)
    assert action_row._dtype is data_type


def test_polling_thread(qtbot: QtBot, happi_client: happi.Client):
    dev = happi_client.search()[0].get()
    model = PolledDeviceModel(dev)
    thread = model._poll_thread
    qtbot.wait_until(lambda: model._poll_thread.running)
    # assert model._poll_thread.running

    old_value = dev.position
    dev.set(old_value + 4)

    # raise if this is not emitted within 5s timeout
    qtbot.wait_signal(model._poll_thread.data_changed)
    # stop and clean up thread
    model.stop()
    qtbot.wait_until(lambda: thread.isFinished())
