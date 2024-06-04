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
from copy import deepcopy
from dataclasses import dataclass, field
from typing import (Any, Dict, Generator, List, Literal, Optional, Sequence,
                    Tuple, Union, cast)

import apischema
import ophyd
import pandas as pd
import yaml
from bluesky_queueserver.manager.profile_ops import (
    existing_plans_and_devices_from_nspace, validate_plan)

from atef import util
from atef.cache import DataCache, _SignalCache, get_signal_cache
from atef.check import Comparison
from atef.config import (ConfigurationFile, PreparedComparison, PreparedFile,
                         PreparedSignalComparison, run_passive_step)
from atef.enums import GroupResultMode, PlanDestination, Severity
from atef.exceptions import PreparationError, PreparedComparisonException
from atef.find_replace import RegexFindReplace
from atef.plan_utils import (BlueskyState, GlobalRunEngine,
                             get_default_namespace, register_run_identifier,
                             run_in_local_RE)
from atef.reduce import ReduceMethod
from atef.result import Result, _summarize_result_severity, incomplete_result
from atef.type_hints import AnyDataclass, AnyPath, Number, PrimitiveType
from atef.yaml_support import init_yaml_support

from . import serialization

logger = logging.getLogger(__name__)


# BlueskyState tracker.  Keys are the id of the top-level ProcedureFile
# Includes one default BlueskyState, with key id(None)
BS_STATE_MAP: Dict[int, BlueskyState] = {}

# Max depth for plan steps
MAX_PLAN_DEPTH = 200


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

    def children(self) -> List[Any]:
        """Return children of this group, as a tree view might expect"""
        return []


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

    def children(self) -> List[Union[ProcedureStep, ProcedureGroup]]:
        """Return children of this group, as a tree view might expect"""
        return self.steps

    def replace_step(
        self,
        old_step: Union[ProcedureStep, ProcedureGroup],
        new_step: Union[ProcedureStep, ProcedureGroup]
    ) -> None:
        util.replace_in_list(old_step, new_step, self.steps)


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
    """A step that sets one or more values and checks one or more values after"""
    actions: List[ValueToTarget] = field(default_factory=list)
    success_criteria: List[ComparisonToTarget] = field(default_factory=list)

    #: Stop performing actions if one fails
    halt_on_fail: bool = True
    #: Only mark the step_result as successful if all actions have succeeded
    require_action_success: bool = True

    def children(self) -> List[ComparisonToTarget]:
        """Return children of this group, as a tree view might expect"""
        return [crit.comparison for crit in self.success_criteria]

    def replace_comparison(
        self,
        old_comp: Comparison,
        new_comp: Comparison
    ) -> None:
        """replace ``old_comp`` with ``new_comp``, in success_criteria"""
        comp_list = [crit.comparison for crit in self.success_criteria]
        try:
            idx = comp_list.index(old_comp)
        except ValueError:
            raise ValueError('attempted to replace a comparison that does not '
                             f'exist: {old_comp} -> {new_comp}')

        new_crit = deepcopy(self.success_criteria[idx])
        new_crit.comparison = new_comp

        util.replace_in_list(
            old=self.success_criteria[idx],
            new=new_crit,
            item_list=self.success_criteria
        )


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
    # TODO: a method for getting the PV from device/attr pairs

    def to_signal(
        self,
        signal_cache: Optional[_SignalCache] = None
    ) -> Optional[ophyd.Signal]:
        """
        Return the signal described by this Target.  First attempts to use the
        device + attr information to look up the signal in happi, falling back
        to the raw PV.

        Returns
        -------
        ophyd.Signal
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
class PlanData:
    #: user-provided name for this plan data.  Not used to identify the run
    name: str = ""
    #: identifier of PlanStep to grab data from.
    #: set via GUI, must match a PreparedPlan.plan_id.  Options should be fixed
    #: and regenerated with every view
    plan_id: Optional[str] = None
    #: plan number (for plans containing nested plans, which return multiple uuids)
    plan_no: int = 0
    #: data point(s) in plan (0-indexed) or slice notation
    #: [row_start, row_end)
    data_points: Union[List[int], Tuple[int, int]] = field(default_factory=list)
    #: Field / column names to grab data from
    field_names: List[str] = field(default_factory=list)
    #: data reduction / operation
    reduction_mode: ReduceMethod = ReduceMethod.average


@dataclass
class ComparisonToPlanData(PlanData):
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
    #: Name to identify this plan
    name: str
    #: The plan name.  Bluesky plan or otherwise
    plan: str
    #: Plan arguments dictionary - argument name to value.
    args: Sequence[Any] = field(default_factory=list)
    #: Plan keyword  arguments dictionary - argument name to value.
    kwargs: Dict[Any, Any] = field(default_factory=dict)
    #: Arguments which should not be configurable.
    fixed_arguments: Optional[Sequence[str]] = None

    def to_plan_item(self: PlanOptions) -> Dict[str, Any]:
        """Makes a plan item (dictionary of parameters) for a given PlanStep"""
        it = {
            "name": self.plan,
            "args": self.args,
            "kwargs": self.kwargs,
            "user_group": "root"}
        return it


@dataclass
class PlanStep(ProcedureStep):
    """A procedure step comprised of one or more bluesky plans."""
    plans: Sequence[PlanOptions] = field(default_factory=list)
    checks: Sequence[Union[ComparisonToTarget, ComparisonToPlanData]] = field(
        default_factory=list
    )

    #: platform for plan to be run on
    destination: PlanDestination = PlanDestination.local
    #: Stop performing plans if one fails
    halt_on_fail: bool = True
    #: Only mark step_result successfull if all steps have succeeded
    require_plan_success: bool = True

    def children(self) -> List[Union[PlanOptions, ComparisonToTarget,
                                     ComparisonToPlanData]]:
        """Return children of this group, as a tree view might expect"""
        # Subject to change as PlanStep is developed
        return self.plans + [check.comparison for check in self.checks]


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
class TemplateStep(ProcedureStep):
    """A procedure step that replaces items an existing checkout"""
    # Path to a valid ProcedureFile or ConfigurationFile
    filename: AnyPath = ''
    # List of edits to be applied to ``file_path``
    edits: List[RegexFindReplace] = field(default_factory=list)


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

    def __post_init__(self):
        # register a BSState for this ProcedureFile
        BS_STATE_MAP[id(self)] = BlueskyState()

    def walk_steps(self) -> Generator[AnyProcedure, None, None]:
        yield self.root
        yield from self.root.walk_steps()

    def children(self) -> List[ProcedureGroup]:
        """Return children of this group, as a tree view might expect"""
        return [self.root]

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

    def validate(self) -> Tuple[bool, str]:
        """Validate the file is properly formed and can be prepared"""
        try:
            prep_file = PreparedProcedureFile.from_origin(self)
            prep_failures = len(prep_file.root.prepare_failures)
            if prep_failures > 0:
                return False, f'Failed to prepare {prep_failures} steps'
        except Exception as ex:
            logger.debug(ex)
            msg = f'Unknown Error: {ex}.'
            return False, msg

        return True, ''


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

        prep_proc_file = PreparedProcedureFile(
            file=file,
            root=PreparedProcedureGroup()
        )

        # PreparedProcedureGroup needs to know about its parent from birth
        # get_bs_state doesn't like orphans
        prepared_root = PreparedProcedureGroup.from_origin(
            group=file.root, parent=prep_proc_file
        )
        prep_proc_file.root = prepared_root

        return prep_proc_file

    async def run(self) -> Result:
        return await self.root.run()


@dataclass
class FailedStep:
    """A step that failed to be prepared for running."""
    #: The data cache to use for the preparation step.
    parent: Optional[PreparedProcedureGroup]
    #: Configuration instance.
    origin: AnyProcedure
    #: overall result of running the step
    combined_result: Result = field(default_factory=incomplete_result)
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
        """Run the step and return the result"""
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
            if isinstance(step, PlanStep):
                return PreparedPlanStep.from_origin(
                    origin=step, parent=parent
                )
            if isinstance(step, TemplateStep):
                return PreparedTemplateStep.from_origin(
                    step=step, parent=parent,
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
        """Run all steps and return a combined result"""
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
        """Re-compute the combined result and return it"""
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

    def walk_steps(self) -> Generator[AnyPreparedProcedure]:
        for step in self.steps:
            step = cast(AnyPreparedProcedure, step)
            yield step
            if hasattr(step, 'walk_steps'):
                yield from step.walk_steps()


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
        """Load, prepare, and run the passive step"""
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
    #: list of prepared success criteria (comparisons)
    prepared_criteria: List[PreparedSignalComparison] = field(
        default_factory=list
    )

    def walk_comparisons(self) -> Generator[PreparedComparison, None, None]:
        """Yields PreparedComparisons in this ProcedureStep"""
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
        self.origin = cast(SetValueStep, self.origin)
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
            action_results = [action.result for action in self.prepared_actions]
        else:
            action_results = []

        criteria_results = [crit.result for crit in self.prepared_criteria]

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
                    origin=value_to_target, parent=prep_step
                )
                prep_step.prepared_actions.append(prep_value_to_signal)
            except Exception as ex:
                return FailedStep(parent=parent, origin=step, exception=ex)

        for comp_to_target in step.success_criteria:
            res = create_prepared_comparison(comp_to_target)
            if isinstance(res, Exception):
                return FailedStep(parent=parent, origin=step, exception=res)
            else:
                prep_step.prepared_criteria.append(res)

        return prep_step


@dataclass
class PreparedTemplateStep(PreparedProcedureStep):
    # configuration origin
    origin: TemplateStep = field(default_factory=TemplateStep)
    # prepared file with edits applied
    file: Union[PreparedFile, PreparedProcedureFile] = field(
        default_factory=PreparedFile
    )

    @classmethod
    def from_origin(
        cls,
        step: TemplateStep,
        parent: Optional[PreparedProcedureGroup] = None
    ) -> PreparedTemplateStep:
        """
        Prepare a TemplateStep for running.  Applies edits and attempts

        Parameters
        ----------
        step : TemplateStep
            the original TemplateStep (not prepared)
        parent : Optional[PreparedProcedureGroup]
            the hierarchical parent for the prepared step.

        Returns
        -------
        PreparedTemplateStep
        """
        # load file
        try:
            orig_file = ConfigurationFile.from_filename(step.filename)
        except apischema.ValidationError:
            logger.debug('failed to open as passive checkout')
            try:
                orig_file = ProcedureFile.from_filename(step.filename)
            except apischema.ValidationError:
                logger.error('failed to open file as either active '
                             'or passive checkout')
                raise ValueError('Could not open the file as either active or '
                                 'passive checkout.')

        # convert and apply edits
        edits = [e.to_action() for e in step.edits]
        edit_results = [edit.apply(target=orig_file) for edit in edits]

        if not all(edit_results):
            return FailedStep(
                origin=step,
                parent=parent,
                exception=PreparationError,
                result=Result(
                    severity=Severity.internal_error,
                    reason=(
                        f'Failed to prepare templated config: ({step.name}) '
                        f'Could not apply all edits.'
                    )
                )
            )

        # verify edited file
        success, msg = orig_file.validate()
        if not success:
            return FailedStep(
                origin=step,
                parent=parent,
                exception=PreparationError,
                combined_result=Result(
                    severity=Severity.internal_error,
                    reason=(
                        f'Failed to prepare templated config: ({step.name}) '
                        f'Configuration not valid when edits were applied. {msg}'
                    )
                )
            )

        # prepare file
        if isinstance(orig_file, ConfigurationFile):
            prep_file = PreparedFile.from_config(file=orig_file)
        else:
            # need to set all the verifications off.
            # TODO: refactor when global settings are implemented
            for orig_step in orig_file.walk_steps():
                orig_step.verify_required = False
            prep_file = PreparedProcedureFile.from_origin(file=orig_file)

        prepared = cls(
            origin=step,
            file=prep_file,
            parent=parent,
        )
        return prepared

    async def _run(self) -> Result:
        """
        Run the edited ProcedureFile

        Returns
        -------
        Result
            the step_reesult for this step
        """
        if isinstance(self.file, PreparedFile):
            result = await run_passive_step(self.file)
        else:
            result = await self.file.run()

        return result


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
    #: parent step that owns this instance
    parent: Optional[PreparedSetValueStep] = None
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
        origin: ValueToTarget,
        parent: Optional[PreparedSetValueStep] = None
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
            parent=parent
        )
        return pvts


@dataclass
class PreparedPlan:
    #: identifying name
    name: str
    #: a link to the original PlanOptions
    origin: PlanOptions
    #: the hierarchical parent of this PreparedPlan
    parent: Optional[PreparedPlanStep] = None
    #: the plan item, suitable for submission to a bluesky queueserver
    item: Dict[str, any] = field(default_factory=dict)
    #: the plan identifer, may be different from origin.plan_id
    plan_id: Optional[str] = None
    #: stashed BlueskyState.  Passed to RunEngine runner
    bs_state: Optional[BlueskyState] = None
    #: result of this step. (did the plan run?)
    result: Result = field(default_factory=incomplete_result)

    async def run(self) -> Result:
        # submit the plan to the destination
        if self.parent.origin.destination != PlanDestination.local:
            self.result = Result(
                severity=Severity.error,
                reason='Only local RunEngine supported at this time'
            )

        run_in_local_RE(self.item, self.plan_id, self.bs_state)
        self.result = Result()
        return self.result

    @classmethod
    def from_origin(
        cls,
        origin: PlanOptions,
        parent: Optional[PreparedPlanStep] = None
    ) -> PreparedPlan:
        # register run identifier, store in prepared_plan name
        bs_state = get_bs_state(parent)
        identifier = register_run_identifier(bs_state, origin.name or origin.plan)
        return cls(
            name=origin.name,
            item=origin.to_plan_item(),
            plan_id=identifier,
            bs_state=bs_state,
            origin=origin,
            parent=parent
        )


@dataclass
class PreparedPlanStep(PreparedProcedureStep):
    #: a link to the original PlanStep
    origin: PlanStep = field(default_factory=PlanStep)
    #: list of PreparedPlan
    prepared_plans: List[PreparedPlan] = field(default_factory=list)
    #: list of PlanOption's that led to failed PreparedPlan's
    prepared_plan_failures: List[PlanOptions] = field(default_factory=list)
    #: list of success criteria
    prepared_checks: List[Union[PreparedSignalComparison,
                                PreparedPlanComparison]] = field(default_factory=list)
    #: list of failed checks
    prepared_checks_failures: List[PreparedComparisonException] = field(
        default_factory=list
    )

    async def _run(self) -> Result:
        """Gather plan options and run the bluesky plan"""
        # Construct plan (get devices, organize args/kwargs, run)
        # verify
        # - gather namespace (plans, devices)
        # - validate_plan
        # send plan to destination (local, queue server, ...)
        # local -> get global run engine, setup
        # qserver -> send to queueserver

        if self.origin.require_plan_success and self.prepared_plan_failures:
            return Result(
                severity=Severity.error,
                reason=('One or more actions failed to prepare: '
                        f'{[plan.name for plan in self.prepared_plan_failures]}')
            )

        # get namespace (based on destination?
        # How will we know what's in the queueserver's destination?)
        # Run the plans
        nspace = get_default_namespace()
        epd = existing_plans_and_devices_from_nspace(nspace=nspace)
        plans, devices, _, __ = epd
        plan_status = [validate_plan(plan.item, allowed_plans=plans,
                                     allowed_devices=devices)
                       for plan in self.prepared_plans]

        validation_status = [status[0] for status in plan_status]
        if not all(validation_status):
            # raise an error, place info in result
            fail_plan_names = [pl.name for pl, st in
                               zip(self.prepared_plans, validation_status)
                               if not st]
            logger.debug(f'One or more plans ({fail_plan_names}) failed validation')
            return Result(
                severity=Severity.error,
                reason=f'One or more plans ({fail_plan_names}) failed validation'
            )

        # send each plan to correct destination
        plan_results = []
        for pplan in self.prepared_plans:
            logger.debug(f'running plan: {pplan.name}...')
            res = await pplan.run()
            logger.debug(f'run completed: {res}')
            plan_results.append(res)

        # run the checks
        for prep_check in self.prepared_checks:
            await prep_check.compare()

        check_results = [check.result for check in self.prepared_checks]

        if self.prepared_checks_failures:
            return Result(
                severity=Severity.error,
                reason=('One or more success criteria failed to initialize: '
                        f'{[check.name for check in self.prepared_checks_failures]}')
            )

        severity = _summarize_result_severity(GroupResultMode.all_,
                                              check_results + plan_results)
        reason = (f'{len(plan_results)} plans run, '
                  f'{len(check_results)} checks passed')
        return Result(severity=severity, reason=reason)

    @classmethod
    def from_origin(
        cls,
        origin: PlanStep,
        parent: Optional[PreparedProcedureGroup] = None
    ) -> PreparedPlanStep:

        prep_step = cls(
            origin=origin,
            parent=parent,
            name=origin.name,
        )

        for plan_step in origin.plans:
            try:
                prep_plan = PreparedPlan.from_origin(plan_step, parent=prep_step)
                prep_step.prepared_plans.append(prep_plan)
            except Exception:
                prep_step.prepared_plan_failures.append(plan_step)

        for check in origin.checks:
            res = create_prepared_comparison(check, parent=prep_step)
            if isinstance(res, Exception):
                prep_step.prepared_checks_failures.append(res)
            else:
                prep_step.prepared_checks.append(res)

        return prep_step


def create_prepared_comparison(
    check: Union[ComparisonToTarget, ComparisonToPlanData],
    parent: Optional[AnyDataclass] = None
) -> Union[PreparedComparison, PreparedComparisonException]:

    output = ValueError('Cannot prepare the provided comparison, type not '
                        f'supported: ({check})')

    if isinstance(check, ComparisonToTarget):
        signal = check.to_signal()
        comp = check.comparison
        try:
            output = PreparedSignalComparison.from_signal(
                signal=signal, comparison=comp, parent=parent
            )
        except Exception as ex:
            output = PreparedComparisonException(
                exception=ex,
                identifier=getattr(signal, 'pvname', ''),
                message='Failed to initialize comparison',
                comparison=comp,
                name=comp.name
            )

    elif isinstance(check, ComparisonToPlanData):
        comp = check.comparison
        try:
            output = PreparedPlanComparison.from_comp_to_plan(
                check, parent=parent
            )
        except Exception as ex:
            output = PreparedComparisonException(
                exception=ex,
                identifier=getattr(check, 'plan_id', ''),
                message='Failed to initialize comparison',
                comparison=comp,
                name=comp.name
            )

    return output


def get_bs_state(dclass: PreparedProcedureStep) -> BlueskyState:
    """
    Get the BlueskyState instance corresponding to ``dclass``.
    Each ProcedureFile gets assigned a single BlueskyState.
    Walk parents up to the top-level PreparedProcedureFile, find its origin
    (ProcedureFile), then return its correspoinding BlueskyState

    Parameters
    ----------
    dclass : PreparedProcedureStep
        the current prepared-variant procedure step

    Returns
    -------
    BlueskyState
        the BlueskyState holding run information and allowed devices/plans
    """
    # dclass should be a Prepared dataclass
    if dclass is None:
        top_dclass = None
    else:
        top_dclass = dclass
        ctr = 0
        # This isn't my finest work, but it does work
        while ((getattr(top_dclass, 'parent', None) is not None) and
                (ctr < MAX_PLAN_DEPTH)):
            top_dclass = top_dclass.parent
            ctr += 1

        if ctr >= MAX_PLAN_DEPTH:
            logger.warning(f'{ctr} "parents" traversed, either the depth of '
                           'this file is excessive or an infinite loop occurred')
        if not isinstance(top_dclass, PreparedProcedureFile):
            logger.debug('top-level dataclass was not a PreparedProcedureFile, '
                         f' {type(top_dclass)}, using default BlueskyState')
            top_dclass = None
        else:
            top_dclass = top_dclass.file  # grab un-prepared ProcedureFile

    top_dclass_id = id(top_dclass)
    if top_dclass_id not in BS_STATE_MAP:
        BS_STATE_MAP[top_dclass_id] = BlueskyState()

    return BS_STATE_MAP[top_dclass_id]


@dataclass
class PreparedPlanComparison(PreparedComparison):
    """
    Unified representation for comparisons to Bluesky Plan data
    """
    #: Original plan data, holds relevant data coordinates
    plan_data: Optional[ComparisonToPlanData] = None
    #: The hierarchical parent of this comparison
    parent: Optional[PreparedPlanStep] = field(default=None, repr=None)
    #: The value from the plan, to which the comparison will take place
    data: Optional[Any] = None

    async def get_data_async(self) -> Any:
        bs_state = get_bs_state(self.parent)
        # get BSState, look for entry?
        data = get_RE_data(bs_state, self.plan_data)
        return data

    async def _compare(self, data: Any) -> Result:
        if data is None:
            # 'None' is likely incompatible with our comparisons and should
            # be raised for separately
            return Result(
                severity=self.comparison.if_disconnected,
                reason=(
                    f"No data available for signal {self.identifier!r} in "
                    f"comparison {self.comparison}"
                ),
            )

        return self.plan_data.comparison.compare(data, identifier=self.identifier)

    @classmethod
    def from_comp_to_plan(
        cls,
        origin: ComparisonToPlanData,
        cache: Optional[DataCache] = None,
        parent: Optional[PreparedPlanStep] = None
    ) -> PreparedPlanComparison:

        identifier = origin.comparison.name + f'[{origin.data_points}]'
        name = origin.name

        if cache is None:
            cache = DataCache()

        return cls(
            plan_data=origin,
            cache=cache,
            identifier=identifier,
            comparison=origin.comparison,
            name=name,
            parent=parent
        )


def get_RE_data(bs_state: BlueskyState, plan_data: PlanData) -> Any:
    """
    Get databroker data from the GlobalRunEngine from the plan specified by
    ``plan_data``.

    Parameters
    ----------
    bs_state : BlueskyState
        the BlueskyState whose run_map holds the uuid for ``plan_data``
    plan_data : PlanData
        the plan data specification (data points, plan number, field names)

    Returns
    -------
    Any
        Array of data specified by ``plan_data``

    Raises
    ------
    RuntimeError
        if the data cannot be found in ``bs_state``
    ValueError
        if ``plan_data`` does not provide parsable datapoints
    """
    gre = GlobalRunEngine()
    run_uuids = bs_state.run_map[plan_data.plan_id]
    if run_uuids is None:
        raise RuntimeError("Data unavailable, run cannot be found.  Has the "
                           f"referenced plan (id: {plan_data.plan_id}) "
                           "been run?")

    # Use index to grab right uuid, data row
    uuid = run_uuids[plan_data.plan_no]
    plan_table: pd.DataFrame = gre.db[uuid].table()
    field_series: pd.Series = plan_table[plan_data.field_names]

    if isinstance(plan_data.data_points, tuple):
        data_slice = slice(*plan_data.data_points)
        data_table = field_series[data_slice].to_numpy()
    elif isinstance(plan_data.data_points, list):
        data_table = field_series.iloc[plan_data.data_points].to_numpy()
    else:
        raise ValueError(f'Unable to parse data point format: {plan_data.data_points}')

    reduced_data = plan_data.reduction_mode.reduce_values(data_table)
    return reduced_data


AnyProcedure = Union[
    ProcedureGroup,
    DescriptionStep,
    PassiveStep,
    SetValueStep,
    PlanStep,
    TemplateStep,
]

AnyPreparedProcedure = Union[
    PreparedProcedureGroup,
    PreparedDescriptionStep,
    PreparedPassiveStep,
    PreparedSetValueStep,
    PreparedPlanStep,
    PreparedTemplateStep,
]
