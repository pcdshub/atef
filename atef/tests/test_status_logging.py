import logging
import os
from typing import Callable, Union
from uuid import uuid4

import pytest
from pytestqt.qtbot import QtBot

import atef.status_logging
from atef.config import ConfigurationFile, PreparedFile
from atef.procedure import PreparedProcedureFile, ProcedureFile
from atef.status_logging import configure_and_get_status_logger
from atef.widgets.config.status_log_viewer import (StatusLogViewer,
                                                   StatusLogWidget)


@pytest.fixture(scope="function", autouse=True)
def mock_status_tempfile_cache(monkeypatch):
    prev = atef.status_logging.STATUS_OUTPUT_TEMPFILE_CACHE
    atef.status_logging.STATUS_OUTPUT_TEMPFILE_CACHE = {}
    yield
    atef.status_logging.STATUS_OUTPUT_TEMPFILE_CACHE = prev


def is_file_empty(filepath):
    if not os.path.exists(filepath):
        return False
    return os.path.getsize(filepath) == 0


def create_blank_prep_passive():
    return PreparedFile.from_config(ConfigurationFile())


def create_blank_prep_active():
    return PreparedProcedureFile.from_origin(ProcedureFile())


@pytest.mark.parametrize("prepared_file_fn,", [
    create_blank_prep_passive, create_blank_prep_active

])
def test_status_tempfile_creation(
    prepared_file_fn: Callable[[], Union[PreparedFile, PreparedProcedureFile]]
):
    tempfile_cache = atef.status_logging.get_status_tempfile_cache()
    prepared_file = prepared_file_fn()

    assert prepared_file.uuid not in tempfile_cache
    status_logger = configure_and_get_status_logger(prepared_file.uuid)

    assert isinstance(status_logger, logging.Logger)
    assert prepared_file.uuid in tempfile_cache

    temp_file = tempfile_cache[prepared_file.uuid]
    assert is_file_empty(temp_file.name)
    status_logger.info("test msg")
    assert not is_file_empty(temp_file.name)

    atef.status_logging.cleanup_status_logger(prepared_file.uuid)
    assert prepared_file.uuid not in tempfile_cache


@pytest.mark.parametrize("prepared_file_fn,", [
    create_blank_prep_passive, create_blank_prep_active
])
def test_logging_stream(
    prepared_file_fn: Callable[[], Union[PreparedFile, PreparedProcedureFile]],
    qtbot: QtBot
):
    # set up logging
    prepared_file = prepared_file_fn()
    status_logger = configure_and_get_status_logger(prepared_file.uuid)
    log_stream = atef.status_logging.QtLoggingStream()
    log_handler = atef.status_logging.QtLogHandler(log_stream)
    status_logger.addHandler(log_handler)

    with qtbot.waitSignal(log_stream.new_message) as blocker:
        status_logger.info("test_msg")

    assert blocker.args is not None
    assert "test_msg" in blocker.args[0]


def test_status_log_widget(qtbot: QtBot):
    status_log_widget = StatusLogWidget()
    qtbot.addWidget(status_log_widget)
    uuid = uuid4()
    logger = configure_and_get_status_logger(uuid)
    status_log_widget.add_tab("this", uuid)
    assert status_log_widget.tab_widget.count() == 1
    viewer = status_log_widget.tab_widget.widget(0)
    assert isinstance(viewer, StatusLogViewer)

    assert viewer.text_edit.toPlainText() == ""
    logger.info("test_msg_2")

    qtbot.waitUntil(lambda: "test_msg_2" in viewer.text_edit.toPlainText())
