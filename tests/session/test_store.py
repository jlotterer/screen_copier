"""Tests for SessionStore (T011): atomic autosave, discovery, retention, resume."""

from __future__ import annotations

import ast
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from screen_copier.models import (
    CaptureMode,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    RecognizedLine,
    Session,
    SessionState,
    UnsupportedVersion,
    Usage,
)
from screen_copier.session import migrations
from screen_copier.session.store import (
    CorruptSession,
    SessionStore,
    UnsupportedSessionVersion,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "screen_copier" / "session"
START = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> SessionStore:
    return SessionStore(tmp_path, clock)


def _result(text: str) -> FrameResult:
    line = RecognizedLine(
        text=text, line_number=1, partial=False, cut_off_right=False, uncertain=False
    )
    return FrameResult(
        has_line_numbers=True, language_guess="python", word_wrap_detected=False, lines=[line]
    )


def _frame(seq: int, status: FrameStatus = FrameStatus.QUEUED, text: str = "x") -> Frame:
    return Frame(
        seq=seq,
        captured_at=START + timedelta(seconds=seq),
        mode=CaptureMode.AUTO,
        image_file=Frame.image_file_for(seq),
        quality=FrameQuality(sharpness=120.5, blurry=False),
        status=status,
        attempts=1 if status is FrameStatus.RECOGNIZED else 0,
        result=_result(text) if status is FrameStatus.RECOGNIZED else None,
    )


def _session_file(tmp_path: Path, session_id: str) -> Path:
    return tmp_path / "sessions" / session_id / "session.json"


# --------------------------------------------------------------------------------------------
# Create, save, load
# --------------------------------------------------------------------------------------------


def test_create_makes_folder_and_session_file(store: SessionStore, tmp_path: Path) -> None:
    session = store.create()
    folder = tmp_path / "sessions" / session.id
    assert (folder / "frames").is_dir()
    assert (folder / "session.json").is_file()
    assert session.created_at == START
    assert session.updated_at == START
    assert session.state is SessionState.CAPTURING
    assert store.load(session.id) == session


def test_round_trip_with_frames_results_and_edited_text(
    store: SessionStore, tmp_path: Path
) -> None:
    session = store.create()
    for seq in (1, 2, 3):
        store.save_frame_image(session, seq, f"jpeg-{seq}".encode())
        session.frames.append(_frame(seq, FrameStatus.RECOGNIZED, text=f"line {seq}"))
    session.edited_text = "edited\ntext\n"
    session.usage = Usage(input_tokens=100, output_tokens=50)
    session.state = SessionState.REVIEWING
    store.save(session)

    loaded = store.load(session.id)
    assert loaded == session
    frames_dir = tmp_path / "sessions" / session.id / "frames"
    for seq in (1, 2, 3):
        assert (frames_dir / f"{seq}.jpg").read_bytes() == f"jpeg-{seq}".encode()
    assert not list(frames_dir.glob("*.tmp"))
    assert not list(frames_dir.parent.glob("*.tmp"))


def test_save_writes_tmp_then_replaces(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = store.create()
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        calls.append((Path(src).name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.save(session)
    assert calls == [("session.json.tmp", "session.json")]


def test_failed_replace_keeps_previous_session(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = store.create()
    store.save_frame_image(session, 1, b"one")
    session.frames.append(_frame(1))
    store.save(session)
    before = _session_file(tmp_path, session.id).read_text(encoding="utf-8")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("simulated crash during replace")

    changed = session.model_copy(deep=True)
    changed.edited_text = "new text"
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(changed)
    monkeypatch.undo()

    assert _session_file(tmp_path, session.id).read_text(encoding="utf-8") == before
    assert store.load(session.id) == session
    assert not (_session_file(tmp_path, session.id).with_name("session.json.tmp")).exists()


def test_failed_frame_image_write_keeps_previous_image(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = store.create()
    store.save_frame_image(session, 1, b"old")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save_frame_image(session, 1, b"new")
    path = tmp_path / "sessions" / session.id / "frames" / "1.jpg"
    assert path.read_bytes() == b"old"
    assert not path.with_name("1.jpg.tmp").exists()


def test_save_frame_image_rejects_bad_seq(store: SessionStore) -> None:
    session = store.create()
    with pytest.raises(ValueError):
        store.save_frame_image(session, 0, b"x")


def test_load_missing_session_raises_file_not_found(store: SessionStore) -> None:
    with pytest.raises(FileNotFoundError):
        store.load("2b1f3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")


@pytest.mark.parametrize(
    "bad_id", ["..", "../x", "not-a-uuid", "ABCDEF00-0000-4000-8000-000000000000"]
)
def test_session_ids_are_validated(store: SessionStore, bad_id: str) -> None:
    with pytest.raises(ValueError):
        store.load(bad_id)
    with pytest.raises(ValueError):
        store.discard(bad_id)


# --------------------------------------------------------------------------------------------
# Corruption and versions
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"[1, 2, 3]",
        b'{"schema_version": 1, "id": "nope"}',
        b"\xff\xfe\x00garbage",
        b'{"id": "x"}',
    ],
    ids=["invalid-json", "not-object", "validation-error", "not-utf8", "no-version"],
)
def test_corrupt_file_is_renamed_and_raises(
    store: SessionStore, tmp_path: Path, clock: FakeClock, content: bytes
) -> None:
    session = store.create()
    path = _session_file(tmp_path, session.id)
    path.write_bytes(content)
    clock.now = datetime(2026, 11, 10, 8, 30, 15, tzinfo=UTC)

    with pytest.raises(CorruptSession) as info:
        store.load(session.id)

    expected = path.with_name("session.corrupt-20261110T083015Z.json")
    assert not path.exists()
    assert expected.read_bytes() == content
    assert info.value.session_id == session.id
    assert info.value.renamed_to == expected


def test_corrupt_rename_timestamp_is_utc_and_unique(
    store: SessionStore, tmp_path: Path, clock: FakeClock
) -> None:
    from datetime import timezone

    session = store.create()
    path = _session_file(tmp_path, session.id)
    clock.now = datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    for _ in range(2):
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(CorruptSession):
            store.load(session.id)
    names = sorted(p.name for p in path.parent.glob("session.corrupt-*.json"))
    assert names == [
        "session.corrupt-20261110T080000Z-1.json",
        "session.corrupt-20261110T080000Z.json",
    ]


def test_id_mismatch_is_corrupt(store: SessionStore, tmp_path: Path) -> None:
    a = store.create()
    b = store.create()
    _session_file(tmp_path, a.id).write_text(b.model_dump_json(), encoding="utf-8")
    with pytest.raises(CorruptSession):
        store.load(a.id)


def test_newer_schema_version_raises_unsupported(store: SessionStore, tmp_path: Path) -> None:
    session = store.create()
    path = _session_file(tmp_path, session.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(UnsupportedVersion) as info:
        store.load(session.id)
    assert isinstance(info.value, UnsupportedSessionVersion)
    assert info.value.found == 99
    assert info.value.session_id == session.id
    assert path.exists()  # not treated as corrupt


def test_load_migrates_and_rewrites(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = store.create()
    path = _session_file(tmp_path, session.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 0
    data["old_text"] = data.pop("edited_text")
    data["old_text"] = "migrated"
    path.write_text(json.dumps(data), encoding="utf-8")

    def v0_to_v1(d: dict[str, Any]) -> dict[str, Any]:
        d = dict(d)
        d["edited_text"] = d.pop("old_text")
        return d

    monkeypatch.setitem(migrations.MIGRATIONS, 0, v0_to_v1)
    loaded = store.load(session.id)
    assert loaded.edited_text == "migrated"
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten["schema_version"] == 1
    assert rewritten["edited_text"] == "migrated"
    assert "old_text" not in rewritten


def test_old_version_without_migration_is_corrupt(store: SessionStore, tmp_path: Path) -> None:
    session = store.create()
    path = _session_file(tmp_path, session.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorruptSession):
        store.load(session.id)


# --------------------------------------------------------------------------------------------
# Discovery, discard, retention, resume
# --------------------------------------------------------------------------------------------


def _mark_saved(store: SessionStore, session: Session, saved_at: datetime) -> None:
    session.state = SessionState.SAVED
    session.saved_at = saved_at
    session.updated_at = saved_at
    store.save(session)


def test_find_unfinished_returns_newest_unsaved(store: SessionStore, clock: FakeClock) -> None:
    older = store.create()
    clock.advance(hours=1)
    newer = store.create()
    newer.updated_at = clock()
    store.save(newer)
    clock.advance(hours=1)
    saved = store.create()
    _mark_saved(store, saved, clock())

    assert store.find_unfinished() == newer
    assert older.id != newer.id


def test_find_unfinished_uses_updated_at(store: SessionStore, clock: FakeClock) -> None:
    first = store.create()
    clock.advance(hours=1)
    store.create()
    clock.advance(hours=1)
    first.updated_at = clock()
    store.save(first)
    assert store.find_unfinished() == first


def test_find_unfinished_none(store: SessionStore, clock: FakeClock) -> None:
    assert store.find_unfinished() is None
    saved = store.create()
    _mark_saved(store, saved, clock())
    assert store.find_unfinished() is None


def test_find_unfinished_skips_corrupt_and_stray_entries(
    store: SessionStore, tmp_path: Path, clock: FakeClock
) -> None:
    good = store.create()
    clock.advance(hours=1)
    bad = store.create()
    _session_file(tmp_path, bad.id).write_text("{bad", encoding="utf-8")
    (tmp_path / "sessions" / "not-a-session").mkdir()
    (tmp_path / "sessions" / "stray.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sessions" / "0b1f3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d").mkdir()  # no session.json

    assert store.find_unfinished() == good
    assert list((tmp_path / "sessions" / bad.id).glob("session.corrupt-*.json"))


def test_find_unfinished_raises_for_newer_unsaved_but_skips_newer_saved(
    store: SessionStore, tmp_path: Path, clock: FakeClock
) -> None:
    session = store.create()
    path = _session_file(tmp_path, session.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnsupportedSessionVersion) as info:
        store.find_unfinished()
    assert info.value.session_id == session.id

    data["state"] = "saved"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.find_unfinished() is None


def test_find_unfinished_on_missing_root(tmp_path: Path, clock: FakeClock) -> None:
    assert SessionStore(tmp_path / "nowhere", clock).find_unfinished() is None


def test_discard_deletes_folder(store: SessionStore, tmp_path: Path) -> None:
    session = store.create()
    store.save_frame_image(session, 1, b"x")
    folder = tmp_path / "sessions" / session.id
    assert folder.is_dir()
    store.discard(session.id)
    assert not folder.exists()
    store.discard(session.id)  # idempotent


def test_cleanup_saved_respects_retention(
    store: SessionStore, tmp_path: Path, clock: FakeClock
) -> None:
    old_saved = store.create()
    _mark_saved(store, old_saved, START)
    recent_saved = store.create()
    _mark_saved(store, recent_saved, START + timedelta(days=2))
    old_unsaved = store.create()
    old_unsaved.created_at = old_unsaved.updated_at = START - timedelta(days=22)
    store.save(old_unsaved)

    clock.now = START + timedelta(days=8)
    store.cleanup_saved(retention_days=7)

    sessions = tmp_path / "sessions"
    assert not (sessions / old_saved.id).exists()
    assert (sessions / recent_saved.id).exists()
    assert (sessions / old_unsaved.id).exists()


def test_cleanup_saved_skips_unreadable(
    store: SessionStore, tmp_path: Path, clock: FakeClock
) -> None:
    bad = store.create()
    _session_file(tmp_path, bad.id).write_text("{bad", encoding="utf-8")
    clock.advance(days=100)
    store.cleanup_saved(retention_days=7)
    assert (tmp_path / "sessions" / bad.id).exists()


def test_prepare_resume_requeues_recognizing_only(store: SessionStore) -> None:
    session = store.create()
    session.frames = [
        _frame(1, FrameStatus.RECOGNIZING),
        _frame(2, FrameStatus.QUEUED),
        _frame(3, FrameStatus.RECOGNIZED),
        _frame(4, FrameStatus.FAILED),
        _frame(5, FrameStatus.DISCARDED),
    ]
    original = session.model_copy(deep=True)

    resumed = store.prepare_resume(session)

    assert [f.status for f in resumed.frames] == [
        FrameStatus.QUEUED,
        FrameStatus.QUEUED,
        FrameStatus.RECOGNIZED,
        FrameStatus.FAILED,
        FrameStatus.DISCARDED,
    ]
    assert resumed.frames[1:] == original.frames[1:]
    assert session == original  # input untouched


def test_crash_and_resume_keeps_frames_and_text(tmp_path: Path, clock: FakeClock) -> None:
    store = SessionStore(tmp_path, clock)
    session = store.create()
    for seq in (1, 2, 3):
        store.save_frame_image(session, seq, b"jpeg")
        session.frames.append(_frame(seq, FrameStatus.RECOGNIZING))
        store.save(session)
    session.edited_text = "text"
    store.save(session)

    restarted = SessionStore(tmp_path, clock)  # simulates a new process
    found = restarted.find_unfinished()
    assert found is not None
    resumed = restarted.prepare_resume(found)
    assert [f.seq for f in resumed.frames] == [1, 2, 3]
    assert all(f.status is FrameStatus.QUEUED for f in resumed.frames)
    assert resumed.edited_text == "text"


# --------------------------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_session_package_does_not_import_pyside6() -> None:
    modules = sorted(SRC.glob("*.py"))
    assert {p.name for p in modules} >= {"__init__.py", "store.py", "migrations.py"}
    for path in modules:
        assert not any(m.split(".")[0] == "PySide6" for m in _imported_modules(path)), path
