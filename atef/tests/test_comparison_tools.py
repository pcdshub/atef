import sys
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import apischema
import pytest

from .. import check, config, tools
from ..cache import DataCache
from ..config import Comparison, PreparedToolConfiguration, ToolConfiguration
from ..enums import Severity
from ..result import Result


async def check_tool(
    tool: tools.Tool,
    by_attr: Dict[str, List[Comparison]],
    shared: Optional[List[Comparison]] = None,
    cache: Optional[DataCache] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Convenience function for checking a tool without creating any configuration
    instances.

    Parameters
    ----------
    tool : Tool
        The tool instance defining which tool to run and with what arguments.
    cache : DataCache, optional
        The data cache to use for this tool and other similar comparisons.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    prepared = PreparedToolConfiguration.from_tool(
        tool=tool,
        by_attr=by_attr,
        shared=shared,
        cache=cache,
    )

    overall = await prepared.compare()
    results = [
        config.get_result_from_comparison(comparison)[1]
        for comparison in prepared.comparisons
    ]
    return overall.severity, results


config_and_severity = pytest.mark.parametrize(
    "conf, severity",
    [
        pytest.param(
            ToolConfiguration(
                tool=tools.Ping(
                    hosts=["127.0.0.1"],
                    count=1,
                ),
                by_attr={
                    "max_time": [check.LessOrEqual(value=1)],
                },
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
                by_attr={
                    "max_time": [check.Less(value=0.0)],
                },
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
@pytest.mark.skipif(sys.platform == 'win32', reason='Ping tool fails on Windows')
async def test_result_severity(
    conf: ToolConfiguration, severity: Severity
):
    overall, results = await check_tool(
        conf.tool, by_attr=conf.by_attr, shared=conf.shared
    )
    assert overall == severity, results


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


@dataclass
class CustomToolResult(tools.ToolResult):
    run_count: int


@dataclass
class CustomTool(tools.Tool):
    result: ClassVar[Optional[CustomToolResult]] = None

    async def run(self) -> CustomToolResult:
        print("Running custom tool...")
        if CustomTool.result is None:
            CustomTool.result = CustomToolResult(result=Result(), run_count=0)

        CustomTool.result.run_count += 1
        return CustomTool.result


@pytest.mark.asyncio
async def test_tool_cache():
    cache = DataCache()
    tool = CustomTool()
    first_data = await cache.get_tool_data(tool)
    assert isinstance(first_data, CustomToolResult)
    assert first_data.run_count == 1

    second_data = await cache.get_tool_data(tool)
    assert first_data is second_data
    assert isinstance(second_data, CustomToolResult)
    assert second_data.run_count == 1


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
