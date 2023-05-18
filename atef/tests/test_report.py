import json
import time
from pathlib import Path

from apischema import ValidationError, deserialize

from atef.cache import get_signal_cache
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.report import ActiveAtefReport, PassiveAtefReport


def load_config(config_path: Path):
    with open(config_path, 'r') as fd:
        serialized = json.load(fd)

    try:
        data = deserialize(ConfigurationFile, serialized)
    except ValidationError:
        try:
            data = deserialize(ProcedureFile, serialized)
        except Exception as ex:
            raise RuntimeError(f'failed to open checkout {ex}')

    return data


def test_demo_report(all_config_path: Path, tmp_path: Path):
    """ smoke test to check that reports can be generated """
    cfg = load_config(all_config_path)
    save_path = tmp_path / 'tmp.pdf'
    save_path.touch()
    if isinstance(cfg, ConfigurationFile):
        prepared_file = PreparedFile.from_config(cfg)
        doc = PassiveAtefReport(str(save_path), config=prepared_file)
    elif isinstance(cfg, ProcedureFile):
        prepared_file = PreparedProcedureFile.from_origin(cfg)
        doc = ActiveAtefReport(str(save_path), config=prepared_file)

    doc.create_report()

    cache = get_signal_cache()
    cache.clear()
    assert len(cache) == 0
    # Give signals time to be destroyed.  Hypothesis below...
    # If run in isolation, this can cause CA context unset, presumably caused
    # by running a callback on a destroyed signal.
    # If run in the full suite, this test takes place in the middle of the pack,
    # providing enough time for cleanup?
    time.sleep(2)
