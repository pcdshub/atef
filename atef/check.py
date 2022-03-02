from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import (Any, Dict, Generator, List, Mapping, Optional, Sequence,
                    Tuple, TypeVar, Union)

import numpy as np
import ophyd

from . import reduce, serialization
from .type_hints import Number, PrimitiveType

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

        value_desc = f"{self.value}{tolerance} (for a result of {self.severity.name})"
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

        desc = self.describe()
        if self.description:
            desc = f"{identifier_prefix}{self.description} ({desc})"
        else:
            desc = f"{identifier_prefix}{desc}"

        return Result(
            severity=self.severity_on_failure,
            reason=(
                f"{desc}: value of {value}"
            ),
        )

    def compare_signal(
        self, signal: ophyd.Signal, *, identifier: Optional[str] = None
    ) -> Result:
        try:
            try:
                if self.reduce_period and self.reduce_period > 0:
                    value = self.reduce_method.subscribe_and_reduce(
                        signal, self.reduce_period
                    )
                else:
                    value = signal.get()
            except TimeoutError:
                return Result(
                    severity=self.if_disconnected,
                    reason=f"Signal disconnected when reading: {signal}"
                )
            identifier = (
                signal.name
                if signal.attr_name.startswith("attr_")
                else signal.dotted_name
            )
            return self.compare(value, identifier=identifier)
        except Exception as ex:
            identifier = identifier or signal.name
            return Result(
                severity=Severity.internal_error,
                reason=(
                    f"Checking if {identifier!r} {self} "
                    f"raised {ex.__class__.__name__}: {ex}"
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
        comparison = "equal to" if not self.invert else "not equal to"
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
    values: List[PrimitiveType] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the comparison in words."""
        values = ", ".join(str(value) for value in self.values)
        return f"one of {values}"

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
        return f"any of:\n{comparisons}"

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


@dataclass
class IdentifierAndComparison:
    #: PV name, attribute name, or test-specific identifier.
    identifiers: List[str] = field(default_factory=list)
    #: The comparisons to perform for *each* of the identifiers.
    comparisons: List[Comparison] = field(default_factory=list)


@dataclass
class Configuration:
    #: Name tied to this configuration.
    name: Optional[str] = None
    #: Description tied to this configuration.
    description: Optional[str] = None
    #: Tags tied to this configuration.
    tags: Optional[List[str]] = None
    #: Comparison checklist for this configuration.
    checklist: List[IdentifierAndComparison] = field(default_factory=list)


@dataclass
class DeviceConfiguration(Configuration):
    #: Happi device names which give meaning to self.checklist[].identifiers.
    devices: List[str] = field(default_factory=list)


@dataclass
class PVConfiguration(Configuration):
    ...


@dataclass
class ConfigurationFile:
    """
    A configuration file comprised of a number of devices/PV configurations.
    """

    configs: List[Union[PVConfiguration, DeviceConfiguration]]

    def get_by_device(self, name: str) -> Generator[DeviceConfiguration, None, None]:
        """Get all configurations that match the device name."""
        ...

    def get_by_pv(self, pvname: str) -> Generator[PVConfiguration, None, None]:
        """Get all configurations that match the PV name."""
        ...

    def get_by_tag(self, *tags: str) -> Generator[Configuration, None, None]:
        """Get all configurations that match the tag name."""
        ...


def check_device(
    device: ophyd.Device, checklist: Sequence[IdentifierAndComparison]
) -> Tuple[Severity, List[Result]]:
    """
    Check a given device using the list of comparisons.

    Parameters
    ----------
    device : ophyd.Device
        The device to check.

    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple attributes may
        share the same checks.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []
    for checklist_item in checklist:
        for comparison in checklist_item.comparisons:
            for attr in checklist_item.identifiers:
                full_attr = f"{device.name}.{attr}"
                logger.debug("Checking %s.%s with comparison %s", full_attr, comparison)
                signal = getattr(device, attr, None)
                if signal is None:
                    result = Result(
                        severity=Severity.internal_error,
                        reason=(
                            f"Attribute {full_attr} does not exist on class "
                            f"{type(device).__name__}"
                        ),
                    )
                else:
                    result = comparison.compare_signal(signal, identifier=full_attr)

                if result.severity > overall:
                    overall = result.severity
                results.append(result)

    return overall, results


def check_pvs(
    checklist: Sequence[IdentifierAndComparison],
    *,
    cache: Optional[Mapping[str, ophyd.Signal]] = None,
) -> Tuple[Severity, List[Result]]:
    """
    Check a PVConfiguration.

    Parameters
    ----------
    checklist : sequence of IdentifierAndComparison
        Comparisons to run on the given device.  Multiple PVs may share the
        same checks.

    Returns
    -------
    overall_severity : Severity
        Maximum severity found when running comparisons.

    results : list of Result
        Individual comparison results.
    """
    overall = Severity.success
    results = []
    cache = cache or get_signal_cache()
    for checklist_item in checklist:
        for comparison in checklist_item.comparisons:
            for pvname in checklist_item.identifiers:
                logger.debug("Checking %s.%s with comparison %s", pvname, comparison)
                signal = cache[pvname]
                # signal.wait_for_connection()
                result = comparison.compare_signal(signal, identifier=pvname)

                if result.severity > overall:
                    overall = result.severity
                results.append(result)

    return overall, results


_CacheSignalType = TypeVar("_CacheSignalType")


@dataclass
class _SignalCache(Mapping[str, _CacheSignalType]):
    signal_type_cls: _CacheSignalType
    pv_to_signal: Dict[str, _CacheSignalType] = field(default_factory=dict)

    def __getitem__(self, pv: str) -> _CacheSignalType:
        """Get a PV from the cache."""
        if pv not in self.pv_to_signal:
            self.pv_to_signal[pv] = self.signal_type_cls(pv, name=pv)

        return self.pv_to_signal[pv]

    def __iter__(self):
        yield from self.pv_to_signal

    def __len__(self):
        return len(self.pv_to_signal)

    def clear(self) -> None:
        """Clear the signal cache."""
        for sig in self.pv_to_signal.values():
            try:
                sig.destroy()
            except Exception:
                logger.debug("Destroy failed for signal %s", sig.name)
        self.pv_to_signal.clear()


_signal_cache = None


def get_signal_cache() -> _SignalCache[ophyd.EpicsSignalRO]:
    """Get the global EpicsSignal cache."""
    global _signal_cache
    if _signal_cache is None:
        _signal_cache = _SignalCache(ophyd.EpicsSignalRO)
    return _signal_cache
