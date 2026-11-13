"""Review mode: source frames, editable text with issue highlights, syntax panel and Save.

Layout: the frames the current line came from on the left (REQ-REV-04), the ``CodeEditor`` on
the right (REQ-REV-02/03), with a toolbar row (issue counter, previous/next issue, conflict
picker, language combo, Save) and a syntax panel below the editor.

Text model. Until the user edits, the editor shows ``render(document)`` and line *i* is
``render_items(document)[i]``. Once edited, the editor text is the truth (``Session.edited_text``)
and editor lines are matched back to the rendered lines with a line diff: a line keeps its
highlight only while its text is unchanged (REQ-REV-05). The diff also maps lines that were
edited in place to their source frames, so the frame panel keeps working after edits.

The view talks to the controller only through ``document_changed``, ``session`` and
``set_edited_text``. The language override is stored on ``controller.session.language`` and is
persisted with the session's next save. Writing the file runs on a worker thread.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from screen_copier.config.settings import Settings
from screen_copier.models import DocLine, DocLineFlag, Document, FrameStatus, Gap, Session
from screen_copier.output.language import EXTENSIONS, normalize_language, vote_language
from screen_copier.output.syntax import SyntaxIssue, check, is_gap_marker
from screen_copier.output.writer import SaveError, default_filename, render_for_save, write_file
from screen_copier.stitching.stitcher import gap_marker, render_items
from screen_copier.ui.code_editor import CodeEditor, IssueKind, LineIssue

SYNTAX_CHECK_DELAY_MS = 400
"""Debounce between the last keystroke and the syntax check (UI latency, not a tunable)."""

EMPTY_SAVE_TOOLTIP = "Nothing to save yet: the document is empty. Capture some frames first."
SAVE_TOOLTIP = "Save the text to a file (Ctrl+S)"

_FLAG_KINDS: dict[DocLineFlag, IssueKind] = {
    DocLineFlag.UNCERTAIN: IssueKind.UNCERTAIN,
    DocLineFlag.CONFLICT: IssueKind.CONFLICT,
    DocLineFlag.CUT_OFF: IssueKind.CUT_OFF,
    DocLineFlag.SOFT_WRAP: IssueKind.SOFT_WRAP,
}

LineKey = tuple[str, tuple[tuple[int, str], ...]]


class ControllerLike(Protocol):
    """The part of ``CaptureController`` this view uses."""

    document_changed: Any

    @property
    def session(self) -> Session | None: ...

    def document(self) -> Document: ...

    def set_edited_text(self, text: str | None) -> None: ...


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _line_key(line: DocLine) -> LineKey:
    """Identity of a DocLine that survives the fresh document copies the stitcher hands out."""
    return line.text, tuple((o.frame_seq, o.text) for o in line.observations)


def issue_reason(item: DocLine | Gap, kinds: Sequence[IssueKind]) -> str:
    """The hover text explaining why a line is highlighted (REQ-REV-03)."""
    if isinstance(item, Gap):
        return (
            f"Gap: {item.description}. Lines may be missing here; capture the missing part "
            "again or fill it in by hand. The marker is kept in the saved file."
        )
    seqs = ", ".join(str(seq) for seq in sorted({o.frame_seq for o in item.observations}))
    reasons: list[str] = []
    for kind in kinds:
        if kind is IssueKind.UNCERTAIN:
            reasons.append("Uncertain: the transcription of this line may be wrong.")
        elif kind is IssueKind.CONFLICT:
            options = " | ".join(item.alternatives)
            reasons.append(
                f"Conflict: frames read this line differently ({options}). "
                "Press Alt+Enter or click the icon to pick one."
            )
        elif kind is IssueKind.CUT_OFF:
            reasons.append("Cut off: the line ran past the right edge of the screen.")
        elif kind is IssueKind.SOFT_WRAP:
            reasons.append(
                "Possible word wrap: this may be the continuation of the line above, "
                "wrapped by the editor on screen."
            )
    reasons.append(f"Source frames: {seqs}.")
    return "\n".join(reasons)


# ============================================================================== workers
class _Signals(QObject):
    image_loaded = Signal(int, object)  # seq, QImage
    save_done = Signal(str, object)  # path, SaveError | None


class _LoadImage(QRunnable):
    def __init__(self, signals: _Signals, seq: int, path: Path) -> None:
        super().__init__()
        self._signals, self._seq, self._path = signals, seq, path

    def run(self) -> None:
        image = QImage(str(self._path))
        self._signals.image_loaded.emit(self._seq, None if image.isNull() else image)


class _WriteFile(QRunnable):
    def __init__(self, signals: _Signals, path: Path, data: bytes) -> None:
        super().__init__()
        self._signals, self._path, self._data = signals, path, data

    def run(self) -> None:
        error: SaveError | None = None
        try:
            write_file(self._path, self._data)
        except SaveError as exc:
            error = exc
        except OSError as exc:  # write_file wraps OS errors; this is a safety net
            error = SaveError(self._path, exc.strerror or str(exc))
        self._signals.save_done.emit(str(self._path), error)


# ============================================================================== widgets
class FramePanel(QScrollArea):
    """The source frames of the current line (REQ-REV-04)."""

    IMAGE_WIDTH = 360

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._title = QLabel("Source frames")
        self._title.setWordWrap(True)
        self._layout.addWidget(self._title)
        self.setWidget(self._content)
        self._frames: list[int] = []
        self._cards: dict[int, tuple[QWidget, QLabel]] = {}

    def frames(self) -> list[int]:
        return list(self._frames)

    def set_frames(self, seqs: Sequence[int], title: str) -> None:
        self._title.setText(title)
        if list(seqs) == self._frames:
            return
        for card, _image in self._cards.values():
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._frames = list(seqs)
        for seq in self._frames:
            card = QWidget(self._content)
            box = QVBoxLayout(card)
            box.setContentsMargins(0, 4, 0, 4)
            box.addWidget(QLabel(f"Frame {seq}"))
            image = QLabel("Loading image…")
            image.setAccessibleName(f"Image of frame {seq}")
            box.addWidget(image)
            self._layout.addWidget(card)
            self._cards[seq] = (card, image)

    def set_image(self, seq: int, image: QImage | None) -> None:
        card = self._cards.get(seq)
        if card is None:
            return
        label = card[1]
        if image is None:
            label.setText("Image not available")
            return
        label.setPixmap(
            QPixmap.fromImage(image).scaledToWidth(
                self.IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
            )
        )


class ConflictPicker(QListWidget):
    """Popup listing a conflict's alternatives; one click (or Enter) picks one (REQ-REV-06)."""

    chosen = Signal(str)

    def __init__(self, alternatives: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setAccessibleName("Conflict alternatives")
        for text in alternatives:
            self.addItem(QListWidgetItem(text if text else "(empty line)"))
        self._alternatives = list(alternatives)
        self.setCurrentRow(0)
        self.itemClicked.connect(self._pick)
        self.itemActivated.connect(self._pick)

    def alternatives(self) -> list[str]:
        return list(self._alternatives)

    def choose(self, index: int) -> None:
        self.chosen.emit(self._alternatives[index])
        self.close()

    def _pick(self, item: QListWidgetItem) -> None:
        self.choose(self.row(item))


# ============================================================================== view
class ReviewView(QWidget):
    """The Review screen. ``save()`` is public so the main window can bind Ctrl+S to it."""

    saved = Signal(str)
    """Emitted with the path after a successful save."""

    def __init__(
        self,
        controller: ControllerLike,
        settings: Settings,
        *,
        frame_image_path: Callable[[int], Path | None] | None = None,
        clock: Callable[[], datetime] = _local_now,
        thread_pool: QThreadPool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._frame_image_path = frame_image_path
        self._clock = clock
        self._pool = thread_pool or QThreadPool.globalInstance()
        # No parent: worker runnables keep it alive even if the view is deleted mid-load.
        self._signals = _Signals()
        self._signals.image_loaded.connect(self._on_image_loaded)
        self._signals.save_done.connect(self._on_save_done)

        self._items: list[DocLine | Gap] = []
        self._original: list[str] = []
        self._cleared: set[LineKey] = set()
        self._issue_map: dict[int, int] = {}  # editor line -> item index (unchanged lines)
        self._frame_map: dict[int, int] = {}  # editor line -> item index (incl. edited in place)
        self._issue_lines: list[int] = []
        self._syntax_issues: list[SyntaxIssue] | None = None
        self._images: dict[int, QImage | None] = {}
        self._loading_images: set[int] = set()
        self._programmatic = False
        self._edited = False
        self._session_id: str | None = None
        self._nav_fresh = True
        self._saving = False
        self._picker: ConflictPicker | None = None
        self._save_dir: Path | None = None

        self._build_ui()
        controller.document_changed.connect(self._on_document_changed)
        self._on_document_changed(controller.document(), {})

    # ------------------------------------------------------------------ construction
    def _build_ui(self) -> None:
        self.editor = CodeEditor(self)
        self.editor.setPlaceholderText("No text yet. Capture frames to fill the document.")
        self.editor.setAccessibleName("Transcribed text")
        self.frame_panel = FramePanel(self)

        self.issue_label = QLabel()
        self.issue_label.setAccessibleName("Issue counter")
        self.prev_button = QPushButton("◀ Previous issue")
        self.prev_button.setToolTip("Previous issue (Shift+F8)")
        self.next_button = QPushButton("Next issue ▶")
        self.next_button.setToolTip("Next issue (F8)")
        self.pick_button = QPushButton("Pick alternative…")
        self.pick_button.setToolTip("Choose one reading of a conflicting line (Alt+Enter)")
        self.language_combo = QComboBox()
        self.language_combo.setAccessibleName("Language")
        self.language_combo.addItems(list(EXTENSIONS))
        self.language_combo.setToolTip("Language used for the syntax check and file extension")
        language_label = QLabel("&Language:")
        language_label.setBuddy(self.language_combo)
        self.save_button = QPushButton("&Save…")
        self.status_label = QLabel()

        bar = QHBoxLayout()
        bar.addWidget(self.issue_label)
        bar.addWidget(self.prev_button)
        bar.addWidget(self.next_button)
        bar.addWidget(self.pick_button)
        bar.addStretch(1)
        bar.addWidget(language_label)
        bar.addWidget(self.language_combo)
        bar.addWidget(self.save_button)

        self.syntax_label = QLabel("Syntax")
        self.syntax_panel = QListWidget()
        self.syntax_panel.setAccessibleName("Syntax errors")
        self.syntax_label.setBuddy(self.syntax_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(bar)
        editor_split = QSplitter(Qt.Orientation.Vertical)
        editor_split.addWidget(self.editor)
        syntax_box = QWidget()
        syntax_layout = QVBoxLayout(syntax_box)
        syntax_layout.setContentsMargins(0, 0, 0, 0)
        syntax_layout.addWidget(self.syntax_label)
        syntax_layout.addWidget(self.syntax_panel)
        editor_split.addWidget(syntax_box)
        editor_split.setStretchFactor(0, 4)
        editor_split.setStretchFactor(1, 1)
        right_layout.addWidget(editor_split)
        right_layout.addWidget(self.status_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.frame_panel)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(split)

        self._syntax_timer = QTimer(self)
        self._syntax_timer.setSingleShot(True)
        self._syntax_timer.setInterval(SYNTAX_CHECK_DELAY_MS)
        self._syntax_timer.timeout.connect(self.run_syntax_check)

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._on_user_cursor_moved)
        self.editor.navigate_requested.connect(self.navigate)
        self.editor.picker_requested.connect(self.open_conflict_picker)
        self.editor.issue_clicked.connect(self._on_issue_clicked)
        self.prev_button.clicked.connect(lambda: self.navigate(-1))
        self.next_button.clicked.connect(lambda: self.navigate(1))
        self.pick_button.clicked.connect(self.open_conflict_picker)
        self.language_combo.currentIndexChanged.connect(self._on_language_picked)
        self.syntax_panel.itemClicked.connect(self._on_syntax_item)
        self.syntax_panel.itemActivated.connect(self._on_syntax_item)
        self.save_button.clicked.connect(self.save)

    # ------------------------------------------------------------------ document loading
    def _on_document_changed(self, document: Document, _stats: object = None) -> None:
        self._items = render_items(document)
        self._original = [
            gap_marker(item) if isinstance(item, Gap) else item.text for item in self._items
        ]
        session = self._controller.session
        session_id = session.id if session is not None else None
        if session_id != self._session_id:
            self._session_id = session_id
            self._edited = False
            self._cleared.clear()
        edited = session.edited_text if session is not None else None
        if edited is not None:
            self._edited = True
            if edited != self.editor.toPlainText():
                self._set_editor_text(edited)
        elif not self._edited:
            self._set_editor_text("\n".join(self._original))
        self._refresh_language()
        self._remap()
        self.run_syntax_check()

    def _set_editor_text(self, text: str) -> None:
        line = self.editor.current_line()
        self._programmatic = True
        try:
            self.editor.setPlainText(text)
        finally:
            self._programmatic = False
        if line:
            self.editor.go_to_line(min(line, self.editor.blockCount() - 1))
        # Fresh text with the cursor at the top: F8 may land on line 1 itself.
        self._nav_fresh = self.editor.textCursor().position() == 0

    def _on_text_changed(self) -> None:
        if self._programmatic:
            return
        self._edited = True
        text = self.editor.toPlainText()
        self._controller.set_edited_text(text)
        self._remap()
        self._syntax_timer.start()

    def _remap(self) -> None:
        """Match editor lines to rendered items and refresh highlights, counter and panels."""
        current = self.editor.toPlainText().split("\n")
        self._issue_map.clear()
        self._frame_map.clear()
        if current == self._original:
            self._issue_map = dict(enumerate(range(len(self._original))))
            self._frame_map = dict(self._issue_map)
        else:
            matcher = difflib.SequenceMatcher(None, self._original, current, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for offset in range(i2 - i1):
                        self._issue_map[j1 + offset] = i1 + offset
                        self._frame_map[j1 + offset] = i1 + offset
                elif tag == "replace" and i2 - i1 == j2 - j1:
                    for offset in range(i2 - i1):
                        self._frame_map[j1 + offset] = i1 + offset
        issues: dict[int, LineIssue] = {}
        for line, index in self._issue_map.items():
            issue = self._issue_for_item(self._items[index])
            if issue is not None:
                issues[line] = issue
        self._issue_lines = sorted(issues)
        self.editor.set_line_issues(issues)
        self._update_counter()
        self._update_save_state()
        self._on_cursor_moved()

    def _issue_for_item(self, item: DocLine | Gap) -> LineIssue | None:
        if isinstance(item, Gap):
            return LineIssue((IssueKind.GAP,), issue_reason(item, (IssueKind.GAP,)))
        if not item.flags or _line_key(item) in self._cleared:
            return None
        kinds = tuple(_FLAG_KINDS[flag] for flag in DocLineFlag if flag in item.flags)
        return LineIssue(kinds, issue_reason(item, kinds))

    # ------------------------------------------------------------------ issues
    def issue_count(self) -> int:
        """Issues still highlighted. Equals ``issue_count(document)`` before any edit."""
        return len(self._issue_lines)

    def issue_lines(self) -> list[int]:
        return list(self._issue_lines)

    def _update_counter(self) -> None:
        count = self.issue_count()
        text = "No issues" if count == 0 else f"{count} issue" + ("" if count == 1 else "s")
        self.issue_label.setText(text)
        self.prev_button.setEnabled(count > 0)
        self.next_button.setEnabled(count > 0)

    def navigate(self, step: int) -> None:
        """Move to the next (``step`` > 0) or previous issue, wrapping around (REQ-REV-05)."""
        if not self._issue_lines:
            return
        line = self.editor.current_line()
        if step > 0:
            if self._nav_fresh and line in self._issue_lines:
                target = line
            else:
                target = next((n for n in self._issue_lines if n > line), self._issue_lines[0])
        else:
            target = next(
                (n for n in reversed(self._issue_lines) if n < line), self._issue_lines[-1]
            )
        self._nav_fresh = False
        self.editor.go_to_line(target)
        self.editor.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # F8 reaches here when another child of the view (combo, list, button) has focus.
        if event.key() == Qt.Key.Key_F8:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.navigate(-1 if shift else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ cursor / frames
    def current_item(self) -> DocLine | Gap | None:
        """The rendered item the cursor's line came from (``None`` for new text)."""
        index = self._frame_map.get(self.editor.current_line())
        return None if index is None else self._items[index]

    def shown_frames(self) -> list[int]:
        return self.frame_panel.frames()

    def _on_user_cursor_moved(self) -> None:
        if not self._programmatic:
            self._nav_fresh = False
        self._on_cursor_moved()

    def _on_cursor_moved(self) -> None:
        line = self.editor.current_line()
        item = self.current_item()
        if isinstance(item, DocLine):
            seqs = sorted({o.frame_seq for o in item.observations})
            label = "Frame" if len(seqs) == 1 else "Frames"
            title = f"Line {line + 1}: {label} " + ", ".join(str(s) for s in seqs)
        elif isinstance(item, Gap):
            seqs, title = [], f"Line {line + 1}: gap ({item.description})"
        else:
            seqs, title = [], f"Line {line + 1}: no source frame (typed text)"
        self.frame_panel.set_frames(seqs, title)
        for seq in seqs:
            self._request_image(seq)
        self.pick_button.setEnabled(self._current_conflict() is not None)

    def _request_image(self, seq: int) -> None:
        if seq in self._images:
            self.frame_panel.set_image(seq, self._images[seq])
            return
        if self._frame_image_path is None or seq in self._loading_images:
            if self._frame_image_path is None:
                self.frame_panel.set_image(seq, None)
            return
        path = self._frame_image_path(seq)
        if path is None:
            self.frame_panel.set_image(seq, None)
            return
        self._loading_images.add(seq)
        self._pool.start(_LoadImage(self._signals, seq, path))

    def _on_image_loaded(self, seq: int, image: QImage | None) -> None:
        self._loading_images.discard(seq)
        self._images[seq] = image
        self.frame_panel.set_image(seq, image)

    # ------------------------------------------------------------------ conflict picker
    def _current_conflict(self) -> DocLine | None:
        line = self.editor.current_line()
        index = self._issue_map.get(line)
        if index is None or line not in self._issue_lines:
            return None
        item = self._items[index]
        if isinstance(item, DocLine) and DocLineFlag.CONFLICT in item.flags and item.alternatives:
            return item
        return None

    def _on_issue_clicked(self, line: int) -> None:
        self.editor.go_to_line(line)
        self.open_conflict_picker()

    def open_conflict_picker(self) -> ConflictPicker | None:
        """Show the alternatives of the conflict on the cursor's line (REQ-REV-06)."""
        item = self._current_conflict()
        if item is None:
            return None
        line = self.editor.current_line()
        picker = ConflictPicker(item.alternatives, self)
        key = _line_key(item)
        picker.chosen.connect(lambda text: self._apply_alternative(line, key, text))
        rect = self.editor.cursorRect()
        picker.move(self.editor.viewport().mapToGlobal(rect.bottomLeft()))
        picker.show()
        picker.setFocus()
        self._picker = picker
        return picker

    def _apply_alternative(self, line: int, key: LineKey, text: str) -> None:
        self._cleared.add(key)
        if self.editor.line_text(line) == text:
            self._remap()  # text unchanged: just drop the highlight
        else:
            self.editor.replace_line(line, text)
        self.editor.setFocus()

    # ------------------------------------------------------------------ language & syntax
    def language(self) -> str:
        return self.language_combo.currentText()

    def _voted_language(self) -> str:
        session = self._controller.session
        if session is None:
            return vote_language([])
        if session.language:
            return normalize_language(session.language)
        guesses = [
            frame.result.language_guess
            for frame in session.frames
            if frame.status is FrameStatus.RECOGNIZED and frame.result is not None
        ]
        return vote_language(guesses)

    def _refresh_language(self) -> None:
        language = self._voted_language()
        if language != self.language_combo.currentText():
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentText(language)
            self.language_combo.blockSignals(False)

    def _on_language_picked(self, _index: int) -> None:
        session = self._controller.session
        if session is not None:
            session.language = self.language()
        self.run_syntax_check()

    def syntax_issues(self) -> list[SyntaxIssue] | None:
        return self._syntax_issues

    def run_syntax_check(self) -> None:
        """Check the editor text for the selected language and fill the syntax panel."""
        self._syntax_timer.stop()
        text = self.editor.toPlainText()
        language = self.language()
        self._syntax_issues = check(text, language) if text.strip() else []
        self.syntax_panel.clear()
        if self._syntax_issues is None:
            self.syntax_label.setText(f"Syntax: no checker for {language}")
        elif not self._syntax_issues:
            self.syntax_label.setText("Syntax: no errors found")
        else:
            count = len(self._syntax_issues)
            self.syntax_label.setText(f"Syntax: {count} error" + ("" if count == 1 else "s"))
            for issue in self._syntax_issues:
                where = f"Line {issue.line}"
                if issue.column is not None:
                    where += f", column {issue.column}"
                item = QListWidgetItem(f"{where}: {issue.message}")
                item.setData(Qt.ItemDataRole.UserRole, issue.line)
                self.syntax_panel.addItem(item)

    def _on_syntax_item(self, item: QListWidgetItem) -> None:
        line = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(line, int):
            self.editor.go_to_line(line - 1)
            self.editor.setFocus()

    # ------------------------------------------------------------------ save
    def can_save(self) -> bool:
        return bool(self.editor.toPlainText().strip()) and not self._saving

    def _update_save_state(self) -> None:
        empty = not self.editor.toPlainText().strip()
        self.save_button.setEnabled(not empty and not self._saving)
        self.save_button.setToolTip(EMPTY_SAVE_TOOLTIP if empty else SAVE_TOOLTIP)

    def gap_count(self) -> int:
        """Gap marker lines still in the editor text."""
        return sum(1 for line in self.editor.toPlainText().split("\n") if is_gap_marker(line))

    def save(self) -> None:
        """Save: gap confirmation, file dialog, write off the GUI thread (REQ-REV-09/11)."""
        if not self.can_save():
            return
        text = self.editor.toPlainText()
        gaps = self.gap_count()
        if gaps:
            noun = "gap" if gaps == 1 else "gaps"
            answer = QMessageBox.question(
                self,
                "Save with gaps?",
                f"The document still has {gaps} {noun}. The gap markers will be saved in the "
                "file and will stop it from compiling.\n\nSave anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return
        language = self.language()
        name = default_filename(language, self._clock())
        start = str(self._save_dir / name) if self._save_dir is not None else name
        ext = EXTENSIONS.get(language, ".txt")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save transcription", start, f"{language} files (*{ext});;All files (*)"
        )
        if not path:
            return
        data = render_for_save(text, self._settings.line_ending)
        self._saving = True
        self._update_save_state()
        self.status_label.setText(f"Saving {path}…")
        self._pool.start(_WriteFile(self._signals, Path(path), data))

    def _on_save_done(self, path: str, error: SaveError | None) -> None:
        self._saving = False
        self._update_save_state()
        if error is not None:
            self.status_label.setText("Save failed.")
            QMessageBox.critical(
                self, "Could not save", f"Could not save the file:\n{path}\n\n{error.os_message}"
            )
            return
        self._save_dir = Path(path).parent
        self.status_label.setText(f"Saved to {path}")
        self.saved.emit(path)
