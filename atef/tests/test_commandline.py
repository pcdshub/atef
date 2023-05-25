import sys

import happi
import pytest

import atef.bin.main as atef_main
from atef.bin import check as bin_check

from .. import util
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


@pytest.mark.asyncio
async def test_check_pv_smoke(mock_signal_cache):  # noqa: F811
    await bin_check.main(
        filename=str(CONFIG_PATH / "pv_based.yml"), signal_cache=mock_signal_cache,
        cleanup=False
    )


@pytest.mark.asyncio
async def test_check_device_smoke(monkeypatch, at2l0):  # noqa: F811
    def get_happi_device_by_name(name, client=None):
        return at2l0

    monkeypatch.setattr(util, "get_happi_device_by_name", get_happi_device_by_name)
    monkeypatch.setattr(happi.Client, "from_config", lambda: None)
    await bin_check.main(filename=str(CONFIG_PATH / "device_based.yml"), cleanup=False)


@pytest.mark.asyncio
async def test_check_ping_localhost_smoke():  # noqa: F811
    await bin_check.main(filename=str(CONFIG_PATH / "ping_localhost.json"),
                         cleanup=False)
