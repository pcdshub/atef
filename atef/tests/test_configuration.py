import pytest

from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile


@pytest.mark.asyncio
async def test_prepared_config(passive_config_path):
    # Quick smoke test to make sure we can prepare our configs
    config_file = ConfigurationFile.from_filename(passive_config_path)
    prepared_file = PreparedFile.from_config(config_file)
    await prepared_file.compare()


@pytest.mark.asyncio
async def test_prepared_procedure(active_config_path):
    # Quick smoke test to make sure we can prepare our configs
    config_file = ProcedureFile.from_filename(active_config_path)
    prepared_file = PreparedProcedureFile.from_origin(config_file)
    await prepared_file.run()
