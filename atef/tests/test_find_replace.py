import copy
import re

import pytest

from atef.config import DeviceConfiguration, PreparedDeviceConfiguration
from atef.widgets.config.find_replace import (get_item_from_path,
                                              replace_item_from_path,
                                              walk_find_match)


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
