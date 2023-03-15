"""
Widgets for manipulating active checkout data (edit-mode)

Widgets here will replace the RunStepPage.run_widget_placeholder widget, and should
subclass DataWidget and DesignerDisplay

Contains several widgets carried over from before active checkout gui development
started, which may not appear in the ``atef config`` GUI.
These will be cleaned... eventually
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import pprint
from typing import (Dict, Generator, List, Optional, Sequence, Type, TypeVar,
                    Union)

import pydm
import pydm.display
import typhos
import typhos.cli
import typhos.display
from qtpy import QtCore, QtWidgets
from qtpy.QtCore import Qt

from atef.check import Result
from atef.config import ConfigurationFile
from atef.widgets.config.data_base import DataWidget
from atef.widgets.config.run_base import create_tree_items
from atef.widgets.config.utils import ConfigTreeModel, TreeItem
from atef.widgets.core import DesignerDisplay

from ...procedure import (DescriptionStep, DisplayOptions, PassiveStep,
                          PlanOptions, PlanStep, ProcedureGroup, ProcedureStep,
                          PydmDisplayStep, TyphosDisplayStep,
                          incomplete_result)

# TODO:  CodeStep, ConfigurationCheckStep,

T = TypeVar("T")

logger = logging.getLogger(__name__)


DEFAULT_STYLESHEET = """
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


class GeneralProcedureWidget(DesignerDisplay, DataWidget):
    """
    Handle fields common to all ProcedureStep dataclasses
    Currently simply a choice of verify-mode with no actual functionality,
    but will likely be expanded
    """
    filename = 'general_procedure_widget.ui'

    verify_combo: QtWidgets.QComboBox
    step_success_combo: QtWidgets.QComboBox

    bool_choices = ('False', 'True')
    verify_combo_items = bool_choices
    step_success_combo_items = bool_choices

    def __init__(self, data: ProcedureStep, **kwargs):
        super().__init__(data=data, **kwargs)
        for text in self.verify_combo_items:
            self.verify_combo.addItem(text)
        for text in self.step_success_combo_items:
            self.step_success_combo.addItem(text)

        self.verify_combo.setCurrentIndex(
            int(self.bridge.verify_required.get())
        )
        self.step_success_combo.setCurrentIndex(
            int(self.bridge.step_success_required.get())
        )

        self.verify_combo.currentIndexChanged.connect(
            self.new_verify_combo
        )
        self.step_success_combo.currentIndexChanged.connect(
            self.new_step_success_combo
        )

    def new_step_success_combo(self, index: int) -> None:
        """
        Slot to handle user input in the "Step Success Required" combo box.
        Uses current bridge to mutate the stored dataclass

        Parameters
        ----------
        index : int
            The index of the combo box.
        """
        self.bridge.step_success_required.put(bool(index))

    def new_verify_combo(self, index: int) -> None:
        """
        Slot to handle user input in the "Verify Required" combo box.
        Uses current bridge to mutate the stored dataclass

        Parameters
        ----------
        index : int
            The index of the combo box.
        """
        self.bridge.verify_required.put(bool(index))


def _create_vbox_layout(
    widget: Optional[QtWidgets.QWidget] = None, alignment: Qt.Alignment = Qt.AlignTop
) -> QtWidgets.QVBoxLayout:
    if widget is not None:
        layout = QtWidgets.QVBoxLayout(widget)
    else:
        layout = QtWidgets.QVBoxLayout()
    layout.setAlignment(alignment)
    return layout


class StepWidgetBase(QtWidgets.QWidget):
    """
    Base class for all procedure step widgets.
    """

    title_widget: Optional[QtWidgets.QLabel]
    description_widget: Optional[QtWidgets.QLabel]

    def __init__(
        self,
        name: Optional[str] = None,
        description: str = "",
        verify: bool = False,
        result: Result = incomplete_result(),
        *,
        parent: Optional[QtWidgets.QWidget] = None,
        **kwargs
    ):
        super().__init__(parent=parent)
        self._title = name
        self._description = description
        self.title_widget = None
        self.description_widget = None
        self.setWindowTitle(name or "Step")
        self.setObjectName(self.windowTitle().replace(" ", "_"))
        self._setup_ui(**kwargs)
        self.updateGeometry()

    def _setup_ui(self, **_):
        raise NotImplementedError(f"To be implemented by subclass: {type(self)}")

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
    """
    Create a QLabel with the given text and object name in the given layout.

    Configures the label to open external links.

    Parameters
    ----------
    layout : `QtWidgets.QLayout`
        The layout to add the label to.

    text : str, optional
        The initial text to set.

    object_name : str, optional
        The object name to set.

    Returns
    -------
    `QtWidgets.QLabel`
    """
    text = text or ""
    label = QtWidgets.QLabel(text)
    label.setOpenExternalLinks(True)
    layout.addWidget(label)
    label.setObjectName(str(object_name or text.replace(" ", "_")[:20] or "label"))
    return label


class PydmDisplayStepWidget(StepWidgetBase, QtWidgets.QFrame):
    """A procedure step which a opens or embeds a PyDM display."""

    display_path: pathlib.Path
    display_widget: Optional[QtWidgets.QWidget]
    toggle_button: Optional[QtWidgets.QToolButton]

    def _setup_ui(self, display: pathlib.Path, options: DisplayOptions):
        layout = _create_vbox_layout(self)
        self.title_widget = _add_label(layout, self.title, object_name="step_title")
        self.description_widget = _add_label(
            layout, self.description, object_name="step_description"
        )

        self.toggle_button = None
        self.display_path = pathlib.Path(display).resolve()
        try:
            self.display_widget = pydm.display.load_file(
                file=str(self.display_path),
                macros=options.macros,
                target=-1,  # TODO: don't show the widget, please...
            )
        except Exception as ex:
            logger.exception("Failed to load PyDM widget: %s", self.display_path)
            _add_label(
                layout,
                text=(
                    f"Error loading PyDM display: {self.display_path}<br />\n"
                    f"{ex.__class__.__name__}: {ex}"
                ),
            )
            return

        if options.embed:
            layout.addWidget(self.display_widget)
        else:
            self.toggle_button = QtWidgets.QToolButton(
                text=f"Open {self.display_path.name}..."
            )
            layout.addWidget(self.toggle_button)
            self.toggle_button.setCheckable(True)
            self.toggle_button.setChecked(False)

            def show_display():
                if self.toggle_button.isChecked():
                    self.display_widget.show()
                else:
                    self.display_widget.hide()

            self.toggle_button.toggled.connect(show_display)


class PlanStepWidget(StepWidgetBase, QtWidgets.QFrame):
    """A procedure step which allows one or more bluesky plans to be run."""

    def _setup_ui(self, plans: Sequence[PlanOptions]):
        layout = _create_vbox_layout(self)
        self.title_widget = _add_label(layout, self.title, object_name="step_title")
        self.description_widget = _add_label(
            layout, self.description, object_name="step_description"
        )
        from ...re_widgets import Model, QtRePlanEditor
        model = Model()
        model.run_engine.clear_connection_status()
        model.run_engine.manager_connecting_ops()
        # editor = QtRunEngineManager(model.run_engine)
        editor = QtRePlanEditor(model.run_engine)
        layout.addWidget(editor)


class TyphosDisplayStepWidget(StepWidgetBase, QtWidgets.QFrame):
    """A procedure step which opens one or more typhos displays."""

    def _setup_ui(self, devices: Dict[str, DisplayOptions]):
        layout = _create_vbox_layout(self)
        self.title_widget = _add_label(layout, self.title, object_name="step_title")
        self.description_widget = _add_label(
            layout, self.description, object_name="step_description"
        )

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


class DescriptionStepWidget(StepWidgetBase, QtWidgets.QFrame):
    """A simple title or descriptive step in the procedure."""

    def _setup_ui(self):
        layout = _create_vbox_layout(self)
        self.title_widget = _add_label(layout, self.title, object_name="step_title")
        self.description_widget = _add_label(
            layout, self.description, object_name="step_description"
        )


class ExpandableFrame(QtWidgets.QFrame):
    """
    A `QtWidgets.QFrame` that can be toggled with a mouse click.

    Contains a QVBoxLayout layout which can have one or more user-provided
    widgets.

    Parameters
    ----------
    text : str
        The title of the frame, shown on the toolbutton.

    parent : QtWidgets.QWidget, optional
        The parent widget.
    """

    toggle_button: QtWidgets.QToolButton
    _button_text: str
    _size_hint: QtCore.QSize

    def __init__(self, text: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self._button_text = text

        self.toggle_button = QtWidgets.QToolButton(text=text)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        self.toggle_button.setStyleSheet("QToolButton { border: none; }")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.toggled.connect(self.on_toggle)

        layout = _create_vbox_layout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        self._size_hint = self.sizeHint()

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        """Add a widget to the content layout."""
        self.layout().addWidget(widget)
        widget.setVisible(self.expanded)

    @property
    def expanded(self) -> bool:
        """Is the expandable frame expanded / not collapsed?"""
        return self.toggle_button.isChecked()

    @property
    def layout_widgets(self) -> Generator[QtWidgets.QWidget, None, None]:
        """Find all user-provided widgets in the content layout."""
        for idx in range(self.layout().count()):
            item = self.layout().itemAt(idx)
            widget = item.widget()
            if widget is not None and widget is not self.toggle_button:
                yield widget

    @QtCore.Slot()
    def on_toggle(self):
        """Toggle the content display."""
        expanded = self.expanded
        self.toggle_button.setText("" if expanded else self._button_text)
        self.toggle_button.setArrowType(
            Qt.DownArrow if expanded else Qt.RightArrow
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
    """A group of procedure steps (or nested groups)."""

    _steps: List[Union[ProcedureStep, ProcedureGroup]]
    _step_widgets: List[Union[ProcedureGroupWidget, StepWidgetBase]]

    def _setup_ui(self, steps: Sequence[Union[ProcedureStep, ProcedureGroup]]):
        self._steps = list(steps)
        self._step_widgets = []

        for step in self._steps:
            try:
                widget = procedure_step_to_widget(step)
            except Exception as ex:
                widget = DescriptionStepWidget(
                    name=step.name,
                    description=(
                        f"atef error: failed to load step {step.name!r} "
                        f"({type(step).__name__}) due to:<br/>\n"
                        f"<strong>{ex.__class__.__name__}</strong>: {ex}"
                    )
                )
                widget.setToolTip(pprint.pformat(dataclasses.asdict(step)))
            self._step_widgets.append(widget)

        layout = layout = _create_vbox_layout(self)
        self.title_widget = _add_label(layout, self.title, object_name="group_title")
        self.description_widget = _add_label(
            layout, self.description, object_name="group_description"
        )

        if not self._step_widgets:
            layout.addWidget(QtWidgets.QLabel("(No steps defined.)"))
            return

        frame = QtWidgets.QFrame()
        frame.setObjectName("group_step_frame")
        content_layout = _create_vbox_layout(frame)

        self._expandable_frame = ExpandableFrame(text=self.title.splitlines()[0])
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
    PlanStep: PlanStepWidget,
    ProcedureGroup: ProcedureGroupWidget,
    PydmDisplayStep: PydmDisplayStepWidget,
    TyphosDisplayStep: TyphosDisplayStepWidget,
}


class AtefProcedure(QtWidgets.QFrame):
    """
    Top-level ATEF procedure widget.

    Contains a scroll area with one or more procedures embedded.
    """

    procedures: List[ProcedureStep]

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent=parent)

        self.procedures = []

        layout = _create_vbox_layout(self)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setAlignment(Qt.AlignTop)
        self._scroll_area.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._scroll_area.setFrameStyle(QtWidgets.QFrame.NoFrame)
        self._scroll_area.setObjectName("scroll_area")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self._scroll_area)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_frame = QtWidgets.QFrame()
        self._scroll_frame.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout = _create_vbox_layout(self._scroll_frame)
        self._scroll_area.setWidget(self._scroll_frame)

    def add_procedure(
        self, procedure: ProcedureStep, *, expand_groups: bool = True
    ) -> StepWidgetBase:
        """Add a procedure to the scroll area."""
        self.procedures.append(procedure)
        widget = procedure_step_to_widget(procedure)
        self._scroll_layout.addWidget(widget)

        if expand_groups and isinstance(widget, ProcedureGroupWidget):
            widget._expandable_frame.toggle_button.setChecked(True)

        return widget


def procedure_step_to_widget(step: ProcedureStep) -> StepWidgetBase:
    """
    Create a widget given the procedure step settings.

    Parameters
    ----------
    step : ProcedureStep

    Returns
    -------
    widget : StepWidgetBase
    """
    cls = type(step)
    widget_cls = _settings_to_widget_class[cls]
    return widget_cls.from_settings(step)


class PassiveEditWidget(DesignerDisplay, DataWidget):
    """
    Widget for selecting and previewing a passive checkout.
    Features readouts for number of checks to run, ... and more?
    """
    filename = 'passive_edit_widget.ui'

    open_button: QtWidgets.QPushButton
    select_button: QtWidgets.QPushButton
    tree_view: QtWidgets.QTreeView

    def __init__(self, *args, data=PassiveStep, **kwargs):
        super().__init__(data=data, **kwargs)
        self.select_file(filename=self.bridge.filepath.get())
        # set up buttons, connect to tree-opening method
        self.select_button.clicked.connect(self.select_file)
        self.open_button.clicked.connect(self.open_in_new_tab)

    def select_file(self, *args, filename: Optional[str] = None, **kwargs) -> None:
        if filename is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a passive checkout',
                filter='Json Files (*.json)',
            )
        if not pathlib.Path(filename).is_file():
            return

        self.bridge.filepath.put(filename)
        self.passive_config = ConfigurationFile.from_filename(filename)
        self.setup_tree(self.passive_config)

    def setup_tree(self, config_file: ConfigurationFile):
        """ Assemble the tree view representation of ``config_file`` """
        # tree data
        root_item = TreeItem(data=config_file)
        create_tree_items(data=config_file.root, parent=root_item)

        model = ConfigTreeModel(data=root_item)

        self.tree_view.setModel(model)
        header = self.tree_view.header()
        header.setSectionResizeMode(header.ResizeToContents)
        # Hide the irrelevant status column
        self.tree_view.setColumnHidden(1, True)
        self.tree_view.expandAll()

    def open_in_new_tab(self):
        window = QtWidgets.QApplication.activeWindow()
        try:
            window.open_file(filename=self.bridge.filepath.get())
        except IsADirectoryError:
            # just prompt if something fails
            window.open_file(filename=None)


class PlanEditPage(DesignerDisplay, DataWidget):
    """
    Widget for creating and editing a plan step
    Accesses the Bluesky RunEngine
    Should include some readout?
    """
    filename = ''
    pass
