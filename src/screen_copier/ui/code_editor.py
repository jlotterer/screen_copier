"""Code editor for Review mode: line numbers, issue icons, issue highlights and tooltips.

``CodeEditor`` is a ``QPlainTextEdit`` with a gutter on the left. Every line that has an issue
gets three cues, so the highlight never relies on color alone (REQ-REV-03, REQ-NFR-08):

* a gutter icon whose *shape* is unique per issue type,
* a full-width background color,
* a tooltip with the reason (on the text and on the gutter).

The editor knows nothing about documents or stitching: ``ReviewView`` decides which lines carry
which issues and passes them in with ``set_line_issues``. Line indexes are 0-based here; the
gutter shows them 1-based.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip, QWidget


class IssueKind(StrEnum):
    """Every kind of issue Review highlights. The first four mirror ``DocLineFlag``."""

    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"
    CUT_OFF = "cut_off"
    SOFT_WRAP = "soft_wrap"
    GAP = "gap"


ISSUE_ORDER: tuple[IssueKind, ...] = (
    IssueKind.GAP,
    IssueKind.CONFLICT,
    IssueKind.UNCERTAIN,
    IssueKind.CUT_OFF,
    IssueKind.SOFT_WRAP,
)
"""Priority order: the first kind present on a line picks the line's background color."""

ISSUE_SHAPES: dict[IssueKind, str] = {
    IssueKind.UNCERTAIN: "circle",
    IssueKind.CONFLICT: "diamond",
    IssueKind.CUT_OFF: "triangle",
    IssueKind.SOFT_WRAP: "hook",
    IssueKind.GAP: "square",
}
"""The gutter icon shape per issue type; each shape is used once (REQ-NFR-08)."""

ISSUE_LABELS: dict[IssueKind, str] = {
    IssueKind.UNCERTAIN: "Uncertain",
    IssueKind.CONFLICT: "Conflict",
    IssueKind.CUT_OFF: "Cut off",
    IssueKind.SOFT_WRAP: "Possible word wrap",
    IssueKind.GAP: "Gap",
}

ISSUE_BACKGROUNDS: dict[IssueKind, QColor] = {
    IssueKind.UNCERTAIN: QColor("#fff3b0"),
    IssueKind.CONFLICT: QColor("#ffd6a5"),
    IssueKind.CUT_OFF: QColor("#ffc9de"),
    IssueKind.SOFT_WRAP: QColor("#cfe8ff"),
    IssueKind.GAP: QColor("#ffb3b3"),
}
"""Line background per issue type (the color cue; the icon shape is the non-color cue)."""

ISSUE_ICON_COLORS: dict[IssueKind, QColor] = {
    IssueKind.UNCERTAIN: QColor("#a07800"),
    IssueKind.CONFLICT: QColor("#c05800"),
    IssueKind.CUT_OFF: QColor("#b0005a"),
    IssueKind.SOFT_WRAP: QColor("#0060b0"),
    IssueKind.GAP: QColor("#c00000"),
}


@dataclass(frozen=True)
class LineIssue:
    """The issues on one editor line and the reason shown on hover."""

    kinds: tuple[IssueKind, ...]
    reason: str

    @property
    def primary(self) -> IssueKind:
        return min(self.kinds, key=ISSUE_ORDER.index)


def draw_issue_icon(painter: QPainter, kind: IssueKind, rect: QRectF) -> None:
    """Draw the icon for ``kind`` inside ``rect``. Each kind has its own shape."""
    color = ISSUE_ICON_COLORS[kind]
    side = min(rect.width(), rect.height()) - 2
    box = QRectF(0, 0, side, side)
    box.moveCenter(rect.center())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color.darker(130), 1.2))
    painter.setBrush(QBrush(color))
    shape = ISSUE_SHAPES[kind]
    left, top, right, bottom = box.left(), box.top(), box.right(), box.bottom()
    cx, cy = box.center().x(), box.center().y()
    if shape == "circle":
        painter.drawEllipse(box)
    elif shape == "diamond":
        painter.drawPolygon(
            QPolygonF(
                [QPointF(cx, top), QPointF(right, cy), QPointF(cx, bottom), QPointF(left, cy)]
            )
        )
    elif shape == "triangle":
        painter.drawPolygon(
            QPolygonF([QPointF(cx, top), QPointF(right, bottom), QPointF(left, bottom)])
        )
    elif shape == "square":
        painter.drawRect(box.adjusted(1, 1, -1, -1))
    else:  # "hook": a return arrow (line goes right, down, then back left with an arrow head)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, max(1.5, side / 7)))
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(left + side * 0.2, top + side * 0.15),
                    QPointF(right - side * 0.1, top + side * 0.15),
                    QPointF(right - side * 0.1, cy + side * 0.2),
                    QPointF(left + side * 0.15, cy + side * 0.2),
                ]
            )
        )
        painter.setBrush(QBrush(color))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(left, cy + side * 0.2),
                    QPointF(left + side * 0.35, cy - side * 0.05),
                    QPointF(left + side * 0.35, cy + side * 0.45),
                ]
            )
        )
    painter.restore()


def issue_icon(kind: IssueKind, size: int = 16) -> QPixmap:
    """The gutter icon for ``kind`` as a pixmap (for legends and tests)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_issue_icon(painter, kind, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


class _Gutter(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self._editor = editor
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_gutter(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            line = self._editor.line_at_y(event.pos().y())
            self._editor.show_line_tooltip(line, event.globalPos(), self)
            return True
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        line = self._editor.line_at_y(int(event.position().y()))
        if line is not None:
            self._editor.go_to_line(line)
            if self._editor.issue_for_line(line) is not None:
                self._editor.issue_clicked.emit(line)
        super().mousePressEvent(event)


class CodeEditor(QPlainTextEdit):
    """Monospace plain-text editor with a line-number and issue-icon gutter."""

    navigate_requested = Signal(int)
    """+1 for F8 (next issue), -1 for Shift+F8 (previous issue)."""
    picker_requested = Signal()
    """Alt+Enter: open the conflict picker for the current line."""
    issue_clicked = Signal(int)
    """A gutter click on a line that has an issue (0-based line)."""

    GUTTER_PADDING = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabChangesFocus(False)
        self._issues: dict[int, LineIssue] = {}
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    # ------------------------------------------------------------------ issues
    def set_line_issues(self, issues: dict[int, LineIssue]) -> None:
        """Replace every line's issues (0-based line index -> issue) and repaint."""
        self._issues = dict(issues)
        self._update_gutter_width()
        self._apply_highlights()
        self._gutter.update()

    def line_issues(self) -> dict[int, LineIssue]:
        return dict(self._issues)

    def issue_for_line(self, line: int | None) -> LineIssue | None:
        return None if line is None else self._issues.get(line)

    def gutter_kinds(self, line: int) -> tuple[IssueKind, ...]:
        """The icon kinds the gutter draws for ``line``, left to right."""
        issue = self._issues.get(line)
        return () if issue is None else tuple(sorted(issue.kinds, key=ISSUE_ORDER.index))

    def tooltip_for_line(self, line: int | None) -> str | None:
        issue = self.issue_for_line(line)
        return None if issue is None else issue.reason

    def line_background(self, line: int) -> QColor | None:
        """The background color applied to ``line``, read back from the extra selections."""
        for selection in self.extraSelections():
            if selection.cursor.blockNumber() == line:
                return selection.format.background().color()
        return None

    def _apply_highlights(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        document = self.document()
        for line, issue in sorted(self._issues.items()):
            block = document.findBlockByNumber(line)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(ISSUE_BACKGROUNDS[issue.primary])
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            selections.append(selection)
        self.setExtraSelections(selections)

    # ------------------------------------------------------------------ cursor
    def current_line(self) -> int:
        return self.textCursor().blockNumber()

    def go_to_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line))
        if not block.isValid():
            block = self.document().lastBlock()
        self.setTextCursor(QTextCursor(block))
        self.centerCursor()

    def line_text(self, line: int) -> str:
        return self.document().findBlockByNumber(line).text()

    def replace_line(self, line: int, text: str) -> None:
        """Replace the text of ``line`` as one undoable edit and leave the cursor on it."""
        block = self.document().findBlockByNumber(line)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, mods = event.key(), event.modifiers()
        if key == Qt.Key.Key_F8:
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.navigate_requested.emit(-1 if shift else 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods & Qt.KeyboardModifier.AltModifier:
            self.picker_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ tooltips
    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            line = self.cursorForPosition(event.pos()).blockNumber()
            self.show_line_tooltip(line, event.globalPos(), self.viewport())
            return True
        return super().viewportEvent(event)

    def show_line_tooltip(self, line: int | None, global_pos: QPoint, widget: QWidget) -> None:
        text = self.tooltip_for_line(line)
        if text:
            QToolTip.showText(global_pos, text, widget)
        else:
            QToolTip.hideText()

    # ------------------------------------------------------------------ gutter
    @property
    def gutter(self) -> QWidget:
        return self._gutter

    def _icon_size(self) -> int:
        return max(8, self.fontMetrics().height() - 2)

    def _icon_columns(self) -> int:
        return max([1, *(len(issue.kinds) for issue in self._issues.values())])

    def gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        numbers = self.fontMetrics().horizontalAdvance("9") * max(3, digits)
        icons = self._icon_columns() * (self._icon_size() + 2)
        return self.GUTTER_PADDING * 3 + numbers + icons

    def _update_gutter_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        contents = self.contentsRect()
        self._gutter.setGeometry(
            QRect(contents.left(), contents.top(), self.gutter_width(), contents.height())
        )

    def line_at_y(self, y: int) -> int | None:
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid():
            bottom = top + self.blockBoundingRect(block).height()
            if top <= y < bottom:
                return block.blockNumber()
            top = bottom
            block = block.next()
        return None

    def paint_gutter(self, event: QPaintEvent) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        icon = self._icon_size()
        icons_left = self.GUTTER_PADDING
        numbers_right = self._gutter.width() - self.GUTTER_PADDING
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        line_height = self.fontMetrics().height()
        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + self.blockBoundingRect(block).height()
            if block.isVisible() and bottom >= event.rect().top():
                number = block.blockNumber()
                painter.setPen(self.palette().text().color())
                painter.drawText(
                    QRectF(0, top, numbers_right, line_height),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(number + 1),
                )
                for column, kind in enumerate(self.gutter_kinds(number)):
                    x = icons_left + column * (icon + 2)
                    draw_issue_icon(painter, kind, QRectF(x, top + 1, icon, line_height - 2))
            top = bottom
            block = block.next()
        painter.end()
