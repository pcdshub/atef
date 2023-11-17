""" Test walking functions """

import pytest

from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.tests.conftest import (active_checkout_configs,
                                 passive_checkout_configs)
from atef.walk import walk_config_file, walk_procedure_file


def passive_walk_params():
    """Zip the checkout paths with their appropriate information"""
    # name, num_configs, num_comparisons
    file_info = {
        'lfe.json': (3, 42),  # 2 configs fail with happi access, 42 remaining comps
        'all_fields.json': (6, 10),  # Entire device config fails w/o happi access
        'blank_passive.json': (5, 0),  # no valid comparisons
        'ping_localhost.json': (2, 4)
    }
    param_list = []
    for filepath in passive_checkout_configs():
        param_list.append((filepath, *file_info[filepath.name]))

    return param_list


@pytest.mark.parametrize(
    'filepath,num_configs,num_comps',
    passive_walk_params()
)
def test_passive_walks(filepath, num_configs, num_comps):
    file = ConfigurationFile.from_filename(filepath)
    prep_file = PreparedFile.from_config(file)
    # also includes root
    print([type(x[0]) for x in walk_config_file(prep_file)])
    assert len(list(walk_config_file(prep_file))) == num_comps + num_configs + 1
    assert len(list(prep_file.walk_groups())) == num_configs
    assert len(list(prep_file.walk_comparisons())) == num_comps


def active_walk_params():
    """Zip the checkout paths with their appropriate information"""
    # name, num_items (tree), num_steps
    file_info = {
        'active_test.json': (5, 4),  # 1 of each step type + 1 comparison
        'blank_active.json': (6, 5),  # same as above + nested group
    }
    param_list = []
    for filepath in active_checkout_configs():
        param_list.append((filepath, *file_info[filepath.name]))

    return param_list


@pytest.mark.parametrize(
    'filepath,num_items,num_steps',
    active_walk_params()
)
def test_active_walks(filepath, num_items, num_steps):
    file = ProcedureFile.from_filename(filepath)
    prep_file = PreparedProcedureFile.from_origin(file)
    # also includes root
    print([type(x[0]) for x in walk_procedure_file(prep_file)])
    assert len(list(walk_procedure_file(prep_file))) == num_items + 1
    assert len(list(file.walk_steps())) == num_steps


# Other ideas for tests:
# - test gathered prepared comparisons match un-prepared (get_relevant_configs_comps)
#   - requires walk_comparisons on un-prepared classes, unification of ordering
# - test tree creation, match original and prepared
#   - requires more tree helpers and may change with future refactors
