import json

import apischema
import pytest

from .. import grafana
from . import conftest


def test_basic():
    assert isinstance(
        apischema.deserialize(
            grafana.AnyPanel,
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 2,
                "panels": [],
                "title": "LFE Vacuum",
                "type": "row",
            },
        ),
        grafana.RowPanel
    )

    assert isinstance(
        apischema.deserialize(
            grafana.AnyPanel,
            {
                "type": "bargauge",
            },
        ),
        grafana.BarGaugePanel
    )


@pytest.mark.parametrize(
    "dashboard_filename",
    [
        conftest.TEST_PATH / "hxr_ebd_fee_checkout_helper.json",
    ]
)
def test_full_dashboard(dashboard_filename):
    json_doc = json.load(open(dashboard_filename))
    print(apischema.deserialize(grafana.Dashboard, json_doc))
