"""Capture-mode screen: warning banner, live preview (or empty / camera-error state), stats bar.

The view talks only to the CaptureController's signals and slots (spec.md §Contracts). It is a
plain ``QWidget`` built with the controller and the ``Settings``; T019 places it in the main
window.

States: ``empty`` (no frame yet), ``preview`` (live frames) and ``error`` (``camera_error`` of
kind ``not_found``, ``unavailable`` or ``disconnected``). The error page offers "Open camera
privacy settings" (``ms-settings:privacy-webcam``) and Retry or Reconnect, which calls
``controller.select_camera`` with the configured camera. The next preview frame returns the view
to the preview state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from screen_copier.config.settings import Settings
from screen_copier.models import CaptureMode
from screen_copier.ui.banner import Banner, QtSoundPlayer, SoundPlayer
from screen_copier.ui.preview_widget import PreviewWidget

TIMES = "\u00d7"  # MULTIPLICATION SIGN, as in "1920x1080"
PRIVACY_SETTINGS_URL = "ms-settings:privacy-webcam"
PRIVACY_BUTTON_TEXT = "Open camera privacy settings"

ERROR_TITLES: dict[str, str] = {
    "not_found": "No camera found",
    "unavailable": "The camera is unavailable",
    "disconnected": "The camera was disconnected",
}

STATE_EMPTY = "empty"
STATE_PREVIEW = "preview"
STATE_ERROR = "error"


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class StatsBar(QWidget):
    """Frames, lines, gaps, issues, tokens, resolution and Auto/Manual, one label each."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.labels: dict[str, QLabel] = {}
        for key in ("frames", "lines", "gaps", "issues", "tokens", "resolution", "mode"):
            label = QLabel()
            label.setObjectName(f"stat_{key}")
            self.labels[key] = label
            layout.addWidget(label)
        layout.addStretch(1)
        self.set_stats({})
        self.set_resolution(None)
        self.set_auto(True)

    def set_stats(self, stats: Mapping[str, Any]) -> None:
        def value(key: str) -> int:
            return int(stats.get(key, 0))

        self.labels["frames"].setText(f"Frames: {value('frames')}")
        self.labels["lines"].setText(f"Lines: {value('lines')}")
        self.labels["gaps"].setText(f"Gaps: {value('gaps')}")
        self.labels["issues"].setText(f"Issues: {value('issues')}")
        self.labels["tokens"].setText(
            f"Tokens: {value('input_tokens')} in / {value('output_tokens')} out"
        )

    def set_resolution(self, size: tuple[int, int] | None) -> None:
        text = "—" if size is None else f"{size[0]}{TIMES}{size[1]}"
        self.labels["resolution"].setText(f"Camera: {text}")

    def set_auto(self, auto: bool) -> None:
        self.labels["mode"].setText("Auto" if auto else "Manual")

    def text(self) -> str:
        return "  ·  ".join(label.text() for label in self.labels.values())


class CameraErrorPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_label = QLabel()
        self.title_label.setObjectName("camera_error_title")
        self.title_label.setStyleSheet("font-size: 16pt; font-weight: bold;")
        self.message_label = QLabel()
        self.message_label.setObjectName("camera_error_message")
        self.message_label.setWordWrap(True)
        self.privacy_button = QPushButton(PRIVACY_BUTTON_TEXT)
        self.retry_button = QPushButton("Retry")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.privacy_button)
        buttons.addWidget(self.retry_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        for label in (self.title_label, self.message_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def show_error(self, kind: str, message: str) -> None:
        self.title_label.setText(ERROR_TITLES.get(kind, "Camera error"))
        self.message_label.setText(message)
        self.retry_button.setText("Reconnect" if kind == "disconnected" else "Retry")


class CaptureView(QWidget):
    """Banner, preview stack (empty / preview / error) and stats bar."""

    def __init__(
        self,
        controller: QObject,
        settings: Settings,
        *,
        sound_player: SoundPlayer | None = None,
        open_url: Callable[[str], None] = _open_url,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._open_url = open_url

        self.banner = Banner(sound_player or QtSoundPlayer(settings), self)
        self.banner.bind(controller)
        self.preview = PreviewWidget(controller, self)
        self.stats_bar = StatsBar(self)
        self.stats_bar.set_auto(settings.capture_mode is CaptureMode.AUTO)

        self.empty_label = QLabel("Starting the camera…")
        self.empty_label.setObjectName("empty_state")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_page = CameraErrorPage(self)
        self.error_page.privacy_button.clicked.connect(self.open_privacy_settings)
        self.error_page.retry_button.clicked.connect(self.retry_camera)

        self.stack = QStackedWidget(self)
        self._pages = {
            STATE_EMPTY: self.empty_label,
            STATE_PREVIEW: self.preview,
            STATE_ERROR: self.error_page,
        }
        for page in self._pages.values():
            self.stack.addWidget(page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.stats_bar)

        controller.preview_frame.connect(self._on_preview_frame)  # type: ignore[attr-defined]
        controller.frame_added.connect(self._on_frame_added)  # type: ignore[attr-defined]
        controller.document_changed.connect(self._on_document_changed)  # type: ignore[attr-defined]
        controller.resolution.connect(self._on_resolution)  # type: ignore[attr-defined]
        controller.camera_error.connect(self._on_camera_error)  # type: ignore[attr-defined]
        self._set_state(STATE_EMPTY)

    # -- state ------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        current = self.stack.currentWidget()
        return next(name for name, page in self._pages.items() if page is current)

    def _set_state(self, state: str) -> None:
        self.stack.setCurrentWidget(self._pages[state])

    def set_auto_mode(self, auto: bool) -> None:
        """Show Auto or Manual in the stats bar (the main window calls this on toggle)."""
        self.stats_bar.set_auto(auto)

    # -- actions ----------------------------------------------------------------------------
    def open_privacy_settings(self) -> None:
        self._open_url(PRIVACY_SETTINGS_URL)

    def retry_camera(self) -> None:
        self._set_state(STATE_EMPTY)
        self._controller.select_camera(self._settings.camera_id)  # type: ignore[attr-defined]

    # -- controller signals -----------------------------------------------------------------
    def _on_preview_frame(self, _image: Any, _quad: Any) -> None:
        if self.state != STATE_PREVIEW:
            self._set_state(STATE_PREVIEW)

    def _on_frame_added(self, _frame: Any) -> None:
        self.banner.play_capture()

    def _on_document_changed(self, _document: Any, stats: Mapping[str, Any]) -> None:
        self.stats_bar.set_stats(stats)

    def _on_resolution(self, width: int, height: int) -> None:
        self.stats_bar.set_resolution((width, height))

    def _on_camera_error(self, kind: str, message: str) -> None:
        self.error_page.show_error(kind, message)
        self._set_state(STATE_ERROR)
