from __future__ import annotations

import asyncio
import dataclasses
import re
import shutil
import sys
import typing
from dataclasses import field
from typing import (Any, ClassVar, Dict, List, Mapping, Optional, Sequence,
                    TypedDict, TypeVar, Union)

from . import serialization
from .check import Result, Severity

#: Arguments that can be passed to tools.
ToolArguments = Dict[str, Any]
T = TypeVar("T", bound="Tool")


class ToolResult(TypedDict):
    """
    The base result dictionary of any tool.
    """
    result: Result


class PingResult(ToolResult):
    """
    The result dictionary of the 'ping' tool.
    """
    #: Host(s) that are alive
    alive: List[str]
    #: Host(s) that are unresponsvie
    unresponsive: List[str]
    #: Host name to time taken.
    times: Dict[str, float]
    #: Minimum time in milliseconds from ``times``.
    min_time: float
    #: Maximum time in milliseconds from ``times``.
    max_time: float


def get_result_value_by_key(result: ToolResult, key: str) -> Any:
    """
    Retrieve the value indicated by the dotted key name from the ToolResult.

    Supports attributes of generic types, items (for mappings as in
    dictionaries), and iterables (by numeric index).

    Parameters
    ----------
    result : dict
        The result (typed) dictionary.
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


@dataclasses.dataclass
@serialization.as_tagged_union
class Tool:
    """
    Base class for atef tool checks.
    """
    result: Optional[ToolResult] = None

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
        # And then the keys that are defined in its TypedDict definition:
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


@dataclasses.dataclass
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

    #: Time pattern for matching the ping output.
    _time_re: ClassVar[re.Pattern] = re.compile(r"time=(.*)\s?ms")
    #: Time to report when unresponsive [ms]
    _unresponsive_time: ClassVar[float] = 100.0

    @staticmethod
    def _result_from_output(host: str, output: str) -> PingResult:
        """
        Fill a PingResult from the results of the ping program.

        Parameters
        ----------
        host : str
            The hostname that ``ping`` was called with.
        output : str
            The decoded output of the subprocess call.

        Returns
        -------
        PingResult
        """
        # NOTE: lazily ignoring non-millisecond-level results here; 1 second+
        # is the same as non-responsive if you ask me...
        times = [float(ms) for ms in Ping._time_re.findall(output)]

        if not times:
            return PingResult(
                result=Result(severity=Severity.error),
                alive=[],
                unresponsive=[host],
                min_time=Ping._unresponsive_time,
                max_time=Ping._unresponsive_time,
                times={host: Ping._unresponsive_time},
            )

        return PingResult(
            result=Result(severity=Severity.success),
            alive=[host],
            unresponsive=[],
            min_time=min(times),
            max_time=max(times),
            times={host: sum(times) / len(times)},
        )

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

        if sys.platform == "win32":
            args = ("/n", str(self.count))
        else:
            args = ("-c", str(self.count))

        proc = await asyncio.create_subprocess_exec(
            str(shutil.which("ping")),
            *args,
            host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        output = await proc.stdout.read()
        await proc.wait()
        return self._result_from_output(host, output.decode(self.encoding))

    async def run(self) -> PingResult:
        """
        Run the "Ping" tool with the current settings.

        Returns
        -------
        PingResult
        """
        result = PingResult(
            result=Result(),
            unresponsive=[],
            alive=[],
            times={},
            min_time=0.0,
            max_time=0.0,
        )
        self.result = result

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
            if isinstance(host_result, Exception):
                result["unresponsive"].append(host)
                result["times"][host] = self._unresponsive_time
                result["max_time"] = self._unresponsive_time
                continue

            result["unresponsive"].extend(host_result["unresponsive"])
            result["alive"].extend(host_result["alive"])
            result["times"].update(host_result["times"])

            times = result["times"].values()
            result["min_time"] = min(times) if times else 0.0
            result["max_time"] = max(times) if times else self._unresponsive_time

        return result
