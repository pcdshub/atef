from pathlib import Path

from atef.cache import get_signal_cache
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.report import ActiveAtefReport, PassiveAtefReport
from atef.type_hints import AnyDataclass


def test_demo_report(
    all_loaded_config: AnyDataclass,
    tmp_path: Path,
):
    """ smoke test to check that reports can be generated """
    save_path = tmp_path / 'tmp.pdf'
    save_path.touch()
    if isinstance(all_loaded_config, ConfigurationFile):
        prepared_file = PreparedFile.from_config(all_loaded_config)
        doc = PassiveAtefReport(str(save_path), config=prepared_file)
    elif isinstance(all_loaded_config, ProcedureFile):
        prepared_file = PreparedProcedureFile.from_origin(all_loaded_config)
        doc = ActiveAtefReport(str(save_path), config=prepared_file)

    doc.create_report()

    cache = get_signal_cache()
    cache.clear()
    assert len(cache) == 0
