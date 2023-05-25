"""
Non-core utilities. Primarily dynamic styling tools.
"""
from typing import ClassVar, Optional

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import QEvent, QObject
from qtpy.QtGui import QPalette
from qtpy.QtWidgets import QLineEdit


class FrameOnEditFilter(QObject):
    """
    A QLineEdit event filter for editing vs not editing style handling.

    This will make the QLineEdit look like a QLabel when the user is
    not editing it.
    """
    def eventFilter(self, object: QLineEdit, event: QEvent) -> bool:
        # Even if we install only on line edits, this can be passed a generic
        # QWidget when we remove and clean up the line edit widget.
        if not isinstance(object, QLineEdit):
            return False
        if event.type() == QEvent.FocusIn:
            self.set_edit_style(object)
            return True
        if event.type() == QEvent.FocusOut:
            self.set_no_edit_style(object)
            return True
        return False

    @staticmethod
    def set_edit_style(object: QLineEdit):
        """
        Set a QLineEdit to the look and feel we want for editing.

        Parameters
        ----------
        object : QLineEdit
            Any line edit widget.
        """
        object.setFrame(True)
        color = object.palette().color(QPalette.ColorRole.Base)
        object.setStyleSheet(
            f"QLineEdit {{ background: rgba({color.red()},"
            f"{color.green()}, {color.blue()}, {color.alpha()})}}"
        )
        object.setReadOnly(False)

    @staticmethod
    def set_no_edit_style(object: QLineEdit):
        """
        Set a QLineEdit to the look and feel we want for not editing.

        Parameters
        ----------
        object : QLineEdit
            Any line edit widget.
        """
        if object.text():
            object.setFrame(False)
            object.setStyleSheet(
                "QLineEdit { background: transparent }"
            )
        object.setReadOnly(True)


def match_line_edit_text_width(
    line_edit: QLineEdit,
    text: Optional[str] = None,
    minimum: int = 40,
    buffer: int = 10,
) -> None:
    """
    Set the width of a line edit to match the text length.

    You can use this in a slot and connect it to the line edit's
    "textChanged" signal. This creates an effect where the line
    edit will get longer when the user types text into it and
    shorter when the user deletes text from it.

    Parameters
    ----------
    line_edit : QLineEdit
        The line edit whose width you'd like to adjust.
    text : str, optional
        The text to use as the basis for our size metrics.
        In a slot you could pass in the text we get from the
        signal update. If omitted, we'll use the current text
        in the widget.
    minimum : int, optional
        The minimum width of the line edit, even when we have no
        text. If omitted, we'll use a default value.
    buffer : int, optional
        The buffer we have on the right side of the rightmost
        character in the line_edit before the edge of the widget.
        If omitted, we'll use a default value.
    """
    font_metrics = line_edit.fontMetrics()
    if text is None:
        text = line_edit.text()
    width = font_metrics.boundingRect(text).width()
    line_edit.setFixedWidth(max(width + buffer, minimum))


def insert_widget(widget: QtWidgets.QWidget, placeholder: QtWidgets.QWidget) -> None:
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


def set_wait_cursor():
    app = QtWidgets.QApplication.instance()
    app.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))


def reset_cursor():
    app = QtWidgets.QApplication.instance()
    app.restoreOverrideCursor()


def busy_cursor(func):
    """
    Decorator for making the cursor busy while a function is running
    Will run in the GUI thread, therefore blocking GUI interaction
    """
    def wrapper(*args, **kwargs):
        set_wait_cursor()
        try:
            func(*args, **kwargs)
        finally:
            reset_cursor()

    return wrapper


class IgnoreInteractionFilter(QObject):
    interaction_events = (
        QEvent.KeyPress, QEvent.KeyRelease, QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick
    )

    def eventFilter(self, a0: QObject, a1: QEvent) -> bool:
        """ ignore all interaction events while this filter is installed """
        if a1.type() in self.interaction_events:
            return True
        else:
            return super().eventFilter(a0, a1)


FILTER = IgnoreInteractionFilter()


class BusyCursorThread(QtCore.QThread):
    """
    Thread to switch the cursor while a task is running.  Pushes the task to a
    thread, allowing GUI interaction in the main thread.

    To use, you should initialize this thread with the function/slot you want to
    run in the thread.  Note the .start method used to kick off this thread must
    be wrapped in a function in order to run... for some reason...

    ``` python
    busy_thread = BusyCursorThread(func=slot_to_run)

    def run_thread():
        busy_thread.start()

    button.clicked.connect(run_thread)
    ```
    """
    task_finished: ClassVar[QtCore.Signal] = QtCore.Signal()
    task_starting: ClassVar[QtCore.Signal] = QtCore.Signal()
    raised_exception: ClassVar[QtCore.Signal] = QtCore.Signal(Exception)

    def __init__(self, *args, func, ignore_events: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = None
        self.func = func
        self.ignore_events = ignore_events
        self.task_starting.connect(self.set_cursor_busy)
        self.task_finished.connect(self.reset_cursor)

    def run(self) -> None:
        # called from .start().  if called directly, will block current thread
        self.task_starting.emit()
        # run the attached method
        try:
            self.func()
        except Exception as ex:
            self.raised_exception.emit(ex)
        finally:
            self.task_finished.emit()

    def set_cursor_busy(self):
        set_wait_cursor()
        if self.ignore_events:
            self.app = QtWidgets.QApplication.instance()
            self.app.installEventFilter(FILTER)

    def reset_cursor(self):
        reset_cursor()
        if self.ignore_events:
            self.app = QtWidgets.QApplication.instance()
            self.app.removeEventFilter(FILTER)
