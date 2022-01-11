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
                    sig1=check.Equals(value=1),
                    sig2=check.Equals(value=2.5),
                    sig3=check.Equals(value="abc"),
                ),
            ),
            ResultSeverity.success,
            id="all_good",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equals(value=1, atol=0),
                },
            ),
            ResultSeverity.error,
            id="multi_attr_no_tol",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equals(value=1, atol=2),
                },
            ),
            ResultSeverity.success,
            id="multi_attr_ok",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": [
                        check.Equals(value=1, atol=2),
                        check.Equals(value=3, atol=4),
                    ],
                },
            ),
            ResultSeverity.success,
            id="multi_attr_multi_test",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks=dict(
                    sig1=check.Equals(value=2),
                    sig2=check.Equals(value=2.5),
                    sig3=check.Equals(value="abc"),
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


@pytest.fixture(
    scope="function",
    params=[0, 1, 2, 3],
)
def at2l0(request):
    signals = {
        0: ophyd.Signal(value=0, name="moving"),
        1: ophyd.Signal(value=1, name="out"),
        2: ophyd.Signal(value=2, name="in"),
        3: ophyd.Signal(value=3, name="unknown"),
    }

    class FakeAT2L0:
        name: str = "fakedev"
        state_attrs: str = (
            "blade_01.state.state blade_02.state.state blade_03.state.state "
            "blade_04.state.state blade_05.state.state blade_06.state.state "
            "blade_07.state.state blade_08.state.state blade_09.state.state "
            "blade_10.state.state blade_11.state.state blade_12.state.state "
            "blade_13.state.state blade_14.state.state blade_15.state.state "
            "blade_16.state.state blade_17.state.state blade_18.state.state "
            "blade_19.state.state"
        )

        def __getattr__(self, attr):
            if attr.startswith("blade_"):
                return signals[request.param]
            raise AttributeError(attr)

    return FakeAT2L0()


def test_at2l0_standin(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    severity = {
        0: ResultSeverity.error,
        1: ResultSeverity.success,
        2: ResultSeverity.warning,
        3: ResultSeverity.error,
    }[state1.get()]
    checks = {
        at2l0.state_attrs: [
            check.NotEquals(
                description="Filter is moving",
                value=0,
                severity_on_failure=ResultSeverity.error,
            ),
            check.NotEquals(
                description="Filter is out of the beam",
                value=1,
                severity_on_failure=ResultSeverity.success,
            ),
            check.NotEquals(
                description="Filter is in the beam",
                value=2,
                severity_on_failure=ResultSeverity.warning,
            ),
            check.GreaterOrEqual(
                description="Filter status unknown",
                value=3,
                severity_on_failure=ResultSeverity.error,
                invert=True,
            ),
        ],
    }

    overall, results = check.check_device(at2l0, checks)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


def test_at2l0_standin_value_map(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    value_to_severity = {
        0: ResultSeverity.error,
        1: ResultSeverity.success,
        2: ResultSeverity.warning,
        3: ResultSeverity.error,
    }

    severity = value_to_severity[state1.get()]
    checks = {
        at2l0.state_attrs: [
            check.ValueSet(
                values=[
                    check.Value(
                        value=0,
                        description="Filter is moving",
                        severity=ResultSeverity.error,
                    ),
                    check.Value(
                        description="Filter is out of the beam",
                        value=1,
                        severity=ResultSeverity.success,
                    ),
                    check.Value(
                        description="Filter is in the beam",
                        value=2,
                        severity=ResultSeverity.warning,
                    ),
                ],
            )
        ]
    }

    overall, results = check.check_device(at2l0, checks)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity
