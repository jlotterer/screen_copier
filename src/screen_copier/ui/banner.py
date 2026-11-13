"""Live warning banner plus capture and gap sounds (REQ-APP-05, REQ-CAM-09).

``Banner`` keeps one entry per warning ``kind``. ``warning(kind, message, True)`` shows or updates
the entry, ``warning(kind, message, False)`` hides it, and each entry has a dismiss button. A
dismissed warning comes back the next time the controller raises it.

Sounds go through a ``SoundPlayer``. The default ``QtSoundPlayer`` plays the two bundled WAV files
with ``QSoundEffect``; it imports QtMultimedia lazily and becomes silent if that fails (for
example on a machine without an audio backend). Tests inject a fake player or effect factory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from screen_copier.config.settings import Settings

log = logging.getLogger(__name__)

CAPTURE_SOUND = "capture"
GAP_SOUND = "gap"
SOUND_FILES: dict[str, str] = {CAPTURE_SOUND: "capture.wav", GAP_SOUND: "gap.wav"}

#: Warnings that play the distinct gap sound when raised (REQ-APP-05).
SOUND_FOR_WARNING: dict[str, str] = {"gap": GAP_SOUND}

WARNING_TITLES: dict[str, str] = {
    "gap": "Gap",
    "ambiguous_overlap": "Ambiguous overlap",
    "cut_off": "Cut-off lines",
    "word_wrap": "Word wrap",
    "offline": "Offline",
    "blurry": "Blurry",
    "never_settles": "Never settles",
    "low_resolution": "Low resolution",
}


class SoundPlayer(Protocol):
    """Plays a named sound (``capture`` or ``gap``)."""

    def play(self, name: str) -> None: ...


def asset_path(file_name: str) -> Path:
    """Filesystem path of a bundled asset (package data under ``screen_copier/assets``)."""
    with as_file(files("screen_copier") / "assets" / file_name) as path:
        return Path(path)


def _default_effect_factory() -> Any:
    from PySide6.QtMultimedia import QSoundEffect  # lazy: may be missing an audio backend

    return QSoundEffect()


class QtSoundPlayer:
    """Plays the bundled WAVs through ``QSoundEffect`` when ``settings.sound_enabled`` is true.

    ``sound_enabled`` is read at play time, so changing the setting takes effect at once. Effects
    are created on first use; ``effect_factory`` returns an object with ``setSource(QUrl)`` and
    ``play()`` (a ``QSoundEffect`` in production, a mock in tests).
    """

    def __init__(self, settings: Settings, effect_factory: Callable[[], Any] | None = None) -> None:
        self._settings = settings
        self._factory = effect_factory or _default_effect_factory
        self._effects: dict[str, Any] = {}
        self._broken = False

    def _effect(self, name: str) -> Any | None:
        if name in self._effects:
            return self._effects[name]
        if self._broken or name not in SOUND_FILES:
            return None
        try:
            effect = self._factory()
            effect.setSource(QUrl.fromLocalFile(str(asset_path(SOUND_FILES[name]))))
        except Exception:  # no QtMultimedia or audio backend: stay silent
            log.warning("Sound is unavailable; continuing without it", exc_info=True)
            self._broken = True
            return None
        self._effects[name] = effect
        return effect

    def play(self, name: str) -> None:
        if not self._settings.sound_enabled:
            return
        effect = self._effect(name)
        if effect is not None:
            effect.play()


class _Entry(QFrame):
    dismissed = Signal(str)

    def __init__(self, kind: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName(f"warning_{kind}")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #fff4ce; border: 1px solid #c19c00; border-radius: 3px; }"
            "QLabel, QToolButton { border: none; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        self.title_label = QLabel(f"<b>{WARNING_TITLES.get(kind, kind)}</b>")
        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.dismiss_button = QToolButton()
        self.dismiss_button.setText("✕")
        self.dismiss_button.setToolTip("Dismiss")
        self.dismiss_button.setAccessibleName(f"Dismiss {WARNING_TITLES.get(kind, kind)} warning")
        self.dismiss_button.clicked.connect(lambda: self.dismissed.emit(self.kind))
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.dismiss_button)

    @property
    def message(self) -> str:
        return self.message_label.text()

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


class Banner(QWidget):
    """Stack of live warnings, one entry per kind, each visible until resolved or dismissed."""

    def __init__(self, sound_player: SoundPlayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sound = sound_player
        self._entries: dict[str, _Entry] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.setVisible(False)

    # -- controller-facing ------------------------------------------------------------------
    def bind(self, controller: QObject) -> None:
        controller.warning.connect(self.on_warning)  # type: ignore[attr-defined]

    def on_warning(self, kind: str, message: str, active: bool) -> None:
        entry = self._entries.get(kind)
        if not active:
            if entry is not None:
                entry.setVisible(False)
            self._update_visibility()
            return
        if entry is None:
            entry = _Entry(kind, message, self)
            entry.dismissed.connect(self.dismiss)
            self._entries[kind] = entry
            self._layout.addWidget(entry)
        else:
            entry.set_message(message)
        entry.setVisible(True)
        self._update_visibility()
        sound = SOUND_FOR_WARNING.get(kind)
        if sound is not None:
            self._sound.play(sound)

    def play_capture(self) -> None:
        """Audible capture feedback (REQ-CAM-09); silent when sound is off."""
        self._sound.play(CAPTURE_SOUND)

    def dismiss(self, kind: str) -> None:
        entry = self._entries.get(kind)
        if entry is not None:
            entry.setVisible(False)
        self._update_visibility()

    # -- queries (used by tests and the main window) ----------------------------------------
    def entry(self, kind: str) -> _Entry | None:
        return self._entries.get(kind)

    def is_shown(self, kind: str) -> bool:
        entry = self._entries.get(kind)
        return entry is not None and not entry.isHidden()

    def message(self, kind: str) -> str | None:
        return self._entries[kind].message if self.is_shown(kind) else None

    def active_kinds(self) -> list[str]:
        return [kind for kind in self._entries if self.is_shown(kind)]

    def _update_visibility(self) -> None:
        self.setVisible(bool(self.active_kinds()))
