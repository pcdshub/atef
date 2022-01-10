from typing import List

import ophyd
import pytest

from .. import check
from ..check import ResultSeverity


@pytest.fixture(scope="function")
def device():
    class Device:
        name = "dev"
        sig1 = ophyd.Signal(value=1, name="dev.sig1")
        sig2 = ophyd.Signal(value=2.5, name="dev.sig2")
        sig3 = ophyd.Signal(value="abc", name="dev.sig3")

    return Device()


@pytest.mark.parametrize(
    "comparisons, severity",
    [
        pytest.param(
            [
                check.Equality(
                    attrs="sig1",
                    value=1,
                ),
                check.Equality(
                    attrs="sig2",
                    value=2.5,
                ),
                check.Equality(
                    attrs="sig3",
                    value="abc",
                ),
            ],
            ResultSeverity.success,
            id="all_good",
        ),
        pytest.param(
            [
                check.Equality(
                    attrs="sig1",
                    value=2,
                    severity_on_failure=ResultSeverity.error
                ),
                check.Equality(
                    attrs="sig2",
                    value=2.5,
                ),
                check.Equality(
                    attrs="sig3",
                    value="abc",
                ),
            ],
            ResultSeverity.error,
            id="sig1_failure",
        ),
    ]
)
def test_basic(
    device, comparisons: List[check.Comparison], severity: ResultSeverity
):
    overall, _ = check.check_device(device=device, comparisons=comparisons)
    assert overall == severity
