import logging
import os
import pathlib

import apischema
import happi
import pytest
import yaml
from pytestqt.qtbot import QtBot
from qtpy import QtCore, QtWidgets

from atef.widgets.happi import HappiDeviceComponentWidget
from atef.widgets.ophyd import OphydDeviceTableWidget

from ..procedure import (DescriptionStep, DisplayOptions, ProcedureGroup,
                         PydmDisplayStep, TyphosDisplayStep)
from ..widgets.config.window import Window

logger = logging.getLogger(__name__)


@pytest.fixture
def test_configs() -> list[pathlib.Path]:
    filenames = ['lfe.json', 'all_fields.json', 'active_test.json']
    test_config_path = pathlib.Path(__file__).parent / 'configs'
    config_paths = [test_config_path / fn for fn in filenames]
    return config_paths


@pytest.fixture
def config(request, test_configs: list[pathlib.Path]):
    i = request.param
    return test_configs[i]


parametrized_groups = pytest.mark.parametrize(
    "group",
    [
        pytest.param(
            DescriptionStep(
                name="Introduction",
                description=(
                    "Introductory <strong>text</strong> can contain HTML "
                    "<ol>"
                    "<li>ListItem 1</li>"
                    "<li>ListItem 2</li>"
                    "<li>ListItem 3</li>"
                    "</ol>"
                ),
            ),
            id="description_step",
        ),
        pytest.param(
            TyphosDisplayStep(
                name="Display 1",
                description="Configure device before beginning",
                devices={
                    "at1k4": DisplayOptions(),
                }
            ),
            id="typhos_step",
            # TODO: will fail on CI until we get a valid happi config there
            marks=pytest.mark.xfail,
        ),
        pytest.param(
            PydmDisplayStep(
                name="Display 2",
                description="PyDM display",
                display=pathlib.Path("pydm.ui"),
                options=DisplayOptions(),
            ),
            id="pydm_display_step",
        ),
        pytest.param(
            ProcedureGroup(
                name="Simple group procedure",
                description="Group procedure notes",
                steps=[
                    DescriptionStep(
                        name="Introduction",
                        description=(
                            "Introductory <strong>text</strong> can contain HTML "
                            "<ol>"
                            "<li>ListItem 4</li>"
                            "<li>ListItem 5</li>"
                            "<li>ListItem 6</li>"
                            "</ol>"
                        ),
                    ),
                ]
            ),
            id="simple_group",
        ),
        pytest.param(
            ProcedureGroup(
                name="Top-level procedure",
                description="Procedure notes",
                steps=[
                    ProcedureGroup(
                        name="Embedded group procedure",
                        description="Group procedure notes",
                        steps=[
                            DescriptionStep(
                                name="Introduction",
                                description=(
                                    "Introductory <strong>text</strong> can contain HTML "
                                    "<ol>"
                                    "<li>ListItem 4</li>"
                                    "<li>ListItem 5</li>"
                                    "<li>ListItem 6</li>"
                                    "</ol>"
                                ),
                            ),
                        ]
                    ),
                ]
            ),
            id="nested_group",
        ),
        pytest.param(
            ProcedureGroup(
                name="Top-level procedure",
                description="Procedure notes",
                steps=[
                    DescriptionStep(
                        name="Introduction",
                        description=(
                            "Introductory <strong>text</strong> can contain HTML "
                            "<ol>"
                            "<li>ListItem 1</li>"
                            "<li>ListItem 2</li>"
                            "<li>ListItem 3</li>"
                            "</ol>"
                        ),
                    ),
                    TyphosDisplayStep(
                        name="Display 1",
                        description="Configure device before beginning",
                        devices={
                            "at1k4": DisplayOptions(),
                        }
                    ),
                    PydmDisplayStep(
                        name="Display 2",
                        description="PyDM display",
                        display=pathlib.Path("pydm.ui"),
                        options=DisplayOptions(),
                    ),
                    ProcedureGroup(
                        name="Embedded group procedure",
                        description="Group procedure notes",
                        steps=[
                            DescriptionStep(
                                name="Introduction",
                                description=(
                                    "Introductory <strong>text</strong> can contain HTML "
                                    "<ol>"
                                    "<li>ListItem 4</li>"
                                    "<li>ListItem 5</li>"
                                    "<li>ListItem 6</li>"
                                    "</ol>"
                                ),
                            ),
                        ]
                    ),
                ]
            ),
            id="complex_group",
        ),
    ]
)


@parametrized_groups
def test_serialization(group):
    print("group is", group)
    serialized = apischema.serialize(group)
    print(serialized)
    print(apischema.deserialize(type(group), serialized))
    print(yaml.dump(serialized))


# Test no longer applicable with change to ProcedureStep fields.
# To be deleted when remaining vestigial portions of
# atef.widgets.config.config.data_active are removed
# @parametrized_groups
# def test_create_widget(request: pytest.FixtureRequest, group: ProcedureStep):
#     widget = procedure_step_to_widget(group)
#     widget.show()
#     qt_utils.save_widget_screenshot(widget, prefix=request.node.name)
#     widget.close()
#     widget.deleteLater()


def test_config_window_basic(qtbot: QtBot):
    """
    Pass if the config gui can open
    """
    window = Window(show_welcome=False)
    qtbot.addWidget(window)


# @pytest.mark.skip()
def test_config_window_save_load(qtbot: QtBot, tmp_path: pathlib.Path,
                                 all_config_path: os.PathLike):
    """
    Pass if the config gui can open a file and save the same file back
    """
    window = Window(show_welcome=False)
    config = pathlib.Path(all_config_path)
    filename = config.name
    source = str(config)
    dest = str(tmp_path / filename)
    window.open_file(filename=source)
    window.save_as(filename=dest)
    with open(source, 'r') as fd:
        source_lines = fd.readlines()
    with open(dest, 'r') as fd:
        dest_lines = fd.readlines()
    assert source_lines == dest_lines
    qtbot.addWidget(window)


@pytest.mark.parametrize('config', [0, 1, 2], indirect=True)
def test_edit_run_toggle(qtbot: QtBot, config: os.PathLike, monkeypatch):
    """
    Pass if the RunTree can be created from an EditTree
    This can hang if a checkout cannot be prepared, so patch to ensure
    the gui continues.

    For now, this is nearly a no-op, since changing to run-mode does not create
    widgets.  But it can be used
    """
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda *a, **k: True)
    window = Window(show_welcome=False)
    window.open_file(filename=str(config))
    print(config)
    qtbot.addWidget(window)
    toggle = window.tab_widget.widget(0).toggle
    toggle.setChecked(True)
    qtbot.waitSignal(window.tab_widget.currentWidget().mode_switch_finished)
    assert window.tab_widget.widget(0).mode == 'run'


@pytest.mark.parametrize('config', [0, 1, 2], indirect=True)
def test_open_all_pages(qtbot: QtBot, config: os.PathLike):
    """Pass if all pages in the test configs can be opened by selecting the treeview"""
    # Issues from enum signals timing out after widget has been gc'd, if cache size
    # drops widget.  For now just make cache arbitrarily large.
    window = Window(show_welcome=False, cache_size=100)
    window.open_file(filename=str(config))

    curr_tree = window.get_current_tree()
    tree_view = curr_tree.tree_view
    curr_index = tree_view.selectionModel().currentIndex()
    curr_page = curr_tree.current_widget

    # Assuming we have less than 100 items in test configs
    for _ in range(100):
        new_index = tree_view.indexBelow(curr_index)
        print(curr_tree.model.data(new_index, 0))
        if curr_tree.model.data(new_index, 0) is None:
            break

        tree_view.setCurrentIndex(new_index)
        qtbot.waitUntil(lambda: curr_page != curr_tree.current_widget)
        curr_page = curr_tree.current_widget
        curr_index = new_index

    # if there are epics calls they may still be going on at test teardown?...
    qtbot.addWidget(window)


@pytest.mark.parametrize('config', [0, 1, 2], indirect=True)
def test_copypaste(qtbot: QtBot, config: os.PathLike):
    """
    Pass if we can copy and paste TreeItems and correctly update
    the affect configurations and procedures.
    """
    window = Window(show_welcome=False, cache_size=100)
    window.open_file(filename=str(config))

    # context menu shouldn't block
    window.context_menu(QtCore.QPoint(0, 0))

    curr_tree = window.get_current_tree()
    tree_view = curr_tree.tree_view
    curr_index = tree_view.selectionModel().currentIndex()
    curr_page = curr_tree.current_widget

    # Assuming we have less than 100 items in test configs
    for _ in range(100):
        new_index = tree_view.indexBelow(curr_index)
        print(curr_tree.model.data(new_index, 0))
        if curr_tree.model.data(new_index, 0) is None:
            break
        tree_view.setCurrentIndex(new_index)
        qtbot.waitUntil(lambda: curr_page != curr_tree.current_widget)
        curr_page = curr_tree.current_widget
        window.copy()
        assert window.clipboard is not None
        try:
            window.paste()
        except RuntimeError as e:
            print(e)
        curr_index = new_index

    qtbot.addWidget(window)


def test_open_happi_viewer(qtbot: QtBot, happi_client: happi.Client):
    """
    Pass if HappiDeviceComponentWidget can be created and refreshed without error
    """
    happi_widget = HappiDeviceComponentWidget(client=happi_client)
    search_widget = happi_widget.item_search_widget

    def search_finished():
        # wait until HappiSearchWidget.refresh_happi's update_gui callback finished
        # refresh button gets disabled until this happens
        assert search_widget.button_refresh.isEnabled()

    qtbot.mouseClick(search_widget.button_refresh, QtCore.Qt.LeftButton)
    qtbot.wait_until(search_finished)
    qtbot.addWidget(happi_widget)


def test_device_table(qtbot: QtBot, happi_client: happi.Client):
    dev = happi_client.search()[0].get()
    otable = OphydDeviceTableWidget(device=dev)
    otable.button_update_data.clicked.emit()
    qtbot.wait_until(lambda: otable.device_table_view.current_model.rowCount() != 0)
    assert otable.device_table_view.current_model.rowCount() == len(dev.component_names)

    # set new device
    new_dev = happi_client.search()[1].get()
    otable.device = new_dev
    assert otable.windowTitle() == new_dev.name
    qtbot.addWidget(otable)
