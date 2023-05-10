"""
Dataclasses for describing active checkout procedures.  These dataclasses come in
normal (edit) and Prepared (run) variants

Edit variants hold data needed to specify the step.
Prepared variants hold a reference to their originating edit-step, along with
Result objects and a .run() method.

Adding a step requires:
- write the edit-variant
- add the edit-variant to the AnyProcedure type hint
- write the run-variant, along with its ._run() and .from_origin() methods
- add the step to PreparedProcedure.from_origin classmethod case statement
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field
from typing import (Any, Dict, Generator, List, Literal, Optional, Sequence,
                    Union, cast)

import apischema
import databroker
import ophyd
import yaml
from bluesky import RunEngine

from atef import util
from atef.cache import _SignalCache, get_signal_cache
from atef.check import Comparison
from atef.config import (ConfigurationFile, PreparedComparison, PreparedFile,
                         PreparedSignalComparison, run_passive_step)
from atef.enums import GroupResultMode, Severity
from atef.exceptions import PreparedComparisonException
from atef.result import Result, _summarize_result_severity, incomplete_result
from atef.type_hints import AnyPath, Number, PrimitiveType
from atef.yaml_support import init_yaml_support

from . import serialization

logger = logging.getLogger(__name__)


class BlueskyState:
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(BlueskyState, cls).__new__(cls)
        return cls.instance

    def __init__(self):
        self.db = databroker.Broker.named('temp')
        self.RE = RunEngine({})
        self.RE.subscribe(self.db.insert)


def walk_steps(step: ProcedureStep) -> Generator[ProcedureStep, None, None]:
    """
    Yield ProedureSteps in ``step``, depth-first.

    Parameters
    ----------
    step : ProcedureStep
        Step to yield ProcedureSteps from

    Yields
    ------
    Generator[ProcedureStep, None, None]
    """
    yield step
    for sub_step in getattr(step, 'steps', []):
        yield from walk_steps(sub_step)


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
    #: verification requirements, is human verification required?
    verify_required: bool = True
    #: step success requirements, does the step need to complete?
    step_success_required: bool = True

    def allow_verify(self) -> bool:
        """
        Whether or not the step can be verified.
        To be further expanded or overloaded in subclass,
        """
        return self.result.severity == Severity.success


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


@dataclass
class DescriptionStep(ProcedureStep):
    """A simple title or descriptive step in the procedure."""
    pass


@dataclass
class PassiveStep(ProcedureStep):
    """A step that runs a passive checkout file"""
    filepath: pathlib.Path = field(default_factory=pathlib.Path)


@dataclass
class SetValueStep(ProcedureStep):
    """ A step that sets one or more values and checks one or more values after """
    actions: List[ValueToTarget] = field(default_factory=list)
    success_criteria: List[ComparisonToTarget] = field(default_factory=list)

    #: Stop performing actions if one fails
    halt_on_fail: bool = True
    #: Only mark the step_result as successful if all actions have succeeded
    require_action_success: bool = True


@dataclass
class Target:
    """
    A destination for a value.  Either an ophyd device+attr pair or EPICS PV
    """
    #: name of target
    name: Optional[str] = None
    #: device name and attr
    device: Optional[str] = None
    attr: Optional[str] = None
    #: EPICS PV
    pv: Optional[str] = None

    def to_signal(
        self,
        signal_cache: Optional[_SignalCache] = None
    ) -> Optional[ophyd.EpicsSignal]:
        """
        Return the signal described by this Target.  First attempts to use the
        device + attr information to look up the signal in happi, falling back
        to the raw PV.

        Returns
        -------
        ophyd.EpicsSignal
            the signal described by this Target
        """
        try:
            if self.device and self.attr:
                device = util.get_happi_device_by_name(self.device)
                signal = getattr(device, self.attr)
            elif self.pv:
                if signal_cache is None:
                    signal_cache = get_signal_cache()
                signal = signal_cache[self.pv]
            else:
                logger.debug('unable to create signal, insufficient information '
                             'to specify signal')
                return
        except Exception as ex:
            logger.debug(f'unable to create signal: ({ex})')
            return

        return signal


@dataclass
class ValueToTarget(Target):
    #: the value to set to the target
    value: Optional[PrimitiveType] = None

    # ophyd.Signal.set() parameters
    #: write timeout
    timeout: Optional[Number] = None
    #: settle time
    settle_time: Optional[Number] = None


@dataclass
class ComparisonToTarget(Target):
    #: the comparison to apply to the target
    comparison: Optional[Comparison] = None


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
        if path.suffix.lower() == '.json':
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


######################
# Prepared Dataclasses
######################


@dataclass
class PreparedProcedureFile:
    """
    A Prepared Procedure file.  Constructs prepared dataclasses for steps
    in the root ProcedureGroup
    """
    #: Corresponding ProcedureFile information
    file: ProcedureFile
    #: Procedure steps defined in the top-level file
    root: PreparedProcedureGroup

    @classmethod
    def from_origin(
        cls,
        file: ProcedureFile,
    ) -> PreparedProcedureFile:
        """
        Prepare a ProcedureFile for running, based off an existing ProcedureFile

        Parameters
        ----------
        file : ProcedureFile
            the procedure file instance
        """
        prepared_root = PreparedProcedureGroup.from_origin(group=file.root)

        prep_proc_file = PreparedProcedureFile(
            file=file,
            root=prepared_root
        )

        prepared_root.parent = prep_proc_file
        return prep_proc_file

    async def run(self) -> Result:
        return await self.root.run()


@dataclass
class FailedStep:
    """ A step that failed to be prepared for running. """
    #: The data cache to use for the preparation step.
    parent: Optional[PreparedProcedureGroup]
    #: Configuration instance.
    origin: AnyProcedure
    #: overall result of running the step
    combined_result: Result
    #: confirmation by the user that result matches expectations
    verify_result: Result = field(default_factory=incomplete_result)
    #: whether or not the step completed successfully
    step_result: Result = field(default_factory=incomplete_result)
    #: Exception that was caught, if available.
    exception: Optional[Exception] = None

    @property
    def result(self) -> Result:
        return self.combined_result


@dataclass
class PreparedProcedureStep:
    """
    Base class for a ProcedureStep that has been prepared to run.
    """
    #: name of this comparison
    name: Optional[str] = None
    #: original procedure step, of which this is the prepared version
    origin: ProcedureStep = field(default_factory=ProcedureStep)
    #: hierarchical parent of this step
    parent: Optional[PreparedProcedureGroup] = None

    #: overall result of running the step
    combined_result: Result = field(default_factory=incomplete_result)
    #: confirmation by the user that result matches expectations
    verify_result: Result = field(default_factory=incomplete_result)
    #: whether or not the step completed successfully
    step_result: Result = field(default_factory=incomplete_result)

    @property
    def result(self) -> Result:
        """
        Combines the step result and verification result based on settings

        Returns
        -------
        Result
            The overall result of this step
        """
        results = []
        reason = ''
        if self.origin.verify_required:
            results.append(self.verify_result)
            if self.verify_result.severity != Severity.success:
                reason += f'Not Verified ({self.verify_result.reason})'
            else:
                reason += f'Verified ({self.verify_result.reason})'

        if self.origin.step_success_required:
            results.append(self.step_result)
            if self.step_result.severity != Severity.success:
                reason += f', Not Successful ({self.step_result.reason})'

        if not results:
            # Nothing required, auto-success
            self.combined_result = Result()
            return self.combined_result

        severity = _summarize_result_severity(GroupResultMode.all_, results)
        self.combined_result = Result(severity=severity, reason=reason)
        return self.combined_result

    async def _run(self) -> Result:
        """
        Run the step.  To be implemented in subclass.
        Returns the step_result
        """
        raise NotImplementedError()

    async def run(self) -> Result:
        """ Run the step and return the result """
        try:
            result = await self._run()
        except Exception as ex:
            result = Result(
                severity=Severity.internal_error,
                reason=str(ex)
            )

        # stash step result
        self.step_result = result
        # return the overall result, including verification
        return self.result

    @classmethod
    def from_origin(
        cls,
        step: AnyProcedure,
        parent: Optional[PreparedProcedureGroup] = None
    ) -> PreparedProcedureStep:
        """
        Prepare a ProcedureStep for running.  If the creation of the prepared step
        fails for any reason, a FailedStep is returned.

        Parameters
        ----------
        step : AnyProcedure
            the ProcedureStep to prepare
        parent : Optional[PreparedProcedureGroup]
            the parent of this step, by default None
        """
        try:
            if isinstance(step, ProcedureGroup):
                return PreparedProcedureGroup.from_origin(
                    group=step, parent=parent
                )
            if isinstance(step, DescriptionStep):
                return PreparedDescriptionStep.from_origin(
                    step=step, parent=parent
                )
            if isinstance(step, PassiveStep):
                return PreparedPassiveStep.from_origin(
                    step=step, parent=parent
                )
            if isinstance(step, SetValueStep):
                return PreparedSetValueStep.from_origin(
                    step=step, parent=parent
                )
            raise NotImplementedError(f"Step type unsupported: {type(step)}")
        except Exception as ex:
            return FailedStep(
                origin=step,
                parent=parent,
                exception=ex,
                combined_result=Result(
                    severity=Severity.internal_error,
                    reason=(
                        f"Failed to instantiate step: {ex}. "
                        f"Step is: {step.name} ({step.description or ''!r})"
                    )
                )
            )


@dataclass
class PreparedProcedureGroup(PreparedProcedureStep):
    #: hierarchical parent of this step
    parent: Optional[Union[PreparedProcedureFile, PreparedProcedureGroup]] = field(
        default=None, repr=False
    )
    #: the steps in this group
    steps: List[AnyPreparedProcedure] = field(default_factory=list)
    #: Steps that failed to be prepared
    prepare_failures: List[FailedStep] = field(default_factory=list)

    @classmethod
    def from_origin(
        cls,
        group: ProcedureGroup,
        parent: Optional[PreparedProcedureGroup | PreparedProcedureFile] = None,
    ) -> PreparedProcedureGroup:
        """
        Prepare a ProcedureGroup for running.  Prepares all of the group's children

        Parameters
        ----------
        group : ProcedureGroup
            the group to prepare
        parent : Optional[PreparedProcedureGroup  |  PreparedProcedureFile]
            the hierarchical parent of this step, by default None

        Returns
        -------
        PreparedProcedureGroup
        """
        prepared = cls(origin=group, parent=parent, steps=[])

        for step in group.steps:
            prep_step = PreparedProcedureStep.from_origin(
                step=cast(AnyPreparedProcedure, step),
                parent=prepared
            )
            if isinstance(prep_step, FailedStep):
                prepared.prepare_failures.append(prep_step)
            else:
                prepared.steps.append(prep_step)

        return prepared

    async def run(self) -> Result:
        """ Run all steps and return a combined result """
        results = []
        for step in self.steps:
            results.append(await step.run())

        if self.prepare_failures:
            result = Result(
                severity=Severity.error,
                reason='At least one step failed to initialize'
            )
        else:
            severity = _summarize_result_severity(GroupResultMode.all_, results)
            result = Result(severity=severity)

        self.step_result = result
        return self.result

    @property
    def result(self) -> Result:
        """ Re-compute the combined result and return it """
        results = []
        for step in self.steps:
            results.append(step.result)

        if self.prepare_failures:
            result = Result(
                severity=Severity.error,
                reason='At least one step failed to initialize'
            )
        else:
            severity = _summarize_result_severity(GroupResultMode.all_, results)
            result = Result(severity=severity)

        self.step_result = result

        return super().result


@dataclass
class PreparedDescriptionStep(PreparedProcedureStep):
    async def _run(self):
        return Result()

    @classmethod
    def from_origin(
        cls,
        step: DescriptionStep,
        parent: Optional[PreparedProcedureGroup] = None
    ) -> PreparedDescriptionStep:
        """
        Prepare a DescriptionStep for running

        Parameters
        ----------
        step : DescriptionStep
            the description step to prepare
        parent : Optional[PreparedProcedureGroup]
            the hierarchical parent of this step, by default None
        """
        return cls(
            origin=step,
            parent=parent,
            name=step.name,
        )


@dataclass
class PreparedPassiveStep(PreparedProcedureStep):
    #: The prepared passive checkout file, holds Results
    prepared_passive_file: Optional[PreparedFile] = None

    async def _run(self) -> Result:
        """ Load, prepare, and run the passive step """
        if not self.prepared_passive_file:
            return Result(severity=Severity.error, reason='No passive checkout to run')
        return await run_passive_step(self.prepared_passive_file)

    @classmethod
    def from_origin(
        cls,
        step: PassiveStep,
        parent: Optional[PreparedProcedureGroup]
    ) -> PreparedPassiveStep:
        """
        Prepare a passive checkout step for running.  Requires the passive checkout
        be accessible for read access

        Parameters
        ----------
        step : PassiveStep
            the original PassiveStep to prepare
        parent : Optional[PreparedProcedureGroup]
            the hierarchical parent to assign to this PreparedPassiveStep

        Returns
        -------
        PreparedPassiveStep
        """
        try:
            passive_file = ConfigurationFile.from_filename(step.filepath)
            prep_passive_file = PreparedFile.from_config(file=passive_file)
        except OSError as ex:
            logger.debug(f'failed to generate prepared passive checkout: {ex}')
            prep_passive_file = None

        return cls(
            origin=step,
            prepared_passive_file=prep_passive_file,
            parent=parent,
            name=step.name
        )


@dataclass
class PreparedSetValueStep(PreparedProcedureStep):
    #: list of prepared actions to take (values to set to a target)
    prepared_actions: List[PreparedValueToSignal] = field(
        default_factory=list
    )
    #: list of actions that failed to be prepared, as they were
    prepare_action_failures: List[ValueToTarget] = field(default_factory=list)
    #: list of prepared success criteria (comparisons)
    prepared_criteria: List[PreparedSignalComparison] = field(
        default_factory=list
    )
    #: list of success criteria that failed to be prepared, as an exception
    prepare_criteria_failures: List[PreparedComparisonException] = field(
        default_factory=list
    )

    def walk_comparisons(self) -> Generator[PreparedComparison, None, None]:
        """ Yields PreparedComparisons in this ProcedureStep """
        yield from self.prepared_criteria

    async def _run(self) -> Result:
        """
        Prepare and execute the actions, record their Results
        Prepare and execute success criteria, record their Results

        Returns
        -------
        Result
            the step_result for this step
        """
        self = cast(SetValueStep, self)
        for prep_action in self.prepared_actions:
            action_result = await prep_action.run()
            if (self.origin.halt_on_fail and action_result.severity > Severity.success):
                self.step_result = Result(
                    severity=Severity.error,
                    reason=f'action failed ({prep_action.name}), step halted'
                )
                return self.step_result
        for prep_criteria in self.prepared_criteria:
            await prep_criteria.compare()

        if self.origin.require_action_success:
            if self.prepare_action_failures:
                return Result(
                    severity=Severity.error,
                    reason=('One or more actions failed to initialize: '
                            f'{[act.name for act in self.prepare_action_failures]}')
                )

            action_results = [action.result for action in self.prepared_actions]
        else:
            action_results = []

        criteria_results = [crit.result for crit in self.prepared_criteria]

        if self.prepare_criteria_failures:
            return Result(
                severity=Severity.error,
                reason=('One or more success criteria failed to initialize: '
                        f'{[crit.name for crit in self.prepare_criteria_failures]}')
            )

        severity = _summarize_result_severity(GroupResultMode.all_,
                                              criteria_results + action_results)

        return Result(severity=severity)

    @classmethod
    def from_origin(
        cls,
        step: SetValueStep,
        parent: Optional[PreparedProcedureGroup]
    ) -> PreparedSetValueStep:
        """
        Prepare a SetValueStep for running.  Gathers and prepares necessary
        signals and comparisons.  Any actions and success criteria that fail
        to be prepared will be stored under the `prepare_action_failures` and
        `prepare_criteria_failures` fields respectively

        Parameters
        ----------
        step : SetValueStep
            the original SetValueStep (not prepared)
        parent : Optional[PreparedProcedureGroup]
            the hierarchical parent for the prepared step.

        Returns
        -------
        PreparedSetValueStep
        """
        prep_step = cls(
            origin=step,
            parent=parent,
            name=step.name
        )

        for value_to_target in step.actions:
            try:
                prep_value_to_signal = PreparedValueToSignal.from_origin(
                    origin=value_to_target
                )
                prep_step.prepared_actions.append(prep_value_to_signal)
            except Exception:
                prep_step.prepare_action_failures.append(value_to_target)

        for comp_to_target in step.success_criteria:
            signal = comp_to_target.to_signal()
            comp = comp_to_target.comparison
            try:
                prep_comp = PreparedSignalComparison.from_signal(
                    signal=signal, comparison=comp
                )
                prep_step.prepared_criteria.append(prep_comp)
            except Exception as ex:
                prep_comp_exc = PreparedComparisonException(
                    exception=ex,
                    identifier=getattr(signal, 'pvname', ''),
                    message='Failed to initialize comparison',
                    comparison=comp,
                    name=comp.name
                )
                prep_step.prepare_criteria_failures.append(prep_comp_exc)

        return prep_step


@dataclass
class PreparedValueToSignal:
    #: identifying name
    name: str
    #: the signal, derived from a Target
    signal: ophyd.Signal
    #: value to set to the signal
    value: PrimitiveType
    #: a link to the original ValueToTarget
    origin: ValueToTarget
    #: The result of the set action
    result: Result = field(default_factory=incomplete_result)

    async def run(self) -> Result:
        """
        Set the stored value to the signal, specifying the settle time and timeout
        if provided.  Returns a Result recording the success of this action

        Returns
        -------
        Result
        """
        # generate kwargs for set, exclude timeout and settle time if not provided
        # in order to use ophyd defaults
        set_kwargs = {'value': self.value}
        if self.origin.timeout is not None:
            set_kwargs.update({'timeout': self.origin.timeout})
        if self.origin.settle_time is not None:
            set_kwargs.update({'settle_time': self.origin.settle_time})

        try:
            status = self.signal.set(**set_kwargs)
            await util.run_in_executor(executor=None, func=status.wait)
        except Exception as ex:
            self.result = Result(severity=Severity.error, reason=ex)
            return self.result

        self.result = Result()
        return self.result

    @classmethod
    def from_origin(
        cls,
        origin: ValueToTarget
    ) -> PreparedValueToSignal:
        """
        Prepare the ValueToSignal for running.

        Parameters
        ----------
        origin : ValueToTarget
            the original ValueToTarget

        Returns
        -------
        PreparedValueToSignal

        Raises
        ------
        ValueError
            if the target cannot return a valid signal
        """
        signal = origin.to_signal()
        if signal is None:
            raise ValueError(f'Target specification invalid: {origin}')

        pvts = cls(
            name=origin.name,
            signal=signal,
            value=origin.value,
            origin=origin,
        )
        return pvts


AnyProcedure = Union[
    ProcedureGroup,
    DescriptionStep,
    PassiveStep,
    SetValueStep,
]

AnyPreparedProcedure = Union[
    PreparedProcedureGroup,
    PreparedDescriptionStep,
    PreparedPassiveStep,
    PreparedSetValueStep,
]
