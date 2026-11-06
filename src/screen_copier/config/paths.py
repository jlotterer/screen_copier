"""Data-dir resolution and atomic file writes (architecture §8.2, §10.3).

Pure logic: must not import PySide6.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import platformdirs

DATA_DIR_ENV = "SCREEN_COPIER_DATA_DIR"
APP_DIR_NAME = "ScreenCopier"
SESSIONS_DIR_NAME = "sessions"
LOGS_DIR_NAME = "logs"


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the app data folder, creating it if needed.

    ``SCREEN_COPIER_DATA_DIR`` wins when set and non-empty; otherwise the per-user platformdirs
    location is used.
    """
    environ = os.environ if env is None else env
    override = environ.get(DATA_DIR_ENV, "").strip()
    path = (
        Path(override).expanduser()
        if override
        else Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False))
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return ``<data_dir>/sessions``, creating it if needed."""
    path = data_dir(env) / SESSIONS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return ``<data_dir>/logs``, creating it if needed."""
    path = data_dir(env) / LOGS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically.

    Writes ``<name>.tmp`` in the same folder, fsyncs it, then ``os.replace``s it over the target,
    so readers see either the old file or the new one, never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
