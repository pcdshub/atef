import json

import apischema
import pytest

from atef.check import Equals
from atef.config import ConfigurationFile, ConfigurationGroup
from atef.enums import Severity
from atef.find_replace import RegexFindReplace
from atef.procedure import (ComparisonToPlanData, DescriptionStep, PlanOptions,
                            PlanStep, PreparedPlanStep, PreparedProcedureFile,
                            PreparedProcedureStep, PreparedTemplateStep,
                            ProcedureFile, ProcedureGroup, TemplateStep)
from atef.result import Result

pass_result = Result()
fail_result = Result(severity=Severity.error)


@pytest.mark.parametrize(
    "verify_required,verify_result,step_success_required,step_result,expected",
    [
        (True, pass_result, True, pass_result, pass_result),  # both pass
        (False, pass_result, True, pass_result, pass_result),
        (True, pass_result, False, pass_result, pass_result),
        (False, pass_result, False, pass_result, pass_result),

        (True, fail_result, True, pass_result, fail_result),  # verify fails
        (True, fail_result, False, pass_result, fail_result),
        (False, fail_result, True, pass_result, pass_result),
        (False, fail_result, False, pass_result, pass_result),

        (True, pass_result, True, fail_result, fail_result),  # step fails
        (True, pass_result, False, fail_result, pass_result),
        (False, pass_result, True, fail_result, fail_result),
        (False, pass_result, False, fail_result, pass_result),

        (True, fail_result, True, fail_result, fail_result),  # both fail
        (True, fail_result, False, fail_result, fail_result),
        (False, fail_result, True, fail_result, fail_result),
        (False, fail_result, False, fail_result, pass_result),
    ]
)
def test_procedure_step_results(
    verify_required: bool,
    verify_result: Result,
    step_success_required: bool,
    step_result: Result,
    expected: Result
):
    """Verify logic used to combine step_result and verify_result"""
    pstep = DescriptionStep()
    prep_pstep = PreparedProcedureStep.from_origin(pstep)
    pstep.verify_required = verify_required
    if verify_result:
        prep_pstep.verify_result = verify_result
    pstep.step_success_required = step_success_required
    if step_result:
        prep_pstep.step_result = step_result

    # verify internal logic for final result
    assert prep_pstep.result.severity == expected.severity
    assert (expected.reason or '') in (prep_pstep.result.reason or '')


@pytest.mark.asyncio
async def test_description_step_results():
    """Pass if DescriptionStep step_result always passes"""
    desc_step = DescriptionStep()
    prep_desc_step = PreparedProcedureStep.from_origin(desc_step)
    await prep_desc_step.run()
    # step phase of the description step always passes
    assert prep_desc_step.step_result == pass_result


@pytest.mark.asyncio
async def test_prepared_procedure(active_config_path):
    procedure_file = ProcedureFile.from_filename(filename=active_config_path)
    # simple smoke test
    ppf = PreparedProcedureFile.from_origin(file=procedure_file)
    await ppf.run()


@pytest.mark.asyncio
async def test_plan_step():
    """Pass if a PlanStep can be prepared and run in isolation"""
    # Note: this is not the standard way of using these plan steps.
    #       Normally PlanSteps will have a top-level ProcedureFile that owns
    #       its own BlueskyState.  Here we use the default None-BlueskyState
    plan_opt_1 = PlanOptions(
        name='plan_opt_1',
        plan='scan',
        args=[['motor1'], ['motor2'], [(0, 10)], 10]
    )

    plan_comp = ComparisonToPlanData(
        plan_id='plan_opt_1', data_points=(0, 1, 4),
        field_names=['motor2'],
        comparison=Equals(name='equals', value=1, invert=True)
    )

    plan_step = PlanStep('one plan', plans=[plan_opt_1], checks=[plan_comp])

    prepared_plan_step = PreparedPlanStep.from_origin(plan_step, parent=None)

    await prepared_plan_step.run()

    # plan step has an additional reason, so we compare directly to the severity
    print(prepared_plan_step.step_result)
    assert prepared_plan_step.step_result.severity == pass_result.severity
    print(prepared_plan_step.prepared_checks[0].result)
    assert prepared_plan_step.prepared_checks[0].result.severity == pass_result.severity
    print(prepared_plan_step.prepared_plans[0].result)
    assert prepared_plan_step.prepared_plans[0].result.severity == pass_result.severity


@pytest.mark.asyncio
async def test_template_step_active_target(tmp_path):
    cfg_path = tmp_path / 'tmp_checkout.json'
    orig_file = ProcedureFile(root=ProcedureGroup(name='root'))
    ser = apischema.serialize(ProcedureFile, orig_file)
    with open(cfg_path, 'w') as fp:
        json.dump(ser, fp)

    replace_title_path = [
        ('atef.config.ConfigurationFile', 'root'),
        ('atef.config.ConfigurationGroup', 'name')
    ]
    replace_title_edit = RegexFindReplace(
        path=replace_title_path,
        search_regex='root',
        replace_text='template replaced title'
    )
    ts = TemplateStep(
        name='template all fields',
        filename=cfg_path,
        edits=[replace_title_edit],
        verify_required=False,
    )

    pts = PreparedTemplateStep.from_origin(step=ts)

    assert pts.file.root.origin.name == 'template replaced title'

    result = await pts.run()
    assert result.severity == Severity.success


@pytest.mark.asyncio
async def test_template_step_passive_target(tmp_path):
    cfg_path = tmp_path / 'tmp_checkout.json'
    orig_file = ConfigurationFile(root=ConfigurationGroup(name='root'))
    ser = apischema.serialize(ConfigurationFile, orig_file)
    with open(cfg_path, 'w') as fp:
        json.dump(ser, fp)

    replace_title_path = [
        ('atef.config.ConfigurationFile', 'root'),
        ('atef.config.ConfigurationGroup', 'name')
    ]
    replace_title_edit = RegexFindReplace(
        path=replace_title_path,
        search_regex='root',
        replace_text='template replaced title'
    )
    ts = TemplateStep(
        name='template all fields',
        filename=cfg_path,
        edits=[replace_title_edit],
        verify_required=False,
    )

    pts = PreparedTemplateStep.from_origin(step=ts)

    assert pts.file.root.config.name == 'template replaced title'

    result = await pts.run()
    assert result.severity == Severity.success
