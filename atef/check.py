from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Union, cast

import numpy as np

Number = Union[int, float]


class ComparisonError(Exception):
    """Raise this exception to error out in a comparator."""


class ComparisonWarning(Exception):
    """Raise this exception to warn in a comparator."""


class ResultSeverity(enum.IntEnum):
    success = 0
    warning = 1
    error = 2
    internal_error = 3


class ReduceMethod(str, enum.Enum):
    average = "average"
    median = "median"
    sum = "sum"
    min = "min"
    max = "max"
    std = "std"

    @property
    def method(self) -> Callable:
        return {
            ReduceMethod.average: np.average,
            ReduceMethod.median: np.median,
            ReduceMethod.sum: np.sum,
            ReduceMethod.min: np.min,
            ReduceMethod.max: np.max,
            ReduceMethod.std: np.std,
        }[self]

    def reduce(self, values: Sequence[PrimitiveType]) -> PrimitiveType:
        """
        Reduce the given values according to the configured method.

        For example, if ``method`` is `ReduceMethod.average`, use `np.average`
        to reduce the provided values into a scalar result.
        """
        return self.method(np.asarray(values))


@dataclass(frozen=True)
class Result:
    severity: ResultSeverity = ResultSeverity.success
    reason: Optional[str] = None


def _is_in_range(
    value: Number, low: Number, high: Number, inclusive: bool = True
) -> bool:
    """Is `value` in the range of low to high?"""
    if inclusive:
        return low <= value <= high
    return low < value < high


success = Result()
PrimitiveType = Union[str, int, bool, float]


@dataclass
class Comparison:
    """
    Base class for all atef value comparisons.
    """

    #: Description tied to this comparison.
    description: Optional[str] = None

    #: Invert the comparison's result.
    invert: bool = False

    #: Period over which the comparison will occur, where multiple samples
    #: may be acquired prior to a result being available.
    period: Optional[int] = None

    #: Reduce collected samples by this method.
    method: ReduceMethod = ReduceMethod.average

    #: If the comparison fails, use this result severity.
    severity_on_failure: ResultSeverity = ResultSeverity.error

    #: If disconnected and unable to perform the comparison, set this
    #: result severity.
    if_disconnected: ResultSeverity = ResultSeverity.error

    def __call__(self, value: Any) -> Optional[Result]:
        return self.compare(value)

    def describe(self) -> str:
        """
        Human-readable description of the comparison operation itself.

        To be implemented by subclass.
        """
        raise NotImplementedError()

    def _compare(self, value: PrimitiveType) -> bool:
        """
        Compare a non-None value using the configured settings.

        To be implemented by subclass.
        """
        raise NotImplementedError()

    @property
    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.describe()})"

    def compare(self, value: Any) -> Result:
        """Compare the provided value using the comparator's settings."""
        if value is None:
            return Result(
                severity=self.if_disconnected,
                reason="Value unset (i.e., disconnected)",
            )

        try:
            passed = self._compare(value)
        except ComparisonError as ex:
            return Result(
                severity=self.severity_on_failure,
                reason=f"Value {value!r} errored: {ex}",
            )
        except ComparisonWarning as ex:
            return Result(
                severity=ResultSeverity.warning,
                reason=f"Value {value!r} warned: {ex}",
            )
        except Exception as ex:
            return Result(
                severity=ResultSeverity.internal_error,
                reason=f"Value {value!r} raised {ex.__class__.__name__}: {ex}",
            )

        if self.invert:
            passed = not passed

        if passed:
            return success

        desc = self.describe()
        if self.description:
            desc = f"{self.description} ({desc})"

        return Result(
            severity=self.severity_on_failure,
            reason=(
                f"{value} failed: {desc}"
            ),
        )


@dataclass
class Equality(Comparison):
    value: PrimitiveType = 0
    rtol: Optional[Number] = None
    atol: Optional[Number] = None

    def describe(self) -> str:
        """Describe the equality comparison in words."""
        comparison = "==" if not self.invert else "!="
        if self.rtol is not None or self.atol is not None:
            tolerance = f" within rtol={self.rtol}, atol={self.atol}"
        else:
            tolerance = ""
        return f"{comparison} {self.value}{tolerance}"

    def _compare(self, value: PrimitiveType) -> bool:
        if self.rtol is not None or self.atol is not None:
            return np.isclose(
                value, self.value,
                rtol=(self.rtol or 0.0),
                atol=(self.atol or 0.0)
            )
        return value == self.value


@dataclass
class AnyValue(Comparison):
    """Comparison passes if the value is in the ``values`` list."""
    values: List[PrimitiveType] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the comparison in words."""
        values = ", ".join(str(value) for value in self.values)
        return f"One of {values}"

    def _compare(self, value: PrimitiveType) -> bool:
        return value in self.values


@dataclass
class AnyComparison(Comparison):
    """Comparison passes if the value matches *any* comparison."""
    comparisons: List[Comparison] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the comparison in words."""
        comparisons = "\n".join(
            comparison.describe()
            for comparison in self.comparisons
        )
        return f"Any of:\n{comparisons}"

    def _compare(self, value: PrimitiveType) -> bool:
        return any(
            comparison._compare(value)
            for comparison in self.comparisons
        )


@dataclass
class Greater(Comparison):
    value: Number = 0

    def describe(self) -> str:
        return f"> {self.value}"

    def _compare(self, value: Number) -> bool:
        return value > self.value


@dataclass
class GreaterOrEqual(Comparison):
    value: Number = 0

    def describe(self) -> str:
        return f">= {self.value}"

    def _compare(self, value: Number) -> bool:
        return value >= self.value


@dataclass
class Less(Comparison):
    value: Number = 0

    def describe(self) -> str:
        return f"< {self.value}"

    def _compare(self, value: Number) -> bool:
        return value < self.value


@dataclass
class LessOrEqual(Comparison):
    value: Number = 0

    def describe(self) -> str:
        return f"<= {self.value}"

    def _compare(self, value: Number) -> bool:
        return value <= self.value


@dataclass
class Range(Comparison):
    low: Optional[Number] = None
    high: Optional[Number] = None
    warn_low: Optional[Number] = None
    warn_high: Optional[Number] = None
    inclusive: bool = True

    def describe(self) -> str:
        checks = []
        open_paren, close_paren = "[]" if self.inclusive else "()"
        if None not in (self.low, self.high):
            checks.append(f"Error if {open_paren}{self.low}, {self.high}{close_paren}")
        if None not in (self.warn_low, self.warn_high):
            checks.append(
                f"Warn if {open_paren}{self.warn_low}, {self.warn_high}{close_paren}"
            )
        return "\n".join(checks)

    def _compare(self, value: Number) -> bool:
        if None not in (self.low, self.high):
            in_range = _is_in_range(
                value,
                low=cast(Number, self.low),
                high=cast(Number, self.high),
                inclusive=self.inclusive,
            )
            if not in_range:
                return False

        if None not in (self.warn_low, self.warn_high):
            in_range = _is_in_range(
                value,
                low=cast(Number, self.warn_low),
                high=cast(Number, self.warn_high),
                inclusive=self.inclusive,
            )

            if not in_range:
                raise ComparisonWarning(
                    f"In warning range ({self.describe()})"
                )
        return True
