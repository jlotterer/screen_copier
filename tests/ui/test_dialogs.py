"""Settings, consent and resume dialogs (T018)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from screen_copier.camera.source import CameraInfo
from screen_copier.config.secrets import KEYRING_SERVICE, KEYRING_USERNAME
from screen_copier.config.settings import LineEnding, Settings
from screen_copier.models import (
    CaptureMode,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    RecognizedLine,
    Session,
)
from screen_copier.ui import dialogs
from screen_copier.ui.dialogs import (
    ConsentDialog,
    ResumeChoice,
    ResumeDialog,
    make_edit_loss_box,
    make_error_box,
    make_gap_save_box,
    session_size,
)
from screen_copier.ui.settings_dialog import (
    AUTH_REQUIRED_TEXT,
    KEY_REJECTED,
    KEY_UNREACHABLE,
    KEY_WORKS,
    SettingsDialog,
)

FULL_KEY = "sk-ant-xxxxabcd"
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
TIMEOUT_MS = 5000


# ============================================================================ fakes
class FakeKeyring:
    def __init__(self, stored: str | None = None, *, broken: bool = False) -> None:
        self.stored = stored
        self.broken = broken

    def get_password(self, service_name: str, username: str) -> str | None:
        if self.broken:
            raise NoKeyringError("no backend")
        assert (service_name, username) == (KEYRING_SERVICE, KEYRING_USERNAME)
        return self.stored

    def set_password(self, service_name: str, username: str, password: str) -> None:
        if self.broken:
            raise KeyringError("no backend")
        self.stored = password

    def delete_password(self, service_name: str, username: str) -> None:
        if self.broken:
            raise KeyringError("no backend")
        if self.stored is None:
            raise PasswordDeleteError("nothing stored")
        self.stored = None


class FakeMessages:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages = FakeMessages(error)


class StubController(QObject):
    auth_required = Signal()
    consent_required = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.selected: list[int] = []
        self.consents = 0

    def select_camera(self, camera_id: int) -> None:
        self.selected.append(camera_id)

    def acknowledge_consent(self) -> None:
        self.consents += 1


class Harness:
    def __init__(
        self,
        qtbot: Any,
        *,
        stored: str | None = FULL_KEY,
        env: dict[str, str] | None = None,
        broken_keyring: bool = False,
        client: FakeClient | None = None,
        cameras: list[CameraInfo] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.controller = StubController()
        self.settings = settings or Settings()
        self.keyring = FakeKeyring(stored, broken=broken_keyring)
        self.client = client or FakeClient()
        self.saved: list[Settings] = []
        self.camera_list = cameras if cameras is not None else [CameraInfo(0, "Integrated")]
        self.dialog = SettingsDialog(
            self.controller,
            self.settings,
            client_factory=lambda: self.client,
            keyring_backend=self.keyring,
            env=env or {},
            cameras=lambda: self.camera_list,
            save=lambda s: self.saved.append(s.model_copy(deep=True)),
        )
        qtbot.addWidget(self.dialog)
        self.qtbot = qtbot
        qtbot.waitUntil(lambda: self.dialog.camera_combo.currentText() != "", timeout=TIMEOUT_MS)


@pytest.fixture
def harness_factory(qtbot: Any):
    made: list[Harness] = []

    def make(**kwargs: Any) -> Harness:
        h = Harness(qtbot, **kwargs)
        made.append(h)
        return h

    yield make
    for h in made:
        h.dialog.wait_for_background()


def all_widget_texts(root: QWidget) -> list[str]:
    texts = [root.windowTitle()]
    for widget in root.findChildren(QWidget):
        if isinstance(widget, QLineEdit):
            texts += [widget.text(), widget.displayText(), widget.placeholderText()]
        elif isinstance(widget, (QLabel, QAbstractButton)):
            texts.append(widget.text())
        elif isinstance(widget, QComboBox):
            texts += [widget.itemText(i) for i in range(widget.count())]
        texts.append(widget.toolTip())
    return texts


def status_error(status: int) -> anthropic.APIStatusError:
    response = httpx.Response(status, request=REQUEST)
    cls = {401: anthropic.AuthenticationError, 403: anthropic.PermissionDeniedError}.get(
        status, anthropic.APIStatusError
    )
    return cls("error", response=response, body=None)


def press_test(h: Harness) -> str:
    with h.qtbot.waitSignal(h.dialog.test_finished, timeout=TIMEOUT_MS) as blocker:
        h.dialog.test_button.click()
    h.dialog.wait_for_background()
    return blocker.args[0]


# ============================================================================ API key
def test_stored_key_is_masked_and_never_shown_in_full(harness_factory: Any) -> None:
    h = harness_factory()
    assert h.dialog.key_field.text() == "••••abcd"
    assert h.dialog.key_state == "key-ok"
    assert not any(FULL_KEY in text for text in all_widget_texts(h.dialog))
    assert not any("xxxx" in text for text in all_widget_texts(h.dialog))


def test_entered_key_is_stored_and_input_cleared(harness_factory: Any) -> None:
    h = harness_factory(stored=None)
    assert h.dialog.key_state == "key-missing"
    assert h.dialog.key_field.text() == ""
    assert not h.dialog.clear_button.isEnabled()
    assert not h.dialog.test_button.isEnabled()

    h.dialog.key_input.setText("  sk-ant-new-key-9876  ")
    h.dialog.save_key_button.click()

    assert h.keyring.stored == "sk-ant-new-key-9876"
    assert h.dialog.key_input.text() == ""
    assert h.dialog.key_field.text() == "••••9876"
    assert h.dialog.key_state == "key-ok"
    assert not any("sk-ant-new" in text for text in all_widget_texts(h.dialog))


def test_clear_removes_the_stored_key(harness_factory: Any) -> None:
    h = harness_factory()
    assert h.dialog.clear_button.isEnabled()
    h.dialog.clear_button.click()
    assert h.keyring.stored is None
    assert h.dialog.key_state == "key-missing"
    assert h.dialog.key_field.text() == ""


def test_test_button_reports_key_works(harness_factory: Any) -> None:
    h = harness_factory()
    h.dialog.model_combo.setCurrentText("claude-haiku-5-5")
    assert press_test(h) == KEY_WORKS
    assert h.dialog.test_result_label.text() == "Key works"
    [call] = h.client.messages.calls
    assert call["max_tokens"] == 1
    assert call["model"] == "claude-haiku-5-5"
    assert call["messages"] == [{"role": "user", "content": "ping"}]


@pytest.mark.parametrize("status", [401, 403])
def test_test_button_reports_rejected_key(harness_factory: Any, status: int) -> None:
    h = harness_factory(client=FakeClient(status_error(status)))
    assert press_test(h) == KEY_REJECTED
    assert h.dialog.test_result_label.text() == "Key rejected"


def test_test_button_reports_connection_error(harness_factory: Any) -> None:
    h = harness_factory(client=FakeClient(anthropic.APIConnectionError(request=REQUEST)))
    assert press_test(h) == KEY_UNREACHABLE
    assert h.dialog.test_result_label.text() == "Could not reach the API"


def test_test_button_reports_other_http_errors(harness_factory: Any) -> None:
    h = harness_factory(client=FakeClient(status_error(500)))
    assert "HTTP 500" in press_test(h)


def test_keyring_unavailable_without_env_shows_guidance(harness_factory: Any) -> None:
    h = harness_factory(stored=None, broken_keyring=True)
    assert h.dialog.key_state == "keyring-unavailable"
    assert "ANTHROPIC_API_KEY" in h.dialog.key_status_label.text()
    assert not h.dialog.save_key_button.isEnabled()
    assert not h.dialog.key_input.isEnabled()
    assert not h.dialog.clear_button.isEnabled()


def test_key_from_environment_disables_clear(harness_factory: Any) -> None:
    h = harness_factory(env={"ANTHROPIC_API_KEY": "sk-ant-envkey-wxyz"})
    assert h.dialog.key_state == "environment"
    assert "environment" in h.dialog.key_status_label.text()
    assert "ANTHROPIC_API_KEY" in h.dialog.key_status_label.text()
    assert not h.dialog.clear_button.isEnabled()
    assert h.dialog.key_field.text() == "••••wxyz"
    assert not any("sk-ant-envkey" in text for text in all_widget_texts(h.dialog))


def test_auth_required_opens_with_explanation(harness_factory: Any) -> None:
    h = harness_factory(stored=None)
    assert not h.dialog.isVisible()
    h.controller.auth_required.connect(h.dialog.show_auth_required)
    h.controller.auth_required.emit()
    h.qtbot.waitUntil(h.dialog.isVisible, timeout=TIMEOUT_MS)
    assert h.dialog.auth_label.isVisible()
    text = h.dialog.auth_label.text()
    assert text == AUTH_REQUIRED_TEXT
    assert "missing" in text and "rejected" in text
    h.dialog.reject()


def test_auth_message_hidden_on_normal_open(harness_factory: Any) -> None:
    h = harness_factory()
    h.dialog.show()
    assert not h.dialog.auth_label.isVisible()
    h.dialog.reject()


# ============================================================================ cameras
def test_camera_picker_lists_cameras_and_preselects_camera_id(harness_factory: Any) -> None:
    cameras = [
        CameraInfo(0, "Integrated Webcam"),
        CameraInfo(1, "USB Camera"),
        CameraInfo(2, "OBS"),
    ]
    h = harness_factory(cameras=cameras, settings=Settings(camera_id=1))
    h.qtbot.waitUntil(lambda: h.dialog.camera_combo.count() == 3, timeout=TIMEOUT_MS)
    assert h.dialog.camera_names() == ["Integrated Webcam", "USB Camera", "OBS"]
    assert h.dialog.camera_combo.currentText() == "USB Camera"
    assert h.dialog.camera_combo.currentData() == 1


def test_camera_picker_keeps_missing_configured_camera(harness_factory: Any) -> None:
    h = harness_factory(cameras=[CameraInfo(0, "Integrated")], settings=Settings(camera_id=3))
    h.qtbot.waitUntil(lambda: h.dialog.camera_combo.count() == 2, timeout=TIMEOUT_MS)
    assert h.dialog.camera_combo.currentData() == 3


# ============================================================================ settings values
def test_ok_saves_changed_values(harness_factory: Any) -> None:
    cameras = [CameraInfo(0, "Integrated"), CameraInfo(1, "USB Camera")]
    h = harness_factory(cameras=cameras)
    h.qtbot.waitUntil(lambda: h.dialog.camera_combo.count() == 2, timeout=TIMEOUT_MS)
    d = h.dialog
    d.camera_combo.setCurrentIndex(d.camera_combo.findData(1))
    d.auto_exposure_check.setChecked(False)
    d.exposure_spin.setValue(-5.0)
    d.capture_mode_combo.setCurrentIndex(d.capture_mode_combo.findData(CaptureMode.MANUAL))
    d.stable_duration_spin.setValue(900)
    d.stability_spin.setValue(3.5)
    d.motion_spin.setValue(8.0)
    d.sharpness_spin.setValue(150.0)
    d.duplicate_spin.setValue(1.5)
    d.max_edge_spin.setValue(2000)
    d.model_combo.setCurrentText("claude-opus-5-5")
    d.concurrency_spin.setValue(5)
    d.min_overlap_spin.setValue(3)
    d.line_similarity_spin.setValue(0.85)
    d.line_ending_combo.setCurrentIndex(d.line_ending_combo.findData(LineEnding.CRLF))
    d.sound_check.setChecked(False)
    d.retention_spin.setValue(30)

    d.buttons.button(d.buttons.StandardButton.Ok).click()

    assert d.result() == QDialog.DialogCode.Accepted
    [saved] = h.saved
    assert saved.camera_id == 1
    assert saved.auto_exposure is False
    assert saved.exposure == -5.0
    assert saved.capture_mode is CaptureMode.MANUAL
    assert saved.stable_duration_ms == 900
    assert saved.stability_threshold == 3.5
    assert saved.motion_threshold == 8.0
    assert saved.sharpness_threshold == 150.0
    assert saved.duplicate_threshold == 1.5
    assert saved.max_image_edge_px == 2000
    assert saved.model == "claude-opus-5-5"
    assert saved.max_concurrent_requests == 5
    assert saved.min_overlap_lines == 3
    assert saved.line_similarity == pytest.approx(0.85)
    assert saved.line_ending is LineEnding.CRLF
    assert saved.sound_enabled is False
    assert saved.session_retention_days == 30
    # The shared settings object is updated in place and the camera restarts.
    assert h.settings.model == "claude-opus-5-5"
    assert h.controller.selected == [1]


def test_ok_without_camera_change_does_not_restart_camera(harness_factory: Any) -> None:
    h = harness_factory(settings=Settings(consent_acknowledged=True))
    h.dialog.wait_for_background()
    h.dialog.sound_check.setChecked(False)
    h.dialog.accept()
    [saved] = h.saved
    assert saved.sound_enabled is False
    assert saved.consent_acknowledged is True
    assert h.controller.selected == []


def test_exposure_change_restarts_camera(harness_factory: Any) -> None:
    h = harness_factory()
    h.dialog.wait_for_background()
    h.dialog.auto_exposure_check.setChecked(False)
    h.dialog.accept()
    assert h.controller.selected == [0]
    assert h.saved[0].auto_exposure is False


def test_cancel_saves_nothing(harness_factory: Any) -> None:
    h = harness_factory()
    h.dialog.concurrency_spin.setValue(9)
    h.dialog.reject()
    assert h.saved == []
    assert h.settings.max_concurrent_requests == 3


def test_save_failure_keeps_dialog_open(qtbot: Any) -> None:
    def failing_save(settings: Settings) -> None:
        raise OSError("disk full")

    dialog = SettingsDialog(
        StubController(),
        Settings(),
        client_factory=FakeClient,
        keyring_backend=FakeKeyring(None),
        env={},
        cameras=list,
        save=failing_save,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.accept()
    assert dialog.isVisible()
    assert "disk full" in dialog.error_label.text()
    dialog.wait_for_background()
    dialog.reject()


# ============================================================================ consent
def test_consent_accept_saves_and_acknowledges_once(qtbot: Any) -> None:
    controller = StubController()
    settings = Settings()
    saved: list[Settings] = []
    dialog = ConsentDialog(controller, settings, save=lambda s: saved.append(s.model_copy()))
    qtbot.addWidget(dialog)
    assert "Anthropic" in dialog.message_label.text()
    dialog.accept_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert settings.consent_acknowledged is True
    assert [s.consent_acknowledged for s in saved] == [True]
    assert controller.consents == 1


def test_consent_decline_does_nothing(qtbot: Any) -> None:
    controller = StubController()
    settings = Settings()
    saved: list[Settings] = []
    dialog = ConsentDialog(controller, settings, save=saved.append)
    qtbot.addWidget(dialog)
    dialog.later_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert settings.consent_acknowledged is False
    assert saved == []
    assert controller.consents == 0


# ============================================================================ resume
def make_session(frames: int, *, edited_text: str | None = None) -> Session:
    now = datetime(2026, 11, 1, tzinfo=UTC)

    def text(i: int) -> str:
        return f"key = '{hashlib.md5(str(i).encode()).hexdigest()}'"

    items = []
    for k in range(frames):
        start = k * 20
        lines = [
            RecognizedLine(
                text=text(i),
                line_number=None,
                partial=False,
                cut_off_right=False,
                uncertain=False,
            )
            for i in range(start, start + 32)
        ]
        items.append(
            Frame(
                seq=k + 1,
                captured_at=now,
                mode=CaptureMode.AUTO,
                image_file=Frame.image_file_for(k + 1),
                quality=FrameQuality(sharpness=150.0, blurry=False),
                status=FrameStatus.RECOGNIZED,
                result=FrameResult(has_line_numbers=False, word_wrap_detected=False, lines=lines),
            )
        )
    return Session(created_at=now, updated_at=now, frames=items, edited_text=edited_text)


def test_resume_dialog_shows_size_with_resume_and_discard(qtbot: Any) -> None:
    session = make_session(5)
    assert session_size(session) == (5, 112)
    dialog = ResumeDialog(session, False)
    qtbot.addWidget(dialog)
    assert dialog.message_label.text() == "Resume previous capture (5 frames, 112 lines)?"
    assert sorted(dialog.visible_buttons()) == ["Discard", "Resume"]
    assert dialog.resume_button is not None
    dialog.resume_button.click()
    assert dialog.choice is ResumeChoice.RESUME
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_resume_dialog_uses_edited_text_and_explicit_lines(qtbot: Any) -> None:
    edited = make_session(5, edited_text="\n".join(f"l{i}" for i in range(112)))
    assert session_size(edited) == (5, 112)
    dialog = ResumeDialog(make_session(5), False, lines=112)
    qtbot.addWidget(dialog)
    assert dialog.message_label.text() == "Resume previous capture (5 frames, 112 lines)?"


def test_resume_dialog_discard(qtbot: Any) -> None:
    dialog = ResumeDialog(make_session(2), False)
    qtbot.addWidget(dialog)
    dialog.discard_button.click()
    assert dialog.choice is ResumeChoice.DISCARD


def test_resume_dialog_newer_version_offers_only_discard(qtbot: Any) -> None:
    dialog = ResumeDialog(None, True)
    qtbot.addWidget(dialog)
    assert "newer version" in dialog.message_label.text()
    assert dialog.visible_buttons() == ["Discard"]
    assert dialog.resume_button is None
    dialog.discard_button.click()
    assert dialog.choice is ResumeChoice.DISCARD


# ============================================================================ helpers
def test_error_box_redacts_keys(qtbot: Any) -> None:
    box = make_error_box(None, "Error", f"Failed with {FULL_KEY}", details=f"trace {FULL_KEY}")
    qtbot.addWidget(box)
    assert FULL_KEY not in box.text()
    assert FULL_KEY not in box.detailedText()
    assert box.icon() is QMessageBox.Icon.Critical


def test_gap_save_and_edit_loss_boxes(qtbot: Any) -> None:
    gap = make_gap_save_box(None, 2)
    edit = make_edit_loss_box(None, "re-transcribe this frame")
    qtbot.addWidget(gap)
    qtbot.addWidget(edit)
    assert "2 gaps" in gap.text()
    assert gap.defaultButton() is gap.button(QMessageBox.StandardButton.Cancel)
    assert "re-transcribe this frame" in edit.text()
    assert edit.button(QMessageBox.StandardButton.Discard) is not None


def test_confirm_helpers_return_choice(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Save)
    assert dialogs.confirm_gap_save(None, 1) is True
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    assert dialogs.confirm_gap_save(None, 1) is False
    assert dialogs.confirm_edit_loss(None) is False
