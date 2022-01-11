import pytest

from .. import check
from ..check import (Comparison, Equals, NotEquals, PrimitiveType, Result,
                     ResultSeverity)


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


success = Result(severity=ResultSeverity.success)


@_parametrize(
    Equals(value=1),
    [1, ResultSeverity.success],
    [0, ResultSeverity.error],
)
def test_equality_basic(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    Equals(value=1, invert=True),
    [0, ResultSeverity.success],
    [1, ResultSeverity.error],
)
def test_equality_inverted(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    NotEquals(value=1),
    [1, ResultSeverity.error],
    [0, ResultSeverity.success],
)
def test_not_equals_basic(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    Equals(value=1, atol=1),
    [0, ResultSeverity.success],
    [1, ResultSeverity.success],
    [2, ResultSeverity.success],
    [-1, ResultSeverity.error],
)
def test_equality_with_atol(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
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
    [0, ResultSeverity.error],
    [1, ResultSeverity.success],
    [2, ResultSeverity.success],
    [3, ResultSeverity.success],
    [4, ResultSeverity.error],
)
def test_any_comparison(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.AnyValue(
        values=[1, 2, 3],
    ),
    [0, ResultSeverity.error],
    [1, ResultSeverity.success],
    [2, ResultSeverity.success],
    [3, ResultSeverity.success],
    [4, ResultSeverity.error],
)
def test_any_value(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.Greater(value=2),
    [1, ResultSeverity.error],
    [2, ResultSeverity.error],
    [3, ResultSeverity.success],
    [4, ResultSeverity.success],
)
def test_greater(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    check.GreaterOrEqual(value=2),
    [1, ResultSeverity.error],
    [2, ResultSeverity.success],
    [3, ResultSeverity.success],
    [4, ResultSeverity.success],
)
def test_greater_equal(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    # < 1 error, 1 ~ 3 warn, 5 ~ 6 warn, > 6 error
    check.Range(low=1, warn_low=3, warn_high=5, high=6, inclusive=True),
    [0, ResultSeverity.error],
    [1, ResultSeverity.warning],
    [2, ResultSeverity.warning],
    [3, ResultSeverity.success],
    [4, ResultSeverity.success],
    [5, ResultSeverity.success],
    [6, ResultSeverity.warning],
    [7, ResultSeverity.error],
)
def test_range_inclusive(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)


@_parametrize(
    # < 1 error, 1 ~ 3 warn, 5 ~ 6 warn, > 6 error
    check.Range(low=1, warn_low=3, warn_high=5, high=6, inclusive=False),
    [0, ResultSeverity.error],
    [1, ResultSeverity.error],
    [2, ResultSeverity.warning],
    [3, ResultSeverity.warning],
    [4, ResultSeverity.success],
    [5, ResultSeverity.warning],
    [6, ResultSeverity.error],
    [7, ResultSeverity.error],
)
def test_range_exclusive(
    comparison: Comparison, value: PrimitiveType, result: ResultSeverity
):
    assert comparison(value).severity == result
    print(comparison(value).reason)
