from typing import Any

import apischema
import pytest

from .. import check, config, tools
from ..check import Severity
from ..config import IdentifierAndComparison, ToolConfiguration

config_and_severity = pytest.mark.parametrize(
    "conf, severity",
    [
        pytest.param(
            ToolConfiguration(
                tool=tools.Ping(
                    hosts=["127.0.0.1"],
                    count=1,
                ),
                checklist=[
                    IdentifierAndComparison(
                        ids=["max_time"],
                        comparisons=[check.LessOrEqual(value=1)]
                    ),
                ]
            ),
            Severity.success,
            id="all_good",
        ),
        pytest.param(
            ToolConfiguration(
                tool=tools.Ping(
                    hosts=["127.0.0.1"],
                    count=1,
                ),
                checklist=[
                    IdentifierAndComparison(
                        ids=["max_time"],
                        comparisons=[check.Less(value=0.0)]
                    ),
                ]
            ),
            Severity.error,
            id="must_fail",
        ),
    ]
)


@config_and_severity
def test_serializable(conf: ToolConfiguration, severity: Severity):
    serialized = apischema.serialize(conf)
    assert apischema.deserialize(ToolConfiguration, serialized) == conf


@config_and_severity
@pytest.mark.asyncio
async def test_result_severity(
    conf: ToolConfiguration, severity: Severity
):
    overall, results = await config.check_tool(conf.tool, conf.checklist)
    assert overall == severity


@pytest.mark.parametrize(
    "tool, key, valid",
    [
        (tools.Ping(), "max_time", True),
        (tools.Ping(), "max_time.abc", False),
        (tools.Ping(), "times.hostname", True),
        (tools.Ping(), "badkey", False),
    ]
)
def test_result_keys(
    tool: tools.Tool, key: str, valid: bool
):
    if not valid:
        with pytest.raises(ValueError) as ex:
            tool.check_result_key(key)
        print("Failed check, as expected:\n", ex)
    else:
        tool.check_result_key(key)


class _TestItem:
    a = {
        "b": [1, 2, 3]
    }


@pytest.mark.parametrize(
    "value, key, expected",
    [
        # abc[1] = "b"
        ("abc", "1", "b"),
        # [1, 2, 3][1] = 2
        ([1, 2, 3], "1", 2),
        # dict(a=dict(b="c"))["a"]["b"] = "c"
        ({"a": {"b": "c"}}, "a.b", "c"),
        # dict(a=dict(b="c"))["a"]["b"][1] = 2
        ({"a": {"b": [1, 2, 3]}}, "a.b.1", 2),
        # _TestItem.a.b[1]
        (_TestItem, "a.b.1", 2),
    ]
)
def test_get_result_value_by_key(
    value: Any, key: str, expected: Any
):
    assert tools.get_result_value_by_key(value, key) == expected


@pytest.mark.parametrize(
    "output, expected",
    [
        pytest.param(
            "Reply from 127.0.0.1: bytes=32 time<1ms TTL=128",
            1.0e-3,
            id="win32_less",
        ),
        pytest.param(
            "Reply from 127.0.0.1: bytes=32 time=10ms TTL=128",
            10e-3,
            id="win32_equal",
        ),
        pytest.param(
            "64 bytes from 1.1.1.1: icmp_seq=0 ttl=55 time=11.000 ms",
            11e-3,
            id="macos",
        ),
        pytest.param(
            "64 bytes from 1.1.1.1: icmp_seq=1 ttl=50 time=3.00 ms",
            3e-3,
            id="linux",
        ),
    ],
)
def test_ping_regex(
    output: str,
    expected: float,
):
    result = tools.PingResult.from_output("", output)
    assert abs(result.max_time - expected) < 1e-6
