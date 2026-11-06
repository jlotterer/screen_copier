"""Frame quality metrics for auto-capture (architecture §5.2).

Pure logic on numpy arrays. Must not import PySide6.

Typical use per preview frame::

    small = prep(frame)
    d_prev = diff(small, prev_small)
    s = sharpness(small)
"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

PREP_WIDTH = 320
"""Width, in pixels, that frames are downscaled to before computing metrics (§5.2)."""


def prep(frame: NDArray[np.uint8], width: int = PREP_WIDTH) -> NDArray[np.uint8]:
    """Convert a BGR (or already grayscale) frame to grayscale and resize it to ``width``.

    The aspect ratio is kept, and ``INTER_AREA`` is used for the resize.
    """
    if frame.ndim == 3:
        if frame.shape[2] == 4:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        elif frame.shape[2] == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        elif frame.shape[2] == 1:
            gray = frame[:, :, 0]
        else:
            raise ValueError(f"unsupported channel count: {frame.shape[2]}")
    elif frame.ndim == 2:
        gray = frame
    else:
        raise ValueError(f"unsupported frame shape: {frame.shape}")

    h, w = gray.shape
    if w == 0 or h == 0:
        raise ValueError("empty frame")
    height = max(1, round(h * width / w))
    return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)


def diff(a: NDArray[np.uint8], b: NDArray[np.uint8]) -> float:
    """Mean absolute difference of two prepped frames, on a 0-255 scale."""
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    return float(np.mean(cv2.absdiff(a, b)))


def sharpness(a: NDArray[np.uint8]) -> float:
    """Variance of the Laplacian of a prepped frame. Higher means sharper."""
    return float(cv2.Laplacian(a, cv2.CV_64F).var())


def is_blurry(sharpness_value: float, sharpness_threshold: float) -> bool:
    """A frame is blurry when its sharpness is below the threshold (REQ-CAM-08)."""
    return sharpness_value < sharpness_threshold
