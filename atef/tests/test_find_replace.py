import copy
import re
from typing import Any, List, Tuple

import pytest

from atef.check import Equals, GreaterOrEqual
from atef.config import (ConfigurationGroup, DeviceConfiguration,
                         PreparedDeviceConfiguration)
from atef.enums import Severity
from atef.find_replace import (get_deepest_dataclass_in_path,
                               get_item_from_path, replace_item_from_path,
                               walk_find_match)


@pytest.mark.parametrize(
    "search_str, simple_path",
    [
        ('motor1', [[(DeviceConfiguration, 'devices'), ("__list__", 0)]]),
        ('motor', [
            [(DeviceConfiguration, 'devices'), ("__list__", 0)],
            [(DeviceConfiguration, 'devices'), ("__list__", 1)]
        ]),
        ('device config 1', [[(DeviceConfiguration, 'name')]]),
        ('setpo', [[(DeviceConfiguration, 'by_attr'), ("__dictkey__", "setpoint")]]),
        ('error', [
            [(DeviceConfiguration, 'by_attr'), ("__dictvalue__", "setpoint"),
             ("__list__", 0), (Equals, 'severity_on_failure'),
             ("__enum__", Severity.error)],
            [(DeviceConfiguration, 'by_attr'), ("__dictvalue__", "setpoint"),
             ("__list__", 0), (Equals, 'if_disconnected'),
             ("__enum__", Severity.error)],
            [(DeviceConfiguration, 'by_attr'), ("__dictvalue__", "readback"),
             ("__list__", 0), (GreaterOrEqual, 'severity_on_failure'),
             ("__enum__", Severity.error)],
            [(DeviceConfiguration, 'by_attr'), ("__dictvalue__", "readback"),
             ("__list__", 0), (GreaterOrEqual, 'if_disconnected'),
             ("__enum__", Severity.error)],
        ]),
    ]
)
def test_walk_find_match(
    device_configuration: DeviceConfiguration,
    search_str: str,
    simple_path: List[Tuple[Any, Any]]
):
    regex = re.compile(search_str)

    def match_fn(input):
        return regex.search(str(input)) is not None

    path = walk_find_match(device_configuration, match_fn)
    for expected_path, found_path in zip(simple_path, path):
        simplified_path = []
        for seg in found_path:
            if not isinstance(seg[0], str):
                item = type(seg[0])
            else:
                item = seg[0]
            simplified_path.append((item, seg[1]))
        assert expected_path == simplified_path


@pytest.mark.parametrize(
    "search_str, simple_path",
    [
        ('integer', [[(ConfigurationGroup, 'values'), ("__dictkey__", "integer")]]),
        ('^1$', [[(ConfigurationGroup, 'values'), ("__dictvalue__", "integer")]])
    ]
)
def test_walk_find_match_2(
    configuration_group: ConfigurationGroup,
    search_str: str,
    simple_path: List[Tuple[Any, Any]]
):
    regex = re.compile(search_str)

    def match_fn(input):
        # if type(input)
        return regex.search(str(input)) is not None

    path = walk_find_match(configuration_group, match_fn)
    for expected_path, found_path in zip(simple_path, path):
        simplified_path = []
        for seg in found_path:
            if not isinstance(seg[0], str):
                item = type(seg[0])
            else:
                item = seg[0]
            simplified_path.append((item, seg[1]))
        assert expected_path == simplified_path


@pytest.mark.parametrize(
    "path, expected_item",
    [
        ([('PVCONFIG', 'name')], 'pv config 1'),  # simple field
        ([('PVCONFIG', 'by_pv'), ('__dictvalue__', "MY:PREFIX:hello"),
          ("__list__", 1), ('EQUALS', 'severity_on_failure'),
          ("__enum__", Severity.warning)],
         'error'),  # deep enum, grabs name for comparison purposes
        ([('PVCONFIG', 'by_pv'), ("__dictkey__", "MY:PREFIX:hello")],
         'MY:PREFIX:hello'),  # dictkey
        ([('PVCONFIG', 'by_pv'), ('__dictvalue__', 'MY:PREFIX:hello'),
          ("__list__", 1)], Equals(value=.1)),  # dclass in list
    ]
)
def test_get_item_from_path(pv_configuration, path, expected_item):
    found_item = get_item_from_path(path, item=pv_configuration)
    assert found_item == expected_item


@pytest.mark.parametrize(
    "search_str, replace_str, flags, num_changes",
    [
        ('motor1', 'enum1', re.UNICODE, 1),  # replace in list
        ('MOTor1', 'enum1', re.UNICODE, 0),  # case sensitivity in list
        ('CoNfIG', 'shmonfig', re.UNICODE, 0),  # case sensitivity in field
        ('device config', 'fart', re.UNICODE, 1),  # replace bare field
        ('MOTor1', 'enum1', re.IGNORECASE, 1),
        ('motor', 'enum', re.IGNORECASE, 2),  # partial match hits 2 devices
        ('error', 'warning', re.UNICODE, 4),  # replace enums
        ('ErROR', 'warning', re.UNICODE, 0),  # enums are case sensitive
        ('Err.*', 'warning', re.IGNORECASE, 4),
        ('setpoint', 'velocity', re.UNICODE, 1),  # replace keys
        ('5', '4444.4', re.UNICODE, 1),  # replace non-strings, in bare field
    ]
)
def test_replace_pipeline(
    device_configuration: DeviceConfiguration,
    search_str: str,
    replace_str: str,
    flags: int,
    num_changes: int
):
    edited_config = copy.deepcopy(device_configuration)

    assert edited_config is not device_configuration

    regex = re.compile(search_str, flags=flags)

    def match_fn(input):
        return regex.search(str(input)) is not None

    def replace_fn(input):
        if isinstance(input, str):
            return regex.sub(replace_str, input)
        elif isinstance(input, int):
            return int(float(replace_str))
        else:  # try to cast as original type
            return type(input)(replace_str)

    match_paths = walk_find_match(device_configuration, match_fn)
    assert len(list(match_paths)) == num_changes
    for path in list(walk_find_match(device_configuration, match_fn)):
        orig_item = get_item_from_path(path[:-1], item=device_configuration)
        replace_item_from_path(edited_config, path, replace_fn=replace_fn)
        new_item = get_item_from_path(path[:-1], item=edited_config)
        assert orig_item != new_item

    # smoke test preparation
    PreparedDeviceConfiguration.from_config(edited_config)


def test_deepest_dclass(configuration_group):
    path = list(walk_find_match(configuration_group, lambda x: x == -10))[0]
    copy_group = copy.deepcopy(configuration_group)
    deepest_orig = get_deepest_dataclass_in_path(path)
    deepest_copy = get_deepest_dataclass_in_path(path, item=copy_group)

    assert deepest_orig == deepest_copy

# Add test for switching files
# add test from widget side (instantiate widget from file, populate change_list)
