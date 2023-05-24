import pathlib
from typing import Callable

import pytest

from atef.config import (ConfigurationFile, DeviceConfiguration, PreparedFile,
                         PVConfiguration)
from atef.widgets.config.utils import get_relevant_pvs


@pytest.mark.asyncio
async def test_prepared_config(passive_config_path):
    # Quick smoke test to make sure we can prepare our configs
    config_file = ConfigurationFile.from_filename(passive_config_path)
    prepared_file = PreparedFile.from_config(config_file)
    await prepared_file.compare()


def test_yaml_equal_json(all_config_path: pathlib.Path, load_config: Callable, tmp_path):
    """ Read json, dump to yaml, compare dataclasses """
    json_config = load_config(all_config_path)

    yaml_path = tmp_path / 'cfg.yaml'
    yaml_path.touch()
    with open(yaml_path, 'w') as fy:
        fy.write(json_config.to_yaml())
        fy.write('\n')

    yaml_config = type(json_config).from_filename(yaml_path)
    assert json_config == yaml_config


def test_gather_pvs(
    pv_configuration: PVConfiguration,
    device_configuration: DeviceConfiguration
):
    # only one pv in pv_configuration, will just grab all PV's
    assert len(get_relevant_pvs('MY:PREFIX:hello', pv_configuration)) == 1
    assert len(get_relevant_pvs('shared', pv_configuration)) == 1

    # 2 devices, 2 attrs
    assert len(get_relevant_pvs('fjdslka', device_configuration)) == 0
    assert len(get_relevant_pvs('shared', device_configuration)) == 4
    assert len(get_relevant_pvs('setpoint', device_configuration)) == 2
