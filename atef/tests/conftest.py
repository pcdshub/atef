import contextlib
import datetime
import pathlib
from typing import Any, Dict, List, Optional

import pydm
import pydm.exception
import pytest
from qtpy import QtWidgets

from ..archive_device import ArchivedValue, ArchiverHelper

TEST_PATH = pathlib.Path(__file__).parent.resolve()
CONFIG_PATH = TEST_PATH / "configs"


def passive_checkout_configs() -> List[pathlib.Path]:
    filenames = ['lfe.json', 'all_fields.json', 'ping_localhost.json']
    config_paths = [CONFIG_PATH / fn for fn in filenames]
    return config_paths


def active_checkout_configs() -> List[pathlib.Path]:
    filenames = ['active_test.json']
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


@pytest.fixture(scope='function', autouse=True)
def non_interactive_qt_application(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'exec_', lambda x: 1)
    monkeypatch.setattr(QtWidgets.QApplication, 'exit', lambda x: 1)
    monkeypatch.setattr(
        pydm.exception, 'raise_to_operator', lambda *_, **__: None
    )
