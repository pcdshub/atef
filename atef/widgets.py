from __future__ import annotations

import dataclasses
import datetime
import pathlib
import sys
from dataclasses import field
from typing import Any, Dict, List, Optional, Sequence, Type, TypeVar, Union

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt

from .utils import as_tagged_union

T = TypeVar("T")


@as_tagged_union
@dataclasses.dataclass
class ProcedureStep:
    title: Optional[str]
    description: str


@dataclasses.dataclass
class DescriptionStep(ProcedureStep):
    ...


@dataclasses.dataclass
class CodeStep(ProcedureStep):
    source_code: str
    arguments: Dict[Any, Any]


@dataclasses.dataclass
class PlanStep(ProcedureStep):
    plan: str
    arguments: Dict[Any, Any]
    fixed_arguments: Optional[Sequence[str]]


@dataclasses.dataclass
class DisplayOptions:
    macros: Dict[str, str] = field(default_factory=dict)
    template: str = "embedded_screen"
    embed: bool = True


@dataclasses.dataclass
class DeviceConfiguration:
    archiver_timestamp: Optional[datetime.datetime]
    values: Dict[str, Any]


@dataclasses.dataclass
class ConfigurationCheckStep(ProcedureStep):
    devices: Dict[str, DeviceConfiguration]


@dataclasses.dataclass
class TyphosDisplayStep(ProcedureStep):
    devices: Dict[str, DisplayOptions]


@dataclasses.dataclass
class PydmDisplayStep(ProcedureStep):
    display: pathlib.Path
    options: DisplayOptions


@dataclasses.dataclass
class ProcedureGroup:
    title: Optional[str]
    description: str
    steps: Sequence[Union[ProcedureStep, ProcedureGroup]]


class StepWidgetBase:
    def __init__(
        self,
        title: Optional[str] = None,
        description: str = "",
        *,
        parent: Optional[QtWidgets.QWidget] = None,
        **kwargs
    ):
        super().__init__(parent=parent)
        self._title = title
        self._description = description
        self.setWindowTitle(title or "Step")
        self.setObjectName(self.windowTitle().replace(" ", "_"))
        self._setup_ui(**kwargs)

    def _setup_ui(self, **kwargs):
        ...

    @QtCore.Property(str, designable=True)
    def title(self) -> str:
        """The step title."""
        return self._title

    @title.setter
    def title(self, value: str):
        self._title = str(value)

    @QtCore.Property(str, designable=True)
    def description(self) -> str:
        """The step description, which may include rich text."""
        return self._description

    @description.setter
    def description(self, value: str):
        self._description = str(value)

    @classmethod
    def from_settings(cls: Type[T], settings: ProcedureStep, **kwargs) -> T:
        return cls(**vars(settings), **kwargs)


def _add_label(
    layout: QtWidgets.QLayout, text: Optional[str], object_name: Optional[str] = None
) -> Optional[QtWidgets.QLabel]:
    """Create a QLabel with the given text and object name."""
    label = QtWidgets.QLabel(text)
    layout.addWidget(label)
    if object_name:
        label.setObjectName(object_name)
    return label


class PydmDisplayStepWidget(StepWidgetBase, QtWidgets.QFrame):
    def _setup_ui(self, display: pathlib.Path, options: DisplayOptions):
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")


class TyphosDisplayStepWidget(StepWidgetBase, QtWidgets.QFrame):
    def _setup_ui(self, devices: Dict[str, DisplayOptions]):
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")


class DescriptionStepWidget(StepWidgetBase, QtWidgets.QFrame):
    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        print("setup", self, self.title, self.description)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")


_settings_to_widget_class = {
    DescriptionStep: DescriptionStepWidget,
    PydmDisplayStep: PydmDisplayStepWidget,
    TyphosDisplayStep: TyphosDisplayStepWidget,
}


class ProcedureGroupWidget(StepWidgetBase, QtWidgets.QFrame):
    _steps: List[Union[ProcedureStep, ProcedureGroup]]
    _step_widgets: List[Union[ProcedureGroupWidget, StepWidgetBase]]

    def _setup_ui(self, steps: Sequence[Union[ProcedureStep, ProcedureGroup]]):
        self._steps = list(steps)
        self._step_widgets = list(
            _settings_to_widget_class[type(step)].from_settings(step)
            for step in self._steps
        )

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        _add_label(layout, self.title, object_name="group_title")
        _add_label(layout, self.description, object_name="group_description")

        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setAlignment(Qt.AlignTop)
        self._scroll_area.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._scroll_area.setFrameStyle(QtWidgets.QFrame.NoFrame)
        self._scroll_area.setObjectName('scroll_area')
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self._scroll_area)

        scroll_layout = QtWidgets.QVBoxLayout()
        self._scroll_frame = QtWidgets.QFrame()
        self._scroll_area.setWidget(self._scroll_frame)
        self._scroll_frame.setLayout(scroll_layout)

        if not self._step_widgets:
            scroll_layout.addWidget(QtWidgets.QLabel("(No steps defined.)"))
            return

        for widget in self._step_widgets:
            scroll_layout.addWidget(widget)
            widget.setMinimumSize(widget.minimumSizeHint())
            widget.setSizePolicy(
                QtWidgets.QSizePolicy.MinimumExpanding,
                QtWidgets.QSizePolicy.Minimum,
            )
            print(widget.children())
        scroll_layout.addWidget(QtWidgets.QLabel("(End of steps)"))


if __name__ == "__main__":
    print("test")
    group = ProcedureGroup(
        title="Top-level procedure",
        description="Procedure notes",
        steps=[
            DescriptionStep(
                title="Introduction",
                description="Introductory text",
            ),
            TyphosDisplayStep(
                title="Display 1",
                description="Configure device before beginning",
                devices={
                    "device_name": DisplayOptions(),
                }
            ),
            PydmDisplayStep(
                title="Display 2",
                description="PyDM display",
                display="/path/to/display.ui",
                options=DisplayOptions(),
            ),
        ]
    )
    print(group)

    import apischema
    import yaml
    serialized = apischema.serialize(group)
    print(serialized)
    print(apischema.deserialize(ProcedureGroup, serialized))
    print(yaml.dump(serialized))

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(
        """
        QLabel#step_title {
            font-weight: bold;
        }

        QLabel#step_description {
            font-weight: normal;
        }

        QLabel#group_title {
            font-weight: bold;
        }

        QLabel#group_description {
            font-weight: normal;
        }
        """
    )

    # window = DescriptionStepWidget.from_settings(group.steps[0])
    window = ProcedureGroupWidget.from_settings(group)
    # window = Window()
    window.show()
    sys.exit(app.exec_())
