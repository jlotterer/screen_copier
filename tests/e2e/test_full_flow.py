"""End-to-end tests of the whole app (T019): startup flow, capture, review, save, resume, errors.

The real ``start_app`` builds the real controller, camera worker (on the fake camera), recognition
queue (on the fake recognizer) and main window. Only ``QFileDialog`` and the blocking resume
dialog's ``exec`` are replaced. ``tmp_path`` is the data dir.

The fake recognizer folder is ``tests/fixtures/stitching/seq_basic/frames``: the five frames
whose stitch is ``expected.txt``. (``tests/fixtures/recognizer/seq_basic`` holds a different,
three-frame sequence from T007 that cannot produce that file.)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from screen_copier.app import AppContext, start_app
from screen_copier.config.logging import LOG_FILE_NAME
from screen_copier.config.settings import Settings, save_settings
from screen_copier.models import FrameStatus, Session, SessionState
from screen_copier.session.store import SessionStore
from screen_copier.stitching.stitcher import render
from screen_copier.ui.dialogs import ResumeDialog
from screen_copier.ui.main_window import Mode

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAMERA_BASIC = FIXTURES / "camera" / "basic"
SEQ_BASIC = FIXTURES / "stitching" / "seq_basic"
TIMEOUT_MS = 20_000
MAX_GAP_S = 0.100  # REQ-NFR-01
PROBE_INTERVAL_MS = 10


# -- helpers -----------------------------------------------------------------------------------


class LatencyProbe:
    """A 10 ms ``QTimer``; records the largest gap between ticks (event-loop latency)."""

    def __init__(self) -> None:
        self.timer = QTimer()
        self.timer.setInterval(PROBE_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self.last: float | None = None
        self.max_gap = 0.0
        self.ticks = 0

    def start(self) -> None:
        self.last = time.perf_counter()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.last is not None:
            self.max_gap = max(self.max_gap, now - self.last)
        self.last = now
        self.ticks += 1


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SCREEN_COPIER_DATA_DIR", str(data))
    monkeypatch.setenv("SCREEN_COPIER_FAKE_CAMERA", str(CAMERA_BASIC))
    monkeypatch.setenv("SCREEN_COPIER_FAKE_RECOGNIZER", str(SEQ_BASIC / "frames"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Manual capture so only Space captures; no audio device in CI.
    save_settings(Settings(capture_mode="manual", sound_enabled=False), data)
    return data


@pytest.fixture
def launch(qtbot) -> Iterator[Callable[..., AppContext]]:  # type: ignore[no-untyped-def]
    contexts: list[AppContext] = []

    def run(**kwargs: Any) -> AppContext:
        context = start_app(**kwargs)
        contexts.append(context)
        qtbot.waitExposed(context.window)
        context.window.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is context.window, timeout=TIMEOUT_MS)
        return context

    yield run
    for context in contexts:
        context.close()


def no_resume(*_: Any) -> str | None:
    raise AssertionError("no resume dialog expected")


def press(qtbot, context: AppContext, key: Qt.Key, modifier=Qt.KeyboardModifier.NoModifier):  # type: ignore[no-untyped-def]
    target = QApplication.focusWidget() or context.window
    qtbot.keyClick(target, key, modifier)


def wait_preview(qtbot, context: AppContext) -> None:  # type: ignore[no-untyped-def]
    qtbot.waitUntil(lambda: context.window.capture_view.state == "preview", timeout=TIMEOUT_MS)


def frames(context: AppContext) -> list[Any]:
    session = context.controller.session
    return [] if session is None else list(session.frames)


def capture(qtbot, context: AppContext, count: int) -> None:  # type: ignore[no-untyped-def]
    start = len(frames(context))
    for i in range(count):
        press(qtbot, context, Qt.Key.Key_Space)
        expected = start + i + 1
        qtbot.waitUntil(lambda n=expected: len(frames(context)) >= n, timeout=TIMEOUT_MS)
        qtbot.waitUntil(
            lambda n=expected: context.window.frame_strip.frame_model.rowCount() >= n,
            timeout=TIMEOUT_MS,
        )


def all_recognized(context: AppContext, n: int) -> bool:
    fs = frames(context)
    return len(fs) == n and all(f.status is FrameStatus.RECOGNIZED for f in fs)


def accept_consent(qtbot, context: AppContext) -> None:  # type: ignore[no-untyped-def]
    window = context.window
    qtbot.waitUntil(
        lambda: window.consent_dialog is not None and window.consent_dialog.isVisible(),
        timeout=TIMEOUT_MS,
    )
    window.consent_dialog.accept_button.click()
    qtbot.waitUntil(lambda: not window.consent_dialog.isVisible(), timeout=TIMEOUT_MS)
    context.window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is context.window, timeout=TIMEOUT_MS)


# -- the full flow -----------------------------------------------------------------------------


def test_full_flow_writes_expected_file_without_freezing(  # type: ignore[no-untyped-def]
    qtbot, launch, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out" / "inventory.py"
    out.parent.mkdir()
    dialog_calls: list[Any] = []

    def fake_dialog(*args: Any, **kwargs: Any) -> tuple[str, str]:
        dialog_calls.append(args)
        return str(out), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))

    context = launch(ask=no_resume)
    window = context.window
    probe = LatencyProbe()
    probe.start()

    wait_preview(qtbot, context)
    assert window.mode is Mode.CAPTURE

    # The first capture is held until consent is acknowledged.
    capture(qtbot, context, 1)
    accept_consent(qtbot, context)
    assert context.settings.consent_acknowledged is True
    capture(qtbot, context, 4)
    qtbot.waitUntil(lambda: all_recognized(context, 5), timeout=TIMEOUT_MS)
    assert [f.seq for f in frames(context)] == [1, 2, 3, 4, 5]

    press(qtbot, context, Qt.Key.Key_Tab)
    assert window.mode is Mode.REVIEW
    assert window.stack.currentWidget() is window.review_view

    with qtbot.waitSignal(window.review_view.saved, timeout=TIMEOUT_MS):
        press(qtbot, context, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    probe.stop()

    assert len(dialog_calls) == 1
    expected = (SEQ_BASIC / "expected.txt").read_bytes()
    assert out.read_bytes() == expected

    # The session is marked saved, so it is not offered for resume next time.
    session = context.controller.session
    assert session.state is SessionState.SAVED
    assert session.output_path == str(out)

    # REQ-NFR-01: the event loop never stalled for more than 100 ms during the whole run.
    assert probe.ticks >= 10
    assert probe.max_gap <= MAX_GAP_S, f"event loop stalled for {probe.max_gap * 1000:.0f} ms"


# -- resume (REQ-SES-04) -----------------------------------------------------------------------


def test_second_instance_resumes_frames_text_and_recognizes_queued_frame(  # type: ignore[no-untyped-def]
    qtbot, launch, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_settings(
        Settings(capture_mode="manual", sound_enabled=False, consent_acknowledged=True), data_dir
    )

    # First instance: frames 1 and 2 recognized, frame 3 still queued when the app goes away.
    first = launch(ask=no_resume)
    wait_preview(qtbot, first)
    capture(qtbot, first, 2)
    qtbot.waitUntil(lambda: all_recognized(first, 2), timeout=TIMEOUT_MS)
    first.controller.queue.pause()
    capture(qtbot, first, 1)
    session_id = first.controller.session.id
    first.close()

    stored = SessionStore(data_dir, lambda: datetime.now(UTC)).load(session_id)
    assert [f.status for f in stored.frames] == [
        FrameStatus.RECOGNIZED,
        FrameStatus.RECOGNIZED,
        FrameStatus.QUEUED,
    ]
    first_text = render(_stitch(stored))

    # Second instance: the resume dialog appears; choose Resume.
    shown: list[ResumeDialog] = []

    def exec_and_resume(self: ResumeDialog) -> int:
        shown.append(self)
        assert self.resume_button is not None
        self.resume_button.click()
        return int(self.result())

    monkeypatch.setattr(ResumeDialog, "exec", exec_and_resume)
    second = launch()
    assert len(shown) == 1
    assert "3 frames" in shown[0].message_label.text()
    assert second.resumed_id == session_id

    window = second.window
    qtbot.waitUntil(
        lambda: (
            second.controller.session is not None and second.controller.session.id == session_id
        ),
        timeout=TIMEOUT_MS,
    )
    # Frames and text restored; the queued frame is recognized.
    qtbot.waitUntil(lambda: window.frame_strip.frame_model.rowCount() == 3, timeout=TIMEOUT_MS)
    qtbot.waitUntil(lambda: all_recognized(second, 3), timeout=TIMEOUT_MS)
    assert [f.seq for f in window.frame_strip.frame_model.frames()] == [1, 2, 3]
    text = window.review_view.editor.toPlainText()
    restored = second.controller.session.frames
    assert [f.result for f in restored[:2]] == [f.result for f in stored.frames[:2]]
    assert text.startswith(first_text.rstrip("\n"))
    assert text == render(_stitch(second.controller.session)).rstrip("\n")
    assert len(text.splitlines()) > len(first_text.splitlines())


def _stitch(session: Session) -> Any:
    from screen_copier.stitching.stitcher import Stitcher

    stitcher = Stitcher(Settings())
    for frame in session.frames:
        if frame.result is not None:
            stitcher.add(frame.seq, frame.result)
    return stitcher.document()


def test_resume_restores_review_mode(  # type: ignore[no-untyped-def]
    qtbot, launch, data_dir: Path
) -> None:
    store = SessionStore(data_dir, lambda: datetime.now(UTC))
    session = store.create()
    session.state = SessionState.REVIEWING
    session.edited_text = "print('edited')\n"
    store.save(session)

    context = launch(ask=lambda *_: "resume")
    window = context.window
    assert window.mode is Mode.REVIEW
    qtbot.waitUntil(
        lambda: window.review_view.editor.toPlainText() == "print('edited')\n",
        timeout=TIMEOUT_MS,
    )


def test_discard_choice_deletes_unfinished_session(  # type: ignore[no-untyped-def]
    launch, data_dir: Path
) -> None:
    store = SessionStore(data_dir, lambda: datetime.now(UTC))
    session = store.create()
    context = launch(ask=lambda *_: "discard")
    assert context.resumed_id is None
    assert not store.session_dir(session.id).exists()


def test_newer_version_session_offers_discard_only(  # type: ignore[no-untyped-def]
    launch, data_dir: Path
) -> None:
    store = SessionStore(data_dir, lambda: datetime.now(UTC))
    session = store.create()
    path = store.session_dir(session.id) / "session.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 999
    path.write_text(json.dumps(raw), encoding="utf-8")

    calls: list[tuple[Any, bool]] = []

    def ask(found: Session | None, unsupported: bool, _settings: Settings) -> str:
        calls.append((found, unsupported))
        return "discard"

    context = launch(ask=ask)
    assert calls == [(None, True)]
    assert context.resumed_id is None
    assert not store.session_dir(session.id).exists()


def test_corrupt_session_tells_user_and_starts_fresh(  # type: ignore[no-untyped-def]
    launch, data_dir: Path
) -> None:
    store = SessionStore(data_dir, lambda: datetime.now(UTC))
    session = store.create()
    (store.session_dir(session.id) / "session.json").write_text("{not json", encoding="utf-8")
    told: list[str] = []
    context = launch(ask=no_resume, notify=lambda _title, text: told.append(text))
    assert len(told) == 1 and "damaged" in told[0]
    assert context.resumed_id is None


# -- retention (REQ-SES-05) --------------------------------------------------------------------


def test_old_saved_session_is_deleted_before_the_window_shows(  # type: ignore[no-untyped-def]
    launch, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    store = SessionStore(data_dir, lambda: now)
    folders = {}
    for days in (8, 6):
        session = store.create()
        session.state = SessionState.SAVED
        session.saved_at = now - timedelta(days=days)
        store.save(session)
        folders[days] = store.session_dir(session.id)
    settings = Settings(capture_mode="manual", sound_enabled=False, session_retention_days=7)
    save_settings(settings, data_dir)

    from screen_copier.ui.main_window import MainWindow

    seen_at_show: dict[int, bool] = {}
    original_show = MainWindow.show

    def show(self: MainWindow) -> None:
        seen_at_show.update({days: folder.exists() for days, folder in folders.items()})
        original_show(self)

    monkeypatch.setattr(MainWindow, "show", show)
    launch(ask=no_resume, clock=lambda: now)
    assert seen_at_show == {8: False, 6: True}


# -- unexpected errors (REQ-APP-07) ------------------------------------------------------------


@pytest.mark.qt_no_exception_capture
def test_exception_in_slot_shows_dialog_logs_traceback_and_keeps_session(  # type: ignore[no-untyped-def]
    qtbot, launch, data_dir: Path
) -> None:
    save_settings(
        Settings(capture_mode="manual", sound_enabled=False, consent_acknowledged=True), data_dir
    )
    context = launch(ask=no_resume)
    wait_preview(qtbot, context)
    capture(qtbot, context, 1)
    qtbot.waitUntil(lambda: all_recognized(context, 1), timeout=TIMEOUT_MS)
    session_id = context.controller.session.id

    def broken_slot() -> None:
        raise RuntimeError("boom in a slot sk-ant-secret123")

    context.window.toolbar.actions()[0].triggered.connect(broken_slot)
    context.window.mode_action.trigger()  # runs toggle_mode, then the broken slot

    box = context.reporter.last_box
    assert box is not None
    qtbot.waitUntil(box.isVisible, timeout=TIMEOUT_MS)
    assert "boom in a slot" in box.text()
    assert "sk-ant-secret123" not in box.text() + box.detailedText()

    context.log_handler.flush()
    log_text = (data_dir / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Traceback" in log_text
    assert "RuntimeError: boom in a slot" in log_text
    assert "broken_slot" in log_text
    assert "sk-ant-secret123" not in log_text

    # The app keeps running and the session still loads.
    assert context.window.isVisible()
    context.controller.wait_for_io()
    loaded = SessionStore(data_dir, lambda: datetime.now(UTC)).load(session_id)
    assert [f.seq for f in loaded.frames] == [1]
