"""Review view: issue highlights and navigation, frame panel, conflict picker, language, syntax
panel and the Save flow (T017). A stub controller stands in for ``CaptureController``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QHelpEvent, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from screen_copier.config.settings import LineEnding, Settings
from screen_copier.models import (
    CaptureMode,
    DocLine,
    DocLineFlag,
    Document,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    Gap,
    GapKind,
    Observation,
    Segment,
    Session,
)
from screen_copier.output.language import extension_for
from screen_copier.output.writer import SaveError, default_filename
from screen_copier.stitching.stitcher import gap_marker, issue_count, render
from screen_copier.ui import code_editor, review_view
from screen_copier.ui.code_editor import (
    ISSUE_ICON_COLORS,
    ISSUE_SHAPES,
    IssueKind,
    issue_icon,
)
from screen_copier.ui.review_view import EMPTY_SAVE_TOOLTIP, ReviewView

NOW = datetime(2026, 11, 13, 9, 30, 15, tzinfo=UTC)


# ============================================================================== helpers
class StubController(QObject):
    document_changed = Signal(object, object)

    def __init__(self, document: Document, session: Session | None) -> None:
        super().__init__()
        self._document = document
        self._session = session
        self.set_edited_text = MagicMock()

    @property
    def session(self) -> Session | None:
        return self._session

    def document(self) -> Document:
        return self._document


def line(
    text: str,
    seqs: tuple[int, ...] = (1,),
    flags: set[DocLineFlag] | None = None,
    alternatives: list[str] | None = None,
) -> DocLine:
    return DocLine(
        text=text,
        observations=[Observation(frame_seq=s, text=text) for s in seqs],
        flags=flags or set(),
        alternatives=alternatives or [],
    )


def frame(seq: int, language: str | None) -> Frame:
    return Frame(
        seq=seq,
        captured_at=NOW,
        mode=CaptureMode.MANUAL,
        image_file=Frame.image_file_for(seq),
        quality=FrameQuality(sharpness=200.0, blurry=False),
        status=FrameStatus.RECOGNIZED,
        result=FrameResult(
            has_line_numbers=False, language_guess=language, word_wrap_detected=False, lines=[]
        ),
    )


def make_session(languages: list[str | None] | None = None) -> Session:
    frames = [frame(i + 1, lang) for i, lang in enumerate(languages or ["python"])]
    return Session(created_at=NOW, updated_at=NOW, frames=frames)


def make_view(
    qtbot: Any,
    document: Document,
    session: Session | None = None,
    **kwargs: Any,
) -> tuple[ReviewView, StubController]:
    controller = StubController(document, session if session is not None else make_session())
    view = ReviewView(controller, Settings(), clock=lambda: NOW, **kwargs)
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.show()
    qtbot.waitExposed(view)
    return view, controller


def gap(description: str = "unknown lines between capture 1 and 2", before: int = 1) -> Gap:
    return Gap(kind=GapKind.OVERLAP, description=description, before_segment=before)


def doc_with_two_uncertain_and_gap() -> Document:
    first = Segment(
        lines=[
            line("def a():"),
            line("    return 1", flags={DocLineFlag.UNCERTAIN}),
            line(""),
        ]
    )
    second = Segment(
        lines=[
            line("def b():", seqs=(2,)),
            line("    return 2", seqs=(2,), flags={DocLineFlag.UNCERTAIN}),
        ]
    )
    return Document(segments=[first, second], gaps=[gap()])


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Every QMessageBox / QFileDialog call is mocked; a test that forgets fails loudly."""
    mocks = {
        "question": MagicMock(return_value=QMessageBox.StandardButton.Cancel),
        "critical": MagicMock(return_value=QMessageBox.StandardButton.Ok),
        "getSaveFileName": MagicMock(return_value=("", "")),
    }
    monkeypatch.setattr(QMessageBox, "question", mocks["question"])
    monkeypatch.setattr(QMessageBox, "critical", mocks["critical"])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", mocks["getSaveFileName"])
    return mocks


# ============================================================================== issues & nav
def test_issue_counter_navigation_and_edit_clears_highlight(qtbot: Any) -> None:
    document = doc_with_two_uncertain_and_gap()
    view, controller = make_view(qtbot, document)
    # Editor lines: 0 def a, 1 uncertain, 2 blank, 3 gap, 4 def b, 5 uncertain.
    assert view.editor.toPlainText() + "\n" == render(document)
    assert view.issue_label.text() == "3 issues"
    assert view.issue_count() == issue_count(document) == 3

    visited = []
    for _ in range(3):
        qtbot.keyClick(view.editor, Qt.Key.Key_F8)
        visited.append(view.editor.current_line())
    assert visited == [1, 3, 5]
    assert view.editor.line_text(3) == gap_marker(document.gaps[0])

    qtbot.keyClick(view.editor, Qt.Key.Key_F8, Qt.KeyboardModifier.ShiftModifier)
    assert view.editor.current_line() == 3

    assert view.editor.line_background(1) is not None
    view.editor.go_to_line(1)
    view.editor.moveCursor(view.editor.textCursor().MoveOperation.EndOfBlock)
    qtbot.keyClicks(view.editor, "0")
    assert view.editor.line_text(1) == "    return 10"
    assert view.issue_label.text() == "2 issues"
    assert view.editor.issue_for_line(1) is None
    assert view.editor.line_background(1) is None
    assert view.editor.issue_for_line(5) is not None  # others keep their highlight
    controller.set_edited_text.assert_called_with(view.editor.toPlainText())


def test_f8_wraps_and_works_from_other_children(qtbot: Any) -> None:
    view, _ = make_view(qtbot, doc_with_two_uncertain_and_gap())
    view.editor.go_to_line(5)
    qtbot.keyClick(view.editor, Qt.Key.Key_F8)
    assert view.editor.current_line() == 1
    view.language_combo.setFocus()
    qtbot.keyClick(view.next_button, Qt.Key.Key_F8)
    assert view.editor.current_line() == 3


def test_inserting_lines_keeps_highlights_on_unchanged_lines(qtbot: Any) -> None:
    view, _ = make_view(qtbot, doc_with_two_uncertain_and_gap())
    view.editor.go_to_line(0)
    qtbot.keyClick(view.editor, Qt.Key.Key_Return)
    assert view.issue_lines() == [2, 4, 6]
    assert view.issue_label.text() == "3 issues"


# ============================================================================== frame panel
def test_cursor_on_line_50_shows_frames_4_and_5(qtbot: Any, tmp_path: Path) -> None:
    lines = [line(f"x{i} = {i}", seqs=(1, 2)) for i in range(1, 50)]
    lines.append(line("target = 50", seqs=(4, 5)))
    lines.extend(line(f"y{i} = {i}", seqs=(6,)) for i in range(51, 60))
    for seq in (4, 5):
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        image.save(str(tmp_path / f"{seq}.png"))
    view, _ = make_view(
        qtbot,
        Document(segments=[Segment(lines=lines)]),
        frame_image_path=lambda seq: tmp_path / f"{seq}.png",
    )
    view.editor.go_to_line(49)
    assert view.shown_frames() == [4, 5]
    qtbot.waitUntil(lambda: view._images.keys() >= {4, 5})
    view.editor.go_to_line(50)
    assert view.shown_frames() == [6]


# ============================================================================== conflict picker
@pytest.mark.parametrize("resolved", ["b", "a"])
def test_conflict_picker_sets_text_and_clears_highlight(qtbot: Any, resolved: str) -> None:
    conflict = line(resolved, flags={DocLineFlag.CONFLICT}, alternatives=["a", "b"])
    view, _controller = make_view(
        qtbot, Document(segments=[Segment(lines=[line("x = 1"), conflict])])
    )
    view.editor.go_to_line(1)
    assert view.pick_button.isEnabled()
    picker = view.open_conflict_picker()
    assert picker is not None
    assert picker.alternatives() == ["a", "b"]
    item = picker.item(0)
    qtbot.mouseClick(
        picker.viewport(), Qt.MouseButton.LeftButton, pos=picker.visualItemRect(item).center()
    )
    assert view.editor.line_text(1) == "a"
    assert view.editor.issue_for_line(1) is None
    assert view.editor.line_background(1) is None
    assert view.issue_label.text() == "No issues"
    assert not picker.isVisible()


def test_alt_enter_opens_picker(qtbot: Any) -> None:
    conflict = line("b", flags={DocLineFlag.CONFLICT}, alternatives=["a", "b"])
    view, _ = make_view(qtbot, Document(segments=[Segment(lines=[conflict])]))
    qtbot.keyClick(view.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
    assert view._picker is not None and view._picker.isVisible()
    qtbot.keyClick(view._picker, Qt.Key.Key_Down)
    qtbot.keyClick(view._picker, Qt.Key.Key_Return)
    assert view.editor.line_text(0) == "b"
    assert view.issue_count() == 0


# ============================================================================== syntax
def python_with_missing_colon() -> Document:
    lines = [line(f"x{i} = {i}") for i in range(1, 12)]
    lines.append(line("def broken()"))  # line 12
    lines.append(line("    return 1"))
    return Document(segments=[Segment(lines=lines)])


def test_syntax_error_listed_clickable_and_save_enabled(qtbot: Any) -> None:
    view, _ = make_view(qtbot, python_with_missing_colon())
    view.run_syntax_check()
    assert view.language() == "python"
    assert view.syntax_panel.count() == 1
    item = view.syntax_panel.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == 12
    assert item.text().startswith("Line 12")
    rect = view.syntax_panel.visualItemRect(item)
    qtbot.mouseClick(view.syntax_panel.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert view.editor.current_line() == 11
    assert view.save_button.isEnabled()


def test_syntax_check_reruns_after_typing(qtbot: Any) -> None:
    view, _ = make_view(qtbot, python_with_missing_colon())
    view.editor.go_to_line(11)
    view.editor.moveCursor(view.editor.textCursor().MoveOperation.EndOfBlock)
    qtbot.keyClicks(view.editor, ":")
    qtbot.waitUntil(lambda: view.syntax_issues() == [])
    assert view.syntax_panel.count() == 0


# ============================================================================== language
def test_language_vote_and_override(qtbot: Any) -> None:
    session = make_session(["python", "py", "json"])
    document = Document(segments=[Segment(lines=[line("x = 1")])])
    view, _ = make_view(qtbot, document, session)
    assert view.language_combo.currentText() == "python"
    assert view.syntax_issues() == []

    view.language_combo.setCurrentText("json")
    assert view.language() == "json"
    assert session.language == "json"
    issues = view.syntax_issues()
    assert issues and issues[0].line == 1  # "x = 1" is not JSON
    assert extension_for(view.language()) == ".json"


def test_stored_language_override_wins(qtbot: Any) -> None:
    session = make_session(["python", "python"])
    session.language = "yaml"
    view, _ = make_view(qtbot, Document(segments=[Segment(lines=[line("a: 1")])]), session)
    assert view.language() == "yaml"


# ============================================================================== issue display
def test_every_issue_type_has_icon_background_and_tooltip(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    flags = [
        DocLineFlag.UNCERTAIN,
        DocLineFlag.CONFLICT,
        DocLineFlag.CUT_OFF,
        DocLineFlag.SOFT_WRAP,
    ]
    lines = [line(f"line_{flag.value}", flags={flag}, alternatives=["p", "q"]) for flag in flags]
    document = Document(
        segments=[Segment(lines=lines), Segment(lines=[line("end", seqs=(2,))])],
        gaps=[gap()],
    )
    view, _ = make_view(qtbot, document)
    editor = view.editor
    expected = {
        0: IssueKind.UNCERTAIN,
        1: IssueKind.CONFLICT,
        2: IssueKind.CUT_OFF,
        3: IssueKind.SOFT_WRAP,
        4: IssueKind.GAP,
    }
    assert set(ISSUE_SHAPES) == set(IssueKind)
    assert len(set(ISSUE_SHAPES.values())) == len(IssueKind)
    icons = {kind: issue_icon(kind, 24).toImage() for kind in IssueKind}
    kinds = list(IssueKind)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1 :]:
            assert icons[a] != icons[b], f"{a} and {b} icons look the same"

    shown: list[str] = []
    monkeypatch.setattr(
        code_editor.QToolTip, "showText", lambda _pos, text, *_a: shown.append(text)
    )
    gutter_image = editor.gutter.grab().toImage()
    for number, kind in expected.items():
        assert editor.gutter_kinds(number) == (kind,)
        assert editor.line_background(number) is not None
        rect = editor.blockBoundingGeometry(editor.document().findBlockByNumber(number)).translated(
            editor.contentOffset()
        )
        y = int(rect.center().y())
        icon_color = ISSUE_ICON_COLORS[kind]
        row = [gutter_image.pixelColor(x, yy) for x in range(0, 30) for yy in range(y - 4, y + 5)]
        assert any(
            abs(c.red() - icon_color.red()) < 40
            and abs(c.green() - icon_color.green()) < 40
            and abs(c.blue() - icon_color.blue()) < 40
            for c in row
        ), f"no {kind} icon drawn in the gutter"

        shown.clear()
        pos = QPoint(int(editor.gutter_width() / 2), y)
        tooltip = QHelpEvent(QEvent.Type.ToolTip, QPoint(5, y), editor.viewport().mapToGlobal(pos))
        editor.viewportEvent(tooltip)
        assert shown, f"no tooltip for {kind}"
        assert shown[0] == editor.tooltip_for_line(number)
        assert shown[0].startswith(code_editor.ISSUE_LABELS[kind].split()[0])
    assert "between capture 1 and 2" in (editor.tooltip_for_line(4) or "")
    assert editor.line_background(5) is None
    assert editor.tooltip_for_line(5) is None


# ============================================================================== editing
def test_typing_calls_set_edited_text_with_full_text(qtbot: Any) -> None:
    view, controller = make_view(qtbot, Document(segments=[Segment(lines=[line("abc")])]))
    view.editor.moveCursor(view.editor.textCursor().MoveOperation.End)
    qtbot.keyClicks(view.editor, "d")
    controller.set_edited_text.assert_called_with("abcd")


def test_loading_does_not_call_set_edited_text(qtbot: Any) -> None:
    _view, controller = make_view(qtbot, doc_with_two_uncertain_and_gap())
    controller.set_edited_text.assert_not_called()


def test_session_edited_text_is_the_truth(qtbot: Any) -> None:
    document = doc_with_two_uncertain_and_gap()
    session = make_session()
    original = render(document).rstrip("\n").split("\n")
    original[5] = "    return 22"
    session.edited_text = "\n".join(original)
    view, controller = make_view(qtbot, document, session)
    assert view.editor.toPlainText() == session.edited_text
    assert view.issue_lines() == [1, 3]
    # A later document_changed does not overwrite the edits.
    controller.document_changed.emit(document, {})
    assert view.editor.toPlainText() == session.edited_text


def test_document_changed_refreshes_unedited_text(qtbot: Any) -> None:
    view, controller = make_view(qtbot, Document())
    assert view.issue_label.text() == "No issues"
    document = doc_with_two_uncertain_and_gap()
    controller.document_changed.emit(document, {"issues": 3})
    assert view.editor.toPlainText() + "\n" == render(document)
    assert view.issue_label.text() == "3 issues"


# ============================================================================== save
def test_empty_document_disables_save(qtbot: Any, no_real_dialogs: dict[str, MagicMock]) -> None:
    view, _ = make_view(qtbot, Document())
    assert not view.save_button.isEnabled()
    assert view.save_button.toolTip() == EMPTY_SAVE_TOOLTIP
    view.save()
    no_real_dialogs["getSaveFileName"].assert_not_called()


def test_save_with_gap_cancel_then_confirm(
    qtbot: Any, tmp_path: Path, no_real_dialogs: dict[str, MagicMock]
) -> None:
    document = doc_with_two_uncertain_and_gap()
    view, _ = make_view(qtbot, document)
    target = tmp_path / "out.py"

    no_real_dialogs["question"].return_value = QMessageBox.StandardButton.Cancel
    view.save()
    assert no_real_dialogs["question"].call_count == 1
    assert "1 gap" in no_real_dialogs["question"].call_args.args[2]
    no_real_dialogs["getSaveFileName"].assert_not_called()
    assert list(tmp_path.iterdir()) == []

    no_real_dialogs["question"].return_value = QMessageBox.StandardButton.Save
    no_real_dialogs["getSaveFileName"].return_value = (str(target), "")
    with qtbot.waitSignal(view.saved, timeout=5000) as blocker:
        view.save()
    assert blocker.args == [str(target)]
    start_name = no_real_dialogs["getSaveFileName"].call_args.args[2]
    assert start_name == default_filename("python", NOW)
    content = target.read_bytes().decode("utf-8")
    assert gap_marker(document.gaps[0]) in content
    assert content.endswith("    return 2\n")


def test_save_without_gaps_skips_confirmation_and_uses_line_ending(
    qtbot: Any, tmp_path: Path, no_real_dialogs: dict[str, MagicMock]
) -> None:
    controller = StubController(
        Document(segments=[Segment(lines=[line("a = 1  "), line("b = 2")])]), make_session()
    )
    view = ReviewView(controller, Settings(line_ending=LineEnding.CRLF), clock=lambda: NOW)
    qtbot.addWidget(view)
    target = tmp_path / "x.py"
    no_real_dialogs["getSaveFileName"].return_value = (str(target), "")
    with qtbot.waitSignal(view.saved, timeout=5000):
        view.save()
    no_real_dialogs["question"].assert_not_called()
    assert target.read_bytes() == b"a = 1\r\nb = 2\r\n"


def test_save_error_shows_os_message_and_view_stays_usable(
    qtbot: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_real_dialogs: dict[str, MagicMock],
) -> None:
    view, _ = make_view(qtbot, Document(segments=[Segment(lines=[line("x = 1")])]))
    target = tmp_path / "x.py"
    no_real_dialogs["getSaveFileName"].return_value = (str(target), "")

    def failing_write(path: Path, data: bytes) -> None:
        raise SaveError(path, "Access is denied")

    monkeypatch.setattr(review_view, "write_file", failing_write)
    view.save()
    qtbot.waitUntil(lambda: no_real_dialogs["critical"].called, timeout=5000)
    assert "Access is denied" in no_real_dialogs["critical"].call_args.args[2]
    assert view.save_button.isEnabled()
    assert not view.editor.isReadOnly()
    assert not target.exists()

    monkeypatch.undo()  # restores the real dialogs too; re-mock them
    monkeypatch.setattr(QFileDialog, "getSaveFileName", MagicMock(return_value=(str(target), "")))
    monkeypatch.setattr(QMessageBox, "critical", MagicMock())
    with qtbot.waitSignal(view.saved, timeout=5000):
        view.save()
    assert target.read_text(encoding="utf-8") == "x = 1\n"
