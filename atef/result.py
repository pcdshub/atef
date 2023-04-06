"""
Results for comparisons or procedures.  This module holds the lynchpin Result
class, as well as accompanying helper functions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Union

from atef import exceptions, util
from atef.enums import GroupResultMode, Severity
from atef.exceptions import PreparedComparisonException


@dataclass(frozen=True)
class Result:
    """
    The result of a check or step.  Contains a severity enum and reason.
    The timestamp field should not be specified at creation, as it will be
    automatically filled.
    """
    severity: Severity = Severity.success
    reason: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow, compare=False)

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


def incomplete_result():
    return Result(severity=Severity.warning, reason='step incomplete')


def successful_result():
    return Result()


def combine_results(results: List[Result]) -> Result:
    """
    Combines results into a single result.
    Takes the highest severity, and currently all the reasons

    Parameters
    ----------
    results : List[Result]
        a list of Results to combine

    Returns
    -------
    Result
        the combined Result
    """
    severity = util.get_maximum_severity([r.severity for r in results])
    reason = str([r.reason for r in results]) or ''

    return Result(severity=severity, reason=reason)


def _summarize_result_severity(
    mode: GroupResultMode,
    results: List[Union[Result, Exception, None]]
) -> Severity:
    """
    Summarize all results based on the configured mode.

    Parameters
    ----------
    mode : GroupResultMode
        The mode to apply to the results.
    results : list of (Result, Exception, or None)
        The list of results.

    Returns
    -------
    Severity
        The calculated severity.
    """
    if any(result is None or isinstance(result, Exception) for result in results):
        return Severity.error

    severities = [
        result.severity for result in results if isinstance(result, Result)
    ]

    if mode == GroupResultMode.all_:
        return util.get_maximum_severity(severities)

    if mode == GroupResultMode.any_:
        return util.get_minimum_severity(severities)

    return Severity.internal_error
