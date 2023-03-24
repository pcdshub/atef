import pytest

from atef.enums import Severity
from atef.procedure import DescriptionStep, ProcedureStep
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
    """ Verify logic used to combine step_result and verify_result """
    pstep = ProcedureStep()
    pstep.verify_required = verify_required
    if verify_result:
        pstep.verify_result = verify_result
    pstep.step_success_required = step_success_required
    if step_result:
        pstep.step_result = step_result

    # verify internal logic for final result
    assert pstep.result.severity == expected.severity
    assert (expected.reason or '') in (pstep.result.reason or '')


def test_description_step_results():
    """ Pass if DescriptionStep step_result always passes """
    desc_step = DescriptionStep()
    desc_step.run()
    # step phase of the description step always passes
    assert desc_step.step_result == pass_result
