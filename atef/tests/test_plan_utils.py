import pytest

from atef.enums import PlanDestination
from atef.plan_utils import BlueskyState, GlobalRunEngine, run_in_local_RE
from atef.procedure import register_run_identifier


def test_run_identifier():
    bs_state = BlueskyState()
    register_run_identifier(bs_state, 'run_name')
    assert 'run_name' in bs_state.run_map

    register_run_identifier(bs_state, 'run_name')
    assert len(bs_state.run_map) == 2
    assert 'run_name' in bs_state.run_map
    assert 'run_name_1' in bs_state.run_map

    register_run_identifier(bs_state, 'run_name')
    assert len(bs_state.run_map) == 3
    assert 'run_name_2' in bs_state.run_map


def test_bluesky_state():
    state = BlueskyState()
    state.get_allowed_plans_and_devices(PlanDestination.local)


@pytest.mark.parametrize(
    "plan_item, num_points", [
        pytest.param(
            {'name': 'scan',
             'args': [['enum1'], ['motor2'], [(0, 10)], 10],
             'kwargs': {},
             'user_group': 'root'},
            10,
            id='scan',
        ),
        pytest.param(
            {'name': 'count',
             'args': [['motor1.acceleration']],
             'kwargs': {'num': 13},
             'user_group': 'root'},
            13,
            id='count',
        ),
        pytest.param(
            {'name': 'count',
             'args': [['motor1.acceleration'], 42],
             'kwargs': {},
             'user_group': 'root'},
            42,
            id='count_arg',
        ),
        pytest.param(
            {'name': 'grid_scan',
             'args': [['enum1'], ['motor1', 'motor2'], [(0, 1, 10), (0, 1, 10)]],
             'kwargs': {'snake_axes': False},
             'user_group': 'root'},
            100,
            id='grid',
        ),]
)
def test_run_local_plan(plan_item, num_points):
    bs_state = BlueskyState()
    run_in_local_RE(item=plan_item, identifier=plan_item['name'], state=bs_state)
    gre = GlobalRunEngine()
    uuids = bs_state.run_map[plan_item['name']]
    print(gre.db[uuids[0]].table())
    assert len(gre.db[uuids[0]].table()) == num_points
