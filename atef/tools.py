from __future__ import annotations

import asyncio
import re
import shutil
import sys
import typing
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, TypeVar, Union

from . import serialization
from .check import Severity
from .exceptions import ToolDependencyMissingException
from .result import Result

T = TypeVar("T", bound="Tool")


@dataclass
class ToolResult:
    """
    The base result dictionary of any tool.
    """
    result: Result


@dataclass
class PingResult(ToolResult):
    """
    The result dictionary of the 'ping' tool.
    """
    #: Host(s) that are alive
    alive: List[str] = field(default_factory=list)
    #: Number of hosts that are alive.
    num_alive: int = 0

    #: Host(s) that are unresponsvie
    unresponsive: List[str] = field(default_factory=list)
    #: Number of hosts that are unresponsive.
    num_unresponsive: int = 0

    #: Host name to time taken.
    times: Dict[str, float] = field(default_factory=dict)
    #: Minimum time in seconds from ``times``.
    min_time: float = 0.0
    #: Maximum time in seconds from ``times``.
    max_time: float = 0.0

    #: Time pattern for matching the ping output.
    _time_re: ClassVar[re.Pattern] = re.compile(r"time[=<](.*)\s?ms")

    def add_host_result(
        self,
        host: str,
        result: Union[PingResult, Exception],
        *,
        failure_time: float = 100.0
    ) -> None:
        """
        Add a new per-host result to this aggregate one.

        Parameters
        ----------
        host : str
            The hostname or IP address.
        result : Union[PingResult, Exception]
            The result to add.  Caught exceptions will be interpreted as a ping
            failure for the given host.
        failure_time : float, optional
            The time to use when failures happen.
        """
        if isinstance(result, Exception):
            self.result = Result(
                severity=Severity.error,
            )
            self.unresponsive.append(host)
            self.times[host] = failure_time
        else:
            self.unresponsive.extend(result.unresponsive)
            self.alive.extend(result.alive)
            self.times.update(result.times)

        times = self.times.values()
        self.min_time = min(times) if times else 0.0
        self.max_time = max(times) if times else failure_time

        self.num_unresponsive = len(self.unresponsive)
        self.num_alive = len(self.alive)

    @classmethod
    def from_output(
        cls, host: str, output: str, unresponsive_time: float = 100.0
    ) -> PingResult:
        """
        Fill a PingResult from the results of the ping program.

        Parameters
        ----------
        host : str
            The hostname that ``ping`` was called with.
        output : str
            The decoded output of the subprocess call.
        unresponsive_time : float, optional
            Time to use for unresponsive or errored hosts.

        Returns
        -------
        PingResult
        """
        # NOTE: lazily ignoring non-millisecond-level results here; 1 second+
        # is the same as non-responsive if you ask me...
        times = [float(ms) / 1000.0 for ms in PingResult._time_re.findall(output)]

        if not times:
            return cls(
                result=Result(severity=Severity.error),
                alive=[],
                unresponsive=[host],
                min_time=unresponsive_time,
                max_time=unresponsive_time,
                times={host: unresponsive_time},
            )

        return cls(
            result=Result(severity=Severity.success),
            alive=[host],
            unresponsive=[],
            min_time=min(times),
            max_time=max(times),
            times={host: sum(times) / len(times)},
        )


def get_result_value_by_key(result: ToolResult, key: str) -> Any:
    """
    Retrieve the value indicated by the dotted key name from the ToolResult.

    Supports attributes of generic types, items (for mappings as in
    dictionaries), and iterables (by numeric index).

    Parameters
    ----------
    result : object
        The result dataclass instance.
    key : str
        The (optionally) dotted key name.

    Raises
    ------
    KeyError
        If the key is blank or otherwise invalid.

    Returns
    -------
    Any
        The data found by the key.
    """
    if not key:
        raise KeyError("No key provided")

    item = result
    path = []
    key_parts = key.split(".")

    while key_parts:
        key = key_parts.pop(0)
        path.append(key)
        try:
            if isinstance(item, Mapping):
                item = item[key]
            elif isinstance(item, Sequence):
                item = item[int(key)]
            else:
                item = getattr(item, key)
        except KeyError:
            path_str = ".".join(path)
            raise KeyError(
                f"{item} does not have key {key!r} ({path_str})"
            ) from None
        except AttributeError:
            path_str = ".".join(path)
            raise KeyError(
                f"{item} does not have attribute {key!r} ({path_str})"
            ) from None
        except Exception:
            path_str = ".".join(path)
            raise KeyError(
                f"{item} does not have {key!r} ({path_str})"
            )

    return item


@dataclass
@serialization.as_tagged_union
class Tool:
    """
    Base class for atef tool checks.
    """

    def check_result_key(self, key: str) -> None:
        """
        Check that the result ``key`` is valid for the given tool.

        For example, ``PingResult`` keys can include ``"min_time"``,
        ``"max_time"``, and so on.

        Parameters
        ----------
        key : str
            The key to check.

        Raises
        ------
        ValueError
            If the key is invalid.
        """
        top_level_key, *parts = key.split(".", 1)
        # Use the return type of the tool's run() method to tell us the
        # ToolResult type:
        run_type: ToolResult = typing.get_type_hints(self.run)["return"]
        # And then the keys that are defined in its definition:
        result_type_hints = typing.get_type_hints(run_type)
        valid_keys = list(result_type_hints)
        if top_level_key not in valid_keys:
            raise ValueError(
                f"Invalid result key for tool {self}: {top_level_key!r}.  Valid "
                f"keys are: {', '.join(valid_keys)}"
            )

        if parts:
            top_level_type = result_type_hints[top_level_key]
            origin = typing.get_origin(top_level_type)
            if origin is None or not issubclass(origin, (Mapping, Sequence)):
                raise ValueError(
                    f"Invalid result key for tool {self}: {top_level_key!r} does "
                    f"not have sub-keys because it is of type {top_level_type}."
                )

    async def run(self, *args, **kwargs) -> ToolResult:
        raise NotImplementedError("To be implemented by subclass")


@dataclass
class Ping(Tool):
    """
    Tool for pinging one or more hosts and summarizing the results.
    """
    #: The hosts to ping.
    hosts: List[str] = field(default_factory=list)
    #: The number of ping attempts to make per host.
    count: int = 3
    #: The assumed output encoding of the 'ping' command.
    encoding: str = "utf-8"

    #: Time to report when unresponsive [sec]
    _unresponsive_time: ClassVar[float] = 100.0

    async def ping(self, host: str) -> PingResult:
        """
        Ping the given host.

        Parameters
        ----------
        host : str
            The host to ping.

        Returns
        -------
        PingResult
        """

        # Ensure we don't ping forever:
        count = min(self.count, 1)

        if sys.platform == "win32":
            args = ("/n", str(count))
        else:
            args = ("-c", str(count))

        ping = shutil.which("ping")

        if ping is None:
            raise ToolDependencyMissingException(
                "The 'ping' binary is unavailable on the currently-defined "
                "PATH"
            )

        proc = await asyncio.create_subprocess_exec(
            ping,
            *args,
            host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        output = await proc.stdout.read()
        await proc.wait()
        return PingResult.from_output(host, output.decode(self.encoding))

    async def run(self) -> PingResult:
        """
        Run the "Ping" tool with the current settings.

        Returns
        -------
        PingResult
        """
        result = PingResult(result=Result())

        if not self.hosts:
            return result

        ping_by_host: Dict[str, Union[Exception, PingResult]] = {}

        async def _ping(host: str) -> None:
            try:
                ping_by_host[host] = await self.ping(host)
            except Exception as ex:
                ping_by_host[host] = ex

        tasks = [asyncio.create_task(_ping(host)) for host in self.hosts]

        try:
            await asyncio.wait(tasks)
        except KeyboardInterrupt:
            for task in tasks:
                task.cancel()
            raise

        for host, host_result in ping_by_host.items():
            result.add_host_result(
                host, host_result, failure_time=self._unresponsive_time
            )

        return result
