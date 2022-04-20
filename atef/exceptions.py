from __future__ import annotations

import typing

from .enums import Severity

if typing.TYPE_CHECKING:
    from .check import AnyConfiguration


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
