import datetime

import pcdsdevices
import pcdsdevices.attenuator
import pcdsdevices.tests.conftest
import pytest

from ..archive_device import ArchivedValue, make_archived_device
from . import conftest

# skip these devices, subclassing is too involved to be automated
SKIP_CLS = ['LightpathMixin', 'LightpathInOutCptMixin']

all_pcdsdevice_classes = pytest.mark.parametrize(
    "cls",
    [
        pytest.param(cls, id=f"{cls.__module__}.{cls.__name__}")
        for cls
        in pcdsdevices.tests.conftest.find_all_device_classes(skip=SKIP_CLS)
    ]
)


@all_pcdsdevice_classes
def test_switch_control_layer(cls):
    make_archived_device(cls)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            ArchivedValue(
                pvname="pvname",
                value=3,
                timestamp=datetime.datetime.now(),
                status=1,
                severity=2,
                appliance=None,
                enum_strs=None,
            ),
            id="simple",
        ),
        pytest.param(
            ArchivedValue(
                pvname="pvname",
                value=3,
                timestamp=datetime.datetime.now(),
                status=1,
                severity=2,
                appliance=None,
                enum_strs=["a", "b", "c"],
            ),
            id="enum_strs"
        ),
    ]
)
def test_archived_value_roundtrip(value):
    api_response = value.to_archapp()
    result = ArchivedValue.from_archapp(value.pvname, value.appliance, **api_response)
    assert result == value


def make_at1l0():
    return make_archived_device(pcdsdevices.attenuator.FeeAtt)(
        prefix="SATT:FEE1:320", name="at1l0"
    )


def test_at1l0():
    default_value = ArchivedValue(
        pvname="pvname",
        value=3,
        timestamp=datetime.datetime.now(),
        status=1,
        severity=2,
        appliance=None,
        enum_strs=None,
    )
    with conftest.MockEpicsArch({}, default_value).use():
        at1l0 = make_at1l0()
        print(f"AT1L0 is: {at1l0}")
        for attr, value in at1l0.time_slip(datetime.datetime.now()).items():
            print(f"{attr} = {value}")

        print("Get", at1l0.get())
