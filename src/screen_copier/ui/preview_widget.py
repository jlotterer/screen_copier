"""Live camera preview with the screen outline, 4 draggable corner handles and Lock/Clear.

Covers REQ-PRE-02 (show the outline), REQ-PRE-03 (drag the corners and lock the outline) and the
visual half of REQ-CAM-09 (a short capture flash on ``frame_added``).

The quad is a ``(4, 2)`` float32 array in frame pixel coordinates ordered TL, TR, BR, BL (the
Preprocess contract). The image is scaled to fit the canvas, keeping its aspect ratio, and the quad
is mapped from frame to widget coordinates for drawing and back for dragging.

Handle behaviour:
- Unlocked and not edited: the handles follow the quad from ``preview_frame`` (smoothed with
  ``QuadSmoother``), or sit just inside the frame corners when there is none.
- Dragging a handle marks the outline as edited; it stops following detection until Lock or Clear.
  Dragging a locked outline does not change the lock until Lock is pressed again.
- Lock calls ``controller.lock_region(quad)`` with the handle positions. Clear calls
  ``controller.lock_region(None)`` and returns to following detection.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from screen_copier.preprocess.detect import SMOOTHING_WINDOW, Quad, QuadSmoother, as_quad

TRIGGER_LABELS: dict[str, str] = {
    "MOVING": "Moving…",
    "SETTLING": "Settling…",
    "WAITING_FOCUS": "Waiting for focus",
    "CAPTURED": "Captured",
}

# UI look-and-feel constants (not user settings).
FLASH_MS = 150
"""How long the capture flash stays on screen."""
HANDLE_RADIUS_PX = 7
"""Drawn radius of a corner handle, in widget pixels."""
HANDLE_HIT_PX = 14
"""A press within this distance of a handle grabs it."""
DEFAULT_INSET = 0.05
"""Default handle inset from the frame edges, as a fraction of width and height."""


def default_quad(width: int, height: int) -> Quad:
    """A rectangle just inside the frame corners, ordered TL, TR, BR, BL."""
    dx, dy = width * DEFAULT_INSET, height * DEFAULT_INSET
    x0, y0, x1, y1 = dx, dy, width - 1 - dx, height - 1 - dy
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


class PreviewCanvas(QWidget):
    """Paints the frame, the outline and its handles; handles are dragged with the mouse."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._image: QImage | None = None
        self._smoother = QuadSmoother(SMOOTHING_WINDOW)
        self._handles: Quad | None = None
        self._edited = False
        self._locked = False
        self._drag_index: int | None = None
        self._flash = False

    # -- state ------------------------------------------------------------------------------
    @property
    def has_frame(self) -> bool:
        return self._image is not None and not self._image.isNull()

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def edited(self) -> bool:
        return self._edited

    @property
    def flash_active(self) -> bool:
        return self._flash

    def handles(self) -> Quad | None:
        """Current handle positions in frame coordinates (a copy), or ``None`` before a frame."""
        return None if self._handles is None else self._handles.copy()

    def set_frame(self, image: QImage, quad: Any) -> None:
        resized = self._image is None or self._image.size() != image.size()
        self._image = image
        if not self._locked and not self._edited:
            smoothed = self._smoother.update(None if quad is None else as_quad(quad))
            if smoothed is not None:
                self._handles = smoothed.astype(np.float32)
            elif self._handles is None or resized:
                self._handles = default_quad(image.width(), image.height())
        self.update()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self._edited = False
        if not locked:
            self._smoother.reset()
            if self._image is not None:
                self._handles = default_quad(self._image.width(), self._image.height())
        self.update()

    def set_flash(self, on: bool) -> None:
        self._flash = on
        self.update()

    # -- geometry ---------------------------------------------------------------------------
    def _transform(self) -> tuple[float, float, float]:
        """``(scale, offset_x, offset_y)`` mapping frame to widget coordinates."""
        if not self.has_frame:
            return 1.0, 0.0, 0.0
        assert self._image is not None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        return scale, (self.width() - iw * scale) / 2, (self.height() - ih * scale) / 2

    def frame_to_widget(self, point: Any) -> QPointF:
        scale, ox, oy = self._transform()
        return QPointF(float(point[0]) * scale + ox, float(point[1]) * scale + oy)

    def widget_to_frame(self, point: QPointF) -> tuple[float, float]:
        scale, ox, oy = self._transform()
        x, y = (point.x() - ox) / scale, (point.y() - oy) / scale
        if self._image is not None:
            x = min(max(x, 0.0), float(self._image.width() - 1))
            y = min(max(y, 0.0), float(self._image.height() - 1))
        return x, y

    def _image_rect(self) -> QRectF:
        assert self._image is not None
        scale, ox, oy = self._transform()
        return QRectF(ox, oy, self._image.width() * scale, self._image.height() * scale)

    def _handle_at(self, pos: QPointF) -> int | None:
        if self._handles is None:
            return None
        best, best_dist = None, float(HANDLE_HIT_PX)
        for index, corner in enumerate(self._handles):
            p = self.frame_to_widget(corner)
            dist = float(np.hypot(p.x() - pos.x(), p.y() - pos.y()))
            if dist <= best_dist:
                best, best_dist = index, dist
        return best

    # -- mouse ------------------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.has_frame:
            self._drag_index = self._handle_at(event.position())
            if self._drag_index is not None:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_index is not None and self._handles is not None:
            x, y = self.widget_to_frame(event.position())
            self._handles[self._drag_index] = (x, y)
            self._edited = True
            self.update()
            event.accept()
            return
        cursor = (
            Qt.CursorShape.SizeAllCursor
            if self._handle_at(event.position()) is not None
            else Qt.CursorShape.ArrowCursor
        )
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_index is not None and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self._drag_index = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- painting ---------------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#202020"))
        if not self.has_frame:
            painter.end()
            return
        assert self._image is not None
        painter.drawImage(self._image_rect(), self._image)
        if self._handles is not None:
            color = QColor("#ffb900") if self._locked else QColor("#00b7ff")
            polygon = QPolygonF([self.frame_to_widget(p) for p in self._handles])
            painter.setPen(
                QPen(color, 2, Qt.PenStyle.SolidLine if self._locked else Qt.PenStyle.DashLine)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)
            painter.setPen(QPen(QColor("black"), 1))
            painter.setBrush(QBrush(color))
            for point in polygon:
                painter.drawEllipse(point, HANDLE_RADIUS_PX, HANDLE_RADIUS_PX)
        if self._flash:
            painter.fillRect(self._image_rect(), QColor(255, 255, 255, 170))
        painter.end()


class PreviewWidget(QWidget):
    """Preview canvas plus the trigger-state label and the Lock and Clear buttons."""

    def __init__(self, controller: QObject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.canvas = PreviewCanvas(self)

        self.state_label = QLabel("")
        self.state_label.setObjectName("trigger_state")
        self.lock_button = QPushButton("Lock")
        self.lock_button.setToolTip("Use this outline for every capture until cleared")
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Go back to automatic screen detection")
        self.lock_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.lock_button.clicked.connect(self.lock)
        self.clear_button.clicked.connect(self.clear)

        row = QHBoxLayout()
        row.addWidget(self.state_label)
        row.addStretch(1)
        row.addWidget(self.lock_button)
        row.addWidget(self.clear_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(row)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(FLASH_MS)
        self._flash_timer.timeout.connect(lambda: self.canvas.set_flash(False))

        controller.preview_frame.connect(self.on_preview_frame)  # type: ignore[attr-defined]
        controller.trigger_state.connect(self.on_trigger_state)  # type: ignore[attr-defined]
        controller.frame_added.connect(self.on_frame_added)  # type: ignore[attr-defined]

    @property
    def flash_active(self) -> bool:
        return self.canvas.flash_active

    def on_preview_frame(self, image: QImage, quad: Any) -> None:
        self.canvas.set_frame(image, quad)
        self.lock_button.setEnabled(True)
        self.clear_button.setEnabled(True)

    def on_trigger_state(self, state: str) -> None:
        self.state_label.setText(TRIGGER_LABELS.get(state, state))

    def on_frame_added(self, _frame: Any = None) -> None:
        self.canvas.set_flash(True)
        self._flash_timer.start()

    def lock(self) -> None:
        quad = self.canvas.handles()
        if quad is None:
            return
        self._controller.lock_region(quad)  # type: ignore[attr-defined]
        self.canvas.set_locked(True)

    def clear(self) -> None:
        self._controller.lock_region(None)  # type: ignore[attr-defined]
        self.canvas.set_locked(False)
