import contextlib
import datetime
from typing import Any, Dict, Optional

import pydm
import pydm.exception
import pytest
from qtpy import QtWidgets

from ..archive_device import ArchivedValue, ArchiverHelper


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
