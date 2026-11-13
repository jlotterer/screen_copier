"""Tests for the Capture-mode view (T015): preview, quad handles, banner, stats, sounds.

Everything runs against a stub controller: a ``QObject`` with the CaptureController's signals and
recording slots. No camera, no API, no disk (other than reading the bundled WAV paths).
"""

from __future__ import annotations

import wave
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from screen_copier.config.settings import Settings
from screen_copier.models import CaptureMode, Document
from screen_copier.ui.banner import CAPTURE_SOUND, GAP_SOUND, QtSoundPlayer, asset_path
from screen_copier.ui.capture_view import (
    PRIVACY_BUTTON_TEXT,
    PRIVACY_SETTINGS_URL,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_PREVIEW,
    CaptureView,
)
from screen_copier.ui.preview_widget import TRIGGER_LABELS

FRAME_W, FRAME_H = 640, 360


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

    def __init__(self) -> None:
        super().__init__()
        self.lock_calls: list[Any] = []
        self.select_calls: list[int] = []

    def lock_region(self, quad: Any) -> None:
        self.lock_calls.append(quad)

    def select_camera(self, camera_id: int) -> None:
        self.select_calls.append(camera_id)


class RecordingPlayer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.played: list[str] = []

    def play(self, name: str) -> None:
        if self.settings.sound_enabled:
            self.played.append(name)


def make_image() -> QImage:
    image = QImage(FRAME_W, FRAME_H, QImage.Format.Format_RGB888)
    image.fill(QColor("darkgray"))
    return image


@pytest.fixture
def controller() -> StubController:
    return StubController()


@pytest.fixture
def settings() -> Settings:
    return Settings(camera_id=2)


@pytest.fixture
def effects() -> dict[str, MagicMock]:
    return {}


@pytest.fixture
def view(qtbot, controller, settings, effects) -> CaptureView:  # type: ignore[no-untyped-def]
    """A view whose sound player uses mocked ``QSoundEffect`` objects keyed by WAV file name."""

    def factory() -> MagicMock:
        effect = MagicMock(name="QSoundEffect")

        def set_source(url: Any) -> None:
            effects[url.fileName()] = effect

        effect.setSource.side_effect = set_source
        return effect

    opened: list[str] = []
    widget = CaptureView(
        controller,
        settings,
        sound_player=QtSoundPlayer(settings, effect_factory=factory),
        open_url=opened.append,
    )
    widget.opened_urls = opened  # type: ignore[attr-defined]
    qtbot.addWidget(widget)
    widget.resize(900, 700)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def played(effects: dict[str, MagicMock], file_name: str) -> int:
    effect = effects.get(file_name)
    return 0 if effect is None else effect.play.call_count


# -- banner ------------------------------------------------------------------------------------


def test_gap_warning_shows_and_hides_banner_entry(view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.warning.emit("gap", "msg", True)
    assert view.banner.is_shown("gap")
    assert view.banner.message("gap") == "msg"
    assert view.banner.isVisible()

    controller.warning.emit("gap", "msg", False)
    assert not view.banner.is_shown("gap")
    assert view.banner.active_kinds() == []
    assert not view.banner.isVisible()


def test_banner_keeps_one_entry_per_kind_and_updates_message(view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.warning.emit("cut_off", "first", True)
    controller.warning.emit("offline", "offline msg", True)
    controller.warning.emit("cut_off", "second", True)
    assert view.banner.active_kinds() == ["cut_off", "offline"]
    assert view.banner.message("cut_off") == "second"


def test_banner_dismiss_button_hides_entry_until_raised_again(qtbot, view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.warning.emit("word_wrap", "wrap", True)
    entry = view.banner.entry("word_wrap")
    assert entry is not None
    qtbot.mouseClick(entry.dismiss_button, Qt.MouseButton.LeftButton)
    assert not view.banner.is_shown("word_wrap")
    controller.warning.emit("word_wrap", "wrap again", True)
    assert view.banner.message("word_wrap") == "wrap again"


# -- preview and quad handles ------------------------------------------------------------------


def test_drag_four_handles_then_lock_and_clear(qtbot, view, controller) -> None:  # type: ignore[no-untyped-def]
    detected = np.array([[50, 40], [590, 30], [600, 330], [40, 320]], dtype=np.float32)
    controller.preview_frame.emit(make_image(), detected)
    canvas = view.preview.canvas
    handles = canvas.handles()
    assert handles is not None
    np.testing.assert_allclose(handles, detected)

    targets = [QPoint(150, 120), QPoint(700, 110), QPoint(720, 450), QPoint(140, 470)]
    for corner, target in zip(handles, targets, strict=True):
        start = canvas.frame_to_widget(corner).toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(canvas, target)
        QTest.mouseRelease(
            canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target
        )

    expected = np.array([canvas.widget_to_frame(QPointF(t)) for t in targets], dtype=np.float32)
    assert not np.allclose(expected, detected, atol=5)
    assert canvas.edited

    # A new detection must not move the edited handles.
    controller.preview_frame.emit(make_image(), detected)

    qtbot.mouseClick(view.preview.lock_button, Qt.MouseButton.LeftButton)
    assert len(controller.lock_calls) == 1
    locked = np.asarray(controller.lock_calls[0])
    assert locked.shape == (4, 2)
    np.testing.assert_allclose(locked, expected, atol=1e-3)
    assert canvas.locked

    qtbot.mouseClick(view.preview.clear_button, Qt.MouseButton.LeftButton)
    assert len(controller.lock_calls) == 2
    assert controller.lock_calls[1] is None
    assert not canvas.locked


def test_handles_follow_detection_and_default_without_quad(view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.preview_frame.emit(make_image(), None)
    handles = view.preview.canvas.handles()
    assert handles is not None and handles.shape == (4, 2)
    assert np.all(handles[:, 0] >= 0) and np.all(handles[:, 0] < FRAME_W)
    assert np.all(handles[:, 1] >= 0) and np.all(handles[:, 1] < FRAME_H)
    # TL, TR, BR, BL order
    assert handles[0, 0] < handles[1, 0] and handles[3, 1] > handles[0, 1]


def test_lock_disabled_before_first_frame(view) -> None:  # type: ignore[no-untyped-def]
    assert not view.preview.lock_button.isEnabled()


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("SETTLING", "Settling…"),
        ("MOVING", "Moving…"),
        ("WAITING_FOCUS", "Waiting for focus"),
        ("CAPTURED", "Captured"),
    ],
)
def test_trigger_state_labels(view, controller, state: str, label: str) -> None:  # type: ignore[no-untyped-def]
    controller.trigger_state.emit(state)
    assert view.preview.state_label.text() == label
    assert TRIGGER_LABELS[state] == label


def test_frame_added_flashes_and_plays_capture_sound(qtbot, view, controller, effects) -> None:  # type: ignore[no-untyped-def]
    controller.preview_frame.emit(make_image(), None)
    controller.frame_added.emit(object())
    assert view.preview.flash_active
    assert played(effects, "capture.wav") == 1
    assert played(effects, "gap.wav") == 0
    qtbot.waitUntil(lambda: not view.preview.flash_active, timeout=2000)


# -- sounds ------------------------------------------------------------------------------------


def test_no_sound_when_disabled(view, controller, settings, effects) -> None:  # type: ignore[no-untyped-def]
    settings.sound_enabled = False
    controller.frame_added.emit(object())
    controller.warning.emit("gap", "gap!", True)
    assert played(effects, "capture.wav") == 0
    assert played(effects, "gap.wav") == 0
    assert view.preview.flash_active  # visual feedback still happens


def test_gap_warning_plays_gap_sound_not_capture_sound(view, controller, effects) -> None:  # type: ignore[no-untyped-def]
    controller.warning.emit("gap", "gap!", True)
    assert played(effects, "gap.wav") == 1
    assert played(effects, "capture.wav") == 0


def test_other_warnings_play_no_sound(view, controller, effects) -> None:  # type: ignore[no-untyped-def]
    controller.warning.emit("blurry", "blurry", True)
    assert played(effects, "gap.wav") == 0
    assert played(effects, "capture.wav") == 0


def test_injected_player_receives_named_sounds(qtbot, controller, settings) -> None:  # type: ignore[no-untyped-def]
    player = RecordingPlayer(settings)
    widget = CaptureView(controller, settings, sound_player=player, open_url=lambda _u: None)
    qtbot.addWidget(widget)
    controller.frame_added.emit(object())
    controller.warning.emit("gap", "gap!", True)
    assert player.played == [CAPTURE_SOUND, GAP_SOUND]


def test_sound_player_is_silent_when_multimedia_is_unavailable(settings) -> None:
    def broken() -> Any:
        raise ImportError("no QtMultimedia")

    player = QtSoundPlayer(settings, effect_factory=broken)
    player.play(GAP_SOUND)  # must not raise
    player.play(CAPTURE_SOUND)


@pytest.mark.parametrize("name", ["capture.wav", "gap.wav"])
def test_bundled_wavs_are_short_and_valid(name: str) -> None:
    path = asset_path(name)
    with wave.open(str(path), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
    assert 0 < duration < 1.0


# -- stats bar ---------------------------------------------------------------------------------


def test_document_changed_updates_stats(view, controller) -> None:  # type: ignore[no-untyped-def]
    stats = {
        "frames": 5,
        "lines": 112,
        "gaps": 1,
        "issues": 2,
        "input_tokens": 100,
        "output_tokens": 20,
    }
    controller.document_changed.emit(Document(), stats)
    labels = view.stats_bar.labels
    assert labels["frames"].text() == "Frames: 5"
    assert labels["lines"].text() == "Lines: 112"
    assert labels["gaps"].text() == "Gaps: 1"
    assert labels["issues"].text() == "Issues: 2"
    assert "100" in labels["tokens"].text() and "20" in labels["tokens"].text()


def test_resolution_shown(view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.resolution.emit(1920, 1080)
    assert "1920\u00d71080" in view.stats_bar.labels["resolution"].text()
    assert "1920\u00d71080" in view.stats_bar.text()


def test_auto_manual_shown(qtbot, controller) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(capture_mode=CaptureMode.MANUAL)
    widget = CaptureView(controller, settings, sound_player=RecordingPlayer(settings))
    qtbot.addWidget(widget)
    assert widget.stats_bar.labels["mode"].text() == "Manual"
    widget.set_auto_mode(True)
    assert widget.stats_bar.labels["mode"].text() == "Auto"


# -- states ------------------------------------------------------------------------------------


def test_camera_error_state_with_privacy_button(qtbot, view, controller, settings) -> None:  # type: ignore[no-untyped-def]
    assert view.state == STATE_EMPTY
    controller.camera_error.emit("unavailable", "msg")
    assert view.state == STATE_ERROR
    page = view.error_page
    assert page.isVisible()
    assert page.message_label.text() == "msg"
    assert page.privacy_button.text() == PRIVACY_BUTTON_TEXT == "Open camera privacy settings"
    qtbot.mouseClick(page.privacy_button, Qt.MouseButton.LeftButton)
    assert view.opened_urls == [PRIVACY_SETTINGS_URL] == ["ms-settings:privacy-webcam"]

    assert page.retry_button.text() == "Retry"
    qtbot.mouseClick(page.retry_button, Qt.MouseButton.LeftButton)
    assert controller.select_calls == [settings.camera_id]


def test_disconnected_offers_reconnect_and_preview_recovers(qtbot, view, controller) -> None:  # type: ignore[no-untyped-def]
    controller.camera_error.emit("disconnected", "gone")
    assert view.error_page.retry_button.text() == "Reconnect"
    controller.camera_error.emit("not_found", "none")
    assert "No camera" in view.error_page.title_label.text()
    controller.preview_frame.emit(make_image(), None)
    assert view.state == STATE_PREVIEW
    assert view.preview.isVisible()
