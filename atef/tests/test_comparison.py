import pytest

from .. import check
from ..check import Comparison, Equals, NotEquals, PrimitiveType, Severity
from ..result import Result


def _parametrize(comparison, *value_and_result):
    m1 = pytest.mark.parametrize(
        "comparison",
        [
            pytest.param(comparison),
        ]
    )
    m2 = pytest.mark.parametrize(
        "value, result",
        [pytest.param(*item) for item in value_and_result],
    )

    def wrapper(test_func):
        return m1(m2(test_func))

    return wrapper


success = Result(severity=Severity.success)


@_parametrize(
    Equals(value=1),
    [1, Severity.success],
    [0, Severity.error],
)
def test_equality_basic(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    Equals(value=1, invert=True),
    [0, Severity.success],
    [1, Severity.error],
)
def test_equality_inverted(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    NotEquals(value=1),
    [1, Severity.error],
    [0, Severity.success],
)
def test_not_equals_basic(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    Equals(value=1, atol=1),
    [0, Severity.success],
    [1, Severity.success],
    [2, Severity.success],
    [-1, Severity.error],
)
def test_equality_with_atol(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.AnyComparison(
        comparisons=[
            check.Equals(value=1),
            check.Equals(value=2),
            check.Equals(value=3),
        ],
    ),
    [0, Severity.error],
    [1, Severity.success],
    [2, Severity.success],
    [3, Severity.success],
    [4, Severity.error],
)
def test_any_comparison(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.AnyValue(
        values=[1, 2, 3],
    ),
    [0, Severity.error],
    [1, Severity.success],
    [2, Severity.success],
    [3, Severity.success],
    [4, Severity.error],
)
def test_any_value(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.Greater(value=2),
    [1, Severity.error],
    [2, Severity.error],
    [3, Severity.success],
    [4, Severity.success],
)
def test_greater(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.GreaterOrEqual(value=2),
    [1, Severity.error],
    [2, Severity.success],
    [3, Severity.success],
    [4, Severity.success],
)
def test_greater_equal(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    # < 1 error, 1 ~ 3 warn, 5 ~ 6 warn, > 6 error
    check.Range(low=1, warn_low=3, warn_high=5, high=6, inclusive=True),
    [0, Severity.error],
    [1, Severity.warning],
    [2, Severity.warning],
    [3, Severity.warning],
    [4, Severity.success],
    [5, Severity.warning],
    [6, Severity.warning],
    [7, Severity.error],
)
def test_range_inclusive(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    # < 1 error, 1 ~ 3 warn, 5 ~ 6 warn, > 6 error
    check.Range(low=1, warn_low=3, warn_high=5, high=6, inclusive=False),
    [0, Severity.error],
    [1, Severity.error],
    [2, Severity.warning],
    [3, Severity.success],
    [4, Severity.success],
    [5, Severity.success],
    [6, Severity.error],
    [7, Severity.error],
)
def test_range_exclusive(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    # < 1 error, 1 ~ 3 warn, 5 ~ 6 warn, > 6 error
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
    [-1, Severity.error],
    [0, Severity.error],
    [1, Severity.success],
    [2, Severity.warning],
    [3, Severity.error],
)
def test_value_set(
    comparison: Comparison, value: PrimitiveType, result: Severity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)
