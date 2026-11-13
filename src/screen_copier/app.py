"""Application entry point: CLI parsing, the startup flow and the global error handler.

Startup order (architecture §14, §16): ``setup_logging()``; ``load_settings()``;
``SessionStore(data_dir(), clock)`` and ``cleanup_saved(session_retention_days)``;
``find_unfinished()`` → ``ResumeDialog``; create the controller (fake camera and recognizer when
the environment variables are set) and the ``MainWindow``; show it, start the camera and resume
the chosen session. ``sys.excepthook`` and Qt's message handler log every unexpected error and
show the error dialog; the session is saved continuously, so the app keeps running.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import TracebackType
from typing import Any

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget

from screen_copier.config.paths import data_dir, logs_dir
from screen_copier.config.secrets import get_api_key
from screen_copier.config.settings import Settings, load_settings, save_settings
from screen_copier.models import Session, SessionState
from screen_copier.recognition.base import Recognizer
from screen_copier.session.store import (
    CORRUPT_PREFIX,
    CorruptSession,
    SessionStore,
    UnsupportedSessionVersion,
)

APP_NAME = "Screen Copier"
DISTRIBUTION_NAME = "screen-copier"
FAKE_RECOGNIZER_ENV = "SCREEN_COPIER_FAKE_RECOGNIZER"

ERROR_TITLE = "Something went wrong"
ERROR_TEXT = (
    "An unexpected error occurred. Your session has been saved and the app keeps running. "
    "Details were written to the log file:\n{log}"
)
CORRUPT_TITLE = "A previous session could not be read"
CORRUPT_TEXT = (
    "A previous session is damaged and cannot be opened. It was set aside, and a new session "
    "will start."
)

log = logging.getLogger(__name__)

AskResume = Callable[[Session | None, bool, Settings], str | None]
"""Shows the resume dialog; returns ``"resume"``, ``"discard"`` or ``None`` (closed)."""


def get_version() -> str:
    """Return the installed package version."""
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
        return "0+unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=DISTRIBUTION_NAME, description=APP_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=get_version(),
        help="print the version and exit",
    )
    parser.add_argument(
        "--create-shortcut",
        action="store_true",
        help='create a "Screen Copier" desktop shortcut and exit',
    )
    return parser


def create_main_window() -> QMainWindow:
    """Create a bare, empty main window (kept for smoke tests). Requires a QApplication."""
    window = QMainWindow()
    window.setWindowTitle(APP_NAME)
    return window


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ===================================================================== error handling
class ErrorReporter:
    """Logs unexpected errors and shows the error dialog (REQ-APP-07).

    Installed as ``sys.excepthook`` (exceptions raised in Qt slots end up there) and as Qt's
    message handler. The dialog is opened non-blocking, so an error inside a nested event loop
    cannot stack modal loops. The previous excepthook is still called (it prints to stderr).
    """

    def __init__(
        self,
        parent: Callable[[], QWidget | None],
        log_file: Path | None,
        *,
        show_dialogs: bool = True,
    ) -> None:
        self._parent = parent
        self._log_file = log_file
        self._show_dialogs = show_dialogs
        self._previous_hook: Callable[..., Any] | None = None
        self._previous_qt_handler: Any = None
        self._installed = False
        self._in_report = False
        self.boxes: list[QMessageBox] = []

    @property
    def last_box(self) -> QMessageBox | None:
        return self.boxes[-1] if self.boxes else None

    def install(self) -> None:
        if self._installed:
            return
        self._previous_hook = sys.excepthook
        sys.excepthook = self.excepthook
        self._previous_qt_handler = qInstallMessageHandler(self.qt_message)
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        if sys.excepthook == self.excepthook:
            sys.excepthook = self._previous_hook or sys.__excepthook__
        qInstallMessageHandler(self._previous_qt_handler)
        self._installed = False

    def excepthook(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            (self._previous_hook or sys.__excepthook__)(exc_type, exc, tb)
            return
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        self.report(f"{exc_type.__name__}: {exc}", details)
        if self._previous_hook not in (None, sys.__excepthook__):
            self._previous_hook(exc_type, exc, tb)  # type: ignore[misc]

    def qt_message(self, msg_type: QtMsgType, context: QMessageLogContext, message: str) -> None:
        where = f" ({context.file}:{context.line})" if context.file else ""
        if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            log.error("Qt: %s%s", message, where)
            self.report(message, None)
        elif msg_type is QtMsgType.QtWarningMsg:
            log.warning("Qt: %s%s", message, where)
        else:
            log.debug("Qt: %s%s", message, where)

    def report(self, summary: str, details: str | None) -> QMessageBox | None:
        if not self._show_dialogs or self._in_report or QApplication.instance() is None:
            return None
        from screen_copier.ui.dialogs import make_error_box

        self._in_report = True
        try:
            text = f"{ERROR_TEXT.format(log=self._log_file or 'the log file')}\n\n{summary}"
            box = make_error_box(self._parent(), ERROR_TITLE, text, details)
            box.finished.connect(lambda _r, b=box: self._forget(b))
            self.boxes.append(box)
            box.open()
            return box
        except Exception:  # never let the error dialog itself raise into the hook
            log.exception("Could not show the error dialog")
            return None
        finally:
            self._in_report = False

    def _forget(self, box: QMessageBox) -> None:
        if box in self.boxes:
            self.boxes.remove(box)
        box.deleteLater()


# ===================================================================== startup
def ask_resume(session: Session | None, unsupported: bool, settings: Settings) -> str | None:
    """Show the ``ResumeDialog`` (blocking, before the main window) and return the choice."""
    from screen_copier.ui.dialogs import ResumeDialog

    dialog = ResumeDialog(session, unsupported, settings=settings)
    dialog.exec()
    return None if dialog.choice is None else str(dialog.choice)


def _corrupt_files(store: SessionStore) -> set[Path]:
    root = store.sessions_dir
    if not root.is_dir():
        return set()
    return set(root.glob(f"*/{CORRUPT_PREFIX}*.json"))


def make_recognizer(settings: Settings, env: Mapping[str, str]) -> Recognizer:
    """``FakeRecognizer`` when ``SCREEN_COPIER_FAKE_RECOGNIZER`` is set, else Claude."""
    fake = env.get(FAKE_RECOGNIZER_ENV, "").strip()
    if fake:
        from screen_copier.recognition.fake import FakeRecognizer

        log.info("Using the fake recognizer")
        return FakeRecognizer(fake)
    from screen_copier.recognition.claude import ClaudeRecognizer, client_factory_for

    return ClaudeRecognizer(client_factory_for(get_api_key), settings)


@dataclass
class AppContext:
    """Everything the startup flow created; ``close()`` tears it down (used by tests too)."""

    settings: Settings
    store: SessionStore
    controller: Any
    window: Any
    reporter: ErrorReporter
    log_handler: logging.Handler
    data_dir: Path
    resumed_id: str | None = None
    notices: list[str] = field(default_factory=list)

    def close(self) -> None:
        self.window.close()
        self.reporter.uninstall()
        root = logging.getLogger()
        if self.log_handler in root.handlers:
            root.removeHandler(self.log_handler)
        self.log_handler.close()


def start_app(
    env: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
    ask: AskResume = ask_resume,
    notify: Callable[[str, str], None] | None = None,
    show_error_dialogs: bool = True,
) -> AppContext:
    """Run the startup flow up to a visible main window. Requires a ``QApplication``."""
    environ: Mapping[str, str] = os.environ if env is None else env
    directory = data_dir(environ)
    handler = setup_logging_for(environ)
    log.info("Starting %s %s", APP_NAME, get_version())

    settings = load_settings(directory)

    def persist(value: Settings) -> None:
        save_settings(value, directory)

    store = SessionStore(directory, clock)
    corrupt_before = _corrupt_files(store)  # loading renames damaged files aside
    try:
        store.cleanup_saved(settings.session_retention_days)
    except OSError:
        log.exception("Retention cleanup failed")

    notices: list[str] = []
    resume_id: str | None = None
    resume_mode_review = False
    try:
        session = store.find_unfinished()
    except UnsupportedSessionVersion as exc:
        log.warning("Unfinished session %s is from a newer version", exc.session_id)
        if ask(None, True, settings) == "discard":
            store.discard(exc.session_id)
        session = None
    except CorruptSession as exc:  # find_unfinished skips these; kept for safety
        log.warning("%s", exc)
        session = None
    if _corrupt_files(store) - corrupt_before:
        notices.append(CORRUPT_TEXT)
    if session is not None:
        choice = ask(session, False, settings)
        if choice == "resume":
            resume_id = session.id
            resume_mode_review = session.state is SessionState.REVIEWING
        elif choice == "discard":
            store.discard(session.id)

    from screen_copier.controller import CaptureController
    from screen_copier.ui.main_window import MainWindow, Mode

    controller = CaptureController(
        store=store,
        recognizer=make_recognizer(settings, environ),
        settings=settings,
        persist_settings=persist,
        clock=clock,
    )

    def settings_dialog(parent: QWidget) -> Any:
        from screen_copier.recognition.claude import client_factory_for
        from screen_copier.ui.settings_dialog import SettingsDialog

        return SettingsDialog(
            controller,
            settings,
            client_factory=client_factory_for(get_api_key),
            save=persist,
            parent=parent,
        )

    window = MainWindow(
        controller,
        settings,
        store,
        settings_dialog_factory=settings_dialog,
        persist_settings=persist,
        clock=clock,
    )
    reporter = ErrorReporter(lambda: window, handler_path(handler), show_dialogs=show_error_dialogs)
    reporter.install()

    window.show()
    for text in notices:
        if notify is not None:
            notify(CORRUPT_TITLE, text)
        else:
            QMessageBox.information(window, CORRUPT_TITLE, text)
    controller.start_camera()
    if resume_id is not None:
        if resume_mode_review:
            window.set_mode(Mode.REVIEW, confirm=False)
        controller.resume_session(resume_id)

    return AppContext(
        settings=settings,
        store=store,
        controller=controller,
        window=window,
        reporter=reporter,
        log_handler=handler,
        data_dir=directory,
        resumed_id=resume_id,
        notices=notices,
    )


def setup_logging_for(env: Mapping[str, str]) -> logging.Handler:
    from screen_copier.config.logging import setup_logging

    return setup_logging(logs_dir(env))


def handler_path(handler: logging.Handler) -> Path | None:
    filename = getattr(handler, "baseFilename", None)
    return Path(filename) if filename else None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the app. Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = build_parser().parse_args(args)
    except SystemExit as exc:  # --version / --help / bad args
        return exc.code if isinstance(exc.code, int) else 0
    if options.create_shortcut:
        from screen_copier import shortcut

        return shortcut.create_shortcut()

    app = QApplication.instance() or QApplication([sys.argv[0], *args])
    app.setApplicationName(APP_NAME)
    context = start_app()
    try:
        return app.exec()
    finally:
        context.reporter.uninstall()


if __name__ == "__main__":
    sys.exit(main())
