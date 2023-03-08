from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import (Any, Dict, Generator, Literal, Optional, Sequence, Union,
                    cast)

import apischema
import databroker
import yaml
from bluesky import RunEngine

from atef.cache import DataCache
from atef.check import Result, incomplete
from atef.config import (ConfigurationFile, PreparedFile,
                         _summarize_result_severity, run_passive_step)
from atef.enums import GroupResultMode, Severity
from atef.type_hints import AnyPath
from atef.yaml_support import init_yaml_support

from . import serialization

logger = logging.getLogger(__name__)


def incomplete_result():
    return incomplete


class BlueskyState(object):
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(BlueskyState, cls).__new__(cls)
        return cls.instance

    def __init__(self):
        self.db = databroker.Broker.named('temp')
        self.RE = RunEngine({})
        self.RE.subscribe(self.db.insert)


@dataclasses.dataclass
@serialization.as_tagged_union
class ProcedureStep:
    """
    A basic step in an atef procedure.

    This is used as a base class for all valid procedure steps (and groups).
    """
    #: The title of the procedure
    name: Optional[str] = None
    #: A description of narrative explanation of setup steps, what is to happen, etc.
    description: Optional[str] = None
    #: The hierarchical parent of this step.
    parent: Optional[ProcedureGroup] = None
    #: overall result of running the step
    _result: Result = field(default_factory=incomplete_result)
    #: confirmation by the user that result matches expectations
    verify_result: Result = field(default_factory=incomplete_result)
    #: verification requirements, is human verification required?
    verify_required: bool = False
    #: whether or not the step completed successfully
    step_result: Result = field(default_factory=incomplete_result)
    #: step success requirements, does the step need to complete?
    step_success_required: bool = False

    def _run(self) -> Result:
        """ Run the comparison.  To be implemented in subclass. """
        raise NotImplementedError()

    def run(self) -> Result:
        """ Run the step and return the result """
        try:
            result = self._run()
        except Exception as ex:
            result = Result(
                severity=Severity.internal_error,
                reason=str(ex)
            )

        # stash step result
        self.step_result = result
        # return the overall result, including verification
        return self.result

    @property
    def result(self) -> Result:
        """
        Combines the step result and verification result based on settings

        Returns
        -------
        Result
            The result of this step
        """
        results = []
        reason = ''
        if self.verify_required:
            results.append(self.verify_result)
            if self.verify_result.severity != Severity.success:
                reason += f'Not Verified ({self.verify_result.reason}),'
            else:
                reason += f'Verified ({self.verify_result.reason})'

        if self.step_success_required:
            results.append(self.step_result)
            if self.step_result.severity != Severity.success:
                reason += f'Not Successful ({self.step_result.reason})'

        if not results:
            # Nothing required, auto-success
            self._result = Result()
            return self._result

        severity = _summarize_result_severity(GroupResultMode.all_, results)
        self._result = Result(severity=severity, reason=reason)
        return self._result

    def allow_verify(self) -> bool:
        """
        Whether or not the step can be verified.
        To be further expanded or overloaded in subclass,
        """
        return self.result.severity == Severity.success


@dataclass
class DescriptionStep(ProcedureStep):
    """A simple title or descriptive step in the procedure."""
    def _run(self) -> Result:
        """ Always a successful result, allowing verification """
        return Result()


@dataclass
class CodeStep(ProcedureStep):
    """Run source code in a procedure."""
    #: The source code to execute.
    source_code: str = ''
    #: Arguments to pass into the code.
    arguments: Dict[Any, Any] = field(default_factory=dict)


@dataclass
class PlanOptions:
    """Options for a bluesky plan scan."""
    #: The plan name.
    plan: str
    #: Plan arguments dictionary - argument name to value.
    args: Sequence[Any]
    #: Plan keyword  arguments dictionary - argument name to value.
    kwargs: Dict[Any, Any]
    #: Arguments which should not be configurable.
    fixed_arguments: Optional[Sequence[str]]


@dataclass
class PlanStep(ProcedureStep):
    """A procedure step comprised of one or more bluesky plans."""
    plans: Sequence[PlanOptions] = field(default_factory=list)

    def _run(self) -> Result:
        """ Gather plan options and run the bluesky plan """
        # get global run engine
        # Construct plan (get devices, organize args/kwargs, run)

        return super()._run()


@dataclass
class DisplayOptions:
    """Options for a typhos or PyDM display."""
    #: Macros for the display.
    macros: Dict[str, str] = field(default_factory=dict)
    #: The template name or screen display path.
    template: str = "embedded_screen"
    #: Embed the display in the procedure? (or pop it out)
    embed: bool = True


@dataclass
class DeviceConfiguration:
    """Device configuration for comparison."""
    #: The timestamp this configuration is associated with.
    archiver_timestamp: Optional[datetime.datetime]
    #: The device dotted attribute name to value.
    values: Dict[str, Any]


@dataclass
class ConfigurationCheckStep(ProcedureStep):
    """Step which checks device configuration versus a given timestamp."""
    #: Device name to device configuration information.
    devices: Dict[str, DeviceConfiguration] = field(default_factory=dict)


@dataclass
class TyphosDisplayStep(ProcedureStep):
    """A procedure step which opens one or more typhos displays."""
    #: Happi device name to display options.
    devices: Dict[str, DisplayOptions] = field(default_factory=dict)


@dataclass
class PydmDisplayStep(ProcedureStep):
    """A procedure step which a opens a PyDM display."""
    #: The display path.
    display: pathlib.Path = field(default_factory=pathlib.Path)
    #: Options for displaying.
    options: DisplayOptions = field(default_factory=DisplayOptions)


@dataclass
class PassiveStep(ProcedureStep):
    """A step that runs a passive checkout file"""
    filepath: pathlib.Path = field(default_factory=pathlib.Path)

    def _run(self) -> Result:
        """ Load, prepare, and run the passive step """
        config = ConfigurationFile.from_filename(self.filepath)

        prepared_config = PreparedFile.from_config(file=config,
                                                   cache=DataCache())

        # run passive checkout.  Will need to set up asyncio loop
        loop = asyncio.get_event_loop()
        coroutine = run_passive_step(prepared_config)
        result = loop.run_until_complete(coroutine)

        return result


@dataclass
class ProcedureGroup(ProcedureStep):
    """A group of procedure steps (or nested groups)."""
    #: Steps included in the procedure.
    steps: Sequence[Union[ProcedureStep, ProcedureGroup]] = field(default_factory=list)

    def walk_steps(self) -> Generator[AnyProcedure, None, None]:
        for step in self.steps:
            step = cast(AnyProcedure, step)
            yield step
            if isinstance(step, ProcedureGroup):
                yield from step.walk_steps()

    def run(self) -> Result:
        results = []
        for step in self.steps:
            results.append(step.run())

        severity = _summarize_result_severity(GroupResultMode.all_, results)
        result = Result(severity=severity)

        self.step_result = result
        return self.result


AnyProcedure = Union[
    ProcedureGroup,
    DescriptionStep,
    TyphosDisplayStep,
    PydmDisplayStep
]


@dataclass
class ProcedureFile:
    """
    File comprised of several Procedure steps

    Essentially identical to Configuration File.  Consider refactoring
    if design/methods do not diverge
    """
    #: atef configuration file version information.
    version: Literal[0] = field(default=0, metadata=apischema.metadata.required)
    #: Top-level configuration group.
    root: ProcedureGroup = field(default_factory=ProcedureGroup)

    def walk_steps(self) -> Generator[AnyProcedure, None, None]:
        yield self.root
        yield from self.root.walk_steps()

    @classmethod
    def from_filename(cls, filename: AnyPath) -> ProcedureFile:
        path = pathlib.Path(filename)
        if path.suffix == '.json':
            config = ProcedureFile.from_json(path)
        else:
            config = ProcedureFile.from_yaml(path)
        return config

    @classmethod
    def from_json(cls, filename: AnyPath) -> ProcedureFile:
        """Load a configuration file from JSON."""
        with open(filename) as fp:
            serialized_config = json.load(fp)
        return apischema.deserialize(cls, serialized_config)

    @classmethod
    def from_yaml(cls, filename: AnyPath) -> ProcedureFile:
        """Load a configuration file from yaml."""
        with open(filename) as fp:
            serialized_config = yaml.safe_load(fp)
        return apischema.deserialize(cls, serialized_config)

    def to_json(self):
        """Dump this configuration file to a JSON-compatible dictionary."""
        return apischema.serialize(ProcedureFile, self, exclude_defaults=True)

    def to_yaml(self):
        """Dump this configuration file to yaml."""
        init_yaml_support()
        return yaml.dump(self.to_json())

    def compare(self) -> Result:
        return self.root.compare()


def run_procedure(
    process: ProcedureFile,
):
    """
    Run a procedure given a ProcedureFile.

    Gross skeleton code I am organizing my thoughts
    """
    for step in process.walk_steps():
        print(step)
        # check that the step has been completed

        # if verified
        step.verify = True
        step.result = Result()

    return process
