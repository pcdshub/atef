from pathlib import Path
from typing import Callable

from atef.cache import get_signal_cache
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.report import ActiveAtefReport, PassiveAtefReport


def test_demo_report(
    all_config_path: Path,
    tmp_path: Path,
    load_config: Callable,
):
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
