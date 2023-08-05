from atef.plan_utils import BlueskyState
from atef.procedure import register_run_identifier


def test_run_identifier():
    bs_state = BlueskyState()
    register_run_identifier(bs_state, 'run_name')
    assert 'run_name' in bs_state.run_map

    register_run_identifier(bs_state, 'run_name')
    assert len(bs_state.run_map) == 2
    assert 'run_name' in bs_state.run_map
    assert 'run_name_1' in bs_state.run_map
