import contextlib
import datetime
import json
import pathlib
import tempfile
from functools import partial
from typing import Any, Dict, List, Optional, get_args

import happi
import ophyd
import pydm
import pydm.exception
import pytest
import simplejson
from apischema import ValidationError, deserialize
from qtpy import QtWidgets

import atef
from atef.cache import get_signal_cache
from atef.check import Equals, Greater, GreaterOrEqual, LessOrEqual, NotEquals
from atef.config import (AnyConfiguration, ConfigurationFile,
                         ConfigurationGroup, DeviceConfiguration,
                         PVConfiguration, TemplateConfiguration,
                         ToolConfiguration)
from atef.find_replace import RegexFindReplace
from atef.procedure import (AnyProcedure, ComparisonToTarget, ProcedureFile,
                            SetValueStep, ValueToTarget)
from atef.tools import Ping
from atef.type_hints import AnyDataclass
from atef.util import ophyd_cleanup
from atef.widgets.config.page import ComparisonPage, PageWidget
from atef.widgets.config.window import DualTree

from ..archive_device import ArchivedValue, ArchiverHelper

TEST_PATH = pathlib.Path(__file__).parent.resolve()
CONFIG_PATH = TEST_PATH / "configs"


def passive_checkout_configs() -> List[pathlib.Path]:
    filenames = ['lfe.json', 'all_fields.json', 'blank_passive.json',
                 'ping_localhost.json']
    config_paths = [CONFIG_PATH / fn for fn in filenames]
    return config_paths


def active_checkout_configs() -> List[pathlib.Path]:
    filenames = ['active_test.json', 'blank_active.json']
    config_paths = [CONFIG_PATH / fn for fn in filenames]
    return config_paths


PASSIVE_CONFIG_PATHS = passive_checkout_configs()
ACTIVE_CONFIG_PATHS = active_checkout_configs()
ALL_CONFIG_PATHS = PASSIVE_CONFIG_PATHS + ACTIVE_CONFIG_PATHS


@pytest.fixture(params=PASSIVE_CONFIG_PATHS)
def passive_config_path(request) -> pathlib.Path:
    return request.param


@pytest.fixture(params=ACTIVE_CONFIG_PATHS)
def active_config_path(request) -> pathlib.Path:
    return request.param


@pytest.fixture(params=ALL_CONFIG_PATHS)
def all_config_path(request) -> pathlib.Path:
    return request.param


class MockEpicsArch:
    """
    Mock archapp.EpicsArch.

    Parameters
    ----------
    database : Dict[str, ArchivedValue]
        Dictionary of pv name to ArchivedValue.

    default_value : ArchivedValue, optional
        If provided, PVs not in the database will be assigned this value.
    """

    database: Dict[str, ArchivedValue]
    default_value: Optional[ArchivedValue]

    def __init__(
        self,
        database: Dict[str, ArchivedValue],
        default_value: Optional[ArchivedValue] = None,
    ):
        self.database = database
        self.default_value = default_value

    def get_snapshot(
        self, *pvnames: str, at: datetime.datetime
    ) -> Dict[str, Dict[str, Any]]:
        result = {}
        for pv in pvnames:
            value = self.database.get(pv, self.default_value)
            if value is not None:
                result[pv] = value.to_archapp()

        return result

    @contextlib.contextmanager
    def use(self):
        helper = ArchiverHelper.instance()
        orig = helper.appliances
        helper.appliances = [self]
        try:
            yield
        finally:
            helper.appliances = orig


@pytest.fixture(scope='session', autouse=True)
def qapp(pytestconfig):
    global application
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = pydm.PyDMApplication(use_main_window=False)
    return application


@pytest.fixture(scope='session', autouse=True)
def ophyd_setup_teardown():
    """
    Set up ophyd to not spend a long time waiting for connections
    Clean up ophyd - avoid teardown errors by stopping callbacks.
    """
    ophyd.signal.EpicsSignalBase.set_defaults(connection_timeout=0.25)
    yield
    ophyd_cleanup()


@pytest.fixture(scope='function', autouse=True)
def non_interactive_qt_application(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'exec_', lambda x: 1)
    monkeypatch.setattr(QtWidgets.QApplication, 'exit', lambda x: 1)
    monkeypatch.setattr(
        pydm.exception, 'raise_to_operator', lambda *_, **__: None
    )


def load_config(config_path):
    with open(config_path, 'r') as fd:
        serialized = json.load(fd)

    try:
        data = deserialize(ConfigurationFile, serialized)
    except ValidationError:
        try:
            data = deserialize(ProcedureFile, serialized)
        except Exception as ex:
            raise RuntimeError(f'failed to open checkout {ex}')

    return data


@pytest.fixture(params=ALL_CONFIG_PATHS)
def all_loaded_config(request):
    return load_config(request.param)


@pytest.fixture(params=PASSIVE_CONFIG_PATHS)
def passive_loaded_config(request):
    return load_config(request.param)


@pytest.fixture(params=ACTIVE_CONFIG_PATHS)
def active_loaded_config(request):
    return load_config(request.param)


class EnumDevice(ophyd.sim.SynAxis):
    enum = ophyd.Component(ophyd.sim.EnumSignal, value='OUT',
                           enum_strings=('OUT', 'YAG', 'UNKNOWN'), kind='hinted')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The sim signal doesn't have self.enum_strs, just self._enum_strs
        setattr(self.enum, 'enum_strs', self.enum._enum_strs)
        # our signals appear to return ints, instead of strings by default
        self.enum.get = partial(self.enum.get, as_string=False)


@pytest.fixture(scope='session')
def sim_db() -> List[happi.OphydItem]:
    items = []
    sim1 = {
        'name': 'motor1',
        'z': 400,
        '_id': 'motor1',
        'prefix': 'MY:MOTOR1',
        'beamline': 'LCLS',
        'type': 'OphydItem',
        'device_class': 'ophyd.sim.SynAxis',
        'args': list(),
        'kwargs': {'name': '{{name}}', 'prefix': '{{prefix}}'},
        'location_group': 'LOC',
        'functional_group': 'FUNC',
    }

    sim2 = {
        'name': 'motor2',
        'z': 200,
        '_id': 'motor2',
        'prefix': 'MY:MOTOR2',
        'beamline': 'LCLS',
        'type': 'OphydItem',
        'device_class': 'ophyd.sim.SynAxis',
        'args': list(),
        'kwargs': {'name': '{{name}}', 'prefix': '{{prefix}}'},
        'location_group': 'LOC',
        'functional_group': 'FUNC',
    }

    sim3 = {
        'name': 'enum1',
        'z': 500,
        '_id': 'enum1',
        'prefix': 'MY:MOTORENUM',
        'beamline': 'LCLS',
        'type': 'OphydItem',
        'device_class': 'atef.tests.conftest.EnumDevice',
        'args': list(),
        'kwargs': {'name': '{{name}}', 'prefix': '{{prefix}}'},
        'location_group': 'LOC',
        'functional_group': 'FUNC',
    }

    sim4 = {
        'name': 'enum2',
        'z': 600,
        '_id': 'enum2',
        'prefix': 'MY:MOTORENUM',
        'beamline': 'LCLS',
        'type': 'OphydItem',
        'device_class': 'atef.tests.conftest.EnumDevice',
        'args': list(),
        'kwargs': {'name': '{{name}}', 'prefix': '{{prefix}}'},
        'location_group': 'LOC',
        'functional_group': 'FUNC',
    }

    for info in [sim1, sim2, sim3, sim4]:
        items.append(happi.OphydItem(**info))
    return items


@pytest.fixture(scope='session')
def mockjsonclient():
    # Write underlying database
    with tempfile.NamedTemporaryFile(mode='w') as handle:
        simplejson.dump({}, handle)
        handle.flush()  # flush buffer to write file
        # Return handle name
        db = happi.backends.json_db.JSONBackend(handle.name)
        yield happi.Client(database=db)
        # tempfile will be deleted once context manager is resolved


@pytest.fixture(scope='session')
def happi_client(mockjsonclient: happi.Client, sim_db: List[happi.OphydItem]):
    for item in sim_db:
        mockjsonclient.add_item(item)
    return mockjsonclient


@pytest.fixture(scope='session')
def monkeymodule():
    # monkeypatch requires function scope, which works fine for the first test
    # that uses the final mock_happi fixture.  Note that all fixtures called by
    # mock_happi must also be function-scoped as a result
    # Subsequent tests will fail due to the
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope='session', autouse=True)
def mock_happi(monkeymodule: Any, happi_client: happi.Client):
    # give `pvname` to all the components, since they don't exist on sim devices
    for result in happi_client.search():
        dev = result.get()
        for cpt_name in dev.component_names:
            cpt = getattr(dev, cpt_name)
            if not hasattr(cpt, 'pvname'):
                setattr(cpt, 'pvname', f'{dev.prefix}:{cpt_name}')

    def return_client():
        return happi_client

    monkeymodule.setattr(atef.util, 'get_happi_client', return_client)
    # Only one of these should be needed, but after adding the PlanStep
    # tests both were needed (test_plan_step failed without atef.util,
    # test_gather_pvs failed with atef.util and without from_config)
    monkeymodule.setattr(happi.Client, 'from_config', return_client)


@pytest.fixture(scope='function')
def mock_pv(monkeypatch: Any):
    # Register the PV "MY:PV" to the signal cache
    sig_cache = get_signal_cache()
    signal = sig_cache['MY:PV']
    monkeypatch.setattr(signal, 'get', lambda: 1)
    monkeypatch.setattr(signal, 'read', lambda: {
        'MY:PV': {'value': 1, 'timestamp': datetime.datetime.now().timestamp()}
    })


@pytest.fixture
def configuration_group():
    group = ConfigurationGroup(
        name='config_group',
        values={'integer': 1, 'string': 'a sample string'},
        configs=[
            PVConfiguration(
                name='pv config 1',
                by_pv={"GDET:FEE1:241:ENRC": [Greater(value=-10)]}
            ),
            PVConfiguration(
                name='pv config 2',
                shared=[NotEquals(value=0)]
            ),
        ]
    )
    return group


@pytest.fixture
def make_page():
    def make_page_fn(cfg: AnyDataclass) -> PageWidget:
        if isinstance(cfg, get_args(AnyConfiguration)):
            file = ConfigurationFile()
            file.root.configs.append(cfg)
        elif isinstance(cfg, get_args(AnyProcedure)):
            file = ProcedureFile()
            file.root.steps.append(cfg)
        else:
            raise NotImplementedError()

        tree = DualTree(orig_file=file, widget_cache_size=50)
        tree.select_by_data(cfg)
        cfg_page = tree.current_widget

        return cfg_page

    return make_page_fn


@pytest.fixture
def pv_configuration():
    group = PVConfiguration(
        name='pv config 1',
        by_pv={"MY:PREFIX:hello": [Greater(value=-10), Equals(value=.1)]},
        shared=[LessOrEqual(value=44)]
    )
    return group


@pytest.fixture
def device_configuration():
    group = DeviceConfiguration(
        name='device config 1',
        devices=['motor1', 'motor2'],
        by_attr={"setpoint": [Equals(value=5)],
                 "readback": [GreaterOrEqual(value=9.4)]},
        shared=[]
    )
    return group


@pytest.fixture
def tool_configuration():
    group = ToolConfiguration(
        name='ping tool',
        tool=Ping(hosts=['psbuild-rhel7', 'localhost']),
        shared=[Equals(value=3)]
    )
    return group


@pytest.fixture
def template_configuration():
    replace_title_path = [
        ('atef.config.ConfigurationFile', 'root'),
        ('atef.config.ConfigurationGroup', 'name')
    ]
    replace_title_edit = RegexFindReplace(
        path=replace_title_path,
        search_regex='root',
        replace_text='template replaced title'
    )
    group = TemplateConfiguration(
        name='template all fields',
        filename=CONFIG_PATH / 'blank_passive.json',
        edits=[replace_title_edit],
    )
    return group


@pytest.fixture
def set_value_step():
    action = ValueToTarget(
        name="set to 1", pv="MY:PREFIX:dt", value=1.0,
        timeout=2.0, settle_time=2.0
    )

    check = ComparisonToTarget(
        pv="MY:PREFIX:dt",
        comparison=Equals(
            name="eq_check",
            description="simple verify",
            value=1.0,
        )
    )
    step = SetValueStep(
        actions=[action],
        success_criteria=[check],
    )

    return step


@pytest.fixture
def comparison_page():
    comp = Equals(value=3)
    return ComparisonPage(comp)
