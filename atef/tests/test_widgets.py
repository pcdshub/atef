import logging
import pathlib

import apischema
import pytest
import yaml
from pytestqt.qtbot import QtBot

from ..procedure import (DescriptionStep, DisplayOptions, ProcedureGroup,
                         ProcedureStep, PydmDisplayStep, TyphosDisplayStep)
from ..widgets import procedure_step_to_widget
from . import qt_utils

logger = logging.getLogger(__name__)


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


@parametrized_groups
def test_create_widget(request: pytest.FixtureRequest, group: ProcedureStep):
    widget = procedure_step_to_widget(group)
    widget.show()
    qt_utils.save_widget_screenshot(widget, prefix=request.node.name)
    widget.close()
    widget.deleteLater()


def test_config_window_basic(qtbot: QtBot):
    """
    Pass if the config gui can open
    """
    from ..widgets.config.window import Window
    window = Window()
    qtbot.add_widget(window)


def test_config_window_save_load(qtbot: QtBot, tmp_path: pathlib.Path):
    """
    Pass if the config gui can open a file and save the same file back
    """
    from ..widgets.config.window import Window
    window = Window(show_welcome=False)
    qtbot.add_widget(window)
    test_configs = pathlib.Path(__file__).parent / 'configs'
    for filename in ('lfe.json', 'all_fields.json'):
        config_path = test_configs / filename
        source = str(config_path)
        dest = str(tmp_path / filename)
        window.open_file(filename=source)
        window.save_as(filename=dest)
        with open(source) as fd:
            source_lines = fd.readlines()
        with open(dest) as fd:
            dest_lines = fd.readlines()
        assert source_lines == dest_lines
