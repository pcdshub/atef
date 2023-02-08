"""
Experimental version handling.
1. Pick the version for use in __init__.py and cli.py without polluting the namespace.
2. Defer evaluation of the version until it is checked to save 0.3s on import
3. Use the git version in a git checkout and _version otherwise.
"""
from collections import UserString
from pathlib import Path
from typing import Optional


class VersionProxy(UserString):
    def __init__(self):
        self._version = None

    def _get_version(self) -> Optional[str]:
        # Checking for directory is faster than failing out of get_version
        if (Path(__file__).parent.parent / '.git').exists():
            try:
                # Git checkout
                from setuptools_scm import get_version
                return get_version(root="..", relative_to=__file__)
            except (ImportError, LookupError):
                ...

        # Check this second because it can exist in a git repo if we've
        # done a build at least once.
        try:
            from ._version import version  # noqa: F401
            return version
        except ImportError:
            # I don't know how this can happen but let's be prepared
            ...

        return None

    @property
    def data(self) -> str:
        # This is accessed by UserString to allow us to lazily fill in the
        # information
        if self._version is None:
            self._version = self._get_version() or '0.0.unknown'

        return self._version


__version__ = version = VersionProxy()
