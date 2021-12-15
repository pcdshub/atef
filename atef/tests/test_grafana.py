import json

import apischema
import pytest

from ..grafana import AnyPanel, Dashboard


def test_basic():
    print(
        apischema.deserialize(
            AnyPanel,
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 2,
                "panels": [],
                "title": "LFE Vacuum",
                "type": "row",
            },
        )
    )

    print(
        apischema.deserialize(
            AnyPanel,
            {
                "type": "bargauge",
            },
        )
    )


@pytest.mark.parametrize(
    "dashboard_filename",
    [
        "hxr_ebd_fee_checkout_helper.json",
    ]
)
def test_full_dashboard(dashboard_filename):
    json_doc = json.load(open(dashboard_filename))
    print(apischema.deserialize(Dashboard, json_doc))
