from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import (Any, Callable, ClassVar, Dict, Generator, List, Optional,
                    Sequence, Tuple, Type, Union)

import numpy as np
import ophyd

from . import serialization

Number = Union[int, float]

logger = logging.getLogger(__name__)


class Severity(enum.IntEnum):
    success = 0
    warning = 1
    error = 2
    internal_error = 3


class ComparisonException(Exception):
    """Raise this exception to exit a comparator and set severity."""
    severity = Severity.success


class ComparisonError(ComparisonException):
    """Raise this exception to error out in a comparator."""
    severity = Severity.error


class ComparisonWarning(ComparisonException):
    """Raise this exception to warn in a comparator."""
    severity = Severity.warning


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
    severity: Severity = Severity.success
    reason: Optional[str] = None


def _is_in_range(
    value: Number, low: Number, high: Number, inclusive: bool = True
) -> bool:
    """Is `value` in the range of low to high?"""
    if inclusive:
        return low <= value <= high
    return low < value < high


def _raise_for_severity(severity: Severity, reason: str):
    if severity == Severity.success:
        return True
    if severity == Severity.warning:
        raise ComparisonWarning(reason)
    raise ComparisonError(reason)


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
    severity: Severity = Severity.success

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
class ValueRange:
    """A range of primitive values with optional metadata."""
    #: The low value for comparison.
    low: Number
    #: The high value for comparison.
    high: Number
    #: Should the low and high values be included in the range?
    inclusive: bool = True
    #: Check if inside the range.
    in_range: bool = True
    #: A description of what the value represents.
    description: str = ""
    #: Severity to set on a match (if applicable).
    severity: Severity = Severity.success

    def __str__(self) -> str:
        open_paren, close_paren = "[]" if self.inclusive else "()"
        inside = "inside" if self.in_range else "outside"
        range_desc = f"{inside} {open_paren}{self.low}, {self.high}{close_paren}"
        value_desc = f"{range_desc} -> {self.severity.name}"
        if self.description:
            return f"{self.description} ({value_desc})"
        return value_desc

    def compare(self, value: Number) -> bool:
        """Compare the provided value with this range."""
        in_range = _is_in_range(
            value, low=self.low, high=self.high, inclusive=self.inclusive
        )
        if self.in_range:
            # Normal functionality - is value in the range?
            return in_range

        # Inverted - is value outside of the range?
        return not in_range


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

    #: If applicable, request and compare string values rather than the default
    #: specified.
    string: Optional[bool] = None

    #: If the comparison fails, use this result severity.
    severity_on_failure: Severity = Severity.error

    #: If disconnected and unable to perform the comparison, set this
    #: result severity.
    if_disconnected: Severity = Severity.error

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
        except ComparisonException as ex:
            return Result(
                severity=ex.severity,
                reason=f"Value {value!r} {ex.severity.name}: {ex}",
            )
        except Exception as ex:
            return Result(
                severity=Severity.internal_error,
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
                _raise_for_severity(
                    compare_value.severity, reason=f"== {compare_value}"
                )
                return True
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
    """Comparison: value > self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"> {self.value}"

    def _compare(self, value: Number) -> bool:
        return value > self.value


@dataclass
class GreaterOrEqual(Comparison):
    """Comparison: value >= self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f">= {self.value}"

    def _compare(self, value: Number) -> bool:
        return value >= self.value


@dataclass
class Less(Comparison):
    """Comparison: value < self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"< {self.value}"

    def _compare(self, value: Number) -> bool:
        return value < self.value


@dataclass
class LessOrEqual(Comparison):
    """Comparison: value <= self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"<= {self.value}"

    def _compare(self, value: Number) -> bool:
        return value <= self.value


@dataclass
class Range(Comparison):
    """
    A range comparison.

    Notes
    -----
    If the following inequality holds, the range comparison will succeed:

        low < value < high  (inclusive=False)
        low <= value <= high  (inclusive=True)

    Additionally, warning levels may be specified.  These should be configured
    such that:

        low <= warn_low <= warn_high <= high

    With these warning levels configured, a warning will be raised when the
    value falls within the following ranges.  For ``inclusive=False``::

        low < value < warn_low
        warn_high < value < high

    or, when ``inclusive=True``:

        low <= value <= warn_low
        warn_high <= value <= high
    """
    #: The low end of the range, which must be <= high.
    low: Number = 0
    #: The high end of the range, which must be >= low.
    high: Number = 0
    #: The low end of the warning range, which must be <= warn_high.
    warn_low: Optional[Number] = None
    #: The high end of the warning range, which must be >= warn_low.
    warn_high: Optional[Number] = None
    #: Should the low and high values be included in the range?
    inclusive: bool = True

    @property
    def ranges(self) -> Generator[ValueRange, None, None]:
        yield ValueRange(
            low=self.low,
            high=self.high,
            description=self.description or "",
            inclusive=self.inclusive,
            in_range=False,
            severity=self.severity_on_failure,
        )

        if self.warn_low is not None and self.warn_high is not None:
            yield ValueRange(
                low=self.low,
                high=self.warn_low,
                description=self.description or "",
                inclusive=self.inclusive,
                in_range=True,
                severity=Severity.warning,
            )
            yield ValueRange(
                low=self.warn_high,
                high=self.high,
                description=self.description or "",
                inclusive=self.inclusive,
                in_range=True,
                severity=Severity.warning,
            )

    def describe(self) -> str:
        return "\n".join(str(range_) for range_ in self.ranges)

    def _compare(self, value: Number) -> bool:
        for range_ in self.ranges:
            if range_.compare(value):
                _raise_for_severity(range_.severity, str(range_))

        return True


ItemToChecks = Dict[
    str,
    Union[Comparison, Sequence[Comparison]],
]


@dataclass
class Configuration:
    #: Description tied to this comparison.
    description: Optional[str] = None


@dataclass
class DeviceConfiguration(Configuration):
    #: Dictionary of attribute name to sequence of checks (or single check).
    checks: ItemToChecks = field(default_factory=dict)


@dataclass
class PVConfiguration(Configuration):
    #: Dictionary of PV name to sequence of checks (or single check).
    checks: ItemToChecks = field(default_factory=dict)


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
            severity=Severity.internal_error,
            reason=(
                f"Checking attribute {attr!r} with {comparison} "
                f"raised {ex.__class__.__name__}: {ex}"
            ),
        )


def check_device(
    device: ophyd.Device, attr_to_checks: ItemToChecks
) -> Tuple[Severity, List[Result]]:
    """
    Check a given device using the list of comparisons.

    Parameters
    ----------
    device : ophyd.Device
        The device to check.

    attr_to_checks : dict of attribute to Comparison(s)
        Comparisons to run on the given device.  Multiple attributes may
        share the same checks. To specify multiple attribute names, delimit
        the names by spaces.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
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


class _PVDevice(ophyd.Device):
    _pv_to_attr_: ClassVar[Dict[str, str]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set all signals to be just their PV name for now.
        for attr in self.component_names:
            sig = getattr(self, attr)
            sig.name = getattr(sig, "pvname", sig.name)


_pv_to_device_cache = {}


def pvs_to_device(pvs: Sequence[str]) -> Type[_PVDevice]:
    """Take PV-based items to check and make a device out of them."""
    pv_names = tuple(
        sum((item.strip().split() for item in sorted(pvs)), [])
    )
    if pv_names in _pv_to_device_cache:
        return _pv_to_device_cache[pv_names]

    pv_to_attr = {
        pv: f"attr_{idx}"
        for idx, pv in enumerate(pv_names)
    }
    components = {
        attr: ophyd.device.Component(ophyd.EpicsSignalRO, pv, kind="config")
        for pv, attr in pv_to_attr.items()
    }
    device = ophyd.device.create_device_from_components(
        name="PVDevice",
        base_class=_PVDevice,
        **components
    )
    device._pv_to_attr_ = pv_to_attr
    _pv_to_device_cache[pv_names] = device
    return device


def pv_config_to_device_config(
    config: PVConfiguration,
) -> Tuple[Type[_PVDevice], DeviceConfiguration]:
    """Take PV-based items to check and make a device out of them."""
    device = pvs_to_device(list(config.checks))
    attr_checks: ItemToChecks = {}
    for item, checks in sorted(config.checks.items()):
        attrs = " ".join(device._pv_to_attr_[pv] for pv in item.strip().split())
        attr_checks[attrs] = checks

    return device, DeviceConfiguration(
        description=config.description,
        checks=attr_checks,
    )
