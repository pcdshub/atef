import apischema
import ophyd
import ophyd.sim
import pytest

from .. import check, reduce
from ..check import Severity


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
            Severity.success,
            id="all_good",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equals(value=1, atol=0),
                },
            ),
            Severity.error,
            id="multi_attr_no_tol",
        ),
        pytest.param(
            check.DeviceConfiguration(
                checks={
                    "sig1 sig2": check.Equals(value=1, atol=2),
                },
            ),
            Severity.success,
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
            Severity.success,
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
            Severity.error,
            id="sig1_failure",
        ),
    ]
)


@config_and_severity
def test_serializable(config: check.DeviceConfiguration, severity: Severity):
    serialized = apischema.serialize(config)
    assert apischema.deserialize(check.DeviceConfiguration, serialized) == config


@config_and_severity
def test_result_severity(
    device, config: check.DeviceConfiguration, severity: Severity
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
        0: Severity.error,
        1: Severity.success,
        2: Severity.warning,
        3: Severity.error,
    }[state1.get()]
    checks = {
        at2l0.state_attrs: [
            check.NotEquals(
                description="Filter is moving",
                value=0,
                severity_on_failure=Severity.error,
            ),
            check.NotEquals(
                description="Filter is out of the beam",
                value=1,
                severity_on_failure=Severity.success,
            ),
            check.NotEquals(
                description="Filter is in the beam",
                value=2,
                severity_on_failure=Severity.warning,
            ),
            check.GreaterOrEqual(
                description="Filter status unknown",
                value=3,
                severity_on_failure=Severity.error,
                invert=True,
            ),
        ],
    }

    overall, results = check.check_device(at2l0, checks)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


def test_at2l0_standin_reduce(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    state1.put(1.0)
    checks = {
        at2l0.state_attrs.split()[0]: [
            check.Equals(
                description="Duration test",
                value=1,
                reduce_method=reduce.ReduceMethod.average,
                reduce_period=0.1,
                severity_on_failure=Severity.error,
            ),
        ],
    }

    overall, results = check.check_device(at2l0, checks)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == Severity.success


def test_at2l0_standin_value_map(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    value_to_severity = {
        0: Severity.error,
        1: Severity.success,
        2: Severity.warning,
        3: Severity.error,
    }

    severity = value_to_severity[state1.get()]
    checks = {
        at2l0.state_attrs: [
            check.ValueSet(
                values=[
                    check.Value(
                        value=0,
                        description="Filter is moving",
                        severity=Severity.error,
                    ),
                    check.Value(
                        description="Filter is out of the beam",
                        value=1,
                        severity=Severity.success,
                    ),
                    check.Value(
                        description="Filter is in the beam",
                        value=2,
                        severity=Severity.warning,
                    ),
                ],
            )
        ]
    }

    overall, results = check.check_device(at2l0, checks)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


def test_pv_conversion():
    dev = check.pvs_to_device(["pv1 pv2", "pv3"])
    assert dev._pv_to_attr_ == {
        # Due to sort order
        "pv1": "attr_0",
        "pv2": "attr_1",
        "pv3": "attr_2",
    }

    # TODO: FakeEpicsSignal has no pvname attribute
    # fake_dev = ophyd.sim.make_fake_device(dev)(name="test")
    # assert fake_dev.attr_0.name == "pv1"
    # assert fake_dev.attr_1.name == "pv2"
    # assert fake_dev.attr_2.name == "pv3"


def test_pv_config_to_device_config():
    check1 = check.Equals(value=1)
    check2 = check.Equals(value=2)

    pv_config = check.PVConfiguration(
        description="abc",
        checks={
            "pv1 pv2": check1,
            "pv3": check2,
        }
    )

    _, config = check.pv_config_to_device_config(pv_config)
    assert list(config.checks) == [
        "attr_0 attr_1",
        "attr_2",
    ]

    assert config.description == pv_config.description
