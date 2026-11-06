"""Tests for screen detection (TASK-06; REQ-PRE-01, REQ-PRE-04)."""

from __future__ import annotations

import itertools
import subprocess
import sys
from types import ModuleType

import numpy as np
import pytest

from screen_copier.preprocess.detect import (
    QuadSmoother,
    as_quad,
    detect_screen,
    order_corners,
)

ORDERED = np.array([[10, 20], [110, 25], [105, 90], [12, 85]], dtype=np.float32)


@pytest.mark.parametrize(("yaw", "pitch"), [(15, 0), (-15, 0), (0, 15), (15, 5), (10, -10)])
def test_skewed_screen_is_detected(synth: ModuleType, yaw: float, pitch: float) -> None:
    sample = synth.make_synthetic(yaw_deg=yaw, pitch_deg=pitch, seed=3)
    quad = detect_screen(sample.frame)
    assert quad is not None
    assert quad.shape == (4, 2)
    assert quad.dtype == np.float32
    # Corners come back ordered TL, TR, BR, BL and land close to the true corners.
    assert np.abs(quad - sample.quad).max() <= 3.0


def test_grayscale_frame_is_accepted(synth: ModuleType) -> None:
    sample = synth.make_synthetic(seed=4)
    gray = sample.frame.mean(axis=2).astype(np.uint8)
    quad = detect_screen(gray)
    assert quad is not None
    assert np.abs(quad - sample.quad).max() <= 3.0


def test_uniform_gray_image_has_no_screen() -> None:
    frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
    assert detect_screen(frame) is None


def test_small_screen_is_ignored(synth: ModuleType) -> None:
    # fill=0.35 -> roughly 0.35 * 0.35 * (16/10) / (16/9) ~ 11% of the frame, below the 20% floor.
    sample = synth.make_synthetic(yaw_deg=0, fill=0.35, seed=5)
    assert detect_screen(sample.frame) is None


def test_largest_candidate_wins(synth: ModuleType) -> None:
    sample = synth.make_synthetic(yaw_deg=0, fill=0.8, seed=6)
    frame = sample.frame.copy()
    # A second, smaller bright rectangle that is still above the area floor would lose; here we
    # just make sure a bright rectangle inside the screen (e.g. a dialog) is not picked instead.
    frame[400:700, 700:1200] = 250
    quad = detect_screen(frame)
    assert quad is not None
    assert np.abs(quad - sample.quad).max() <= 3.0


@pytest.mark.parametrize("perm", list(itertools.permutations(range(4))))
def test_order_corners_any_input_order(perm: tuple[int, ...]) -> None:
    np.testing.assert_array_equal(order_corners(ORDERED[list(perm)]), ORDERED)


def test_order_corners_rotated_quad() -> None:
    # A quad rotated ~30° is still ordered starting from the corner nearest the origin.
    quad = np.array([[50, 0], [137, 50], [87, 137], [0, 87]], dtype=np.float32)
    ordered = order_corners(quad[[2, 0, 3, 1]])
    np.testing.assert_array_equal(ordered, quad)


def test_as_quad_validates() -> None:
    np.testing.assert_array_equal(as_quad(ORDERED.tolist()), ORDERED)
    with pytest.raises(ValueError):
        as_quad([[0, 0], [1, 0], [1, 1]])
    with pytest.raises(ValueError):
        as_quad([[0, 0], [1, 0], [1, np.nan], [0, 1]])


def test_smoother_averages_last_five() -> None:
    smoother = QuadSmoother()
    assert smoother.current is None
    for i in range(7):
        smoothed = smoother.update(ORDERED + i)
    # Only offsets 2..6 are in the window: mean offset 4.
    assert smoothed is not None
    np.testing.assert_allclose(smoothed, ORDERED + 4)
    np.testing.assert_allclose(smoother.current, ORDERED + 4)


def test_smoother_partial_window() -> None:
    smoother = QuadSmoother()
    smoother.update(ORDERED)
    smoothed = smoother.update(ORDERED + 2)
    np.testing.assert_allclose(smoothed, ORDERED + 1)


def test_smoother_missed_detection_clears_history() -> None:
    smoother = QuadSmoother()
    smoother.update(ORDERED + 100)
    assert smoother.update(None) is None
    assert smoother.current is None
    np.testing.assert_allclose(smoother.update(ORDERED), ORDERED)


def test_smoother_reset_and_validation() -> None:
    smoother = QuadSmoother(window=2)
    smoother.update(ORDERED)
    smoother.reset()
    assert smoother.current is None
    with pytest.raises(ValueError):
        QuadSmoother(window=0)


def test_preprocess_does_not_import_pyside6() -> None:
    code = (
        "import sys\n"
        "import screen_copier.preprocess.detect, screen_copier.preprocess.warp\n"
        "bad = [m for m in sys.modules if m.split('.')[0] == 'PySide6']\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
