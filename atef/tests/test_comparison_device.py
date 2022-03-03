from typing import List

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
                devices=[],
                checklist=[
                    check.IdentifierAndComparison(
                        identifiers=["sig1"],
                        comparisons=[check.Equals(value=1)]
                    ),
                    check.IdentifierAndComparison(
                        identifiers=["sig2"],
                        comparisons=[check.Equals(value=2.5)]),
                    check.IdentifierAndComparison(
                        identifiers=["sig3"],
                        comparisons=[check.Equals(value="abc")]
                    ),
                ]
            ),
            Severity.success,
            id="all_good",
        ),
        pytest.param(
            check.DeviceConfiguration(
                devices=[],
                checklist=[
                    check.IdentifierAndComparison(
                        identifiers=["sig1", "sig2"],
                        comparisons=[check.Equals(value=1, atol=0)],
                    ),
                ],
            ),
            Severity.error,
            id="multi_attr_no_tol",
        ),
        pytest.param(
            check.DeviceConfiguration(
                devices=[],
                checklist=[
                    check.IdentifierAndComparison(
                        identifiers=["sig1", "sig2"],
                        comparisons=[check.Equals(value=1, atol=2)],
                    ),
                ],
            ),
            Severity.success,
            id="multi_attr_ok",
        ),
        pytest.param(
            check.DeviceConfiguration(
                devices=[],
                checklist=[
                    check.IdentifierAndComparison(
                        identifiers=["sig1", "sig2"],
                        comparisons=[
                            check.Equals(value=1, atol=2),
                            check.Equals(value=3, atol=4),
                        ],
                    ),
                ],
            ),
            Severity.success,
            id="multi_attr_multi_test",
        ),
        pytest.param(
            check.DeviceConfiguration(
                devices=[],
                checklist=[
                    check.IdentifierAndComparison(
                        identifiers=["sig1"],
                        comparisons=[check.Equals(value=2)],
                    ),
                    check.IdentifierAndComparison(
                        identifiers=["sig2"],
                        comparisons=[check.Equals(value=2.5)],
                    ),
                    check.IdentifierAndComparison(
                        identifiers=["sig3"],
                        comparisons=[check.Equals(value="abc")],
                    ),
                ]
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
    overall, _ = check.check_device(device=device, checklist=config.checklist)
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
        state_attrs: List[str] = (
            "blade_01.state.state blade_02.state.state blade_03.state.state "
            "blade_04.state.state blade_05.state.state blade_06.state.state "
            "blade_07.state.state blade_08.state.state blade_09.state.state "
            "blade_10.state.state blade_11.state.state blade_12.state.state "
            "blade_13.state.state blade_14.state.state blade_15.state.state "
            "blade_16.state.state blade_17.state.state blade_18.state.state "
            "blade_19.state.state"
        ).split()

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
    checklist = [
        check.IdentifierAndComparison(
            identifiers=at2l0.state_attrs,
            comparisons=[
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
        ),
    ]

    overall, results = check.check_device(at2l0, checklist=checklist)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


def test_at2l0_standin_reduce(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    state1.put(1.0)
    checklist = [
        check.IdentifierAndComparison(
            identifiers=at2l0.state_attrs[:1],
            comparisons=[
                check.Equals(
                    description="Duration test",
                    value=1,
                    reduce_method=reduce.ReduceMethod.average,
                    reduce_period=0.1,
                    severity_on_failure=Severity.error,
                ),
            ],
        ),
    ]

    overall, results = check.check_device(at2l0, checklist=checklist)
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
    checklist = [
        check.IdentifierAndComparison(
            identifiers=at2l0.state_attrs,
            comparisons=[
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
                ),
            ]
        )
    ]

    overall, results = check.check_device(at2l0, checklist=checklist)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


@pytest.fixture
def mock_signal_cache() -> check._SignalCache[ophyd.sim.FakeEpicsSignalRO]:
    cache = check._SignalCache(ophyd.sim.FakeEpicsSignalRO)
    cache["pv1"].sim_put(1)
    cache["pv2"].sim_put(1)
    cache["pv3"].sim_put(2)
    return cache


@pytest.mark.parametrize(
    "checklist, expected_severity",
    [
        pytest.param(
            [
                check.IdentifierAndComparison(
                    identifiers=["pv1", "pv2"],
                    comparisons=[check.Equals(value=1)],
                ),
                check.IdentifierAndComparison(
                    identifiers=["pv3"],
                    comparisons=[check.Equals(value=2)],
                )
            ],
            Severity.success,
            id="exact_values_ok",
        ),
        pytest.param(
            [
                check.IdentifierAndComparison(
                    identifiers=["pv1", "pv2"],
                    comparisons=[check.Equals(value=2)],
                ),
            ],
            Severity.error,
            id="values_wrong",
        ),
    ],
)
def test_pv_config(
    mock_signal_cache: check._SignalCache[ophyd.sim.FakeEpicsSignalRO],
    checklist: List[check.IdentifierAndComparison],
    expected_severity: check.Severity
):
    overall, _ = check.check_pvs(checklist, cache=mock_signal_cache)
    assert overall == expected_severity
