import apischema
import ophyd
import pytest

from .. import check
from ..check import ResultSeverity


@pytest.fixture(scope="function")
def device():
    class Device:
        name = "dev"
        sig1 = ophyd.Signal(value=1, name="dev.sig1")
        sig2 = ophyd.Signal(value=2.5, name="dev.sig2")
        sig3 = ophyd.Signal(value="abc", name="dev.sig3")

    return Device()


config_and_severity = pytest.mark.parametrize(
    "config, severity",
    [
        pytest.param(
            check.DeviceConfiguration(
                checks=dict(
                    sig1=check.Equality(value=1),
                    sig2=check.Equality(value=2.5),
                    sig3=check.Equality(value="abc"),
                ),
            ),
            ResultSeverity.success,
            id="all_good",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equality(value=1, atol=0),
                },
            ),
            ResultSeverity.error,
            id="multi_attr_no_tol",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equality(value=1, atol=2),
                },
            ),
            ResultSeverity.success,
            id="multi_attr_ok",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": [
                        check.Equality(value=1, atol=2),
                        check.Equality(value=3, atol=4),
                    ],
                },
            ),
            ResultSeverity.success,
            id="multi_attr_multi_test",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks=dict(
                    sig1=check.Equality(value=2),
                    sig2=check.Equality(value=2.5),
                    sig3=check.Equality(value="abc"),
                ),
            ),
            ResultSeverity.error,
            id="sig1_failure",
        ),
    ]
)


@config_and_severity
def test_serializable(config: check.DeviceConfiguration, severity: ResultSeverity):
    serialized = apischema.serialize(config)
    assert apischema.deserialize(check.DeviceConfiguration, serialized) == config


@config_and_severity
def test_result_severity(
    device, config: check.DeviceConfiguration, severity: ResultSeverity
):
    overall, _ = check.check_device(device=device, attr_to_checks=config.checks)
    assert overall == severity
