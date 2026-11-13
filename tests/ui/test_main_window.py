"""Tests for the main window (T019): modes, toolbar, shortcuts, confirmations, dialogs.

The window runs against a stub controller: a ``QObject`` with the CaptureController's signals and
recording slots, holding a real ``Session``. Keys are sent with pytest-qt, so they go through Qt's
shortcut handling exactly like real key presses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QToolButton

from screen_copier.config.settings import Settings
from screen_copier.models import (
    CaptureMode,
    DocLine,
    Document,
    Frame,
    FrameQuality,
    FrameStatus,
    Observation,
    Segment,
    Session,
    SessionState,
)
from screen_copier.session.store import SessionStore
from screen_copier.ui import main_window as mw
from screen_copier.ui.main_window import MainWindow, Mode

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
TIMEOUT_MS = 5000


class SilentPlayer:
    def play(self, name: str) -> None:
        pass


class StubController(QObject):
    preview_frame = Signal(object, object)
    trigger_state = Signal(str)
    frame_added = Signal(object)
    frame_updated = Signal(object)
    document_changed = Signal(object, object)
    warning = Signal(str, str, bool)
    auth_required = Signal()
    camera_error = Signal(str, str)
    consent_required = Signal()
    resolution = Signal(int, int)

    def __init__(self, session: Session | None, document: Document | None = None) -> None:
        super().__init__()
        self._session = session
        self._document = document or Document()
        self.calls: list[tuple[str, Any]] = []
        self.queue = MagicMock(name="queue")

    @property
    def session(self) -> Session | None:
        return self._session

    def document(self) -> Document:
        return self._document

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    # slots
    def capture_now(self) -> None:
        self.calls.append(("capture_now", None))

    def set_auto(self, enabled: bool) -> None:
        self.calls.append(("set_auto", enabled))

    def discard_frame(self, seq: int) -> None:
        self.calls.append(("discard_frame", seq))

    def retry_frame(self, seq: int) -> None:
        self.calls.append(("retry_frame", seq))

    def rerecognize_frame(self, seq: int) -> None:
        self.calls.append(("rerecognize_frame", seq))

    def lock_region(self, quad: Any) -> None:
        self.calls.append(("lock_region", quad))

    def select_camera(self, camera_id: int) -> None:
        self.calls.append(("select_camera", camera_id))

    def acknowledge_consent(self) -> None:
        self.calls.append(("acknowledge_consent", None))

    def new_session(self) -> None:
        self.calls.append(("new_session", None))
        self._session = Session(created_at=NOW, updated_at=NOW)
        self._document = Document()
        self.document_changed.emit(self._document, {})

    def resume_session(self, session_id: str) -> None:
        self.calls.append(("resume_session", session_id))

    def set_edited_text(self, text: str | None) -> None:
        self.calls.append(("set_edited_text", text))
        if self._session is not None:
            self._session.edited_text = text

    def shutdown(self) -> None:
        self.calls.append(("shutdown", None))


def make_frame(seq: int, status: FrameStatus = FrameStatus.QUEUED) -> Frame:
    return Frame(
        seq=seq,
        captured_at=NOW,
        mode=CaptureMode.MANUAL,
        image_file=Frame.image_file_for(seq),
        quality=FrameQuality(sharpness=200.0, blurry=False),
        status=status,
    )


def make_document(*texts: str) -> Document:
    lines = [DocLine(text=t, observations=[Observation(frame_seq=1, text=t)]) for t in texts]
    return Document(segments=[Segment(lines=lines)])


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path, lambda: NOW)


@pytest.fixture
def session() -> Session:
    return Session(created_at=NOW, updated_at=NOW)


@pytest.fixture
def controller(session: Session) -> StubController:
    return StubController(session, make_document("def f():", "    return 1"))


@pytest.fixture
def settings() -> Settings:
    return Settings(capture_mode=CaptureMode.MANUAL, sound_enabled=False)


@pytest.fixture
def dialogs() -> list[MagicMock]:
    return []


@pytest.fixture
def window(qtbot, controller, settings, store, dialogs) -> MainWindow:  # type: ignore[no-untyped-def]
    def factory(parent: Any) -> QDialog:
        dialog = QDialog(parent)
        dialog.show_auth_required = MagicMock(name="show_auth_required")  # type: ignore[attr-defined]
        dialogs.append(dialog)  # type: ignore[arg-type]
        return dialog

    persisted: list[Settings] = []
    win = MainWindow(
        controller,
        settings,
        store,
        settings_dialog_factory=factory,
        persist_settings=persisted.append,
        sound_player=SilentPlayer(),
        clock=lambda: NOW,
    )
    win.persisted = persisted  # type: ignore[attr-defined]
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=TIMEOUT_MS)
    return win


def key(qtbot, window: MainWindow, k: Qt.Key, mod=Qt.KeyboardModifier.NoModifier) -> None:  # type: ignore[no-untyped-def]
    target = QApplication.focusWidget() or window
    qtbot.keyClick(target, k, mod)


# ===================================================================== layout


def test_window_has_toolbar_stack_and_bottom_dock(window: MainWindow) -> None:
    texts = [a.text() for a in window.toolbar_actions()]
    assert texts[:5] == ["Review", "Auto", "Capture", "Save", "Settings"]
    assert window.stack.indexOf(window.capture_view) >= 0
    assert window.stack.indexOf(window.review_view) >= 0
    assert window.dockWidgetArea(window.frame_dock) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert window.frame_dock.widget() is window.frame_strip
    assert window.mode is Mode.CAPTURE
    assert window.stack.currentWidget() is window.capture_view


# ===================================================================== modes (REQ-APP-03)


def test_tab_switches_to_review_and_back(qtbot, window: MainWindow) -> None:  # type: ignore[no-untyped-def]
    key(qtbot, window, Qt.Key.Key_Tab)
    assert window.mode is Mode.REVIEW
    assert window.stack.currentWidget() is window.review_view
    assert window.mode_action.text() == "Capture"

    key(qtbot, window, Qt.Key.Key_Tab)
    assert window.mode is Mode.CAPTURE
    assert window.stack.currentWidget() is window.capture_view
    assert window.mode_action.text() == "Review"


def test_mode_is_recorded_on_the_session(qtbot, window: MainWindow, session: Session) -> None:  # type: ignore[no-untyped-def]
    key(qtbot, window, Qt.Key.Key_Tab)
    assert session.state is SessionState.REVIEWING
    key(qtbot, window, Qt.Key.Key_Tab)
    assert session.state is SessionState.CAPTURING


# ===================================================================== shortcuts (REQ-APP-04)


def test_space_captures_in_capture_mode(qtbot, window: MainWindow, controller) -> None:  # type: ignore[no-untyped-def]
    key(qtbot, window, Qt.Key.Key_Space)
    assert controller.names().count("capture_now") == 1


def test_a_toggles_auto(qtbot, window: MainWindow, controller, settings) -> None:  # type: ignore[no-untyped-def]
    key(qtbot, window, Qt.Key.Key_A)
    assert ("set_auto", True) in controller.calls
    assert settings.capture_mode is CaptureMode.AUTO
    assert window.auto_action.isChecked()
    assert window.persisted[-1] is settings  # type: ignore[attr-defined]
    key(qtbot, window, Qt.Key.Key_A)
    assert controller.calls[-1] == ("set_auto", False)
    assert settings.capture_mode is CaptureMode.MANUAL


def test_space_in_review_editor_types_a_space_and_does_not_capture(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller
) -> None:
    key(qtbot, window, Qt.Key.Key_Tab)
    editor = window.review_view.editor
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus, timeout=TIMEOUT_MS)
    before = editor.toPlainText()

    key(qtbot, window, Qt.Key.Key_Space)
    key(qtbot, window, Qt.Key.Key_A)

    assert "capture_now" not in controller.names()
    assert "set_auto" not in controller.names()
    assert editor.toPlainText() == " a" + before
    assert not window.capture_action.isEnabled()
    assert not window.auto_action.isEnabled()
    assert not window.discard_action.isEnabled()


def test_delete_discards_the_selected_frame_but_not_while_typing(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, session: Session
) -> None:
    frame = make_frame(1)
    session.frames.append(frame)
    controller.frame_added.emit(frame)
    window.frame_strip.select_seq(1)
    assert window.frame_strip.selected_seq() == 1

    key(qtbot, window, Qt.Key.Key_Delete)
    assert controller.calls.count(("discard_frame", 1)) == 1

    key(qtbot, window, Qt.Key.Key_Tab)
    window.review_view.editor.setFocus()
    qtbot.waitUntil(window.review_view.editor.hasFocus, timeout=TIMEOUT_MS)
    key(qtbot, window, Qt.Key.Key_Delete)
    assert controller.calls.count(("discard_frame", 1)) == 1


def test_ctrl_s_saves_from_review(qtbot, window: MainWindow, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    saves: list[bool] = []
    monkeypatch.setattr(window.review_view, "save", lambda: saves.append(True))
    key(qtbot, window, Qt.Key.Key_Tab)
    key(qtbot, window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert saves == [True]


def test_ctrl_s_in_capture_switches_to_review_then_saves(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, monkeypatch
) -> None:
    saves: list[bool] = []
    monkeypatch.setattr(window.review_view, "save", lambda: saves.append(True))
    key(qtbot, window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert window.mode is Mode.REVIEW
    assert saves == [True]


def test_every_toolbar_action_has_a_keyboard_shortcut(window: MainWindow) -> None:
    """REQ-NFR-08: every toolbar action can be used from the keyboard."""
    actions = window.toolbar_actions()
    assert len(actions) >= 5
    for action in actions:
        assert not action.shortcut().isEmpty(), action.text()
        assert action in window.actions()  # active even when the toolbar is hidden
        shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        assert shortcut in action.toolTip()
    shortcuts = {a.shortcut().toString() for a in window.actions()}
    assert {"Space", "A", "Tab", "Ctrl+S", "Del", "Ctrl+N"} <= shortcuts


def test_toolbar_buttons_are_also_focusable(window: MainWindow) -> None:
    for action in window.toolbar_actions():
        button = window.toolbar.widgetForAction(action)
        assert isinstance(button, QToolButton)
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus or not action.shortcut().isEmpty()


def test_ctrl_comma_opens_settings(qtbot, window: MainWindow, dialogs) -> None:  # type: ignore[no-untyped-def]
    key(qtbot, window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert len(dialogs) == 1
    assert dialogs[0].isVisible()
    dialogs[0].reject()


# ===================================================================== edit-loss (REQ-REV-12)


def test_leaving_review_after_edits_asks_and_cancel_keeps_edits(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, session: Session, monkeypatch
) -> None:
    asked: list[str] = []
    answer = {"value": False}

    def confirm(parent: Any, action: str = "continue") -> bool:
        asked.append(action)
        return answer["value"]

    monkeypatch.setattr(mw, "confirm_edit_loss", confirm)

    key(qtbot, window, Qt.Key.Key_Tab)
    editor = window.review_view.editor
    editor.setFocus()
    qtbot.keyClicks(editor, "x = 1")
    edited = editor.toPlainText()
    assert session.edited_text == edited

    key(qtbot, window, Qt.Key.Key_Tab)
    assert asked == [mw.EDIT_LOSS_ACTION]
    assert window.mode is Mode.REVIEW
    assert window.stack.currentWidget() is window.review_view
    assert window.review_view.editor.toPlainText() == edited
    assert session.edited_text == edited

    answer["value"] = True
    key(qtbot, window, Qt.Key.Key_Tab)
    assert len(asked) == 2
    assert window.mode is Mode.CAPTURE
    assert ("set_edited_text", None) in controller.calls
    assert session.edited_text is None
    # Back in Review, the text follows the stitched document again.
    key(qtbot, window, Qt.Key.Key_Tab)
    assert window.review_view.editor.toPlainText() == "def f():\n    return 1"


def test_leaving_review_without_edits_does_not_ask(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, monkeypatch
) -> None:
    monkeypatch.setattr(mw, "confirm_edit_loss", MagicMock(side_effect=AssertionError))
    key(qtbot, window, Qt.Key.Key_Tab)
    key(qtbot, window, Qt.Key.Key_Tab)
    assert window.mode is Mode.CAPTURE


# ===================================================================== new session


def test_ctrl_n_with_unsaved_frames_cancel_keeps_session_confirm_calls_new_session(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, session: Session, monkeypatch
) -> None:
    session.frames.append(make_frame(1, FrameStatus.QUEUED))
    controller.frame_added.emit(session.frames[0])
    answers = [False, True]
    asked: list[bool] = []

    def confirm(parent: Any) -> bool:
        asked.append(True)
        return answers.pop(0)

    monkeypatch.setattr(mw, "confirm_new_session", confirm)

    key(qtbot, window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert len(asked) == 1
    assert "new_session" not in controller.names()
    assert controller.session is session
    assert [f.seq for f in session.frames] == [1]
    assert window.frame_strip.frame_model.rowCount() == 1

    key(qtbot, window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert len(asked) == 2
    assert controller.names().count("new_session") == 1
    assert controller.session is not session
    assert window.frame_strip.frame_model.rowCount() == 0
    assert window.mode is Mode.CAPTURE


def test_ctrl_n_without_frames_does_not_ask(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, monkeypatch
) -> None:
    monkeypatch.setattr(mw, "confirm_new_session", MagicMock(side_effect=AssertionError))
    key(qtbot, window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    assert controller.names().count("new_session") == 1


def test_confirm_new_session_dialog_defaults_to_cancel(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    seen: list[QMessageBox] = []

    def fake_exec(box: QMessageBox) -> int:
        seen.append(box)
        return int(QMessageBox.StandardButton.Cancel)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert mw.confirm_new_session(None) is False
    assert seen[0].defaultButton() is seen[0].button(QMessageBox.StandardButton.Cancel)


# ===================================================================== dialogs and session state


def test_consent_required_opens_consent_dialog(qtbot, window: MainWindow, controller) -> None:  # type: ignore[no-untyped-def]
    controller.consent_required.emit()
    dialog = window.consent_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.accept_button.click()
    assert ("acknowledge_consent", None) in controller.calls
    assert not dialog.isVisible()


def test_auth_required_opens_settings_and_resumes_queue_after_ok(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, dialogs
) -> None:
    controller.auth_required.emit()
    assert len(dialogs) == 1
    dialogs[0].show_auth_required.assert_called_once()
    controller.queue.resume.assert_not_called()
    dialogs[0].accept()
    controller.queue.resume.assert_called_once()


def test_auth_settings_cancelled_keeps_queue_paused(  # type: ignore[no-untyped-def]
    qtbot, window: MainWindow, controller, dialogs
) -> None:
    controller.auth_required.emit()
    dialogs[0].reject()
    controller.queue.resume.assert_not_called()


def test_successful_save_marks_session_saved(  # type: ignore[no-untyped-def]
    window: MainWindow, controller, session: Session
) -> None:
    window.review_view.saved.emit("/tmp/out.py")
    assert session.state is SessionState.SAVED
    assert session.saved_at == NOW
    assert session.output_path == "/tmp/out.py"
    assert controller.calls[-1][0] == "set_edited_text"  # requests a session save
    assert not window.has_unsaved_frames()


def test_frame_strip_follows_the_session_folder(  # type: ignore[no-untyped-def]
    window: MainWindow, controller, session: Session, store: SessionStore
) -> None:
    frame = make_frame(1)
    session.frames.append(frame)
    controller.frame_added.emit(frame)
    assert window.frame_strip._session_dir == store.session_dir(session.id)
    assert window.frame_strip.frame_model.rowCount() == 1


def test_close_shuts_the_controller_down(window: MainWindow, controller) -> None:  # type: ignore[no-untyped-def]
    window.close()
    assert ("shutdown", None) in controller.calls
