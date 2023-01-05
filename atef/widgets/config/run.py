""" helpers for run mode """

import logging
from typing import Generator, Union

from qtpy.QtWidgets import (QLabel, QPushButton, QSpacerItem, QStyle,
                            QToolButton, QWidget)

from atef.config import Configuration, ConfigurationFile, PreparedFile
from atef.procedure import ProcedureFile, ProcedureStep
from atef.widgets.config.page import AtefItem, PageWidget
from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)


def walk_tree_items(item: AtefItem) -> Generator[AtefItem, None, None]:
    yield item

    for child_idx in range(item.childCount()):
        yield from walk_tree_items(item.child(child_idx))


FileType = Union[ProcedureFile, ConfigurationFile]


def walk_config_tree(
    config: FileType, sub_name=None
) -> Generator[Union[ProcedureStep, Configuration], None, None]:
    # Starting case: file dataclasses
    if not sub_name:
        if isinstance(config, (ConfigurationFile, PreparedFile)):
            sub_name = 'configs'
        elif isinstance(config, ProcedureFile):
            sub_name = 'steps'
        else:
            raise TypeError(f'config type ({type(config)}) is not supported')
        print('initial file')
        yield config
        yield from walk_config_tree(config.root, sub_name=sub_name)

    # Standard case, steps and substeps
    else:
        yield config
        for sub_config in getattr(config, sub_name, []):
            yield from walk_config_tree(sub_config, sub_name=sub_name)
        for attr_cfg_list in getattr(config, 'by_attr', {}).values():
            for attr_cfg in attr_cfg_list:
                yield from walk_config_tree(attr_cfg, sub_name=sub_name)
        for attr_cfg_list in getattr(config, 'by_pv', {}).values():
            for attr_cfg in attr_cfg_list:
                yield from walk_config_tree(attr_cfg, sub_name=sub_name)
        for attr_cfg in getattr(config, 'comparisons', []):
            yield from walk_config_tree(attr_cfg, sub_name=sub_name)
        for shared_cfg in getattr(config, 'shared', []):
            yield from walk_config_tree(shared_cfg, sub_name=sub_name)


def run_active_step(config):  # takes procedure steps and groups
    """
    Runs a given step and returns a result.
    Does not change the verification status of the step, which is left to
    other processes
    """
    result = config.run()

    return result


def make_run_page(widget: QWidget, config) -> QWidget:
    """
    Make a run version of the requested widget.

    Add buttons to existing widget to ensure methods pass through

    widget: widget to modify

    config: should be a Configuration or Proccess dataclass that
    can hold a result
    """
    check_widget = RunCheck()
    # currently assumes vertical layout.
    # Taking old widgets and re-doing layout is difficult.

    # make existing widgets read-only
    for idx in range(widget.layout().count()):
        wid = widget.layout().itemAt(idx).widget()
        if wid:
            wid.setEnabled(False)
    widget.layout().addWidget(check_widget)
    widget.run_check = check_widget

    # TODO: options for different widget layouts
    # TODO: link config page to
    return widget


class RunCheck(DesignerDisplay, QWidget):
    """
    Widget to be added to run widgets

    Connections: (to establish)
    - next button
    - verify button to pop-out and record
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

    unknown_icon = QStyle.SP_TitleBarContextHelpButton
    complete_icon = QStyle.SP_DialogApplyButton
    fail_icon = QStyle.SP_DialogCancelButton

    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        icon = self.style().standardIcon(self.unknown_icon)
        self.status_label.setPixmap(icon.pixmap(25, 25))

        if config:
            self.setup_buttons(config=config)

    def setup_buttons(self, config=None, prev_widget=None):
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
        pass


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
