"""The main window: toolbar, stacked Capture/Review views, frame strip and shortcuts (§14).

The window composes the view widgets (T015 to T018) and owns the mode switch and every keyboard
shortcut of the UIStructure contract:

=============  =============================================
Space          Capture (Capture mode only)
A              Toggle Auto (Capture mode only)
Tab            Switch mode
Ctrl+S         Save
Delete         Discard the selected frame
Ctrl+N         New session
Ctrl+,         Settings
=============  =============================================

F8 / Shift+F8 (next / previous issue) are handled inside ``ReviewView``. The capture-only
shortcuts (Space, A, Delete) are disabled while a text editor (the ``CodeEditor``) has focus, so
typing never captures or discards anything (REQ-APP-04).

The window talks to the controller through its public signals and slots. Like ``ReviewView``
(which stores the language override there), it records the session's ``state``/``saved_at``/
``output_path`` on ``controller.session``; those reach disk with the controller's next save,
which the window requests through ``set_edited_text`` (the controller's debounced save).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QTextEdit,
    QToolBar,
    QWidget,
)

from screen_copier.config.settings import Settings, save_settings
from screen_copier.models import CaptureMode, Frame, FrameStatus, Session, SessionState
from screen_copier.session.store import SessionStore
from screen_copier.ui.banner import SoundPlayer
from screen_copier.ui.capture_view import CaptureView
from screen_copier.ui.dialogs import ConsentDialog, confirm_edit_loss
from screen_copier.ui.frame_strip import FrameStrip
from screen_copier.ui.review_view import ReviewView

log = logging.getLogger(__name__)

APP_TITLE = "Screen Copier"

SHORTCUT_CAPTURE = "Space"
SHORTCUT_AUTO = "A"
SHORTCUT_MODE = "Tab"
SHORTCUT_SAVE = "Ctrl+S"
SHORTCUT_DISCARD = "Delete"
SHORTCUT_NEW = "Ctrl+N"
SHORTCUT_SETTINGS = "Ctrl+,"

EDIT_LOSS_ACTION = "capture more frames"
NEW_SESSION_TITLE = "Start a new session?"
NEW_SESSION_TEXT = (
    "The current session has frames that have not been saved to a file. "
    "Starting a new session puts them aside. Continue?"
)


class Mode(StrEnum):
    CAPTURE = "capture"
    REVIEW = "review"


def confirm_new_session(parent: QWidget | None) -> bool:
    """Ask before Ctrl+N throws away a session with unsaved frames."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(NEW_SESSION_TITLE)
    box.setText(NEW_SESSION_TEXT)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_text_input(widget: QWidget | None) -> bool:
    return isinstance(widget, QPlainTextEdit | QTextEdit | QLineEdit)


SettingsDialogFactory = Callable[[QWidget], QDialog]


class MainWindow(QMainWindow):
    """One window with two modes, Capture and Review (REQ-APP-03)."""

    def __init__(
        self,
        controller: QObject,
        settings: Settings,
        store: SessionStore,
        *,
        settings_dialog_factory: SettingsDialogFactory | None = None,
        persist_settings: Callable[[Settings], None] = save_settings,
        sound_player: SoundPlayer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller: Any = controller
        self._settings = settings
        self._store = store
        self._settings_dialog_factory = settings_dialog_factory
        self._persist_settings = persist_settings
        self._clock = clock
        self._mode = Mode.CAPTURE
        self._session_id: str | None = None
        self._auth_pending = False
        self.consent_dialog: ConsentDialog | None = None
        self.settings_dialog: QDialog | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 800)

        # Connected before the frame strip is built, so a new session's folder is set before
        # the strip receives that session's first frame (Qt calls slots in connection order).
        self._controller.frame_added.connect(self._on_frame_added)
        self._controller.document_changed.connect(self._sync_session)

        self.capture_view = CaptureView(controller, settings, sound_player=sound_player)
        self.review_view = self._make_review_view()
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.capture_view)
        self.stack.addWidget(self.review_view)
        self.setCentralWidget(self.stack)

        self.frame_strip = FrameStrip(controller, None)  # type: ignore[arg-type]
        self.frame_strip.setAccessibleName("Captured frames")
        self.frame_dock = QDockWidget("Frames", self)
        self.frame_dock.setObjectName("frame_dock")
        self.frame_dock.setWidget(self.frame_strip)
        self.frame_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.frame_dock)

        self._build_actions()
        self._controller.consent_required.connect(self.show_consent)
        self._controller.auth_required.connect(self._on_auth_required)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.connect(self._on_focus_changed)
        self._sync_session()
        self._apply_mode()

    # ================================================================ construction
    def _make_review_view(self) -> ReviewView:
        view = ReviewView(self._controller, self._settings, frame_image_path=self._frame_image_path)
        view.saved.connect(self._on_saved)
        # The editor consumes Tab (it would insert a tab); route it to the mode switch instead.
        view.editor.installEventFilter(self)
        return view

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride)
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Tab
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and watched is self.review_view.editor
        ):
            if event.type() == QEvent.Type.KeyPress:
                self.toggle_mode()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _frame_image_path(self, seq: int) -> Path | None:
        session: Session | None = self._controller.session
        if session is None:
            return None
        return self._store.session_dir(session.id) / Frame.image_file_for(seq)

    def _action(
        self, text: str, shortcut: str, slot: Callable[..., Any], tip: str, *, checkable=False
    ) -> QAction:
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.setCheckable(checkable)
        action.setToolTip(f"{tip} ({QKeySequence(shortcut).toString()})")
        action.setStatusTip(tip)
        if checkable:
            action.toggled.connect(slot)
        else:
            action.triggered.connect(slot)
        self.addAction(action)  # shortcuts work even if the toolbar is hidden
        return action

    def _build_actions(self) -> None:
        self.mode_action = self._action(
            "Review", SHORTCUT_MODE, self.toggle_mode, "Switch between Capture and Review"
        )
        self.auto_action = self._action(
            "Auto", SHORTCUT_AUTO, self.set_auto, "Capture automatically", checkable=True
        )
        self.auto_action.blockSignals(True)
        self.auto_action.setChecked(self._settings.capture_mode is CaptureMode.AUTO)
        self.auto_action.blockSignals(False)
        self.capture_action = self._action(
            "Capture", SHORTCUT_CAPTURE, self.capture, "Capture a frame now"
        )
        self.save_action = self._action("Save", SHORTCUT_SAVE, self.save, "Save the text")
        self.settings_action = self._action(
            "Settings", SHORTCUT_SETTINGS, self.open_settings, "Open the settings"
        )
        self.new_action = self._action(
            "New session", SHORTCUT_NEW, self.new_session, "Start a new session"
        )
        self.discard_action = self._action(
            "Discard frame", SHORTCUT_DISCARD, self.discard_selected, "Discard the selected frame"
        )

        self.toolbar = QToolBar("Main toolbar", self)
        self.toolbar.setObjectName("main_toolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for action in (
            self.mode_action,
            self.auto_action,
            self.capture_action,
            self.save_action,
            self.settings_action,
            self.new_action,
        ):
            self.toolbar.addAction(action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)

    def toolbar_actions(self) -> list[QAction]:
        return [a for a in self.toolbar.actions() if not a.isSeparator()]

    # ================================================================ modes
    @property
    def mode(self) -> Mode:
        return self._mode

    def toggle_mode(self) -> None:
        self.set_mode(Mode.CAPTURE if self._mode is Mode.REVIEW else Mode.REVIEW)

    def set_mode(self, mode: Mode, *, confirm: bool = True) -> bool:
        """Switch mode. Leaving Review with edits asks first (REQ-REV-12); returns False if the
        user cancelled."""
        mode = Mode(mode)
        if mode is self._mode:
            return True
        session: Session | None = self._controller.session
        if mode is Mode.CAPTURE and session is not None and session.edited_text is not None:
            if confirm and not confirm_edit_loss(self, EDIT_LOSS_ACTION):
                return False
            self._discard_edits()
        self._mode = mode
        self._record_mode(session)
        self._apply_mode()
        return True

    def _discard_edits(self) -> None:
        """Drop the manual edits so the text follows the stitched document again."""
        self._controller.set_edited_text(None)
        # ReviewView keeps showing edited text once edited; a fresh view re-renders the document.
        old = self.review_view
        self.review_view = self._make_review_view()
        self.stack.insertWidget(self.stack.indexOf(old), self.review_view)
        self.stack.removeWidget(old)
        old.deleteLater()

    def _record_mode(self, session: Session | None) -> None:
        """Remember the mode on the session so a resume restores it (REQ-SES-04)."""
        if session is None or session.state is SessionState.SAVED:
            return
        state = SessionState.REVIEWING if self._mode is Mode.REVIEW else SessionState.CAPTURING
        if session.state is not state:
            session.state = state
            self._request_session_save(session)

    def _request_session_save(self, session: Session) -> None:
        # The controller's only save entry point for UI-side changes: set_edited_text saves the
        # session after its debounce (and on shutdown).
        self._controller.set_edited_text(session.edited_text)

    def _apply_mode(self) -> None:
        review = self._mode is Mode.REVIEW
        self.stack.setCurrentWidget(self.review_view if review else self.capture_view)
        self.mode_action.setText("Capture" if review else "Review")
        self.mode_action.setToolTip(
            f"Switch to {'Capture' if review else 'Review'} mode ({SHORTCUT_MODE})"
        )
        self.setWindowTitle(f"{APP_TITLE} — {'Review' if review else 'Capture'}")
        if review:
            self.review_view.editor.setFocus()
        else:
            self.capture_view.setFocus()
        self._update_actions()

    def _on_focus_changed(self, _old: QWidget | None, _new: QWidget | None) -> None:
        self._update_actions()

    def editor_has_focus(self) -> bool:
        focus = QApplication.focusWidget()
        if focus is None or focus.window() is not self:
            return False
        return _is_text_input(focus)

    def _update_actions(self) -> None:
        typing = self.editor_has_focus()
        capture_mode = self._mode is Mode.CAPTURE
        self.capture_action.setEnabled(capture_mode and not typing)
        self.auto_action.setEnabled(capture_mode and not typing)
        self.discard_action.setEnabled(not typing)

    # ================================================================ actions
    def capture(self) -> None:
        if self._mode is Mode.CAPTURE and not self.editor_has_focus():
            self._controller.capture_now()

    def set_auto(self, enabled: bool) -> None:
        self._controller.set_auto(enabled)
        self._settings.capture_mode = CaptureMode.AUTO if enabled else CaptureMode.MANUAL
        self.capture_view.set_auto_mode(enabled)
        try:
            self._persist_settings(self._settings)
        except OSError:
            log.exception("Could not save the capture mode")

    def save(self) -> None:
        if self._mode is not Mode.REVIEW and not self.set_mode(Mode.REVIEW):
            return
        self.review_view.save()

    def discard_selected(self) -> None:
        if self.editor_has_focus():
            return
        seq = self.frame_strip.selected_seq()
        if seq is not None:
            self._controller.discard_frame(seq)

    def has_unsaved_frames(self) -> bool:
        session: Session | None = self._controller.session
        if session is None or session.state is SessionState.SAVED:
            return False
        return any(f.status is not FrameStatus.DISCARDED for f in session.frames)

    def new_session(self) -> None:
        """Ctrl+N: confirm when the session has unsaved frames, then start fresh."""
        if self.has_unsaved_frames() and not confirm_new_session(self):
            return
        self._controller.new_session()
        self._mode = Mode.CAPTURE
        self._sync_session()
        self._apply_mode()

    def open_settings(self, auth_required: bool = False) -> QDialog | None:
        if self._settings_dialog_factory is None:
            return None
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            dialog = self.settings_dialog
        else:
            dialog = self._settings_dialog_factory(self)
            dialog.finished.connect(self._on_settings_finished)
            self.settings_dialog = dialog
        if auth_required and hasattr(dialog, "show_auth_required"):
            dialog.show_auth_required()
        else:
            dialog.open()
        return dialog

    def _on_settings_finished(self, result: int) -> None:
        """Resume a queue paused for a missing or rejected key once the user confirms."""
        if not self._auth_pending or result != QDialog.DialogCode.Accepted:
            return
        self._auth_pending = False
        queue = getattr(self._controller, "queue", None)
        if queue is not None:
            queue.resume()

    def _on_auth_required(self) -> None:
        self._auth_pending = True
        self.open_settings(auth_required=True)

    def show_consent(self) -> ConsentDialog:
        """Open the one-time consent notice (non-blocking) when the controller holds a frame."""
        if self.consent_dialog is not None and self.consent_dialog.isVisible():
            return self.consent_dialog
        dialog = ConsentDialog(
            self._controller, self._settings, save=self._persist_settings, parent=self
        )
        self.consent_dialog = dialog
        dialog.open()
        return dialog

    # ================================================================ controller signals
    def _on_frame_added(self, _frame: Frame) -> None:
        self._sync_session()
        session: Session | None = self._controller.session
        if session is not None and session.state is SessionState.SAVED:
            session.state = SessionState.CAPTURING  # more frames after a save: unfinished again

    def _sync_session(self, *_: object) -> None:
        session: Session | None = self._controller.session
        session_id = session.id if session is not None else None
        if session_id == self._session_id:
            return
        self._session_id = session_id
        folder = self._store.session_dir(session_id) if session_id is not None else None
        self.frame_strip.set_session_dir(folder)

    def _on_saved(self, path: str) -> None:
        session: Session | None = self._controller.session
        if session is None:
            return
        session.state = SessionState.SAVED
        session.saved_at = self._clock()
        session.output_path = path
        self._request_session_save(session)

    # ================================================================ lifecycle
    def closeEvent(self, event: QCloseEvent) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            with contextlib.suppress(RuntimeError, TypeError):
                app.focusChanged.disconnect(self._on_focus_changed)
        shutdown = getattr(self._controller, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)
