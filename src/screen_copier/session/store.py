"""Session persistence: atomic autosave, discovery, retention and resume (architecture §10.3).

Layout: ``<data_dir>/sessions/<id>/session.json`` and ``<data_dir>/sessions/<id>/frames/<seq>.jpg``.
Frame images are written before the ``session.json`` that references them (REQ-NFR-07). Every
write goes to ``<name>.tmp`` in the same folder, is fsynced, then ``os.replace``d over the target,
so a crash leaves either the old file or the new one.

Pure logic: must not import PySide6. All methods do disk I/O, so callers in the UI must run them
off the GUI thread.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from screen_copier.config.paths import SESSIONS_DIR_NAME, atomic_write_text
from screen_copier.models import (
    Frame,
    FrameStatus,
    Session,
    SessionState,
    UnsupportedVersion,
)
from screen_copier.session.migrations import MigrationError, migrate, schema_version_of

log = logging.getLogger(__name__)

SESSION_FILE = "session.json"
FRAMES_DIR = "frames"
CORRUPT_PREFIX = "session.corrupt-"


class CorruptSession(Exception):
    """``session.json`` could not be parsed or validated. It has been renamed aside."""

    def __init__(self, session_id: str, renamed_to: Path | None, reason: str) -> None:
        self.session_id = session_id
        self.renamed_to = renamed_to
        self.reason = reason
        super().__init__(f"Session {session_id} is corrupt: {reason}")


class UnsupportedSessionVersion(UnsupportedVersion):
    """``UnsupportedVersion`` that also names the session, so the caller can offer discard."""

    def __init__(self, session_id: str, found: int, supported: int) -> None:
        super().__init__(found, supported)
        self.session_id = session_id


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _is_session_id(name: str) -> bool:
    try:
        parsed = uuid.UUID(name)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == name


class SessionStore:
    """Reads and writes sessions under ``<data_dir>/sessions/``.

    ``clock`` returns the current time as a timezone-aware UTC datetime; real time is never read.
    """

    def __init__(self, data_dir: Path, clock: Callable[[], datetime]) -> None:
        self._root = Path(data_dir) / SESSIONS_DIR_NAME
        self._clock = clock

    # ---------------------------------------------------------------------------------------
    # Paths
    # ---------------------------------------------------------------------------------------

    @property
    def sessions_dir(self) -> Path:
        return self._root

    def session_dir(self, session_id: str) -> Path:
        """Folder of a session. Rejects anything that is not a canonical uuid4 (no traversal)."""
        if not _is_session_id(session_id):
            raise ValueError(f"not a session id: {session_id!r}")
        return self._root / session_id

    def _session_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / SESSION_FILE

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now

    # ---------------------------------------------------------------------------------------
    # Create and save
    # ---------------------------------------------------------------------------------------

    def create(self) -> Session:
        """Create a new empty session, its folder and its first ``session.json``."""
        now = self._now()
        session = Session(created_at=now, updated_at=now)
        (self.session_dir(session.id) / FRAMES_DIR).mkdir(parents=True, exist_ok=True)
        self.save(session)
        return session

    def save_frame_image(self, session: Session, seq: int, jpeg: bytes) -> None:
        """Write ``frames/<seq>.jpg`` atomically, before saving the session that refers to it."""
        if seq < 1:
            raise ValueError(f"seq must be >= 1, got {seq}")
        path = self.session_dir(session.id) / Frame.image_file_for(seq)
        _atomic_write_bytes(path, jpeg)

    def save(self, session: Session) -> None:
        """Write ``session.json`` atomically (``session.json.tmp``, fsync, ``os.replace``)."""
        atomic_write_text(self._session_file(session.id), session.model_dump_json(indent=2))

    # ---------------------------------------------------------------------------------------
    # Load
    # ---------------------------------------------------------------------------------------

    def load(self, session_id: str) -> Session:
        """Load a session, applying migrations (and rewriting the file if any ran).

        Raises ``CorruptSession`` (after renaming the file aside) for invalid JSON or data,
        ``UnsupportedSessionVersion`` (an ``UnsupportedVersion``) for a newer schema, and
        ``FileNotFoundError`` if the session has no ``session.json``.
        """
        path = self._session_file(session_id)
        content = path.read_bytes()
        try:
            raw = json.loads(content.decode("utf-8"))
            if not isinstance(raw, dict):
                raise MigrationError("session.json must contain a JSON object")
            original_version = schema_version_of(raw)
            data = migrate(raw)
            session = Session.model_validate(data)
        except UnsupportedVersion as exc:
            raise UnsupportedSessionVersion(session_id, exc.found, exc.supported) from exc
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            # JSONDecodeError, UnicodeDecodeError and MigrationError are ValueErrors; KeyError and
            # TypeError come from a migration step meeting malformed data.
            renamed = self._quarantine(path)
            log.warning("Session %s is corrupt (%s); moved to %s", session_id, exc, renamed)
            raise CorruptSession(session_id, renamed, str(exc)) from exc
        if session.id != session_id:
            renamed = self._quarantine(path)
            reason = f"id {session.id!r} does not match its folder"
            raise CorruptSession(session_id, renamed, reason)
        if original_version != session.schema_version:
            log.info(
                "Migrated session %s from schema %d to %d",
                session_id,
                original_version,
                session.schema_version,
            )
            self.save(session)
        return session

    def _quarantine(self, path: Path) -> Path | None:
        base = self._now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = path.with_name(f"{CORRUPT_PREFIX}{base}.json")
        counter = 1
        while target.exists():
            target = path.with_name(f"{CORRUPT_PREFIX}{base}-{counter}.json")
            counter += 1
        try:
            os.replace(path, target)
        except OSError:
            log.exception("Could not rename corrupt session file %s", path)
            return None
        return target

    # ---------------------------------------------------------------------------------------
    # Discovery, discard, retention, resume
    # ---------------------------------------------------------------------------------------

    def _stored_ids(self) -> Iterator[str]:
        if not self._root.is_dir():
            return
        for child in sorted(self._root.iterdir()):
            if child.is_dir() and _is_session_id(child.name) and (child / SESSION_FILE).is_file():
                yield child.name

    def _raw_state(self, session_id: str) -> Any:
        try:
            raw = json.loads(self._session_file(session_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw.get("state") if isinstance(raw, dict) else None

    def find_unfinished(self) -> Session | None:
        """Return the newest session whose state is not ``saved`` (by ``updated_at``), or None.

        Corrupt sessions are renamed aside and skipped. A session from a newer app version that
        is not marked saved raises ``UnsupportedSessionVersion`` so the caller can tell the user
        and offer discard only (REQ-SES-07).
        """
        unfinished: list[Session] = []
        for session_id in self._stored_ids():
            try:
                session = self.load(session_id)
            except CorruptSession:
                continue
            except UnsupportedVersion:
                if self._raw_state(session_id) == SessionState.SAVED.value:
                    continue
                raise
            except OSError:
                log.exception("Could not read session %s", session_id)
                continue
            if session.state is not SessionState.SAVED:
                unfinished.append(session)
        if not unfinished:
            return None
        return max(unfinished, key=lambda s: (s.updated_at, s.created_at))

    def discard(self, session_id: str) -> None:
        """Delete the session folder immediately. A missing folder is not an error."""
        folder = self.session_dir(session_id)
        if folder.exists():
            shutil.rmtree(folder)

    def cleanup_saved(self, retention_days: int) -> None:
        """Delete ``saved`` sessions whose ``saved_at`` is more than ``retention_days`` ago.

        Unsaved sessions are never deleted here. Unreadable sessions are skipped.
        """
        cutoff = self._now() - timedelta(days=retention_days)
        for session_id in self._stored_ids():
            try:
                session = self.load(session_id)
            except (CorruptSession, UnsupportedVersion, OSError):
                continue
            if session.state is not SessionState.SAVED:
                continue
            saved_at = session.saved_at or session.updated_at
            if saved_at < cutoff:
                log.info("Deleting saved session %s (saved %s)", session_id, saved_at)
                self.discard(session_id)

    def prepare_resume(self, session: Session) -> Session:
        """Return a copy in which ``recognizing`` frames are back to ``queued``.

        In-flight requests do not survive a restart, so they are re-queued. Other frames are
        unchanged. The input is not modified, and nothing is written to disk.
        """
        resumed = session.model_copy(deep=True)
        for frame in resumed.frames:
            if frame.status is FrameStatus.RECOGNIZING:
                frame.status = FrameStatus.QUEUED
        return resumed
