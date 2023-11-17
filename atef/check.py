"""
Dataclasses for describing comparisons.  Comparisons generally subclass ``Comparison``,
which hold ``Value`` and ``DynamicValue`` objects.  Comparisons involving
``DynamicValue`` must be prepared before comparisons can be run.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from typing import Any, Generator, Iterable, List, Optional, Sequence

import numpy as np
import ophyd

from . import reduce, serialization, util
from .cache import DataCache
from .enums import Severity
from .exceptions import (ComparisonError, ComparisonException,
                         ComparisonWarning, DynamicValueError,
                         UnpreparedComparisonException)
from .result import Result, successful_result
from .type_hints import Number, PrimitiveType

logger = logging.getLogger(__name__)


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


@dataclass
class Value:
    """A primitive (static) value with optional metadata."""
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

    def get(self) -> PrimitiveType:
        """Get the value from this container."""
        return self.value

    def compare(self, value: PrimitiveType) -> bool:
        """Compare the provided value with this one, using tolerance settings."""
        if ((self.rtol is not None or self.atol is not None)
                and not isinstance(value, (str, bool))):
            return np.isclose(
                value, self.value,
                rtol=(self.rtol or 0.0),
                atol=(self.atol or 0.0)
            )

        return value == self.value


@dataclass
@serialization.as_tagged_union
class DynamicValue:
    """
    A primitive value from an external source that may change over time.
    This necessarily picks up a runtime performance cost and getting
    the value is not guaranteed to be successful. If unsuccessful,
    this will raise a DynamicValueError from the original exception.

    Includes settings for reduction of multiple samples.

    Value will be cached on preparation, and this value used for comparisons
    """
    #: Value is now optional, and will be filled in when prepared
    value: Optional[PrimitiveType] = None

    #: Period over which the value will be read
    reduce_period: Optional[Number] = None

    #: Reduce collected samples by this reduce method
    reduce_method: reduce.ReduceMethod = reduce.ReduceMethod.average

    #: If applicable, request and compare string values rather than the default
    string: Optional[bool] = None

    def __str__(self) -> str:
        kwds = (f"{key}={value}" for key, value in asdict(self).items()
                if (value is not None))
        return f"{type(self).__name__}({', '.join(kwds)}) [{self.value}]"

    def get(self) -> PrimitiveType:
        """
        Return the cached value from `prepare`, or raise a `DynamicValueError` if there is no such value.
        """
        if self.value is not None:
            return self.value
        else:
            raise DynamicValueError('Dynamic value has not been prepared.')

    async def prepare(self, cache: DataCache) -> None:
        """
        Implement in child class to get the current value from source.
        Should set the self.value
        """
        raise NotImplementedError()


@dataclass
class EpicsValue(DynamicValue):
    """
    A primitive value sourced from an EPICS PV.
    This will create and cache an EpicsSignalRO object, and defer
    to that signal's get handling.
    """
    # as of 3.10, use kw_only=True to allow mandatory arguments after the inherited
    # optional ones.  Until then, these must have a default
    #: The EPICS PV to use.
    pvname: str = ''

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare the EpicsValue.  Accesses the EPICS PV using the data
        cache provided.

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.  If unspecified, a new data
            cache will be instantiated.

        Raises
        ------
        DynamicValueError
            if the EpicsValue does not have a pv specified
        """
        if not self.pvname:
            raise DynamicValueError('No PV specified')

        if cache is None:
            cache = DataCache()

        data = await cache.get_pv_data(
            self.pvname.strip(),
            reduce_period=self.reduce_period,
            reduce_method=self.reduce_method,
            string=self.string or False,
        )
        self.value = data


@dataclass
class HappiValue(DynamicValue):
    """
    A primitive value sourced from a specific happi device signal.
    This will query happi to cache a Signal object, and defer to
    that signal's get handling.
    """
    #: The name of the device to use.
    device_name: str = ''
    #: The attr name of the signal to get from.
    signal_attr: str = ''

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare the HappiValue. Accesses the specified device and component
        from the happi database.

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.  If unspecified, a new data
            cache will be instantiated.

        Raises
        ------
        DynamicValueError
            if the EpicsValue does not have a pv specified
        """
        if not self.device_name or not self.signal_attr:
            raise DynamicValueError('Happi value is unspecified')

        if cache is None:
            cache = DataCache()

        device = util.get_happi_device_by_name(self.device_name)
        signal = getattr(device, self.signal_attr)
        data = await cache.get_signal_data(
            signal,
            reduce_period=self.reduce_period,
            reduce_method=self.reduce_method,
            string=self.string or False,
        )
        self.value = data


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

    def __post_init__(self):
        self.is_prepared: bool = False

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
        if not self.is_prepared:
            raise UnpreparedComparisonException(
                f"Comparison {self} was not prepared."
            )

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

        # Some comparisons may be done with array values; require that
        # all match for a success here:
        if isinstance(passed, Iterable):
            passed = all(passed)

        if passed:
            return successful_result()

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

        Parameters
        ----------
        signal : ophyd.Signal
            The signal.

        Returns
        -------
        Any
            The acquired data.

        Raises
        ------
        TimeoutError
            If the get operation times out.
        """
        return reduce.get_data_for_signal(
            signal,
            reduce_period=self.reduce_period,
            reduce_method=self.reduce_method,
            string=self.string or False,
        )

    async def get_data_for_signal_async(
        self,
        signal: ophyd.Signal,
        *,
        executor: Optional[concurrent.futures.Executor] = None
    ) -> Any:
        """
        Get data for the given signal, according to the string and data
        reduction settings.

        Parameters
        ----------
        signal : ophyd.Signal
            The signal.
        executor : concurrent.futures.Executor, optional
            The executor to run the synchronous call in.  Defaults to
            the loop-defined default executor.

        Returns
        -------
        Any
            The acquired data.

        Raises
        ------
        TimeoutError
            If the get operation times out.
        """
        return await reduce.get_data_for_signal_async(
            signal,
            reduce_period=self.reduce_period,
            reduce_method=self.reduce_method,
            string=self.string or False,
            executor=executor,
        )

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Implement in subclass to grab and cache dynamic values.
        This is expected to set self.is_prepared to True if
        successful.
        """
        # TODO: think about renaming this method, collides with PreparedComparison
        # Why would we have to prepare the comparison AND make a prepared comparison?
        raise NotImplementedError()


@dataclass
class BasicDynamic(Comparison):
    value_dynamic: Optional[DynamicValue] = None

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare this comparison's value data.  If a value_dynamic is specified,
        prepare its data

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.
        """
        if self.value_dynamic is not None:
            await self.value_dynamic.prepare(cache)
            self.value = self.value_dynamic.get()
        self.is_prepared = True


@dataclass
class Equals(BasicDynamic):
    value: PrimitiveType = 0.0
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
        if self.value_dynamic is None:
            dynamic = " "
        else:
            dynamic = f" {self.value_dynamic}"
        return f"{comparison}{dynamic}{self._value}"

    def _compare(self, value: PrimitiveType) -> bool:
        return self._value.compare(value)


@dataclass
class NotEquals(BasicDynamic):
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
        if self.value_dynamic is None:
            dynamic = " "
        else:
            dynamic = f" {self.value_dynamic} "
        return f"{comparison}{dynamic}{self._value}"

    def _compare(self, value: PrimitiveType) -> bool:
        return not self._value.compare(value)


@dataclass
class ValueSet(Comparison):
    """A set of values with corresponding severities and descriptions."""
    # Review: really a "value sequence"/list as the first ones have priority,
    # but that sounds like a vector version of "Value" above; better ideas?
    values: Sequence[Value] = field(default_factory=list)
    values_dynamic: Sequence[Optional[DynamicValue]] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the equality comparison in words."""
        accumulated_values = []
        for value, dynamic in zip_longest(self.values, self.values_dynamic):
            if dynamic is None:
                accumulated_values.append(value)
            else:
                accumulated_values.append(dynamic)
        values = "\n".join(
            str(value)
            for value in accumulated_values
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

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare this comparison's value data.  If a value_dynamic is specified,
        prepare its data

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.
        """
        # TODO revisit this logic, seems to overwrite normal values.
        # How are these populated?  is there a value for every dynamic?
        for value, dynamic in zip(self.values, self.values_dynamic):
            if dynamic is not None:
                await dynamic.prepare(cache)
                value.value = dynamic.get()
        self.is_prepared = True


@dataclass
class AnyValue(Comparison):
    """Comparison passes if the value is in the ``values`` list."""
    values: List[PrimitiveType] = field(default_factory=list)
    values_dynamic: List[Optional[DynamicValue]] = field(default_factory=list)

    def describe(self) -> str:
        """Describe the comparison in words."""
        accumulated_values = []
        for value, dynamic in zip_longest(self.values, self.values_dynamic):
            if dynamic is None:
                accumulated_values.append(value)
            else:
                accumulated_values.append(dynamic)
        values = ", ".join(str(value) for value in accumulated_values)
        return f"one of {values}"

    def _compare(self, value: PrimitiveType) -> bool:
        return value in self.values

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare this comparison's value data.  Prepares each DynamicValue in the
        value_dynamic list, if specified.

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.
        """
        for index, dynamic in enumerate(self.values_dynamic):
            if dynamic is not None:
                await dynamic.prepare(cache)
                self.values[index] = dynamic.get()
        self.is_prepared = True


@dataclass
class AnyComparison(Comparison):
    """Comparison passes if *any* contained comparison passes."""
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

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare this comparison's value data.  Prepares all comparisons contained
        in this comparison.

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.
        """
        # TODO make sure all comparisons have a prepare?  Or allow for case where
        # non-dynamic comparisons that don't have prepare
        for comp in self.comparisons:
            await comp.prepare(cache)
        self.is_prepared = True

    def children(self) -> List[Comparison]:
        """Return children of this group, as a tree view might expect"""
        return self.comparisons

    def replace_comparison(
        self,
        old_comp: Comparison,
        new_comp: Comparison,
    ) -> None:
        """
        Replace ``old_comp`` with ``new_comp`` in this dataclass.
        A common method for all dataclasses that hold comparisons.

        Parameters
        ----------
        old_comp : Comparison
            Comparsion to replace
        new_comp : Comparison
            Comparison to replace ``old_comp`` with
        """
        util.replace_in_list(
            old=old_comp,
            new=new_comp,
            item_list=self.comparisons,
        )


@dataclass
class Greater(BasicDynamic):
    """Comparison: value > self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"> {self.value_dynamic or self.value}: {self.description}"

    def _compare(self, value: Number) -> bool:
        return value > self.value


@dataclass
class GreaterOrEqual(BasicDynamic):
    """Comparison: value >= self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f">= {self.value_dynamic or self.value}: {self.description}"

    def _compare(self, value: Number) -> bool:
        return value >= self.value


@dataclass
class Less(BasicDynamic):
    """Comparison: value < self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"< {self.value_dynamic or self.value}: {self.description}"

    def _compare(self, value: Number) -> bool:
        return value < self.value


@dataclass
class LessOrEqual(BasicDynamic):
    """Comparison: value <= self.value."""
    value: Number = 0

    def describe(self) -> str:
        return f"<= {self.value_dynamic or self.value}: {self.description}"

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
    low_dynamic: Optional[DynamicValue] = None
    #: The high end of the range, which must be >= low.
    high: Number = 0
    high_dynamic: Optional[DynamicValue] = None
    #: The low end of the warning range, which must be <= warn_high.
    warn_low: Optional[Number] = None
    warn_low_dynamic: Optional[DynamicValue] = None
    #: The high end of the warning range, which must be >= warn_low.
    warn_high: Optional[Number] = None
    warn_high_dynamic: Optional[DynamicValue] = None
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
        text = "\n".join(str(range_) for range_ in self.ranges)
        if self.low_dynamic is not None:
            text.append(f"\n Dynamic low value: {self.low_dynamic}")
        if self.high_dynamic is not None:
            text.append(f"\n Dynamic high value: {self.high_dynamic}")
        if self.warn_low_dynamic is not None:
            text.append(f"\n Dynamic warn_low value: {self.warn_low_dynamic}")
        if self.warn_high_dynamic is not None:
            text.append(
                f"\n Dynamic warn_high value: {self.warn_high_dynamic}"
            )
        return text

    def _compare(self, value: Number) -> bool:
        for range_ in self.ranges:
            if range_.compare(value):
                _raise_for_severity(range_.severity, str(range_))

        return True

    async def prepare(self, cache: Optional[DataCache] = None) -> None:
        """
        Prepare this comparison's value data.  If a value_dynamic is specified,
        prepare its data.  Prepares the high/low limits along with dynamic high/low
        warning values if they exist

        Parameters
        ----------
        cache : DataCache, optional
            The data cache instance, if available.
        """
        if self.low_dynamic is not None:
            await self.low_dynamic.prepare(cache)
            self.low = self.low_dynamic.get()
        if self.high_dynamic is not None:
            await self.high_dynamic.prepare(cache)
            self.high = self.high_dynamic.get()
        if self.warn_low_dynamic is not None:
            await self.warn_low_dynamic.prepare(cache)
            self.warn_low = self.warn_low_dynamic.get()
        if self.warn_high_dynamic is not None:
            await self.warn_high_dynamic.prepare(cache)
            self.warn_high = self.warn_high_dynamic.get()
        self.is_prepared = True


ALL_COMPARISONS = [Equals, NotEquals, Greater, GreaterOrEqual, Less, LessOrEqual,
                   Range, ValueSet, AnyValue, AnyComparison]
