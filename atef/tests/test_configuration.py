import asyncio

import pytest

from atef.config import ConfigurationFile, PreparedFile

from .conftest import PASSIVE_CONFIG_PATHS


@pytest.mark.parametrize('config_path', PASSIVE_CONFIG_PATHS)
def test_prepared_config(config_path):
    # Quick smoke test to make sure we can prepare our configs
    config_file = ConfigurationFile.from_filename(config_path)
    prepared_file = PreparedFile.from_config(config_file)
    asyncio.run(prepared_file.compare())
