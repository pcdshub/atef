from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, List, Optional, Sequence, Tuple,
                    Union, cast)

import numpy as np
import ophyd

from . import serialization

Number = Union[int, float]

logger = logging.getLogger(__name__)


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
class Value:
    """A primitive value with optional metadata."""
    #: The value for comparison.
    value: PrimitiveType
    #: A description of what the value represents.
    description: str = ""
    #: Relative tolerance value.
    rtol: Optional[Number] = None
    #: Absolute tolerance value.
    atol: Optional[Number] = None
    #: Severity to set on a match (if applicable).
    severity: ResultSeverity = ResultSeverity.success

    def __str__(self) -> str:
        if self.rtol is not None or self.atol is not None:
            rtol = f"rtol={self.rtol}" if self.rtol is not None else ""
            atol = f"atol={self.atol}" if self.atol is not None else ""
            tolerance = " within " + ", ".join(tol for tol in (rtol, atol) if tol)
        else:
            tolerance = ""

        value_desc = f"{self.value}{tolerance} -> {self.severity.name}"
        if self.description:
            return f"{self.description} ({value_desc})"
        return value_desc

    def compare(self, value: PrimitiveType) -> bool:
        """Compare the provided value with this one, using tolerance settings."""
        if self.rtol is not None or self.atol is not None:
            return np.isclose(
                value, self.value,
                rtol=(self.rtol or 0.0),
                atol=(self.atol or 0.0)
            )
        return value == self.value


@dataclass
@serialization.as_tagged_union
class Comparison:
    """
    Base class for all atef value comparisons.
    """

    #: Description tied to this comparison.
    description: Optional[str] = None

    #: Invert the comparison's result.  Normally, a valid comparison - that is,
    #: one that evaluates to True - is considered successful.  When `invert` is
    #: set, such a comparison would be considered a failure.
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

    def __str__(self) -> str:
        try:
            desc = self.describe()
        except Exception as ex:
            desc = (
                f"{self.__class__.__name__}.describe() failure "
                f"({ex.__class__.__name__}: {ex})"
            )
        return f"{self.__class__.__name__}({desc})"

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
class Equals(Comparison):
    value: PrimitiveType = 0
    rtol: Optional[Number] = None
    atol: Optional[Number] = None

    @property
    def _value(self) -> Value:
        return Value(
            value=self.value,
            rtol=self.rtol,
            atol=self.atol,
            description=self.description or "",
        )

    def describe(self) -> str:
        """Describe the equality comparison in words."""
        comparison = "Equal to" if not self.invert else "Not equal to"
        return f"{comparison} {self._value}"

    def _compare(self, value: PrimitiveType) -> bool:
        return self._value.compare(value)


@dataclass
class NotEquals(Comparison):
    # Less confusing shortcut for `Equals(..., invert=True)`
    value: PrimitiveType = 0
    rtol: Optional[Number] = None
    atol: Optional[Number] = None

    @property
    def _value(self) -> Value:
        return Value(
            value=self.value,
            rtol=self.rtol,
            atol=self.atol,
            description=self.description or "",
        )

    def describe(self) -> str:
        """Describe the equality comparison in words."""
        comparison = "Equal to" if self.invert else "Not equal to"
        return f"{comparison} {self._value}"

    def _compare(self, value: PrimitiveType) -> bool:
        return not self._value.compare(value)


@dataclass
class ValueSet(Comparison):
    """A set of values with corresponding severities and descriptions."""
    # Review: really a "value sequence"/list as the first ones have priority,
    # but that sounds like a vector version of "Value" above; better ideas?
    values: Sequence[Value] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the equality comparison in words."""
        values = "\n".join(
            str(value)
            for value in self.values
        )
        return f"Any of:\n{values}"

    def _compare(self, value: PrimitiveType) -> bool:
        for compare_value in self.values:
            if compare_value.compare(value):
                if compare_value.severity == ResultSeverity.success:
                    return True

                reason = f"== {compare_value}"
                if compare_value.severity == ResultSeverity.warning:
                    raise ComparisonWarning(reason)
                raise ComparisonError(reason)
        return False


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


AttrToChecks = Dict[
    str,
    Union[Comparison, Sequence[Comparison]],
]


@dataclass
class DeviceConfiguration:
    #: Description tied to this comparison.
    description: Optional[str] = None

    # TODO: default severity settings?
    checks: AttrToChecks = field(default_factory=dict)


def _single_attr_comparison(
    device: ophyd.Device, attr: str, comparison: Comparison
) -> Result:
    try:
        signal = getattr(device, attr)
        try:
            value = signal.get()
        except TimeoutError:
            return Result(
                severity=comparison.if_disconnected,
                reason=f"Signal disconnected when reading: {signal}"
            )
        return comparison.compare(value)
    except Exception as ex:
        return Result(
            severity=ResultSeverity.internal_error,
            reason=(
                f"Checking attribute {attr!r} with {comparison} "
                f"raised {ex.__class__.__name__}: {ex}"
            ),
        )


def check_device(
    device: ophyd.Device, attr_to_checks: AttrToChecks
) -> Tuple[ResultSeverity, List[Result]]:
    """
    Check a given device using the list of comparisons.

    Parameters
    ----------
    device : ophyd.Device
        The device to check.

    attr_to_checks : dict of attribute to Comparison(s)
        Comparisons to run on the given device.

    Returns
    -------
    overall_severity : ResultSeverity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = ResultSeverity.success
    results = []
    for attrs, checks in attr_to_checks.items():
        checks = tuple([checks] if isinstance(checks, Comparison) else checks)
        for comparison in checks:
            for attr in attrs.strip().split():
                logger.debug(
                    "Checking %s.%s with comparison %s",
                    device.name, attr, comparison
                )
                result = _single_attr_comparison(device, attr, comparison)
                if result.severity > overall:
                    overall = result.severity
                results.append(result)

    return overall, results
