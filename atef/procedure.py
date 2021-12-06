from __future__ import annotations

import dataclasses
import datetime
import pathlib
from dataclasses import field
from typing import Any, Dict, Optional, Sequence, Union

from .utils import as_tagged_union


@as_tagged_union
@dataclasses.dataclass
class ProcedureStep:
    title: Optional[str]
    description: str


@dataclasses.dataclass
class DescriptionStep(ProcedureStep):
    ...


@dataclasses.dataclass
class CodeStep(ProcedureStep):
    source_code: str
    arguments: Dict[Any, Any]


@dataclasses.dataclass
class PlanStep(ProcedureStep):
    plan: str
    arguments: Dict[Any, Any]
    fixed_arguments: Optional[Sequence[str]]


@dataclasses.dataclass
class DisplayOptions:
    macros: Dict[str, str] = field(default_factory=dict)
    template: str = "embedded_screen"
    embed: bool = True


@dataclasses.dataclass
class DeviceConfiguration:
    archiver_timestamp: Optional[datetime.datetime]
    values: Dict[str, Any]


@dataclasses.dataclass
class ConfigurationCheckStep(ProcedureStep):
    devices: Dict[str, DeviceConfiguration]


@dataclasses.dataclass
class TyphosDisplayStep(ProcedureStep):
    devices: Dict[str, DisplayOptions]


@dataclasses.dataclass
class PydmDisplayStep(ProcedureStep):
    display: pathlib.Path
    options: DisplayOptions


@dataclasses.dataclass
class ProcedureGroup:
    title: Optional[str]
    description: str
    steps: Sequence[Union[ProcedureStep, ProcedureGroup]]
