from typing import Callable

import pytest
from ophyd.signal import EpicsSignal
from ophyd.sim import SynSignal
from pytestqt.qtbot import QtBot
from qtpy.QtCore import Qt

from atef import cache
from atef.enums import Severity
from atef.procedure import PreparedProcedureFile
from atef.widgets.config.run_base import RunCheck
from atef.widgets.config.window import DualTree


@pytest.mark.parametrize("click_abort,", [
    (True,),
    (False,),
])
def test_abort_basic(
    qtbot: QtBot,
    make_page: Callable,
    set_value_step,
    click_abort: bool,
):
    """ensure widgets appear and operate as expected"""
    # monkeypatch cache to use a simple dummy signal
    # forgive me for my typing sins
    cache._signal_cache = cache._SignalCache(lambda pv, name: SynSignal(name=pv))

    page = make_page(set_value_step)

    # prepare for run
    tree: DualTree = page.full_tree
    tree.mode = "run"
    tree.switch_mode("run")
    qtbot.wait_signal(tree.mode_switch_finished)

    assert isinstance(tree.prepared_file, PreparedProcedureFile)

    # show run mode widget
    tree.show_page_for_data(tree.current_item, mode=tree.mode)
    run_widget = tree.current_widget
    run_check = run_widget.run_check
    assert isinstance(run_check, RunCheck)
    assert run_check.run_button.isHidden() is False

    assert isinstance(tree.prepared_file.root.steps[0].prepared_actions[0].signal,
                      SynSignal)

    qtbot.mouseClick(run_check.run_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not run_check.abort_button.isHidden())

    if click_abort:
        qtbot.mouseClick(run_check.abort_button, Qt.LeftButton)

    qtbot.waitUntil(lambda: not run_check.run_button.isHidden())

    # check the results
    assert tree.running_task and tree.running_task.done()
    if click_abort:
        assert tree.prepared_file.root.result.severity != Severity.success
    else:
        assert tree.prepared_file.root.result.severity == Severity.success

    # reset signal cache
    cache._signal_cache = cache._SignalCache(EpicsSignal)
