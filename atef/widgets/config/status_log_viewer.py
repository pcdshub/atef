import datetime
import logging
import shutil
from uuid import UUID

from qtpy import QtCore, QtWidgets

from atef.status_logging import get_status_tempfile_cache
from atef.widgets.core import DesignerDisplay

logger = logging.getLogger(__name__)


class StatusLogWidget(DesignerDisplay, QtWidgets.QWidget):
    """
    Main widget for viewing all status log files.

    Updates automatically as each logging tempfile is written to
    """
    filename = "status_log_widget.ui"

    tab_widget: QtWidgets.QTabWidget
    save_button: QtWidgets.QPushButton

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_button.clicked.connect(self.save_current_log)

    def add_tab(self, name: str, uuid: UUID):
        viewer = StatusLogViewer(uuid=uuid)
        self.tab_widget.addTab(viewer, name)

    def save_current_log(self):
        viewer = self.tab_widget.currentWidget()
        assert isinstance(viewer, StatusLogViewer)
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent=self,
            caption='Save log file as',
            filter='Text Files (*.txt)',
        )
        if not filename:
            return
        try:
            shutil.copyfile(viewer.fp, filename)
        except OSError:
            logger.exception(f'Error saving file {filename}')

    def clear_tabs(self):
        # We need to clean up file watching, since removing a tab does not
        # close / delete the page widget
        while self.tab_widget.count() > 0:
            widget = self.tab_widget.widget(0)
            widget.close()
            self.tab_widget.removeTab(0)


class StatusLogViewer(DesignerDisplay, QtWidgets.QWidget):
    """
    Viewer for an individual log file.
    """
    filename = "status_log_viewer.ui"

    text_edit: QtWidgets.QPlainTextEdit
    datetime_edit: QtWidgets.QDateTimeEdit

    def __init__(self, *args, uuid: UUID, **kwargs):
        # grab the filehandle from the cache
        super().__init__(*args, **kwargs)
        tempfile = get_status_tempfile_cache()[uuid]
        self.fp = tempfile.name
        self.watcher = QtCore.QFileSystemWatcher(self)
        self.watcher.addPath(self.fp)
        self.watcher.fileChanged.connect(self.update_edit)

        self.update_edit()

    def update_edit(self):
        try:
            with open(self.fp) as fp:
                self.text_edit.setPlainText(fp.read())
        except FileNotFoundError:
            self.text_edit.setPlainText("Log file cannot be found, please refresh")

        self.datetime_edit.setDateTime(datetime.datetime.now())
        self.text_edit.verticalScrollBar().setValue(
            self.text_edit.verticalScrollBar().maximum()
        )

    def close(self) -> bool:
        self.watcher.removePaths(self.watcher.files())
        return super().close()
