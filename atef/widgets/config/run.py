""" helpers for run mode """

import asyncio
import logging
from typing import Generator, List, Union

from qtpy import QtCore
from qtpy.QtWidgets import (QLabel, QLayout, QPushButton, QSpacerItem, QStyle,
                            QToolButton, QWidget)

from atef import util
from atef.check import Result
from atef.config import (AnyPreparedConfiguration, ConfigurationFile,
                         PreparedComparison)
from atef.enums import Severity
from atef.procedure import ProcedureFile, ProcedureStep
from atef.widgets.config.page import AtefItem, PageWidget
from atef.widgets.core import DesignerDisplay

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
    configs: List[Union[PreparedComparison, ProcedureStep,
                        AnyPreparedConfiguration]]
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
        widget to f
    configs : List[Union[PreparedComparison,
                         ProcedureStep,
                         AnyPreparedConfiguration]]
        A list of atef dataclasses that can each hold a Result.

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
    check_widget = RunCheck(configs=configs)
    widget.layout().addWidget(check_widget)
    widget.run_check = check_widget

    return widget


def disable_widget(widget: QWidget) -> QWidget:
    """ Disable widget, recurse through layouts """
    for idx in range(widget.layout().count()):
        layout_item = widget.layout().itemAt(idx)
        if isinstance(layout_item, QLayout):
            disable_widget(layout_item)
        else:
            wid = layout_item.widget()
            if wid:
                wid.setEnabled(False)
    return widget


def combine_results(results: List[Result]) -> Result:
    """
    Combines results into a single result.

    Takes the highest severity, and currently all the reasons
    """

    severity = util.get_maximum_severity([r.severity for r in results])
    reason = str([r.reason for r in results]) or ''

    return Result(severity=severity, reason=reason)


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
    status_label: QLabel
    status_verify_spacer: QSpacerItem
    verify_button: QToolButton
    verify_run_spacer: QSpacerItem
    run_button: QPushButton
    run_next_spacer: QSpacerItem
    next_button: QPushButton

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

    def __init__(self, *args, configs=None, **kwargs):
        super().__init__(*args, **kwargs)
        icon = self.style().standardIcon(self.style_icons[Severity.warning])
        self.status_label.setPixmap(icon.pixmap(25, 25))
        self.configs = configs

        if configs:
            self.setup_buttons(configs=configs)
            # initialize tooltip
            self.update_status_label_tooltip()

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

    def infer_step_type(self, config: Union[PreparedComparison, ProcedureStep]) -> str:
        if hasattr(config, 'compare'):
            return 'passive'
        elif hasattr(config, 'run'):
            return 'active'

        # TODO: Uncomment this when we've fixed up the comparison walking...
        # raise TypeError(f'incompatible type ({type(config)}), '
        #                 'cannot infer active or passive')

    def _make_run_slot(self, configs) -> None:

        def run_slot():
            """ Slot that runs each step in the config list """
            for cfg in configs:
                config_type = self.infer_step_type(cfg)
                if config_type == 'active':
                    cfg.run()
                elif config_type == 'passive':
                    asyncio.run(cfg.compare())
                else:
                    raise TypeError('incompatible type found: '
                                    f'{config_type}, {cfg}')

                self.update_status()

        self.run_button.clicked.connect(run_slot)

    def update_status(self) -> None:
        if not self.configs:
            logger.warning('No config associated with this step')
            return
        combined_result = combine_results(self.results)

        chosen_icon = self.style_icons[combined_result.severity]
        icon = self.style().standardIcon(chosen_icon)

        self.status_label.setPixmap(icon.pixmap(25, 25))
        self.update_status_label_tooltip()

    def update_status_label_tooltip(self) -> None:
        tt = ''
        for r in self.results:
            uni_icon = self.unicode_icons[r.severity]
            tt += f'{uni_icon}: {r.reason or "-"}<br>'

        self.status_label.setToolTip('<p>' + tt.rstrip('<br>') + '</p>')

    def event(self, event: QtCore.QEvent) -> bool:
        # Catch tooltip events to update status tooltip
        if event.type() == QtCore.QEvent.ToolTip:
            self.update_status_label_tooltip()
        return super().event(event)

    @property
    def results(self) -> List[Result]:
        return [c.result for c in self.configs]

    def setup_next_button(self, next_item) -> None:
        page = self.parent()

        def inner_navigate(*args, **kwargs):
            page.navigate_to(next_item)

        self.next_button.clicked.connect(inner_navigate)

    def setup_verify_button(self) -> None:
        """
        Verify status button.

        If passive checkout, remove button and spacer
        If active checkout, read verify options and expose
        """
        step_types = {self.infer_step_type(step) for step in self.configs}
        if len(step_types) > 1:
            logger.debug('Multiple config types found, disabling verify')
            return
        elif 'passive' in step_types:
            self.verify_button.hide()
            self.layout().itemAt(3).changeSize(0, 0)
        else:
            # TODO: verify functionality for active checkouts
            return


class RunPage(PageWidget):
    """
    Base Widget for running passive checkout steps and displaying their
    results

    Will always have a RunCheck widget, which should be connected after
    instantiation via ``RunCheck.setup_buttons()``
    """
    filename = ''

    run_check: RunCheck

    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_name_desc_tags_init()
        self.insert_widget(
            RunCheck(),
            self.run_check
        )


class PassiveRunPage(RunPage):
    filename = 'passive_run_page.ui'
