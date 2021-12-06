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
    """
    A basic step in an atef procedure.

    This is used as a base class for all valid procedure steps (and groups).
    """
    #: The title of the procedure
    title: Optional[str]
    #: A description of narrative explanation of setup steps, what is to happen, etc.
    description: str


@dataclasses.dataclass
class DescriptionStep(ProcedureStep):
    """A simple title or descriptive step in the procedure."""
    ...


@dataclasses.dataclass
class CodeStep(ProcedureStep):
    """Run source code in a procedure."""
    #: The source code to execute.
    source_code: str
    #: Arguments to pass into the code.
    arguments: Dict[Any, Any]


class PlanOptions:
    """Options for a bluesky plan scan."""
    #: The plan name.
    plan: str
    #: Plan arguments dictionary - argument name to value.
    arguments: Dict[Any, Any]
    #: Arguments which should not be configurable.
    fixed_arguments: Optional[Sequence[str]]


@dataclasses.dataclass
class PlanStep(ProcedureStep):
    """A procedure step comprised of one or more bluesky plans."""
    plan: Sequence[PlanOptions]


@dataclasses.dataclass
class DisplayOptions:
    """Options for a typhos or PyDM display."""
    #: Macros for the display.
    macros: Dict[str, str] = field(default_factory=dict)
    #: The template name or screen display path.
    template: str = "embedded_screen"
    #: Embed the display in the procedure? (or pop it out)
    embed: bool = True


@dataclasses.dataclass
class DeviceConfiguration:
    """Device configuration for comparison."""
    #: The timestamp this configuration is associated with.
    archiver_timestamp: Optional[datetime.datetime]
    #: The device dotted attribute name to value.
    values: Dict[str, Any]


@dataclasses.dataclass
class ConfigurationCheckStep(ProcedureStep):
    """Step which checks device configuration versus a given timestamp."""
    #: Device name to device configuration information.
    devices: Dict[str, DeviceConfiguration]


@dataclasses.dataclass
class TyphosDisplayStep(ProcedureStep):
    """A procedure step which opens one or more typhos displays."""
    #: Happi device name to display options.
    devices: Dict[str, DisplayOptions]


@dataclasses.dataclass
class PydmDisplayStep(ProcedureStep):
    """A procedure step which a opens a PyDM display."""
    #: The display path.
    displays: pathlib.Path
    #: Options for displaying.
    options: DisplayOptions


@dataclasses.dataclass
class ProcedureGroup:
    """A group of procedure steps (or nested groups)."""
    #: The title of the group
    title: Optional[str]
    #: A description of narrative explanation of setup steps, what is to happen, etc.
    description: str
    #: Steps included in the procedure.
    steps: Sequence[Union[ProcedureStep, ProcedureGroup]]
