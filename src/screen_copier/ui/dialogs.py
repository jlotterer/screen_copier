"""Small dialogs shared by the views (architecture §14, REQ-SES-04, REQ-SES-06, REQ-SES-07).

* ``ConsentDialog``: the one-time notice that frames are sent to Anthropic's API.
* ``ResumeDialog``: offer to resume or discard an unfinished session, or only discard one saved by
  a newer app version.
* Helpers for the error dialog and the gap-save and edit-loss confirmations. Each has a
  ``make_*`` builder (used by tests and callers that want ``open()``) and a blocking wrapper.

When each dialog appears is decided by the main window (T019).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from screen_copier.config.logging import redact
from screen_copier.config.settings import Settings, save_settings
from screen_copier.models import FrameStatus, Session
from screen_copier.stitching.stitcher import Stitcher

log = logging.getLogger(__name__)


class ConsentController(Protocol):
    def acknowledge_consent(self) -> None: ...


# ===================================================================== consent (REQ-SES-06)
CONSENT_TITLE = "Images are sent to Anthropic"
CONSENT_TEXT = (
    "Screen Copier transcribes each captured frame with Anthropic's Claude API. "
    "The images of your screen are sent over the internet to Anthropic for processing.\n\n"
    "Only capture screens whose contents you are allowed to share with Anthropic. "
    "Nothing is sent until you acknowledge this notice, and you will only be asked once."
)


class ConsentDialog(QDialog):
    """One-time notice. Accepting remembers the consent and releases the held frames."""

    def __init__(
        self,
        controller: ConsentController,
        settings: Settings,
        *,
        save: Callable[[Settings], None] = save_settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._save = save
        self.setWindowTitle(CONSENT_TITLE)
        self.setModal(True)

        self.message_label = QLabel(CONSENT_TEXT)
        self.message_label.setWordWrap(True)

        self.buttons = QDialogButtonBox()
        self.accept_button = self.buttons.addButton(
            "I understand, send frames", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.later_button = self.buttons.addButton(
            "Not now", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.accept_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message_label)
        layout.addWidget(self.buttons)

    def accept(self) -> None:
        self._settings.consent_acknowledged = True
        try:
            self._save(self._settings)
        except OSError:
            log.exception("Could not save the consent acknowledgment")
        self._controller.acknowledge_consent()
        super().accept()


# ===================================================================== resume (REQ-SES-04/07)
class ResumeChoice(StrEnum):
    RESUME = "resume"
    DISCARD = "discard"


NEWER_VERSION_TEXT = (
    "An unfinished capture was saved by a newer version of Screen Copier, so this version "
    "can't open it. Update Screen Copier to keep it, or discard it."
)


def session_size(session: Session, settings: Any | None = None) -> tuple[int, int]:
    """Return ``(frames, lines)`` for an unfinished session.

    Frames excludes discarded ones. Lines are the edited text's lines once the user has edited,
    else the lines of the document stitched from the recognized frames (``settings`` supplies the
    stitcher's thresholds; defaults are used when it is ``None``).
    """
    frames = [f for f in session.frames if f.status is not FrameStatus.DISCARDED]
    if session.edited_text is not None:
        return len(frames), len(session.edited_text.splitlines())
    stitcher = Stitcher(settings if settings is not None else Settings())
    for frame in sorted(frames, key=lambda f: f.seq):
        if frame.status is FrameStatus.RECOGNIZED and frame.result is not None:
            stitcher.add(frame.seq, frame.result)
    lines = sum(len(segment.lines) for segment in stitcher.document().segments)
    return len(frames), lines


def resume_text(frames: int, lines: int) -> str:
    frame_word = "frame" if frames == 1 else "frames"
    line_word = "line" if lines == 1 else "lines"
    return f"Resume previous capture ({frames} {frame_word}, {lines} {line_word})?"


class ResumeDialog(QDialog):
    """Resume or discard an unfinished session; discard only for a newer-version session.

    After ``exec()``, ``choice`` is ``ResumeChoice.RESUME``, ``ResumeChoice.DISCARD``, or ``None``
    if the dialog was closed without choosing.
    """

    def __init__(
        self,
        session: Session | None,
        unsupported: bool,
        *,
        settings: Any | None = None,
        lines: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice: ResumeChoice | None = None
        self.unsupported = unsupported or session is None
        self.setWindowTitle("Unfinished capture")
        self.setModal(True)

        if self.unsupported:
            text = NEWER_VERSION_TEXT
        else:
            assert session is not None
            if lines is None:
                frames, lines = session_size(session, settings)
            else:
                frames = sum(1 for f in session.frames if f.status is not FrameStatus.DISCARDED)
            text = resume_text(frames, lines)
        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)

        self.buttons = QDialogButtonBox()
        self.resume_button: QPushButton | None = None
        if not self.unsupported:
            self.resume_button = self.buttons.addButton(
                "Resume", QDialogButtonBox.ButtonRole.AcceptRole
            )
            self.resume_button.clicked.connect(lambda: self._choose(ResumeChoice.RESUME))
            self.resume_button.setDefault(True)
        self.discard_button = self.buttons.addButton(
            "Discard", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self.discard_button.clicked.connect(lambda: self._choose(ResumeChoice.DISCARD))

        layout = QVBoxLayout(self)
        layout.addWidget(self.message_label)
        layout.addWidget(self.buttons)

    def visible_buttons(self) -> list[str]:
        return [button.text() for button in self.buttons.buttons()]

    def _choose(self, choice: ResumeChoice) -> None:
        self.choice = choice
        if choice is ResumeChoice.RESUME:
            self.accept()
        else:
            self.done(QDialog.DialogCode.Rejected)


# ===================================================================== message boxes
def make_error_box(
    parent: QWidget | None, title: str, message: str, details: str | None = None
) -> QMessageBox:
    """Build the error dialog. Text and details are redacted so a key can never be shown."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(redact(message))
    if details:
        box.setDetailedText(redact(details))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    return box


def show_error(
    parent: QWidget | None, title: str, message: str, details: str | None = None
) -> None:
    make_error_box(parent, title, message, details).exec()


def make_gap_save_box(parent: QWidget | None, gaps: int) -> QMessageBox:
    """Confirm saving a document that still has gaps (missing lines)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Save with gaps?")
    noun = "gap" if gaps == 1 else "gaps"
    box.setText(
        f"The document still has {gaps} {noun} where lines are missing. "
        "The gap markers will be saved in the file. Save anyway?"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box


def confirm_gap_save(parent: QWidget | None, gaps: int) -> bool:
    return make_gap_save_box(parent, gaps).exec() == QMessageBox.StandardButton.Save


def make_edit_loss_box(parent: QWidget | None, action: str = "continue") -> QMessageBox:
    """Confirm an action that throws away the user's manual edits."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Discard your edits?")
    box.setText(f"Your manual edits to the text will be lost if you {action}. Continue?")
    box.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box


def confirm_edit_loss(parent: QWidget | None, action: str = "continue") -> bool:
    return make_edit_loss_box(parent, action).exec() == QMessageBox.StandardButton.Discard
