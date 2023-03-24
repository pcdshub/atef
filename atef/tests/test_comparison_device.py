from typing import Dict, List, Optional, Tuple

import apischema
import ophyd
import ophyd.sim
import pytest

from .. import cache, check, reduce, util
from ..check import Comparison, Severity
from ..config import (ConfigurationFile, ConfigurationGroup,
                      DeviceConfiguration, PreparedDeviceConfiguration,
                      PreparedFile, PreparedPVConfiguration, PVConfiguration,
                      get_result_from_comparison)
from ..exceptions import PreparedComparisonException
from ..result import Result


async def check_device(
    device: ophyd.Device,
    by_attr: Dict[str, List[Comparison]],
    shared: Optional[List[Comparison]] = None,
    cache: Optional[cache.DataCache] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Convenience function for checking an ophyd Device without creating any
    configuration instances.

    Parameters
    ----------
    device : ophyd.Device
        The device or devices to check.
    by_attr : dict of attribute to comparison list
        Comparisons to run on the given device by dotted attribute (component)
        name.
    shared : list of Comparison, optional
        Comparisons to be run on every identifier.
    cache : DataCache, optional
        The data cache to use for this and other similar comparisons.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """

    prepared = PreparedDeviceConfiguration.from_device(
        device=device,
        by_attr=by_attr,
        shared=shared,
        cache=cache,
    )
    overall = await prepared.compare()
    results = [
        get_result_from_comparison(comparison)[1] for comparison in prepared.comparisons
    ]
    return overall.severity, results


async def check_pvs(
    by_pv: Dict[str, List[Comparison]],
    shared: Optional[List[Comparison]] = None,
    cache: Optional[cache.DataCache] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Convenience function for checking a set of PVs without creating any
    configuration instances.

    Parameters
    ----------
    by_pv : dict of PV name to comparison list
        Run the provided checks on each of the given PVs.
    shared : list of Comparison, optional
        Additionally run these checks on all PVs in the ``by_pv`` dictionary.
    cache : DataCache, optional
        The data cache to use for this and other similar comparisons.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    prepared = PreparedPVConfiguration.from_pvs(
        by_pv=by_pv,
        shared=shared,
        cache=cache,
    )
    overall = await prepared.compare()
    results = [
        get_result_from_comparison(comparison)[1]
        for comparison in prepared.comparisons
    ]
    return overall.severity, results


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
            DeviceConfiguration(
                devices=[],
                by_attr={
                    "sig1": [check.Equals(value=1)],
                    "sig2": [check.Equals(value=2.5)],
                    "sig3": [check.Equals(value="abc")],
                }
            ),
            Severity.success,
            id="all_good",
        ),
        pytest.param(
            DeviceConfiguration(
                devices=[],
                by_attr={
                    "sig1": [],
                    "sig2": [],
                },
                shared=[check.Equals(value=1, atol=0)],
            ),
            Severity.error,
            id="multi_attr_no_tol",
        ),
        pytest.param(
            DeviceConfiguration(
                devices=[],
                by_attr={
                    "sig1": [],
                    "sig2": [],
                },
                shared=[check.Equals(value=1, atol=2)],
            ),
            Severity.success,
            id="multi_attr_ok",
        ),
        pytest.param(
            DeviceConfiguration(
                devices=[],
                by_attr={
                    "sig1": [],
                    "sig2": [],
                },
                shared=[
                    check.Equals(value=1, atol=2),
                    check.Equals(value=3, atol=4),
                ],
            ),
            Severity.success,
            id="multi_attr_multi_test",
        ),
        pytest.param(
            DeviceConfiguration(
                devices=[],
                by_attr={
                    "sig1": [check.Equals(value=2)],
                    "sig2": [check.Equals(value=2.5)],
                    "sig3": [check.Equals(value="abc")],
                }
            ),
            Severity.error,
            id="sig1_failure",
        ),
    ]
)


@config_and_severity
def test_serializable(config: DeviceConfiguration, severity: Severity):
    serialized = apischema.serialize(config)
    assert apischema.deserialize(DeviceConfiguration, serialized) == config


@config_and_severity
@pytest.mark.asyncio
async def test_result_severity(
    device, config: DeviceConfiguration, severity: Severity
):
    overall, _ = await check_device(
        device=device, by_attr=config.by_attr, shared=config.shared
    )
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


@pytest.mark.asyncio
async def test_at2l0_standin(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    severity = {
        0: Severity.error,
        1: Severity.success,
        2: Severity.warning,
        3: Severity.error,
    }[state1.get()]
    shared_comparisons = [
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
    ]

    by_attr = {attr: [] for attr in at2l0.state_attrs}

    overall, results = await check_device(
        at2l0, by_attr=by_attr, shared=shared_comparisons
    )
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


@pytest.mark.asyncio
async def test_at2l0_standin_reduce(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    state1.put(1.0)
    by_attr = {
        at2l0.state_attrs[0]: [
            check.Equals(
                description="Duration test",
                value=1,
                reduce_method=reduce.ReduceMethod.average,
                reduce_period=0.1,
                severity_on_failure=Severity.error,
            ),
        ],
    }

    overall, results = await check_device(at2l0, by_attr=by_attr)
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == Severity.success


@pytest.mark.asyncio
async def test_at2l0_standin_value_map(at2l0):
    state1: ophyd.Signal = getattr(at2l0, "blade_01.state.state")
    value_to_severity = {
        0: Severity.error,
        1: Severity.success,
        2: Severity.warning,
        3: Severity.error,
    }

    severity = value_to_severity[state1.get()]
    shared = [
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
    by_attr = {attr: [] for attr in at2l0.state_attrs}

    overall, results = await check_device(
        at2l0, by_attr=by_attr, shared=shared
    )
    print("\n".join(res.reason or "n/a" for res in results))
    assert overall == severity


@pytest.fixture
def mock_signal_cache() -> cache._SignalCache[ophyd.sim.FakeEpicsSignalRO]:
    mock_cache = cache._SignalCache(ophyd.sim.FakeEpicsSignalRO)
    mock_cache["pv1"].sim_put(1)
    mock_cache["pv2"].sim_put(1)
    mock_cache["pv3"].sim_put(2)
    return mock_cache


@pytest.fixture
def data_cache(
    mock_signal_cache: cache._SignalCache[ophyd.sim.FakeEpicsSignalRO],
) -> cache.DataCache:
    return cache.DataCache(
        signals=mock_signal_cache,
    )


@pytest.mark.parametrize(
    "by_pv, expected_severity",
    [
        pytest.param(
            {
                "pv1": [check.Equals(value=1)],
                "pv2": [check.Equals(value=1)],
                "pv3": [check.Equals(value=2)],
            },
            Severity.success,
            id="exact_values_ok",
        ),
        pytest.param(
            {
                "pv1": [check.Equals(value=2)],
                "pv2": [check.Equals(value=2)],
            },
            Severity.error,
            id="values_wrong",
        ),
    ],
)
@pytest.mark.asyncio
async def test_pv_config(
    data_cache: cache.DataCache,
    by_pv: Dict[str, List[Comparison]],
    expected_severity: check.Severity
):
    overall, _ = await check_pvs(by_pv, cache=data_cache)
    assert overall == expected_severity


@pytest.fixture
def get_by_config_file() -> ConfigurationFile:
    return ConfigurationFile(
        root=ConfigurationGroup(
            configs=[
                DeviceConfiguration(
                    tags=["a"],
                    devices=["dev_a", "dev_b"],
                    by_attr={"attr3": [], "attr2": []},
                    shared=[check.Equals(value=1)],
                ),
                DeviceConfiguration(
                    tags=["a"],
                    devices=["dev_b", "dev_c"],
                    by_attr={"attr1": [], "attr2": []},
                    shared=[check.Equals(value=1)],
                ),
                PVConfiguration(
                    tags=["a"],
                    by_pv={"pv1": [], "pv2": []},
                    shared=[check.Equals(value=1)],
                ),
                PVConfiguration(
                    tags=["a", "c"],
                    by_pv={"pv3": []},
                    shared=[check.Equals(value=1)],
                ),
            ]
        )
    )


def test_get_by_device(get_by_config_file: ConfigurationFile):
    a_checks = list(get_by_config_file.get_by_device("dev_a"))
    b_checks = list(get_by_config_file.get_by_device("dev_b"))
    c_checks = list(get_by_config_file.get_by_device("dev_c"))
    assert (a_checks + c_checks) == b_checks


def test_get_by_pv(get_by_config_file: ConfigurationFile):
    conf, = list(get_by_config_file.get_by_pv("pv1"))
    assert isinstance(conf, PVConfiguration)
    assert "pv1" in conf.by_pv


def test_get_by_tag(get_by_config_file: ConfigurationFile):
    assert len(list(get_by_config_file.get_by_tag("a"))) == 4
    assert len(list(get_by_config_file.get_by_tag("c"))) == 1


def test_bad_device_raises(monkeypatch):
    def get_by_name(name: str, *, client=None):
        from ..exceptions import HappiLoadError

        raise HappiLoadError("Load error")

    monkeypatch.setattr(util, "get_happi_device_by_name", get_by_name)

    config = DeviceConfiguration(
        devices=["abc"],
    )

    with pytest.raises(PreparedComparisonException) as exc_info:
        PreparedDeviceConfiguration.from_config(config)

    ex = exc_info.value
    assert isinstance(ex, PreparedComparisonException)
    assert "abc" in str(ex)
    assert ex.identifier == "abc"


def test_bad_device_in_file(monkeypatch):
    my_device = object()

    def get_by_name(name: str, *, client=None):
        from ..exceptions import HappiLoadError

        if name == "abc":
            raise HappiLoadError("Load error")
        return my_device

    monkeypatch.setattr(util, "get_happi_device_by_name", get_by_name)

    file = ConfigurationFile()
    file.root.configs = [
        DeviceConfiguration(
            devices=["abc"],
        ),
        DeviceConfiguration(
            devices=["def"],
        ),
    ]

    prepared = PreparedFile.from_config(file)
    assert len(prepared.root.prepare_failures) == 1
    assert "abc" in str(prepared.root.prepare_failures[0])
    assert len(prepared.root.configs) == 1
    assert isinstance(prepared.root.configs[0], PreparedDeviceConfiguration)
    assert prepared.root.configs[0].devices == [my_device]
