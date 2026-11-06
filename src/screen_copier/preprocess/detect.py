"""Screen quad detection (architecture §6.1). Pure logic: no PySide6.

A *quad* is a ``(4, 2)`` float32 array of pixel coordinates in the source frame, ordered
top-left, top-right, bottom-right, bottom-left.
"""

from __future__ import annotations

import math
from collections import deque

import cv2
import numpy as np
import numpy.typing as npt

type Quad = npt.NDArray[np.float32]
"""Screen corners, shape (4, 2), float32, ordered TL, TR, BR, BL."""

type Image = npt.NDArray[np.uint8]

# Algorithm constants from architecture §6.1 (not user-tunable settings).
BLUR_KSIZE = 5
CANNY_LOW = 50
CANNY_HIGH = 150
DILATE_KSIZE = 3
APPROX_EPSILON_FRAC = 0.02
"""``approxPolyDP`` tolerance as a fraction of the contour perimeter."""
MIN_AREA_FRAC = 0.20
"""A candidate quad must cover at least this fraction of the frame."""
SMOOTHING_WINDOW = 5
"""Number of recent detections averaged for the preview overlay."""


def as_quad(points: npt.ArrayLike) -> Quad:
    """Convert ``points`` to a quad array, validating shape and values (does not reorder)."""
    quad = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if quad.shape != (4, 2):
        raise ValueError(f"a quad needs exactly 4 (x, y) points, got shape {quad.shape}")
    if not np.all(np.isfinite(quad)):
        raise ValueError("quad coordinates must be finite")
    return quad


def order_corners(points: npt.ArrayLike) -> Quad:
    """Order 4 points TL, TR, BR, BL (image coordinates, y pointing down).

    Points are sorted clockwise by angle around their centroid, then rotated so the point
    nearest the image origin (smallest ``x + y``) comes first.
    """
    pts = as_quad(points)
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    pts = pts[np.argsort(angles, kind="stable")]
    start = int(np.argmin(pts.sum(axis=1)))
    return np.roll(pts, -start, axis=0)


def _to_gray(frame: Image) -> Image:
    if frame.ndim == 2:
        return frame
    if frame.ndim == 3 and frame.shape[2] == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    raise ValueError(f"unsupported frame shape {frame.shape}")


def detect_screen(frame: Image) -> Quad | None:
    """Find the source screen in a camera frame.

    Returns the largest convex 4-corner contour covering at least ``MIN_AREA_FRAC`` of the
    frame, ordered TL, TR, BR, BL, or ``None`` if nothing qualifies.
    """
    gray = _to_gray(frame)
    height, width = gray.shape[:2]
    min_area = MIN_AREA_FRAC * width * height

    blurred = cv2.GaussianBlur(gray, (BLUR_KSIZE, BLUR_KSIZE), 0)
    edges = cv2.Canny(blurred, CANNY_LOW, CANNY_HIGH)
    edges = cv2.dilate(edges, np.ones((DILATE_KSIZE, DILATE_KSIZE), dtype=np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best: npt.NDArray[np.int32] | None = None
    best_area = min_area
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue  # cheap reject before approximating
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, APPROX_EPSILON_FRAC * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        area = cv2.contourArea(approx)
        if area >= best_area:
            best, best_area = approx, area

    if best is None:
        return None
    return order_corners(best.reshape(4, 2))


class QuadSmoother:
    """Averages corner positions over the last few detections, for the preview overlay only.

    Capture never uses the smoothed quad (§6.1). A missed detection (``None``) clears the
    history, so the overlay disappears instead of showing a stale outline.
    """

    def __init__(self, window: int = SMOOTHING_WINDOW) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        self._history: deque[Quad] = deque(maxlen=window)

    def update(self, quad: Quad | None) -> Quad | None:
        """Add the latest detection and return the smoothed quad (``None`` if not detected)."""
        if quad is None:
            self._history.clear()
            return None
        self._history.append(as_quad(quad))
        return self.current

    @property
    def current(self) -> Quad | None:
        if not self._history:
            return None
        return np.mean(np.stack(self._history), axis=0).astype(np.float32)

    def reset(self) -> None:
        self._history.clear()


def quad_area(quad: Quad) -> float:
    """Area of a quad in square pixels (shoelace formula)."""
    x, y = quad[:, 0].astype(np.float64), quad[:, 1].astype(np.float64)
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def edge_lengths(quad: Quad) -> tuple[float, float, float, float]:
    """Lengths of the top, right, bottom and left edges."""
    tl, tr, br, bl = (quad[i].astype(np.float64) for i in range(4))
    return (
        math.dist(tl, tr),
        math.dist(tr, br),
        math.dist(br, bl),
        math.dist(bl, tl),
    )
