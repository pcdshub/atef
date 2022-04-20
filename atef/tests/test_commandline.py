import sys

import pytest

import atef.bin.main as atef_main

from .. import check, util
from .conftest import CONFIG_PATH
from .test_comparison_device import at2l0, mock_signal_cache  # noqa: F401


def test_help_main(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['--help'])
    atef_main.main()


@pytest.mark.parametrize('subcommand', list(atef_main.COMMANDS))
def test_help_module(monkeypatch, subcommand):
    monkeypatch.setattr(sys, 'argv', [subcommand, '--help'])
    with pytest.raises(SystemExit):
        atef_main.main()


def test_check_pv_smoke(monkeypatch, mock_signal_cache):  # noqa: F811
    from atef.bin.check import main as check_main
    monkeypatch.setattr(check, "get_signal_cache", lambda: mock_signal_cache)
    check_main(filename=str(CONFIG_PATH / "pv_based.yml"))


def test_check_device_smoke(monkeypatch, at2l0):  # noqa: F811
    from atef.bin.check import main as check_main
    monkeypatch.setattr(util, "get_happi_device_by_name", lambda: at2l0)
    check_main(filename=str(CONFIG_PATH / "device_based.yml"))
