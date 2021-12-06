from __future__ import annotations

import pathlib
import sys
from typing import (Dict, Generator, List, Optional, Sequence, Type, TypeVar,
                    Union)

import pydm
import pydm.display
import typhos
import typhos.cli
import typhos.display
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Qt

from .procedure import (DescriptionStep, DisplayOptions, ProcedureGroup,
                        ProcedureStep, PydmDisplayStep, TyphosDisplayStep)

# TODO:  CodeStep, PlanStep, ConfigurationCheckStep,

T = TypeVar("T")


def _create_vbox_layout(
    widget: Optional[QtWidgets.QWidget] = None, alignment: Qt.Alignment = Qt.AlignTop
) -> QtWidgets.QVBoxLayout:
    if widget is not None:
        layout = QtWidgets.QVBoxLayout(widget)
    else:
        layout = QtWidgets.QVBoxLayout()
    layout.setAlignment(alignment)
    return layout


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
        layout = _create_vbox_layout(self)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")

        if options.embed:
            widget = pydm.display.load_file(
                file=str(pathlib.Path(display).resolve()),
                macros=options.macros,
                target=-1,  # TODO: don't show the widget, please...
            )
            layout.addWidget(widget)
        self.updateGeometry()


class TyphosDisplayStepWidget(StepWidgetBase, QtWidgets.QFrame):
    def _setup_ui(self, devices: Dict[str, DisplayOptions]):
        layout = _create_vbox_layout(self)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")

        for device_name, display_options in devices.items():
            display = typhos.display.TyphosDeviceDisplay(scrollable=False)
            display.display_type = display_options.template
            device, = typhos.cli.create_devices([device_name])
            display.setObjectName("typhos_display")
            display.add_device(device)
            layout.addWidget(display)
            display.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.MinimumExpanding,
            )

        self.updateGeometry()


class DescriptionStepWidget(StepWidgetBase, QtWidgets.QFrame):
    def _setup_ui(self):
        layout = _create_vbox_layout(self)
        _add_label(layout, self.title, object_name="step_title")
        _add_label(layout, self.description, object_name="step_description")


class ExpandableFrame(QtWidgets.QFrame):
    def __init__(self, title: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self._content_layout = None
        self.toggle_button = QtWidgets.QToolButton(
            text=title, checkable=True, checked=False
        )
        self.toggle_button.setStyleSheet("QToolButton { border: none; }")
        self.toggle_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonTextBesideIcon
        )
        self.toggle_button.setArrowType(QtCore.Qt.RightArrow)
        self.toggle_button.toggled.connect(self.on_toggle)

        layout = _create_vbox_layout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        self._size_hint = self.sizeHint()

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        self.layout().addWidget(widget)
        widget.setVisible(self.expanded)

    @property
    def expanded(self) -> bool:
        """Is the expandable frame expanded / not collapsed?"""
        return self.toggle_button.isChecked()

    @property
    def layout_widgets(self) -> Generator[QtWidgets.QWidget, None, None]:
        for idx in range(self.layout().count()):
            item = self.layout().itemAt(idx)
            widget = item.widget()
            if widget is not None and widget is not self.toggle_button:
                yield widget

    @QtCore.Slot()
    def on_toggle(self):
        expanded = self.expanded
        self.toggle_button.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )

        widgets = list(self.layout_widgets)
        for widget in widgets:
            widget.setVisible(expanded)

        # min_height = self._size_hint.height()
        # if expanded and widgets:
        #     min_height += sum(w.sizeHint().height() for w in widgets)

        # self.setMinimumHeight(min_height)
        self.updateGeometry()


class ProcedureGroupWidget(StepWidgetBase, QtWidgets.QFrame):
    _steps: List[Union[ProcedureStep, ProcedureGroup]]
    _step_widgets: List[Union[ProcedureGroupWidget, StepWidgetBase]]

    def _setup_ui(self, steps: Sequence[Union[ProcedureStep, ProcedureGroup]]):
        self._steps = list(steps)
        self._step_widgets = list(
            _settings_to_widget_class[type(step)].from_settings(step)
            for step in self._steps
        )

        layout = layout = _create_vbox_layout(self)
        _add_label(layout, self.title, object_name="group_title")
        _add_label(layout, self.description, object_name="group_description")

        if not self._step_widgets:
            layout.addWidget(QtWidgets.QLabel("(No steps defined.)"))
            return

        frame = QtWidgets.QFrame()
        frame.setObjectName("group_step_frame")
        content_layout = _create_vbox_layout(frame)

        self._expandable_frame = ExpandableFrame()
        layout.addWidget(self._expandable_frame)
        self._expandable_frame.add_widget(frame)

        for widget in self._step_widgets:
            content_layout.addWidget(widget)
            widget.setMinimumSize(widget.minimumSizeHint())
            widget.setSizePolicy(
                QtWidgets.QSizePolicy.MinimumExpanding,
                QtWidgets.QSizePolicy.Minimum,
            )
        content_layout.addWidget(QtWidgets.QLabel("(End of steps)"))


_settings_to_widget_class = {
    DescriptionStep: DescriptionStepWidget,
    PydmDisplayStep: PydmDisplayStepWidget,
    TyphosDisplayStep: TyphosDisplayStepWidget,
    ProcedureGroup: ProcedureGroupWidget,
}


class AtefProcedure(QtWidgets.QFrame):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        layout = _create_vbox_layout(self)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setAlignment(Qt.AlignTop)
        self._scroll_area.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._scroll_area.setFrameStyle(QtWidgets.QFrame.NoFrame)
        self._scroll_area.setObjectName('scroll_area')
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self._scroll_area)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_frame = QtWidgets.QFrame()
        self._scroll_frame.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout = _create_vbox_layout(self._scroll_frame)
        self._scroll_area.setWidget(self._scroll_frame)

    def add_widget(self, widget: QtWidgets.QWidget):
        self._scroll_layout.addWidget(widget)


if __name__ == "__main__":
    group = ProcedureGroup(
        title="Top-level procedure",
        description="Procedure notes",
        steps=[
            DescriptionStep(
                title="Introduction",
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
                title="Display 1",
                description="Configure device before beginning",
                devices={
                    "at1k4": DisplayOptions(),
                }
            ),
            PydmDisplayStep(
                title="Display 2",
                description="PyDM display",
                display=pathlib.Path("pydm.ui"),
                options=DisplayOptions(),
            ),
            ProcedureGroup(
                title="Embedded group procedure",
                description="Group procedure notes",
                steps=[
                    DescriptionStep(
                        title="Introduction",
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

        QFrame#group_step_frame {
            border-radius: 2px;
            border-left: 2px solid darkgray;
        }

        #typhos_display {
            border: 2px dotted black;
        }
        """
    )

    # window = DescriptionStepWidget.from_settings(group.steps[0])
    group = ProcedureGroupWidget.from_settings(group)
    group._expandable_frame.toggle_button.setChecked(True)
    procedure = AtefProcedure()
    procedure.add_widget(group)
    procedure.show()
    sys.exit(app.exec_())
