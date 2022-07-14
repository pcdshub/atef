from __future__ import annotations

import dataclasses
import typing
from dataclasses import field
from typing import Any, ClassVar, Dict, List, Type, TypedDict, TypeVar

from . import serialization
from .check import Result

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
    #: Max time from ``times``.
    max_time: float


@dataclasses.dataclass
@serialization.as_tagged_union
class Tool:
    """
    Base class for atef tool checks.
    """
    result_type: ClassVar[Type[ToolResult]] = ToolResult

    def check_result_key(self, key: str) -> None:
        valid_keys = list(typing.get_type_hints(self.result_type))
        if key not in valid_keys:
            raise KeyError(
                f"Invalid result key for tool {self}: {key!r}.  Valid "
                f"keys are: {', '.join(valid_keys)}"
            )

    async def run(self, *args, **kwargs) -> ToolResult:
        raise NotImplementedError("To be implemented by subclass")


@dataclasses.dataclass
class Ping(Tool):
    result_type: ClassVar[Type[ToolResult]] = PingResult
    hosts: List[str] = field(default_factory=list)

    async def run(self) -> PingResult:
        return PingResult(
            result=Result(),
            unresponsive=[],
            alive=[],
            times={},
            max_time=0.0,
        )
