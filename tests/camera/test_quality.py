"""Tests for camera/quality.py (architecture §5.2)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from screen_copier.camera.quality import PREP_WIDTH, diff, is_blurry, prep, sharpness
from screen_copier.camera.trigger import TriggerConfig

MOTION_THRESHOLD = TriggerConfig().motion_threshold
SHARPNESS_THRESHOLD = TriggerConfig().sharpness_threshold


def text_image(width: int = 1280, height: int = 720, offset_y: int = 0) -> np.ndarray:
    """A BGR image of dark-on-light 'code', with all lines shifted down by ``offset_y``."""
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    line_h = 28
    for i in range(-2, height // line_h + 2):
        y = 30 + i * line_h + offset_y
        text = f"{i:03d}  def function_{i}(arg, other): return arg * {i} + other  # x"
        cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)
    return img


def test_prep_is_gray_and_320_wide_keeping_aspect() -> None:
    small = prep(text_image(1280, 720))
    assert small.ndim == 2
    assert small.dtype == np.uint8
    assert small.shape == (180, PREP_WIDTH)


def test_prep_accepts_grayscale_input() -> None:
    gray = cv2.cvtColor(text_image(640, 480), cv2.COLOR_BGR2GRAY)
    assert prep(gray).shape == (240, PREP_WIDTH)


def test_prep_rejects_bad_shape() -> None:
    with pytest.raises(ValueError):
        prep(np.zeros((2, 2, 2, 2), dtype=np.uint8))


def test_diff_of_identical_frames_is_zero() -> None:
    a = prep(text_image())
    assert diff(a, a.copy()) == 0.0


def test_diff_of_shifted_text_exceeds_motion_threshold() -> None:
    a = prep(text_image())
    b = prep(text_image(offset_y=14))
    assert diff(a, b) > MOTION_THRESHOLD


def test_diff_uses_absolute_value_without_uint8_wraparound() -> None:
    a = np.full((10, 10), 10, dtype=np.uint8)
    b = np.full((10, 10), 30, dtype=np.uint8)
    assert diff(a, b) == pytest.approx(20.0)
    assert diff(b, a) == pytest.approx(20.0)


def test_diff_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        diff(np.zeros((4, 4), np.uint8), np.zeros((4, 5), np.uint8))


def test_sharp_text_is_sharper_than_blurred_copy() -> None:
    img = text_image()
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=10)
    sharp_value = sharpness(prep(img))
    blurred_value = sharpness(prep(blurred))
    assert sharp_value > blurred_value
    assert not is_blurry(sharp_value, SHARPNESS_THRESHOLD)
    assert is_blurry(blurred_value, SHARPNESS_THRESHOLD)


def test_is_blurry_boundary() -> None:
    assert is_blurry(99.9, 100.0)
    assert not is_blurry(100.0, 100.0)
