from __future__ import annotations

import typing
from typing import Optional

from .enums import Severity

if typing.TYPE_CHECKING:
    from .check import Comparison
    from .config import AnyConfiguration, PreparedConfiguration


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


class HappiUnavailableError(ConfigFileHappiError):
    """Config file load error: happi is unavailable."""
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
    #: The relevant configuration
    config: Optional[AnyConfiguration]
    #: The parent container of the comparison that failed to prepare.
    prepared: Optional[PreparedConfiguration] = None
    #: The comparison related to the exception, if applicable.
    comparison: Optional[Comparison]
    #: The name of the associated configuration.
    name: Optional[str] = None

    def __init__(
        self,
        exception: Exception,
        identifier: str,
        message: Optional[str] = None,
        config: Optional[AnyConfiguration] = None,
        prepared: Optional[PreparedConfiguration] = None,
        comparison: Optional[Comparison] = None,
        name: Optional[str] = None,
    ):
        if message is None:
            message = str(exception)
        super().__init__(message)
        self.exception = exception
        self.identifier = identifier
        self.comparison = comparison
        self.config = config
        self.prepared = prepared
        self.name = name


class ToolException(Exception):
    """Base exception for tool-related errors."""


class ToolDependencyMissingException(Exception):
    """Required dependency for a tool to work is unavailable."""
