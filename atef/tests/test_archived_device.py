import pcdsdevices.tests.conftest
import pytest

from ..archive_device import make_archived_device

all_pcdsdevice_classes = pytest.mark.parametrize(
    "cls",
    [
        pytest.param(cls, id=f"{cls.__module__}.{cls.__name__}")
        for cls in pcdsdevices.tests.conftest.find_all_device_classes()
    ]
)


@all_pcdsdevice_classes
def test_switch_control_layer(cls):
    make_archived_device(cls)
