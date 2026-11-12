"""Rendering, naming and atomically writing the saved file (architecture §13.3).

Pure logic: must not import PySide6. Callers run ``write_file`` off the GUI thread.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from screen_copier.config.settings import LineEnding
from screen_copier.output.language import extension_for

_SEPARATORS: dict[LineEnding, str] = {LineEnding.LF: "\n", LineEnding.CRLF: "\r\n"}


class SaveError(Exception):
    """Writing the file failed. ``str(error)`` and ``os_message`` carry the OS message."""

    def __init__(self, path: Path, os_message: str) -> None:
        super().__init__(os_message)
        self.path = path
        self.os_message = os_message


def render_for_save(text: str, line_ending: LineEnding) -> bytes:
    """Return the file bytes: lines right-stripped, trailing blank lines dropped, one final line
    ending, UTF-8 without a BOM. Gap marker lines are kept verbatim."""
    separator = _SEPARATORS[LineEnding(line_ending)]
    if text.startswith("﻿"):
        text = text[1:]
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return (separator.join(lines) + separator).encode("utf-8")


def default_filename(language: str, now: datetime) -> str:
    """Return ``capture-YYYYMMDD-HHMMSS.<ext>`` for ``now`` in local time."""
    local = now.astimezone() if now.tzinfo is not None else now
    return f"capture-{local:%Y%m%d-%H%M%S}{extension_for(language)}"


def write_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same directory, then replace).

    Raises ``SaveError`` carrying the OS message on any OS failure; the target is left untouched.
    """
    path = Path(path)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent or Path(".")
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise SaveError(path, exc.strerror or str(exc)) from exc
    finally:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
