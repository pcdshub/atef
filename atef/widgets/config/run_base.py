"""
Widgets and helpers for run mode

Widgets here should map onto edit widgets, often from atef.widgets.config.data
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from typing import (TYPE_CHECKING, Any, ClassVar, Generator, List, Optional,
                    Union)

from qtpy import QtCore
from qtpy.QtWidgets import (QDialogButtonBox, QLabel, QLayout, QLineEdit,
                            QMenu, QPushButton, QSpacerItem, QStyle,
                            QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from atef.check import Comparison
from atef.config import (AnyPreparedConfiguration, Configuration,
                         ConfigurationFile, PreparedComparison,
                         PreparedConfiguration, PreparedFile)
from atef.enums import Severity
from atef.procedure import (PreparedProcedureFile, PreparedProcedureGroup,
                            PreparedProcedureStep, ProcedureFile,
                            ProcedureStep, walk_steps)
from atef.result import Result, combine_results
from atef.widgets.config.utils import TreeItem
from atef.widgets.core import DesignerDisplay
from atef.widgets.utils import BusyCursorThread

# avoid circular imports
if TYPE_CHECKING:
    from atef.widgets.config.page import AtefItem

logger = logging.getLogger(__name__)


def walk_tree_items(item: AtefItem) -> Generator[AtefItem, None, None]:
    yield item

    for child_idx in range(item.childCount()):
        yield from walk_tree_items(item.child(child_idx))


FileType = Union[ProcedureFile, ConfigurationFile]


def run_active_step(config):  # takes procedure steps and groups
    """
    Runs a given step and returns a result.
    Does not change the verification status of the step, which is left to
    other processes
    """
    result = config.run()

    return result


def make_run_page(
    widget: QWidget,
    prepared_data: List[Union[PreparedComparison, AnyPreparedConfiguration,
                              PreparedProcedureStep, PreparedProcedureGroup]],
) -> QWidget:
    """
    Disables all the widgets in ``widget`` and adds the RunCheck widget.
    RunCheck widget holds the functionality needed to execute the
    comparison or process

    Add buttons to existing widget to ensure existing navigation methods
    pass through

    Parameters
    ----------
    widget : QWidget
        widget to convert
    data : Union[PreparedComparison, ProcedureStep, AnyPreparedConfiguration]
        atef dataclasses corresponding to the widget
    prepared_file: Optional[PreparedFile]
        PreparedFile to collect relevant comparisons from (Passive checkouts only)

    Returns
    -------
    QWidget
        The run version of ``widget``
    """
    # TODO: consider options for different widget layouts
    # currently assumes vertical layout.

    # make existing widgets read-only
    disable_widget(widget)
    # add RunCheck to end of layout
    check_widget = RunCheck(data=prepared_data)

    # mimic placeholder configuration
    check_widget_placeholder = QWidget(parent=widget)
    vlayout = QVBoxLayout()
    vlayout.setContentsMargins(0, 0, 0, 0)
    check_widget_placeholder.setLayout(vlayout)
    vlayout.addWidget(check_widget)
    widget.layout().addWidget(check_widget_placeholder)

    widget.run_check = check_widget

    return widget


def disable_widget(widget: QWidget) -> QWidget:
    """ Disable widget, recurse through layouts """
    # TODO: revisit, is there a better way to do this?
    for idx in range(widget.layout().count()):
        layout_item = widget.layout().itemAt(idx)
        if isinstance(layout_item, QLayout):
            disable_widget(layout_item)
        else:
            wid = layout_item.widget()
            if wid:
                wid.setEnabled(False)
    return widget


def infer_step_type(config: Union[PreparedComparison, PreparedProcedureStep]) -> str:
    # TODO: find a better way to decide the step type
    if hasattr(config, 'compare'):
        return 'passive'
    elif hasattr(config, 'run'):
        return 'active'

    # TODO: Uncomment this when we've fixed up the comparison walking...
    # raise TypeError(f'incompatible type ({type(config)}), '
    #                 'cannot infer active or passive')


def get_relevant_configs_comps(
    prepared_file: PreparedFile,
    original_c: Union[Configuration, Comparison]
) -> List[Union[PreparedConfiguration, PreparedComparison]]:
    """
    Gather all the PreparedConfiguration or PreparedComparison dataclasses
    that correspond to the original comparison or config.

    Phrased another way: maps prepared comparisons onto the comparison
    seen in the GUI

    Currently for passive checkout files only

    Parameters
    ----------
    prepared_file : PreparedFile
        the file containing configs or comparisons to be gathered
    original_c : Union[Configuration, Comparison]
        the comparison to match PreparedComparison or PreparedConfigurations to

    Returns
    -------
    List[Union[PreparedConfiguration, PreparedComparison]]:
        the configuration or comparison dataclasses related to ``original_c``
    """
    matched_c = []

    for config in prepared_file.walk_groups():
        if config.config is original_c:
            matched_c.append(config)

    for comp in prepared_file.walk_comparisons():
        if comp.comparison is original_c:
            matched_c.append(comp)

    return matched_c


def get_prepared_step(
    prepared_file: PreparedProcedureFile,
    origin: Union[ProcedureStep, Comparison],
) -> List[Union[PreparedProcedureStep, PreparedComparison]]:
    """
    Gather all PreparedProcedureStep dataclasses the correspond to the original
    ProcedureStep.
    If a PreparedProcedureStep also has comparisions, use the walk_comparisons
    method to check if the "origin" matches any of thoes comparisons

    Only relevant for active checkouts.

    Parameters
    ----------
    prepared_file : PreparedProcedureFile
        the PreparedProcedureFile to search through
    origin : Union[ProcedureStep, Comparison]
        the step / comparison to match

    Returns
    -------
    List[Union[PreparedProcedureStep, PreparedComparison]]
        the PreparedProcedureStep's or PreparedComparison's related to ``origin``
    """
    # As of the writing of this docstring, this helper is only expected to return
    # lists of length 1.  However in order to match the passive checkout workflow,
    # we still return a list of relevant steps or comparisons.
    matched_steps = []
    for pstep in walk_steps(prepared_file.root):
        if getattr(pstep, 'origin', None) is origin:
            matched_steps.append(pstep)
        # check PreparedComparisons, which might be included in some steps
        if hasattr(pstep, 'walk_comparisons'):
            for comp in pstep.walk_comparisons():
                if comp.comparison is origin:
                    matched_steps.append(comp)

    return matched_steps


class ResultStatus(QLabel):
    """
    A simple QLabel that changes its icon based on a Result.
    Holds onto the whole dataclass with a .result field, rather than a singular
    result.  (which can be discarded at any time)

    Use the .update() slot to request this label update its icon and tooltip
    """
    style_icons = {
        Severity.success: QStyle.SP_DialogApplyButton,
        Severity.warning : QStyle.SP_TitleBarContextHelpButton,
        Severity.internal_error: QStyle.SP_DialogCancelButton,
        Severity.error: QStyle.SP_DialogCancelButton
    }

    unicode_icons = {
        # check mark
        Severity.success: '<span style="color: green;">&#10004;</span>',
        Severity.warning : '<span style="color: orange;">?</span>',
        # x mark
        Severity.internal_error: '<span style="color: red;">&#10008;</span>',
        Severity.error: '<span style="color: red;">&#10008;</span>',
    }

    def __init__(self, *args, data: Any, **kwargs):
        super().__init__(*args, **kwargs)
        icon = self.style().standardIcon(self.style_icons[Severity.warning])
        self.setPixmap(icon.pixmap(25, 25))
        self.data = data

    def update(self) -> None:
        """ Slot for updating this label """
        self.update_icon()
        self.update_tooltip()

    def update_icon(self) -> None:
        """ read the result and update the icon accordingly """
        chosen_icon = self.style_icons[self.data.result.severity]
        icon = self.style().standardIcon(chosen_icon)
        self.setPixmap(icon.pixmap(25, 25))

    def update_tooltip(self) -> None:
        """ Helper method to update tooltip based on ``results`` """
        result = self.data.result
        uni_icon = self.unicode_icons[result.severity]
        tt = f'<p>{uni_icon}: {result.reason or "-"}</p>'
        self.setToolTip(tt)

    def event(self, event: QtCore.QEvent) -> bool:
        """ Overload event method to update tooltips on tooltip-request """
        # Catch relevant events to update status tooltip
        if event.type() in (QtCore.QEvent.ToolTip, QtCore.QEvent.Paint):
            self.update()
        return super().event(event)


class RunCheck(DesignerDisplay, QWidget):
    """
    Widget to be added to run widgets

    Connections: (to establish)
    - next button
    - verify button to pop-out and record

    Parent widget must be a PageWidget
    """
    filename = 'run_check.ui'

    # Left to right
    result_label: QLabel
    result_verify_spacer: QSpacerItem
    verify_button: QToolButton
    verify_label: QLabel
    verify_run_spacer: QSpacerItem
    run_button: QPushButton
    run_success_label: QLabel
    run_next_spacer: QSpacerItem
    next_button: QPushButton

    results_updated: ClassVar[QtCore.Signal] = QtCore.Signal()

    style_icons = {
        Severity.success: QStyle.SP_DialogApplyButton,
        Severity.warning : QStyle.SP_TitleBarContextHelpButton,
        Severity.internal_error: QStyle.SP_DialogCancelButton,
        Severity.error: QStyle.SP_DialogCancelButton
    }

    unicode_icons = {
        # check mark
        Severity.success: '<span style="color: green;">&#10004;</span>',
        Severity.warning : '<span style="color: orange;">?</span>',
        # x mark
        Severity.internal_error: '<span style="color: red;">&#10008;</span>',
        Severity.error: '<span style="color: red;">&#10008;</span>',
    }

    def __init__(
        self,
        *args,
        data: Optional[list[Union[ProcedureStep, PreparedComparison]]] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        icon = self.style().standardIcon(self.style_icons[Severity.warning])
        self.result_label.setPixmap(icon.pixmap(25, 25))
        self.data = data

        if data:
            self.setup_buttons(configs=data)
            # initialize tooltip
            self.update_all_icons_tooltips()

    def setup_buttons(self, configs, next_widget: AtefItem = None) -> None:
        """
        Wire up buttons to the provided config dataclass.
        Run results and verification information will be saved to the
        provided ``config`` dataclass.
        For passive checkouts this should be a Prepared variant.

        Link Run button to the .run() or .compare() method of config
        Link Next button of previous widget to this widget's parent
        Link Verify button to verify method if it exists.  If not
        remove button and spacer.
        Link Status to result of Run procedure
        """
        self._make_run_slot(configs)
        if next_widget:
            self.setup_next_button(next_widget)

        self.setup_verify_button()

    def _make_run_slot(self, configs) -> None:

        def run_slot(*args, **kwargs):
            """ Slot that runs each step in the config list """
            for cfg in configs:
                config_type = infer_step_type(cfg)
                if config_type == 'active':
                    asyncio.run(cfg.run())
                elif config_type == 'passive':
                    asyncio.run(cfg.compare())
                else:
                    raise TypeError('incompatible type found: '
                                    f'{config_type}, {cfg}')

                self.results_updated.emit()
                self.update_all_icons_tooltips()

        # send this to a non-gui thread
        self.busy_thread = BusyCursorThread(func=run_slot, ignore_events=True)

        def run_thread():
            self.busy_thread.start()

        self.run_button.clicked.connect(run_thread)

    def update_icon(self, label: QLabel, results: List[Result]) -> None:
        """ Helper method to update icon on ``label`` based on ``results`` """
        combined_step_result = combine_results(results)

        chosen_icon = self.style_icons[combined_step_result.severity]
        icon = self.style().standardIcon(chosen_icon)

        label.setPixmap(icon.pixmap(25, 25))

    def update_label_tooltip(self, label: QLabel, results: List[Result]) -> None:
        """ Helper method to update tooltip for ``label`` based on ``results`` """
        tt = ''
        for r in results:
            uni_icon = self.unicode_icons[r.severity]
            tt += f'{uni_icon}: {r.reason or "-"}<br>'

        label.setToolTip('<p>' + tt.rstrip('<br>') + '</p>')

    def update_all_icons_tooltips(self) -> None:
        """ Convenience method for updating all the icons and tooltips """
        if not self.data:
            logger.warning('No config associated with this step')
            return

        self.update_icon(self.result_label, self.results)
        self.update_label_tooltip(self.result_label, self.results)

        # Extras for active checkouts
        if self.step_results:
            self.update_icon(self.run_success_label, self.step_results)
            self.update_label_tooltip(self.run_success_label, self.step_results)

        if self.verify_results:
            self.update_icon(self.verify_label, self.verify_results)
            self.update_label_tooltip(self.verify_label, self.verify_results)

    def event(self, event: QtCore.QEvent) -> bool:
        """ Overload event method to update tooltips on tooltip-request """
        # Catch tooltip events to update status tooltip
        if event.type() == QtCore.QEvent.ToolTip:
            self.update_all_icons_tooltips()
        return super().event(event)

    @property
    def results(self) -> List[Result]:
        return [c.result for c in self.data]

    @property
    def step_results(self) -> List[Result]:
        try:
            return [c.step_result for c in self.data]
        except AttributeError:
            return None

    @property
    def verify_results(self) -> List[Result]:
        try:
            return [c.verify_result for c in self.data]
        except AttributeError:
            return None

    def setup_next_button(self, next_item=None) -> None:
        """ Link RunCheck's next button to the next widget in the tree """
        # rise out of placeholder into containing PageWidget
        page = self.parent().parent()

        def inner_navigate(*args, **kwargs):
            page.navigate_to(next_item)

        if next_item:
            self.next_button.clicked.connect(inner_navigate)

    def setup_verify_button(self) -> None:
        """
        Verify status button.

        If passive checkout, remove button and spacer
        If active checkout, read verify options and expose
        """
        step_types = {infer_step_type(step) for step in self.data}
        if len(step_types) > 1:
            logger.debug('Multiple config types found, disabling verify')
            return
        elif 'passive' in step_types:
            self.verify_button.hide()
            self.run_success_label.hide()
            self.verify_label.hide()
            # Hide verify_run_spacer, not exposed by DesignerDisplay
            self.layout().itemAt(5).changeSize(0, 0)
        else:
            # Set up verify button depending on settings
            widget = VerifyEntryWidget()

            widget_action = QWidgetAction(self.verify_button)
            widget_action.setDefaultWidget(widget)

            widget_menu = QMenu(self.verify_button)
            widget_menu.addAction(widget_action)
            self.verify_button.setMenu(widget_menu)

            # slots and connections for VerifyEntryWidget buttons
            def set_verify(success: bool):
                reason = widget.reason_line_edit.text()
                if success:
                    severity = Severity.success
                else:
                    severity = Severity.error
                # do this for all, but expect only one dataclass
                for step in self.data:
                    step.verify_result = Result(severity=severity, reason=reason)

                self.update_all_icons_tooltips()

                widget_menu.hide()

            def verify_success_slot():
                set_verify(True)
                self.results_updated.emit()

            def verify_fail_slot():
                set_verify(False)
                self.results_updated.emit()

            widget.verify_button_box.accepted.connect(verify_success_slot)
            widget.verify_button_box.rejected.connect(verify_fail_slot)


class VerifyEntryWidget(DesignerDisplay, QWidget):
    """ Simple text entry widget to prompt for a verification result and reason """
    filename = 'verify_entry_widget.ui'

    reason_line_edit: QLineEdit
    verify_button_box: QDialogButtonBox

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # modify button box labels
        self.verify_button_box.button(QDialogButtonBox.Ok).setText('Verify')
        self.verify_button_box.button(QDialogButtonBox.Cancel).setText('Reject')


def create_tree_items(
    data: Any,
    parent: TreeItem,
    prepared_file: Optional[PreparedFile] = None
) -> None:
    """
    Recursively create the tree starting from the given data
    Optionally associate prepared dataclasses with the TreeItems
    """
    for cfg in getattr(data, 'configs', []):
        if prepared_file:
            # Grab relevant comps/configs so tree item can hold results
            prep_configs = get_relevant_configs_comps(prepared_file, cfg)
        else:
            prep_configs = None
        item = TreeItem(cfg, prepared_data=prep_configs)
        create_tree_items(cfg, item, prepared_file=prepared_file)
        parent.addChild(item)

    # look into configs, by_attr, shared
    # merges List[List[Comparison]] --> List[Comparison] with itertools
    config_categories = [
        getattr(data, 'shared', []),
        itertools.chain.from_iterable(getattr(data, 'by_attr', {}).values()),
        itertools.chain.from_iterable(getattr(data, 'by_pv', {}).values())
    ]
    for comp_list in config_categories:
        for comp in comp_list:
            if prepared_file:
                # Grab relevant comps/configs so tree item can hold results
                prep_configs = get_relevant_configs_comps(prepared_file, comp)
            else:
                prep_configs = None
            item = TreeItem(comp, prepared_data=prep_configs)
            parent.addChild(item)
