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
import datetime
import logging
import pathlib
import pprint
from typing import (ClassVar, Dict, Generator, List, Optional, Sequence, Type,
                    TypeVar, Union)

import pydm
import pydm.display
import qtawesome
import typhos
import typhos.cli
import typhos.display
from ophyd import EpicsSignal
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialogButtonBox

from atef import util
from atef.check import Equals
from atef.config import ConfigurationFile
from atef.result import Result, incomplete_result
from atef.widgets.config.data_base import DataWidget, SimpleRowWidget
from atef.widgets.config.run_base import create_tree_items
from atef.widgets.config.utils import (ConfigTreeModel, TableWidgetWithAddRow,
                                       TreeItem)
from atef.widgets.core import DesignerDisplay
from atef.widgets.happi import HappiDeviceComponentWidget
from atef.widgets.ophyd import OphydAttributeData

from ...procedure import (ComparisonToTarget, DescriptionStep, DisplayOptions,
                          PassiveStep, PlanOptions, PlanStep, ProcedureGroup,
                          ProcedureStep, PydmDisplayStep, SetValueStep, Target,
                          TyphosDisplayStep, ValueToTarget)

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
    load_time_label: QtWidgets.QLabel

    def __init__(self, *args, data=PassiveStep, **kwargs):
        super().__init__(data=data, **kwargs)
        self.select_file(filepath=self.bridge.filepath.get())

        self.select_button.setIcon(qtawesome.icon('fa.folder-open-o'))
        self.open_button.setIcon(qtawesome.icon('mdi.open-in-new'))
        # set up buttons, connect to tree-opening method
        self.select_button.clicked.connect(self.select_file)
        self.open_button.clicked.connect(self.open_in_new_tab)

    def select_file(self, *args, filepath: Optional[str] = None, **kwargs) -> None:
        """
        Select the passive checkout file to be loaded into the widget's tree view.
        If no filename is provided, opens a QFileDialog to prompt the user for a file

        Parameters
        ----------
        filepath : Optional[str], optional
            filepath to the passive checkout, by default None
        """
        if filepath is None:
            filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
                parent=self,
                caption='Select a passive checkout',
                filter='Json Files (*.json)',
            )
        if not pathlib.Path(filepath).is_file():
            return

        self.bridge.filepath.put(filepath)
        self.passive_config = ConfigurationFile.from_filename(filepath)
        self.setup_tree(self.passive_config)
        self.load_time_label.setText(f'Loaded: {datetime.datetime.now().ctime()}')

    def setup_tree(self, config_file: ConfigurationFile) -> None:
        """
        Assemble the tree view representation of ``config_file``

        Parameters
        ----------
        config_file : ConfigurationFile
            Passive checkout configuration file dataclass
        """
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

    def open_in_new_tab(self, *args, **kwargs) -> None:
        """
        Slot for opening the selected passive checkout in a new tab.
        """
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


class SetValueEditWidget(DesignerDisplay, DataWidget):
    """
    Widget for creating and editing a SetValueStep.
    Contains a table of actions and a table of checks to execute after actions
    have completed

    actions_table will be filled with ActionRowWidget
    checks_table will be filled with CheckRowWidget
    """
    filename = 'set_value_edit_widget.ui'

    action_success_radio_button: QtWidgets.QRadioButton
    step_failure_radio_button: QtWidgets.QRadioButton

    actions_table: TableWidgetWithAddRow
    actions_table_placeholder: QtWidgets.QWidget
    checks_table: TableWidgetWithAddRow
    checks_table_placeholder: QtWidgets.QWidget

    def __init__(self, *args, data=SetValueStep, **kwargs):
        super().__init__(*args, data=data, **kwargs)

        self.actions_table = TableWidgetWithAddRow(
            add_row_text='Add new action',
            title_text='Actions',
            row_widget_cls=ActionRowWidget
        )
        self.actions_table.table_updated.connect(
            self.update_list(self.actions_table, self.bridge.actions)
        )
        self.insert_widget(self.actions_table, self.actions_table_placeholder)

        self.checks_table = TableWidgetWithAddRow(
            add_row_text='Add new check',
            title_text='Checks',
            row_widget_cls=CheckRowWidget
        )
        self.checks_table.table_updated.connect(
            self.update_list(self.checks_table, self.bridge.success_criteria)
        )
        self.insert_widget(self.checks_table, self.checks_table_placeholder)

        # add existing actions to table
        for action in data.actions:
            self.actions_table.add_row(data=action)
        for check in data.success_criteria:
            self.checks_table.add_row(data=check)

    def update_list(self, table_widget: QtWidgets.QTableWidget, bridge_attr):
        def inner_slot():
            row_data = []
            for row_index in range(table_widget.rowCount()):
                row_widget = table_widget.cellWidget(row_index, 0)
                data = getattr(row_widget, 'data', None)
                if data:
                    row_data.append(data)

            bridge_attr.put(row_data)

        return inner_slot

    def insert_widget(self, widget: QtWidgets.QWidget, placeholder: QtWidgets.QWidget) -> None:
        """
        Helper function for slotting e.g. data widgets into placeholders.
        """
        if placeholder.layout() is None:
            layout = QtWidgets.QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            placeholder.setLayout(layout)
        else:
            old_widget = placeholder.layout().takeAt(0).widget()
            if old_widget is not None:
                # old_widget.setParent(None)
                old_widget.deleteLater()
        placeholder.layout().addWidget(widget)


class TargetRowWidget(DesignerDisplay, SimpleRowWidget):
    """ Base widget with target selection """
    filename = 'action_row_widget.ui'

    target_button: QtWidgets.QToolButton

    def __init__(self, data: Target, **kwargs):
        super().__init__(data=data, **kwargs)
        self.setup_row()
        self.setup_ui()
        # TODO: initialize row using data if it exists, do something with data
        if data.to_signal() is not None:
            self.target_entry_widget.chosen_target = data
            self.target_entry_widget.data_updated.emit()

    def setup_ui(self):
        # target entry widget dropdown from target_button
        self.target_entry_widget = TargetEntryWidget()
        widget_action = QtWidgets.QWidgetAction(self.target_button)
        widget_action.setDefaultWidget(self.target_entry_widget)

        widget_menu = QtWidgets.QMenu(self.target_button)
        widget_menu.addAction(widget_action)
        self.target_button.setMenu(widget_menu)

        # update data on target_button update
        self.target_entry_widget.data_updated.connect(self.update_target)

    def update_target(self):
        """
        Slot for updating data based on the entry_widget
        Move Target data to TargetToValue
        """
        if self.target_entry_widget.chosen_target is None:
            self.bridge.name.put(None)
            self.bridge.device.put(None)
            self.bridge.attr.put(None)
            self.bridge.pv.put(None)
            self.target_button.setText('select a target')
            self.target_button.setToolTip('')
        else:
            target = self.target_entry_widget.chosen_target
            self.bridge.name.put(target.name)
            self.bridge.device.put(target.device)
            self.bridge.attr.put(target.attr)
            self.bridge.pv.put(target.pv)

            if target.device is not None and target.attr is not None:
                self.target_button.setText(f'{target.device}.{target.attr}')
                self.target_button.setToolTip(f'{target.device}.{target.attr}')
            elif target.pv is not None:
                self.target_button.setText(target.pv)
                self.target_button.setToolTip(target.pv)
            else:
                raise ValueError(
                    f'insufficient information to specifiy target: {target}'
                )


class TargetEntryWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Simple text entry widget to prompt for a signal, via PV or ophyd device signal

    resets with each open action, clicking apply emits to signal_selected which
    should be connected to.
    """
    filename = 'target_entry_widget.ui'

    data_updated: ClassVar[QtCore.Signal] = QtCore.Signal()
    chosen_target: Optional[Target] = None

    _search_widget: Optional[HappiDeviceComponentWidget] = None
    pv_edit: QtWidgets.QLineEdit
    signal_button: QtWidgets.QPushButton
    target_button_box: QDialogButtonBox

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # button box setup
        reset_button = self.target_button_box.button(QDialogButtonBox.Reset)
        reset_button.clicked.connect(self.reset_fields)
        apply_button = self.target_button_box.button(QDialogButtonBox.Apply)
        apply_button.clicked.connect(self.confirm_signal)
        # signal select setup
        self.signal_button.clicked.connect(self.pick_signal)
        # PV edit setup
        regexp = QtCore.QRegularExpression(r'^\w+(:\w+)+(\.\w+)*$')
        validator = QtGui.QRegularExpressionValidator(regexp)
        self.pv_edit.setValidator(validator)
        self.pv_edit.textChanged.connect(self.pick_pv)

        self.reset_fields()

    def reset_fields(self):
        self.chosen_target = None
        self.target_button_box.hide()
        self.signal_button.show()
        self.pv_edit.show()
        self.pv_edit.clear()
        self.signal_button.setText('pick a device_signal')
        self.signal_button.setToolTip('')
        self.data_updated.emit()

    def confirm_signal(self):
        # signal button is used
        if self.pv_edit.text() is None:
            self.data_updated.emit()

        sig = EpicsSignal(self.pv_edit.text())
        try:
            sig.wait_for_connection()
        except TimeoutError:
            logger.error(f'Could not connect to PV: {self.pv_edit.text()}')
            self.reset_fields()
            return

        self.chosen_target = Target(pv=self.pv_edit.text())
        self.data_updated.emit()

    def pick_signal(self):
        if self._search_widget is None:
            widget = HappiDeviceComponentWidget(
                client=util.get_happi_client()
            )
            # look at connecting widget.attributes_selected -> List[OphydAttributeData]
            widget.device_widget.attributes_selected.connect(self.set_signal)
            widget.device_widget.attributes_selected.connect(widget.close)
            # prevent multiple selection
            self._search_widget = widget

        self._search_widget.show()
        self._search_widget.activateWindow()

        self.pv_edit.hide()
        self.target_button_box.show()

    def set_signal(self, attr_selected: List[OphydAttributeData]):
        """
        Slot to be connected to
        HappiDeviceComponentWidget.device_widget.attributes_selected
        """
        attr = attr_selected[0]
        logger.debug(f'found attr: {attr}')
        self.signal_button.setText(attr.signal.name)
        self.signal_button.setToolTip(attr.signal.name)
        self.chosen_target = self.attr_to_target(attr)

    def pick_pv(self):
        # prompt for confirmation
        self.signal_button.hide()
        self.target_button_box.show()

    def attr_to_target(self, attr: OphydAttributeData) -> Target:
        # surely there's a better way...
        full_name = attr.signal.name
        dot_attr = attr.attr
        _attr = '_' + dot_attr.replace('.', '_')
        dev_name = full_name[:-len(_attr)]

        return Target(device=dev_name, attr=dot_attr, pv=attr.pvname)


class ActionRowWidget(TargetRowWidget):
    filename = 'action_row_widget.ui'

    value_input_placeholder: QtWidgets.QWidget
    value_button_box: QtWidgets.QDialogButtonBox

    def __init__(self, data: Optional[ValueToTarget] = None, **kwargs):
        if data is None:
            data = ValueToTarget()
        super().__init__(data=data, **kwargs)

    def setup_ui(self):
        """ Called by TargetRowWidget.__init__ """
        super().setup_ui()
        self.child_button.hide()
        apply_button = self.value_button_box.button(QDialogButtonBox.Apply)
        apply_button.setText('')
        self.update_input_placeholder()

    def update_target(self):
        super().update_target()

        self.update_input_placeholder()

    def update_input_placeholder(self):
        # TODO: expand validator, edit behavior
        # Could use metadata in the future?
        # Enum drop box?
        sig = self.data.to_signal()
        if sig is None:
            self.edit_widget = QtWidgets.QLabel('(no target set)')
            self.insert_widget(self.edit_widget, self.value_input_placeholder)
            self.value_button_box.hide()
            return
        dtype = type(sig.get())
        self.edit_widget = QtWidgets.QLineEdit()

        if dtype is int:
            validator = QtGui.QIntValidator()
        elif dtype is float:
            validator = QtGui.QDoubleValidator()
        else:
            validator = None

        self.edit_widget.setValidator(validator)
        self.edit_widget.setPlaceholderText(f'({sig.get()}) ⏎')
        if self.bridge.value.get() is not None:
            self.edit_widget.setText(str(self.bridge.value.get()))
            self.edit_widget.setToolTip(str(self.bridge.value.get()))

        # slot for value update on apply button press
        def update_value():
            self.bridge.value.put(dtype(self.edit_widget.text()))
            self.value_button_box.hide()
            self.edit_widget.setFrame(False)

        def value_changed():
            self.value_button_box.show()
            self.edit_widget.setFrame(True)

        self.edit_widget.textChanged.connect(value_changed)

        self.insert_widget(self.edit_widget, self.value_input_placeholder)
        self.value_button_box.show()
        apply_button = self.value_button_box.button(QDialogButtonBox.Apply)
        apply_button.clicked.connect(update_value)

    def insert_widget(self, widget: QtWidgets.QWidget, placeholder: QtWidgets.QWidget) -> None:
        """
        Helper function for slotting e.g. data widgets into placeholders.
        """
        if placeholder.layout() is None:
            layout = QtWidgets.QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            placeholder.setLayout(layout)
        else:
            old_widget = placeholder.layout().takeAt(0).widget()
            if old_widget is not None:
                # old_widget.setParent(None)
                old_widget.deleteLater()
        placeholder.layout().addWidget(widget)


class CheckRowWidget(TargetRowWidget):
    filename = 'check_row_widget.ui'

    check_summary_label: QtWidgets.QLabel

    def __init__(self, data: Optional[ComparisonToTarget] = None, **kwargs):
        if data is None:
            data = ComparisonToTarget(name='untitled_check', comparison=Equals())
        super().__init__(data=data, **kwargs)

        self.name_edit.hide()
