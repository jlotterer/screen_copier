"""Frame strip: captured frames as thumbnails with status badges (REQ-REV-01, architecture §14).

``FrameStrip`` is a horizontal ``QListView`` in icon mode, backed by ``FrameListModel`` and fed
by the controller's ``frame_added`` and ``frame_updated`` signals (items are matched by
``Frame.seq`` and kept in seq order). It acts only through the controller's slots
(``discard_frame``, ``retry_frame``, ``rerecognize_frame``) and emits ``frame_selected(seq)``
for the review view.

Thumbnails and the full-size viewer image are decoded on a thread pool, never on the GUI
thread. Every badge pairs a color with an icon of a distinct shape (REQ-NFR-08).
"""

from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    SignalInstance,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QIcon,
    QImage,
    QImageReader,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QLabel,
    QListView,
    QMenu,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from screen_copier.models import Frame, FrameStatus

log = logging.getLogger(__name__)

# Layout of the strip. These are presentation constants, not pipeline tunables (§8).
THUMB_SIZE = QSize(160, 100)
"""Bounding box a thumbnail is scaled into."""
BADGE_ICON_PX = 14
ITEM_PADDING = 4
BADGE_ROW_HEIGHT = 18
SPINNER_INTERVAL_MS = 100
SPINNER_STEPS = 12
DIMMED_OPACITY = 0.35

LABEL_RETRY = "Retry"
LABEL_DISCARD = "Discard"
LABEL_RERECOGNIZE = "Recognize again"


class FrameController(Protocol):
    """The part of ``CaptureController`` the strip uses."""

    frame_added: SignalInstance
    frame_updated: SignalInstance

    def discard_frame(self, seq: int) -> None: ...
    def retry_frame(self, seq: int) -> None: ...
    def rerecognize_frame(self, seq: int) -> None: ...


# ============================================================================== badges


@dataclass(frozen=True)
class BadgeStyle:
    text: str
    shape: str
    color: QColor


STATUS_BADGES: dict[FrameStatus, BadgeStyle] = {
    FrameStatus.QUEUED: BadgeStyle("Queued", "clock", QColor("#6b7280")),
    FrameStatus.RECOGNIZING: BadgeStyle("Recognizing", "spinner", QColor("#2563eb")),
    FrameStatus.RECOGNIZED: BadgeStyle("Recognized", "check", QColor("#15803d")),
    FrameStatus.FAILED: BadgeStyle("Failed", "cross", QColor("#b91c1c")),
    FrameStatus.DISCARDED: BadgeStyle("Discarded", "minus", QColor("#4b5563")),
}
BLURRY_BADGE = BadgeStyle("Blurry", "triangle", QColor("#b45309"))


@dataclass(frozen=True)
class Badge:
    """One rendered badge: text, an icon with a distinct shape, and a color."""

    text: str
    shape: str
    color: QColor
    icon: QIcon


def badge_icon(shape: str, color: QColor, size: int = BADGE_ICON_PX, step: int = 0) -> QIcon:
    """Draw a badge icon; each shape is distinct so status never relies on color alone."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.5, size / 8))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = size * 0.15
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    c = r.center()
    if shape == "clock":
        p.drawEllipse(r)
        p.drawLine(c, QPointF(c.x(), r.top() + r.height() * 0.2))
        p.drawLine(c, QPointF(r.right() - r.width() * 0.25, c.y()))
    elif shape == "spinner":
        # 12 spokes; the one at ``step`` is darkest, older ones fade out.
        for i in range(SPINNER_STEPS):
            age = (step - i) % SPINNER_STEPS
            col = QColor(color)
            col.setAlphaF(max(0.15, 1.0 - age / SPINNER_STEPS))
            spoke = QPen(col, max(1.5, size / 9))
            spoke.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(spoke)
            a = 2 * math.pi * i / SPINNER_STEPS
            inner, outer = r.width() * 0.22, r.width() * 0.5
            p.drawLine(
                QPointF(c.x() + inner * math.sin(a), c.y() - inner * math.cos(a)),
                QPointF(c.x() + outer * math.sin(a), c.y() - outer * math.cos(a)),
            )
    elif shape == "check":
        path = QPainterPath(QPointF(r.left(), c.y()))
        path.lineTo(r.left() + r.width() * 0.38, r.bottom() - r.height() * 0.1)
        path.lineTo(r.right(), r.top() + r.height() * 0.1)
        p.drawPath(path)
    elif shape == "cross":
        p.drawLine(r.topLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomLeft())
    elif shape == "minus":
        p.drawEllipse(r)
        p.drawLine(QPointF(r.left() + 3, c.y()), QPointF(r.right() - 3, c.y()))
    elif shape == "triangle":
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygonF([QPointF(c.x(), r.top()), r.bottomRight(), r.bottomLeft()]))
    p.end()
    return QIcon(pix)


def badges_for(frame: Frame, spinner_step: int = 0) -> list[Badge]:
    """The status badge, plus "Blurry" when the frame's quality says so."""
    styles = [STATUS_BADGES[frame.status]]
    if frame.quality.blurry:
        styles.append(BLURRY_BADGE)
    out = []
    for s in styles:
        step = spinner_step if s.shape == "spinner" else 0
        out.append(Badge(s.text, s.shape, s.color, badge_icon(s.shape, s.color, step=step)))
    return out


# ============================================================================== image loading


class ImageLoader(QObject):
    """Decodes images on a thread pool and delivers them on the loader's (GUI) thread."""

    loaded = Signal(object, object)  # key, QImage (null if the file could not be read)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)

    def load(self, key: object, path: Path, max_size: QSize | None = None) -> None:
        emit = self.loaded.emit

        def task() -> None:
            image = _read_image(path, max_size)
            # RuntimeError: the loader was deleted while decoding.
            with contextlib.suppress(RuntimeError):
                emit(key, image)

        self.pool.start(task)

    def wait(self, msecs: int = 5000) -> bool:
        return self.pool.waitForDone(msecs)


def _read_image(path: Path, max_size: QSize | None) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if max_size is not None:
        size = reader.size()
        if size.isValid() and not size.isEmpty():
            reader.setScaledSize(size.scaled(max_size, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        log.warning("Could not read frame image %s: %s", path, reader.errorString())
    return image


# ============================================================================== model


class FrameListModel(QAbstractListModel):
    """Frames in seq order. Items are matched by ``Frame.seq``."""

    SeqRole = Qt.ItemDataRole.UserRole + 1
    FrameRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    BadgesRole = Qt.ItemDataRole.UserRole + 4
    BadgeTextRole = Qt.ItemDataRole.UserRole + 5
    SpinnerRole = Qt.ItemDataRole.UserRole + 6
    DimmedRole = Qt.ItemDataRole.UserRole + 7
    ThumbnailRole = Qt.ItemDataRole.UserRole + 8

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frames: list[Frame] = []
        self._thumbs: dict[int, QPixmap] = {}
        self._spinner_step = 0
        self._spinner = QTimer(self)
        self._spinner.setInterval(SPINNER_INTERVAL_MS)
        self._spinner.timeout.connect(self._advance_spinner)
        self.thumbnail_needed: Callable[[Frame], None] | None = None

    # ---------------------------------------------------------------- Qt model API
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._frames)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._frames):
            return None
        frame = self._frames[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"#{frame.seq}"
        if role == Qt.ItemDataRole.ToolTipRole:
            text = f"Frame {frame.seq}: {self._badge_text(frame)}"
            return f"{text}\n{frame.error}" if frame.error else text
        if role == Qt.ItemDataRole.DecorationRole or role == self.ThumbnailRole:
            return self._thumbs.get(frame.seq)
        if role == self.SeqRole:
            return frame.seq
        if role == self.FrameRole:
            return frame
        if role == self.StatusRole:
            return frame.status
        if role == self.BadgesRole:
            return badges_for(frame, self._spinner_step)
        if role == self.BadgeTextRole:
            return self._badge_text(frame)
        if role == self.SpinnerRole:
            return frame.status is FrameStatus.RECOGNIZING
        if role == self.DimmedRole:
            return frame.status is FrameStatus.DISCARDED
        return None

    # ---------------------------------------------------------------- updates
    def upsert(self, frame: Frame) -> None:
        """Insert a new frame in seq order, or replace the one with the same seq."""
        row = self.row_of(frame.seq)
        if row is not None:
            self._frames[row] = frame
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)
        else:
            row = 0
            while row < len(self._frames) and self._frames[row].seq < frame.seq:
                row += 1
            self.beginInsertRows(QModelIndex(), row, row)
            self._frames.insert(row, frame)
            self.endInsertRows()
            if self.thumbnail_needed is not None:
                self.thumbnail_needed(frame)
        self._update_spinner()

    def clear(self) -> None:
        self.beginResetModel()
        self._frames.clear()
        self._thumbs.clear()
        self.endResetModel()
        self._update_spinner()

    def set_thumbnail(self, seq: int, image: QImage) -> None:
        row = self.row_of(seq)
        if row is None or image.isNull():
            return
        self._thumbs[seq] = QPixmap.fromImage(image)
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)

    # ---------------------------------------------------------------- helpers
    def row_of(self, seq: int) -> int | None:
        for row, f in enumerate(self._frames):
            if f.seq == seq:
                return row
        return None

    def frame_at(self, row: int) -> Frame:
        return self._frames[row]

    def frames(self) -> list[Frame]:
        return list(self._frames)

    @staticmethod
    def _badge_text(frame: Frame) -> str:
        return " · ".join(b.text for b in badges_for(frame))

    def spinner_running(self) -> bool:
        return self._spinner.isActive()

    def _update_spinner(self) -> None:
        busy = any(f.status is FrameStatus.RECOGNIZING for f in self._frames)
        if busy and not self._spinner.isActive():
            self._spinner.start()
        elif not busy and self._spinner.isActive():
            self._spinner.stop()

    def _advance_spinner(self) -> None:
        self._spinner_step = (self._spinner_step + 1) % SPINNER_STEPS
        for row, f in enumerate(self._frames):
            if f.status is FrameStatus.RECOGNIZING:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [self.BadgesRole])


# ============================================================================== delegate


class FrameDelegate(QStyledItemDelegate):
    """Paints a thumbnail with the seq number and the badges below it."""

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(
            THUMB_SIZE.width() + 2 * ITEM_PADDING,
            THUMB_SIZE.height() + 2 * BADGE_ROW_HEIGHT + 3 * ITEM_PADDING,
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        painter.save()
        rect: QRect = option.rect  # type: ignore[attr-defined]
        state = option.state  # type: ignore[attr-defined]
        palette = option.palette  # type: ignore[attr-defined]
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, palette.highlight())
        if index.data(FrameListModel.DimmedRole):
            painter.setOpacity(DIMMED_OPACITY)

        thumb_rect = QRect(
            rect.left() + ITEM_PADDING,
            rect.top() + ITEM_PADDING,
            THUMB_SIZE.width(),
            THUMB_SIZE.height(),
        )
        pix = index.data(FrameListModel.ThumbnailRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            scaled = pix.size().scaled(THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(QPoint(0, 0), scaled)
            target.moveCenter(thumb_rect.center())
            painter.drawPixmap(target, pix)
        else:
            painter.fillRect(thumb_rect, QColor("#e5e7eb"))
        painter.setPen(palette.text().color())
        painter.drawText(
            thumb_rect.adjusted(2, 0, 0, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            str(index.data(Qt.ItemDataRole.DisplayRole)),
        )

        y = thumb_rect.bottom() + ITEM_PADDING
        for badge in index.data(FrameListModel.BadgesRole) or []:
            row = QRect(rect.left() + ITEM_PADDING, y, THUMB_SIZE.width(), BADGE_ROW_HEIGHT)
            bg = QColor(badge.color)
            bg.setAlphaF(0.15)
            painter.fillRect(row, bg)
            icon_rect = QRect(
                row.left() + 2,
                row.top() + (BADGE_ROW_HEIGHT - BADGE_ICON_PX) // 2,
                BADGE_ICON_PX,
                BADGE_ICON_PX,
            )
            badge.icon.paint(painter, icon_rect)
            painter.setPen(badge.color)
            painter.drawText(
                row.adjusted(BADGE_ICON_PX + 6, 0, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                badge.text,
            )
            y += BADGE_ROW_HEIGHT
        painter.restore()


# ============================================================================== viewer


class FrameViewerDialog(QDialog):
    """Shows one frame at full size. The image is decoded off the GUI thread."""

    def __init__(self, frame: Frame, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.seq = frame.seq
        self.setWindowTitle(f"Frame {frame.seq}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.image_label = QLabel("Loading…")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.image_label)
        scroll.setWidgetResizable(True)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        self.resize(1000, 700)
        self._loader = ImageLoader(self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.load(frame.seq, path)

    def _on_loaded(self, _key: object, image: QImage) -> None:
        if image.isNull():
            self.image_label.setText("The frame image could not be read.")
            return
        self.image_label.setPixmap(QPixmap.fromImage(image))

    def image_size(self) -> QSize:
        pix = self.image_label.pixmap()
        return QSize() if pix is None or pix.isNull() else pix.size()

    def wait(self, msecs: int = 5000) -> bool:
        return self._loader.wait(msecs)

    def done(self, result: int) -> None:
        self._loader.wait()
        super().done(result)


# ============================================================================== strip


class FrameStrip(QListView):
    """Horizontal strip of frame thumbnails; states: empty, populated."""

    frame_selected = Signal(int)

    def __init__(
        self,
        controller: FrameController,
        session_dir: Path | str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._session_dir = Path(session_dir) if session_dir is not None else None
        self.viewer: FrameViewerDialog | None = None

        self.frame_model = FrameListModel(self)
        self.frame_model.thumbnail_needed = self._request_thumbnail
        self.setModel(self.frame_model)
        self.setItemDelegate(FrameDelegate(self))

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setSpacing(ITEM_PADDING)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setFixedHeight(
            THUMB_SIZE.height()
            + 2 * BADGE_ROW_HEIGHT
            + 6 * ITEM_PADDING
            + self.horizontalScrollBar().sizeHint().height()
        )

        self._thumbs = ImageLoader(self)
        self._thumbs.loaded.connect(self._on_thumbnail)

        controller.frame_added.connect(self.frame_model.upsert)
        controller.frame_updated.connect(self.frame_model.upsert)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.doubleClicked.connect(self._on_double_clicked)
        self.frame_model.rowsInserted.connect(self._scroll_to_new)

    # ---------------------------------------------------------------- public
    def set_session_dir(self, session_dir: Path | str | None) -> None:
        """Switch to another session folder (new or resumed session); clears the strip."""
        self._session_dir = Path(session_dir) if session_dir is not None else None
        self.frame_model.clear()

    def selected_seq(self) -> int | None:
        rows = self.selectionModel().selectedRows()
        return rows[0].data(FrameListModel.SeqRole) if rows else None

    def select_seq(self, seq: int) -> None:
        row = self.frame_model.row_of(seq)
        if row is not None:
            self.setCurrentIndex(self.frame_model.index(row))

    def index_of(self, seq: int) -> QModelIndex:
        row = self.frame_model.row_of(seq)
        return QModelIndex() if row is None else self.frame_model.index(row)

    def build_context_menu(self, seq: int) -> QMenu | None:
        """Menu for one frame: Discard, Retry (failed only), Recognize again."""
        row = self.frame_model.row_of(seq)
        if row is None:
            return None
        frame = self.frame_model.frame_at(row)
        menu = QMenu(self)
        discard = menu.addAction(LABEL_DISCARD)
        discard.setEnabled(frame.status is not FrameStatus.DISCARDED)
        discard.triggered.connect(lambda: self._controller.discard_frame(seq))
        if frame.status is FrameStatus.FAILED:
            retry = menu.addAction(LABEL_RETRY)
            retry.triggered.connect(lambda: self._controller.retry_frame(seq))
        again = menu.addAction(LABEL_RERECOGNIZE)
        again.setEnabled(frame.status is FrameStatus.RECOGNIZED)
        again.triggered.connect(lambda: self._controller.rerecognize_frame(seq))
        return menu

    def open_viewer(self, seq: int) -> FrameViewerDialog | None:
        row = self.frame_model.row_of(seq)
        if row is None or self._session_dir is None:
            return None
        frame = self.frame_model.frame_at(row)
        if self.viewer is not None:
            self.viewer.close()
        viewer = FrameViewerDialog(frame, self._session_dir / frame.image_file, self)
        viewer.destroyed.connect(lambda *_: self._forget_viewer(viewer))
        self.viewer = viewer
        viewer.show()
        return viewer

    def wait_for_thumbnails(self, msecs: int = 5000) -> bool:
        return self._thumbs.wait(msecs)

    # ---------------------------------------------------------------- events
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            seq = self.selected_seq()
            if seq is not None:
                self._controller.discard_frame(seq)
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        self.setCurrentIndex(index)
        menu = self.build_context_menu(index.data(FrameListModel.SeqRole))
        if menu is not None:
            self._exec_menu(menu, event.globalPos())
            menu.deleteLater()

    def _exec_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        """Run the menu modally; separate so tests can replace it."""
        menu.exec(global_pos)

    def closeEvent(self, event: Any) -> None:
        self._thumbs.wait()
        super().closeEvent(event)

    # ---------------------------------------------------------------- slots
    def _request_thumbnail(self, frame: Frame) -> None:
        if self._session_dir is None:
            return
        key = (self._session_dir, frame.seq)
        self._thumbs.load(key, self._session_dir / frame.image_file, THUMB_SIZE)

    def _on_thumbnail(self, key: object, image: QImage) -> None:
        session_dir, seq = key  # type: ignore[misc]
        if session_dir == self._session_dir:  # ignore loads from a previous session
            self.frame_model.set_thumbnail(seq, image)

    def _on_selection_changed(self, *_: object) -> None:
        seq = self.selected_seq()
        if seq is not None:
            self.frame_selected.emit(seq)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.open_viewer(index.data(FrameListModel.SeqRole))

    def _scroll_to_new(self, _parent: QModelIndex, _first: int, last: int) -> None:
        if last == self.frame_model.rowCount() - 1:
            self.scrollTo(self.frame_model.index(last))

    def _forget_viewer(self, viewer: FrameViewerDialog) -> None:
        if self.viewer is viewer:
            self.viewer = None
