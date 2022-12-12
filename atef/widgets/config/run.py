""" helpers for run mode """

from typing import Generator

from qtpy.QtWidgets import QLabel, QPushButton, QStyle, QWidget

from atef.widgets.config.page import AtefItem
from atef.widgets.core import DesignerDisplay


def walk_tree_items(item: AtefItem) -> Generator[AtefItem, None, None]:
    yield item

    for child_idx in range(item.childCount()):
        yield from walk_tree_items(item.child(child_idx))


def make_run_page(widget: QWidget) -> QWidget:
    """
    Make a run version of the requested widget.

    If a run-specific version of the widget exists, return that.
    Otherwise makes a read-only copy of the widget with run controls

    Add buttons to existing widget to ensure methods pass through
    """
    check_widget = RunCheck()
    # currently assumes vertical layout.
    # Taking old widgets and re-doing layout is difficult.
    widget.layout().addWidget(check_widget)
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

    run_button: QPushButton
    status_label: QLabel

    unknown_icon = QStyle.SP_TitleBarContextHelpButton
    complete_icon = QStyle.SP_DialogApplyButton
    fail_icon = QStyle.SP_DialogCancelButton

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        icon = self.style().standardIcon(self.unknown_icon)
        self.status_label.setPixmap(icon.pixmap(25, 25))
