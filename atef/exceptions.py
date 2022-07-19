from __future__ import annotations

import typing
from typing import List, Optional

from .enums import Severity

if typing.TYPE_CHECKING:
    from .check import Comparison
    from .config import AnyConfiguration, PathItem


class ConfigFileLoadError(Exception):
    """Generic configuration file loading failure."""
    ...


class ConfigFileHappiError(ConfigFileLoadError):
    """Config file load error relating to a happi device."""
    dev_name: str
    config: AnyConfiguration


class MissingHappiDeviceError(ConfigFileHappiError):
    """Config file load error: the happi device doesn't exist."""
    ...


class HappiLoadError(ConfigFileHappiError):
    """Config file load error: the happi device couldn't be instantiated."""
    ...


class ComparisonException(Exception):
    """Raise this exception to exit a comparator and set severity."""
    severity = Severity.success


class ComparisonError(ComparisonException):
    """Raise this exception to error out in a comparator."""
    severity = Severity.error


class ComparisonWarning(ComparisonException):
    """Raise this exception to warn in a comparator."""
    severity = Severity.warning


class PreparedComparisonException(Exception):
    """Exception caught during preparation of comparisons."""
    #: The exception instance itself.
    exception: Exception
    #: The identifier used for the comparison.
    identifier: str
    #: The comparison itself.
    comparison: Comparison
    #: The hierarhical path that led to this prepared comparison.
    path: List[PathItem]
    #: The name of the associated configuration.
    name: Optional[str] = None

    def __init__(
        self,
        exception: Exception,
        identifier: str,
        comparison: Comparison,
        name: Optional[str] = None,
        path: Optional[List[PathItem]] = None,
    ):
        super().__init__(str(exception))
        self.exception = exception
        self.identifier = identifier
        self.comparison = comparison
        self.name = name
        self.path = path or []


class ToolException(Exception):
    """Base exception for tool-related errors."""


class ToolDependencyMissingException(Exception):
    """Required dependency for a tool to work is unavailable."""
