from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Generator, List, Optional, Sequence, Union, Iterator

import numpy as np
import ophyd

from . import exceptions, reduce, serialization, util
from .enums import Severity
from .exceptions import (ComparisonError, ComparisonException,
                         ComparisonWarning, PreparedComparisonException,
                         DynamicValueError)
from .type_hints import Number, PrimitiveType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Result:
    severity: Severity = Severity.success
    reason: Optional[str] = None

    @classmethod
    def from_exception(cls, error: Exception) -> Result:
        """Convert an error exception to a Result."""
        severity = Severity.internal_error
        if isinstance(error, exceptions.ConfigFileHappiError):
            reason = f"Failed to load: {error.dev_name}"
        elif isinstance(error, PreparedComparisonException):
            if error.comparison is not None:
                severity = error.comparison.severity_on_failure
            reason = (
                f"Failed to prepare comparison {error.name!r} for "
                f"{error.identifier!r}: {error}"
            )
        else:
            reason = f"Failed to load: {type(error).__name__}: {error}"

        return cls(
            severity=severity,
            reason=reason,
        )


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


@dataclass
class Value:
    """A primitive value with optional metadata."""
    #: The value for comparison.
    value: Union[PrimitiveType, DynamicValue]
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

        # Since "success" is likely implicit here, only specify the resulting
        # severity in the description when it's not "success":
        #   at2l0.blade_01.state.state not equal to 0
        #   (for a result of success): Filter is moving
        # becomes
        #   at2l0.blade_01.state.state not equal to 0: Filter is moving
        if self.severity == Severity.success:
            value_desc = f"{self.value}{tolerance}"
        else:
            value_desc = f"{self.value}{tolerance} (for a result of {self.severity.name})"

        if self.description:
            return f"{value_desc}: {self.description}"
        return value_desc

    def compare(self, value: PrimitiveType) -> bool:
        """Compare the provided value with this one, using tolerance settings."""
        if isinstance(self.value, DynamicValue):
            our_value = self.value.get()
        else:
            our_value = self.value
        if self.rtol is not None or self.atol is not None:
            return np.isclose(
                value, our_value,
                rtol=(self.rtol or 0.0),
                atol=(self.atol or 0.0)
            )
        return value == our_value


class DynamicValue:
    """
    A primitive value from an external source that may change over time.

    This necessarily picks up a runtime performance cost and getting
    the value is not guaranteed to be successful. If unsuccessful,
    This will raise a DynamicValueError from the original exception.
    """
    _last_value: Optional[PrimitiveType] = None

    def __str__(self) -> str:
        kwds = (f"{key}={value}" for key, value in asdict(self))
        return f"{type(self)}({', '.join(kwds)}) [{self._last_value}]"

    def get(self) -> PrimitiveType:
        """
        Call the child's get routine and wrap for error handling.

        This also sets up a cached last value to use in the repr.
        """
        try:
            self._last_value = self._get()
        except Exception as exc:
            raise DynamicValueError(
                'Error loading dynamic value'
            ) from exc
        else:
            return self._last_value

    def _get(self) -> PrimitiveType:
        """
        Implement in child class to get the current value from source.
        """
        raise NotImplementedError()


@dataclass
class EpicsValue(DynamicValue):
    """
    A primitive value sourced from an EPICS PV.

    This will create and cache an EpicsSignalRO object, and defer
    to that signal's get handling.
    """
    #: The EPICS PV to use.
    pvname: str

    def _get(self) -> PrimitiveType:
        if not hasattr(self, "_signal"):
            self._signal = ophyd.EpicsSignalRO(self.pvname)
        return self._signal.get()


@dataclass
class HappiValue(DynamicValue):
    """
    A primitive value sourced from a specific happi device signal.

    This will query happi to cache a Signal object, and defer to
    that signal's get handling.
    """
    #: The name of the device to use.
    device_name: str
    #: The attr name of the signal to get from.
    signal_attr: str

    def _get(self) -> PrimitiveType:
        if not hasattr(self, "_signal"):
            device = util.get_happi_device_by_name(self.device_name)
            self._signal = getattr(device, self.signal_attr)
        return self._signal.get()


@dataclass
class ValueRange:
    """A range of primitive values with optional metadata."""
    #: The low value for comparison.
    low: Union[Number, DynamicValue]
    #: The high value for comparison.
    high: Union[Number, DynamicValue]
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
        if isinstance(self.low, DynamicValue):
            our_low = self.low.get()
        else:
            our_low = self.low
        if isinstance(self.high, DynamicValue):
            our_high = self.high.get()
        else:
            our_high = self.high
        in_range = _is_in_range(
            value, low=our_low, high=our_high, inclusive=self.inclusive
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

    Subclasses of Comparison will be serialized as a tagged union.  This means
    that the subclass name will be used as an identifier for the generated
    serialized dictionary (and JSON object).
    """
    # Short name to use in the UI
    name: Optional[str] = None

    #: Description tied to this comparison.
    description: Optional[str] = None

    #: Invert the comparison's result.  Normally, a valid comparison - that is,
    #: one that evaluates to True - is considered successful.  When `invert` is
    #: set, such a comparison would be considered a failure.
    invert: bool = False

    #: Period over which the comparison will occur, where multiple samples
    #: may be acquired prior to a result being available.
    reduce_period: Optional[Number] = None

    #: Reduce collected samples by this reduce method.
    reduce_method: reduce.ReduceMethod = reduce.ReduceMethod.average

    #: If applicable, request and compare string values rather than the default
    #: specified.
    string: Optional[bool] = None

    #: If the comparison fails, use this result severity.
    severity_on_failure: Severity = Severity.error

    #: If disconnected and unable to perform the comparison, set this
    #: result severity.
    if_disconnected: Severity = Severity.error

    def __call__(self, value: Any) -> Optional[Result]:
        """Run the comparison against ``value``."""
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
            return self.describe()
        except Exception as ex:
            return (
                f"{self.__class__.__name__}.describe() failure "
                f"({ex.__class__.__name__}: {ex})"
            )
        # return f"{self.__class__.__name__}({desc})"

    def compare(self, value: Any, identifier: Optional[str] = None) -> Result:
        """
        Compare the provided value using the comparator's settings.

        Parameters
        ----------
        value :
            The value to compare.

        identifier : str, optional
            An identifier that goes along with the provided value.  Used for
            severity result descriptions.
        """
        if value is None:
            return Result(
                severity=self.if_disconnected,
                reason="Value unset (i.e., disconnected)",
            )

        identifier_prefix = f"{identifier} " if identifier else ""

        try:
            passed = self._compare(value)
        except ComparisonException as ex:
            return Result(
                severity=ex.severity,
                reason=f"{identifier_prefix}Value {value!r} {ex.severity.name}: {ex}",
            )
        except Exception as ex:
            return Result(
                severity=Severity.internal_error,
                reason=(
                    f"{identifier_prefix}Value {value!r} "
                    f"raised {ex.__class__.__name__}: {ex}"
                ),
            )

        if self.invert:
            passed = not passed

        if passed:
            return success

        desc = f"{identifier_prefix}{self.describe()}"
        return Result(
            severity=self.severity_on_failure,
            reason=(
                f"{desc}: value of {value}"
            ),
        )

    def get_data_for_signal(self, signal: ophyd.Signal) -> Any:
        """
        Get data for the given signal, according to the string and data
        reduction settings.
        """
        if self.reduce_period and self.reduce_period > 0:
            return self.reduce_method.subscribe_and_reduce(
                signal, self.reduce_period
            )

        if self.string:
            return signal.get(as_string=True)

        return signal.get()

    def compare_signal(
        self, signal: ophyd.Signal, *, identifier: Optional[str] = None
    ) -> Result:
        """
        Compare the provided signal's value using the comparator's settings.

        Parameters
        ----------
        signal : ophyd.Signal
            The signal to get data from and run a comparison on.

        identifier : str, optional
            An identifier that goes along with the provided signal.  Used for
            severity result descriptions.  Defaults to the signal's dotted
            name.
        """
        try:
            identifier = identifier or signal.dotted_name
            try:
                value = self.get_data_for_signal(signal)
            except TimeoutError:
                return Result(
                    severity=self.if_disconnected,
                    reason=f"Signal disconnected when reading: {signal}"
                )
            return self.compare(value, identifier=identifier)
        except Exception as ex:
            return Result(
                severity=Severity.internal_error,
                reason=(
                    f"Checking if {identifier!r} {self} "
                    f"raised {ex.__class__.__name__}: {ex}"
                ),
            )


@dataclass
class Equals(Comparison):
    value: Union[PrimitiveType, DynamicValue] = 0.0
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
        comparison = "equal to" if not self.invert else "not equal to"
        return f"{comparison} {self._value}"

    def _compare(self, value: PrimitiveType) -> bool:
        return self._value.compare(value)


@dataclass
class NotEquals(Comparison):
    # Less confusing shortcut for `Equals(..., invert=True)`
    value: Union[PrimitiveType, DynamicValue] = 0
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
        comparison = "equal to" if self.invert else "not equal to"
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
    values: List[Union[PrimitiveType, DynamicValue]] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the comparison in words."""
        values = ", ".join(str(value) for value in self.values)
        return f"one of {values}"

    def _compare(self, value: PrimitiveType) -> bool:
        return value in self.values or value in self.dynamic_values()

    def dynamic_values(self) -> Iterator[PrimitiveType]:
        for value in self.values:
            if isinstance(value, DynamicValue):
                yield value.get()


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
        return f"any of:\n{comparisons}"

    def _compare(self, value: PrimitiveType) -> bool:
        return any(
            comparison._compare(value)
            for comparison in self.comparisons
        )


@dataclass
class Greater(Comparison):
    """Comparison: value > self.value."""
    value: Union[Number, DynamicValue] = 0

    def describe(self) -> str:
        return f"> {self.value}"

    def _compare(self, value: Number) -> bool:
        if isinstance(self.value, DynamicValue):
            our_value = self.value.get()
        else:
            our_value = self.value
        return value > our_value


@dataclass
class GreaterOrEqual(Comparison):
    """Comparison: value >= self.value."""
    value: Union[Number, DynamicValue] = 0

    def describe(self) -> str:
        return f">= {self.value}"

    def _compare(self, value: Number) -> bool:
        if isinstance(self.value, DynamicValue):
            our_value = self.value.get()
        else:
            our_value = self.value
        return value >= our_value


@dataclass
class Less(Comparison):
    """Comparison: value < self.value."""
    value: Union[Number, DynamicValue] = 0

    def describe(self) -> str:
        return f"< {self.value}"

    def _compare(self, value: Number) -> bool:
        if isinstance(self.value, DynamicValue):
            our_value = self.value.get()
        else:
            our_value = self.value
        return value < our_value


@dataclass
class LessOrEqual(Comparison):
    """Comparison: value <= self.value."""
    value: Union[Number, DynamicValue] = 0

    def describe(self) -> str:
        return f"<= {self.value}"

    def _compare(self, value: Number) -> bool:
        if isinstance(self.value, DynamicValue):
            our_value = self.value.get()
        else:
            our_value = self.value
        return value <= our_value


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
    low: Union[Number, DynamicValue] = 0
    #: The high end of the range, which must be >= low.
    high: Union[Number, DynamicValue] = 0
    #: The low end of the warning range, which must be <= warn_high.
    warn_low: Optional[Union[Number, DynamicValue]] = None
    #: The high end of the warning range, which must be >= warn_low.
    warn_high: Optional[Union[Number, DynamicValue]] = None
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
