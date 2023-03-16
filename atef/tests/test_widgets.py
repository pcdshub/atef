import logging
import os
import pathlib

import apischema
import pytest
import yaml
from pytestqt.qtbot import QtBot
from qtpy import QtWidgets

from atef.config import ConfigurationFile
from atef.widgets.config.page import AtefItem
from atef.widgets.config.run_base import create_tree_items
from atef.widgets.config.utils import TreeItem
from atef.widgets.config.window import EditTree

from ..procedure import (DescriptionStep, DisplayOptions, ProcedureGroup,
                         PydmDisplayStep, TyphosDisplayStep)

logger = logging.getLogger(__name__)


@pytest.fixture
def test_configs() -> list[pathlib.Path]:
    filenames = ['lfe.json', 'all_fields.json', 'active_test.json']
    test_config_path = pathlib.Path(__file__).parent / 'configs'
    config_paths = [test_config_path / fn for fn in filenames]
    return config_paths


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
    from ..widgets.config.window import Window
    window = Window(show_welcome=False)
    qtbot.addWidget(window)


def test_config_window_save_load(qtbot: QtBot, tmp_path: pathlib.Path):
    """
    Pass if the config gui can open a file and save the same file back
    """
    from ..widgets.config.window import Window
    window = Window(show_welcome=False)
    qtbot.addWidget(window)
    test_configs = pathlib.Path(__file__).parent / 'configs'
    for filename in ('lfe.json', 'all_fields.json', 'active_test.json'):
        config_path = test_configs / filename
        source = str(config_path)
        dest = str(tmp_path / filename)
        window.open_file(filename=source)
        window.save_as(filename=dest)
        with open(source, 'r') as fd:
            source_lines = fd.readlines()
        with open(dest, 'r') as fd:
            dest_lines = fd.readlines()
        assert source_lines == dest_lines


def test_edit_run_toggle(qtbot: QtBot, test_configs: list[os.PathLike]):
    """ Smoke test run-mode for all sample configs """
    from ..widgets.config.window import Window
    window = Window(show_welcome=False)
    qtbot.addWidget(window)
    for idx, filename in enumerate(test_configs):
        window.open_file(filename=str(filename))
        toggle = window.tab_widget.widget(idx).toggle
        toggle.setChecked(True)
        toggle.setChecked(False)


def walk_tree(item: TreeItem):
    yield item
    for idx in range(item.childCount()):
        yield from walk_tree(item.child(idx))


def test_create_tree(qtbot: QtBot, test_configs: list[pathlib.Path]):
    """ compare tree structure generated by EditTree and create_tree_items """
    for filename in test_configs:
        if ('all_fields' in str(filename)) or ('active_test' in str(filename)):
            # skip all_fields.json, AnyComparison doesn't walk properly
            # create_tree_items only for passive checkouts
            continue
        print(filename)
        # construct tree: create_tree_items
        cfg_file = ConfigurationFile.from_filename(filename)
        root_item = TreeItem(data=cfg_file.root)
        create_tree_items(cfg_file.root, parent=root_item)

        # construct tree: EditTree
        edit_tree = EditTree(config_file=cfg_file)
        qtbot.addWidget(edit_tree)

        # compare trees
        edit_tree_iter = QtWidgets.QTreeWidgetItemIterator(edit_tree.tree_widget)
        tree_item_iter = walk_tree(root_item)
        ct = 0
        while edit_tree_iter.value():
            et_item: AtefItem = edit_tree_iter.value()
            et_data = et_item.widget.data
            ti_item = next(tree_item_iter)
            ti_data = ti_item._data

            assert ti_data == et_data
            edit_tree_iter += 1
            ct += 1
