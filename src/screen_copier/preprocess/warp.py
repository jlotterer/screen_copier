"""Perspective warp, resize and JPEG encode (architecture §6.2). Pure logic: no PySide6."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from screen_copier.preprocess.detect import (
    Image,
    Quad,
    as_quad,
    detect_screen,
    edge_lengths,
    quad_area,
)

logger = logging.getLogger(__name__)

# Algorithm constants from architecture §6.2 (not user-tunable settings).
DEGENERATE_AREA_FRAC = 0.01
"""A warp whose output is smaller than this fraction of the frame falls back to the full frame."""
JPEG_QUALITY = 90
ASSUMED_FOCAL_FRAC = 0.8
"""Focal length (as a fraction of the frame width) assumed when it cannot be estimated from the
quad, i.e. when the screen is tilted about at most one axis. 0.8 is about a 64° horizontal field
of view, typical for a laptop webcam."""
FOCAL_RANGE_FRAC = (0.3, 3.0)
"""An estimated focal length outside this range (fractions of the frame width) is not trusted."""
MIN_TILT = 0.01
"""The focal length is only estimated when both edge pairs converge at least this much (a
dimensionless measure, about 0.02 per degree of tilt). Below it, one pair is near-parallel and
the estimate is dominated by corner noise."""


@dataclass(frozen=True)
class ProcessResult:
    jpeg: bytes
    """Encoded image: stored as ``frames/<seq>.jpg`` and sent to the API."""
    quad_used: Quad | None
    """The quad that was warped, or ``None`` if the whole frame was used."""
    width: int
    height: int


def _homogeneous(point: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
    return np.array([point[0], point[1], 1.0], dtype=np.float64)


def estimate_aspect_ratio(quad: Quad, frame_size: tuple[int, int]) -> float | None:
    """Estimate the true width/height ratio of the rectangle imaged as ``quad``.

    Uses Zhang & He, "Whiteboard scanning and image enhancement" (2007): assuming square pixels
    and the principal point at the frame center, the focal length is recovered from the quad's
    vanishing points and the aspect ratio follows. When the focal length cannot be recovered
    (the screen is tilted about only one axis, so one pair of edges stays parallel), a typical
    webcam focal length is assumed; the result is then insensitive to that guess.

    Returns ``None`` if the quad is degenerate (e.g. three collinear corners).
    """
    width, height = frame_size
    u0, v0 = width / 2, height / 2
    tl, tr, br, bl = (_homogeneous(p) for p in quad)

    denom2 = float(np.dot(np.cross(tr, br), bl))
    denom3 = float(np.dot(np.cross(bl, br), tr))
    if abs(denom2) < 1e-9 or abs(denom3) < 1e-9:
        return None
    k2 = float(np.dot(np.cross(tl, br), bl)) / denom2
    k3 = float(np.dot(np.cross(tl, br), tr)) / denom3
    n2 = k2 * tr - tl  # direction of the top edge in 3D, up to scale
    n3 = k3 * bl - tl  # direction of the left edge in 3D, up to scale

    def centered(n: npt.NDArray[np.float64]) -> tuple[float, float]:
        return n[0] - u0 * n[2], n[1] - v0 * n[2]

    (a2, b2), (a3, b3) = centered(n2), centered(n3)
    len2, len3 = math.hypot(a2, b2), math.hypot(a3, b3)
    if len2 == 0 or len3 == 0:
        return None
    # How strongly each edge pair converges (0 = parallel in the image).
    tilt2, tilt3 = abs(n2[2]) * width / len2, abs(n3[2]) * width / len3
    focal = ASSUMED_FOCAL_FRAC * width
    if min(tilt2, tilt3) >= MIN_TILT:
        focal_sq = -(a2 * a3 + b2 * b3) / (n2[2] * n3[2])
        lo, hi = (f * width for f in FOCAL_RANGE_FRAC)
        if lo * lo <= focal_sq <= hi * hi:
            focal = math.sqrt(focal_sq)

    norm2 = (a2 * a2 + b2 * b2) / (focal * focal) + n2[2] ** 2
    norm3 = (a3 * a3 + b3 * b3) / (focal * focal) + n3[2] ** 2
    if norm3 <= 0 or not math.isfinite(norm2 / norm3) or norm2 <= 0:
        return None
    return math.sqrt(norm2 / norm3)


def target_size(quad: Quad, frame_size: tuple[int, int]) -> tuple[int, int]:
    """Output (width, height) for warping ``quad``: the estimated true aspect ratio, at the
    resolution of the quad's longest edges (never smaller than either)."""
    top, right, bottom, left = edge_lengths(quad)
    width, height = max(top, bottom), max(left, right)
    aspect = estimate_aspect_ratio(quad, frame_size)
    if aspect is not None and height > 0:
        if width / height < aspect:
            width = height * aspect
        else:
            height = width / aspect
    return max(1, round(width)), max(1, round(height))


def _warp_or_none(frame: Image, quad: Quad) -> Image | None:
    """Warp ``quad`` to an upright rectangle, or ``None`` if the result would be degenerate."""
    frame_h, frame_w = frame.shape[:2]
    frame_area = frame_w * frame_h
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
        logger.warning("Screen outline is not convex; using the full frame")
        return None
    if quad_area(quad) < DEGENERATE_AREA_FRAC * frame_area:
        logger.warning("Screen outline is too small; using the full frame")
        return None
    out_w, out_h = target_size(quad, (frame_w, frame_h))
    if out_w * out_h < DEGENERATE_AREA_FRAC * frame_area:
        logger.warning("Warped screen is degenerate (%dx%d); using the full frame", out_w, out_h)
        return None
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(frame, matrix, (out_w, out_h), flags=cv2.INTER_CUBIC)


def warp_to_rect(frame: Image, quad: Quad) -> Image:
    """Warp the screen outlined by ``quad`` (TL, TR, BR, BL) to an upright rectangle.

    Falls back to the full frame (and logs a warning) if the quad is degenerate.
    """
    warped = _warp_or_none(frame, as_quad(quad))
    return frame if warped is None else warped


def resize_cap(img: Image, max_edge: int) -> Image:
    """Scale ``img`` down so its long edge is at most ``max_edge``. Never upscales."""
    if max_edge < 1:
        raise ValueError("max_edge must be positive")
    height, width = img.shape[:2]
    long_edge = max(width, height)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def encode_jpeg(img: Image, quality: int = JPEG_QUALITY) -> bytes:
    """Encode ``img`` (BGR, or grayscale) as JPEG bytes."""
    if not 0 <= quality <= 100:
        raise ValueError("quality must be between 0 and 100")
    ok, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buffer.tobytes()


def process(frame: Image, locked_quad: Quad | None, max_edge: int) -> ProcessResult:
    """Prepare a captured frame for recognition: pick the quad, warp, cap the size, encode.

    The locked quad, if any, overrides detection (REQ-PRE-03). With neither a locked nor a
    detected quad, or if the quad is degenerate, the whole frame is used (REQ-PRE-04).
    """
    quad = as_quad(locked_quad) if locked_quad is not None else detect_screen(frame)
    image = frame
    if quad is not None:
        warped = _warp_or_none(frame, quad)
        if warped is None:
            quad = None
        else:
            image = warped
    image = resize_cap(image, max_edge)
    height, width = image.shape[:2]
    return ProcessResult(jpeg=encode_jpeg(image), quad_used=quad, width=width, height=height)
