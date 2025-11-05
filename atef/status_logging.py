import logging
import tempfile
from typing import Dict, Optional
from uuid import UUID

from qtpy.QtCore import QObject
from qtpy.QtCore import Signal as QSignal

TempfileCache = Dict[UUID, tempfile._TemporaryFileWrapper]

STATUS_OUTPUT_TEMPFILE_CACHE: Optional[TempfileCache] = None
_SIMPLE_FORMATTER = logging.Formatter("%(asctime)s -- %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S")
_DETAILED_FORMATTER = logging.Formatter("[%(name).8s, %(asctime)s] -- %(message)s")


def get_status_tempfile_cache() -> TempfileCache:
    global STATUS_OUTPUT_TEMPFILE_CACHE
    if STATUS_OUTPUT_TEMPFILE_CACHE is None:
        STATUS_OUTPUT_TEMPFILE_CACHE = {}
    return STATUS_OUTPUT_TEMPFILE_CACHE


def configure_and_get_status_logger(uuid: UUID) -> logging.Logger:
    """setup / initialize a logging file for a specific checkout"""
    _tempfile_cache = get_status_tempfile_cache()
    if uuid in _tempfile_cache:
        # logger has been configured already, just return the logger
        return logging.getLogger(str(uuid))
    # create a tempfile for the uuid
    temp_logging_file = tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8")

    # configure the logger
    logger = logging.getLogger(str(uuid))
    handler = logging.StreamHandler(temp_logging_file)
    handler.setFormatter(_DETAILED_FORMATTER)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Prevent prints to console

    # add to the tempfile cache last, in case something errors out
    _tempfile_cache[uuid] = temp_logging_file
    return logger


def cleanup_status_logger(uuid: UUID):
    _tempfile_cache = get_status_tempfile_cache()
    if uuid not in _tempfile_cache:
        return

    # remove handlers
    logger = logging.getLogger(str(uuid))
    for handler in logger.handlers:
        logger.removeHandler(handler)

    # clean up file
    temp_logging_file = _tempfile_cache.pop(uuid)
    temp_logging_file.close()


class QtLoggingStream(QObject):
    """QObject handler to emit logging messages to the Qt main thread"""
    new_message = QSignal(str)

    def write(self, message: str):
        self.new_message.emit(message)

    def flush(self):
        ...


class QtLogHandler(logging.Handler):
    """
    Logging handler that writes to Qt Stream object
    """
    def __init__(self, stream: QtLoggingStream, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFormatter(_SIMPLE_FORMATTER)
        self.stream = stream

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        self.stream.write(msg)
