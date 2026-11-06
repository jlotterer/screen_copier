"""Rotating file log with API-key redaction (architecture §16, REQ-APP-07, REQ-NFR-03).

Pure logic: must not import PySide6.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from screen_copier.config.paths import logs_dir

LOG_FILE_NAME = "screen-copier.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"

_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_REDACTED = "sk-ant-***"
_HANDLER_MARKER = "_screen_copier_handler"


def redact(text: str) -> str:
    """Replace every ``sk-ant-…`` token with ``sk-ant-***``."""
    return _KEY_PATTERN.sub(_REDACTED, text)


class RedactingFilter(logging.Filter):
    """Redacts API keys from the message, its arguments, tracebacks and stack info."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> logging.Handler:
    """Attach the rotating, redacting file handler to the root logger and return it.

    Calling it again replaces the previous handler instead of adding a second one.
    """
    log_path = (directory if directory is not None else logs_dir()) / LOG_FILE_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactingFilter())
    setattr(handler, _HANDLER_MARKER, True)

    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)]:
        root.removeHandler(old)
        old.close()
    root.addHandler(handler)
    root.setLevel(level)
    return handler
