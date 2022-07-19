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
