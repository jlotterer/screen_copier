"""Tests for warp, resize, encode and ``process`` (TASK-06; REQ-PRE-01/03/04/05, REQ-NFR-02)."""

from __future__ import annotations

import logging
import statistics
import time
from types import ModuleType

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from screen_copier.preprocess.detect import detect_screen
from screen_copier.preprocess.warp import (
    ProcessResult,
    encode_jpeg,
    estimate_aspect_ratio,
    process,
    resize_cap,
    warp_to_rect,
)

MAX_EDGE = 1568  # architecture §8.1 default for max_image_edge_px


def _decode(jpeg: bytes) -> npt.NDArray[np.uint8]:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert image is not None
    return image


def _reference_line_xs(warped: npt.NDArray[np.uint8], sample) -> npt.NDArray[np.float64]:  # type: ignore[no-untyped-def]
    """Sub-pixel x position of the dark reference line in each row of the warped screen."""
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY).astype(np.float64)
    height, width = gray.shape
    top, bottom = sample.line_y_frac
    span = bottom - top
    rows = range(round(height * (top + 0.05 * span)), round(height * (bottom - 0.05 * span)))
    center = sample.line_x_frac * width
    lo, hi = round(center - 0.03 * width), round(center + 0.03 * width)
    xs = []
    for row in rows:
        darkness = np.clip(200.0 - gray[row, lo:hi], 0, None)
        assert darkness.sum() > 0, f"reference line not found in row {row}"
        xs.append(lo + float(np.dot(np.arange(hi - lo), darkness) / darkness.sum()))
    return np.array(xs)


# --------------------------------------------------------------------------------------------
# REQ-PRE-01: perspective correction
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaw", "pitch", "hfov"),
    [
        (15, 0, 65),
        (-15, 0, 65),
        (0, 15, 65),
        (15, 5, 65),
        (10, -10, 65),
        (15, 0, 45),
        (15, 0, 78),
        (15, 15, 60),
    ],
)
def test_skewed_screen_is_rectified(
    synth: ModuleType, yaw: float, pitch: float, hfov: float
) -> None:
    sample = synth.make_synthetic(yaw_deg=yaw, pitch_deg=pitch, hfov_deg=hfov, seed=7)
    quad = detect_screen(sample.frame)
    assert quad is not None
    warped = warp_to_rect(sample.frame, quad)

    height, width = warped.shape[:2]
    assert width / height == pytest.approx(sample.aspect, rel=0.05)

    xs = _reference_line_xs(warped, sample)
    assert xs.max() - xs.min() <= 2.0, "vertical line is not straight and vertical"


def test_frontal_rectangle_aspect_is_exact() -> None:
    quad = np.array([[100, 50], [900, 50], [900, 550], [100, 550]], dtype=np.float32)
    assert estimate_aspect_ratio(quad, (1000, 600)) == pytest.approx(1.6, rel=1e-6)


def test_aspect_of_collinear_quad_is_none() -> None:
    quad = np.array([[0, 0], [100, 0], [200, 0], [300, 0]], dtype=np.float32)
    assert estimate_aspect_ratio(quad, (400, 400)) is None


@pytest.mark.parametrize(("yaw", "pitch"), [(15, 0), (-15, 0), (0, 15), (0, -15), (15, 1), (2, 1)])
def test_aspect_estimate_near_single_axis_tilt(synth: ModuleType, yaw: float, pitch: float) -> None:
    # Single-axis tilt cannot recover the focal length; the assumed one must still be close.
    sample = synth.make_synthetic(yaw_deg=yaw, pitch_deg=pitch, seed=11)
    aspect = estimate_aspect_ratio(sample.quad, (1920, 1080))
    assert aspect == pytest.approx(sample.aspect, rel=0.05)


def test_warp_output_follows_quad() -> None:
    frame = np.zeros((600, 1000, 3), dtype=np.uint8)
    frame[100:300, 200:600] = (0, 0, 255)
    quad = np.array([[200, 100], [600, 100], [600, 300], [200, 300]], dtype=np.float32)
    warped = warp_to_rect(frame, quad)
    assert warped.shape == (200, 400, 3)
    # Interior is the red rectangle.
    assert (warped[10:-10, 10:-10] == (0, 0, 255)).all()


def test_degenerate_quad_falls_back_to_full_frame(caplog: pytest.LogCaptureFixture) -> None:
    frame = np.zeros((600, 1000, 3), dtype=np.uint8)
    tiny = np.array([[10, 10], [30, 10], [30, 30], [10, 30]], dtype=np.float32)
    with caplog.at_level(logging.WARNING):
        assert warp_to_rect(frame, tiny) is frame
    assert "full frame" in caplog.text


def test_non_convex_quad_falls_back_to_full_frame() -> None:
    frame = np.zeros((600, 1000, 3), dtype=np.uint8)
    bowtie = np.array([[0, 0], [900, 500], [900, 0], [0, 500]], dtype=np.float32)
    assert warp_to_rect(frame, bowtie) is frame


# --------------------------------------------------------------------------------------------
# REQ-PRE-05: resize cap, never upscale
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((3000, 2000), (1568, 1045)),
        ((2000, 3000), (1045, 1568)),
        ((800, 600), (800, 600)),
        ((1568, 1000), (1568, 1000)),
        ((1569, 1569), (1568, 1568)),
    ],
)
def test_resize_cap(size: tuple[int, int], expected: tuple[int, int]) -> None:
    width, height = size
    img = np.zeros((height, width, 3), dtype=np.uint8)
    out = resize_cap(img, MAX_EDGE)
    assert (out.shape[1], out.shape[0]) == expected
    assert out.shape[2] == 3


def test_resize_never_upscales_and_returns_input() -> None:
    img = np.zeros((600, 800, 3), dtype=np.uint8)
    assert resize_cap(img, MAX_EDGE) is img


def test_resize_rejects_bad_limit() -> None:
    with pytest.raises(ValueError):
        resize_cap(np.zeros((10, 10, 3), dtype=np.uint8), 0)


def test_encode_jpeg_is_color_jpeg() -> None:
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)
    data = encode_jpeg(img)
    assert data[:2] == b"\xff\xd8"
    decoded = _decode(data)
    assert decoded.shape == (120, 160, 3)
    # Quality 90 is near-lossless on a smooth image.
    smooth = np.full((64, 64, 3), (40, 120, 200), dtype=np.uint8)
    assert np.abs(_decode(encode_jpeg(smooth)).astype(int) - smooth).max() <= 3


def test_encode_jpeg_rejects_bad_quality() -> None:
    with pytest.raises(ValueError):
        encode_jpeg(np.zeros((8, 8, 3), dtype=np.uint8), quality=101)


# --------------------------------------------------------------------------------------------
# process(): REQ-PRE-03 locked override, REQ-PRE-04 full-frame fallback
# --------------------------------------------------------------------------------------------


def test_process_uses_detected_screen(synth: ModuleType) -> None:
    sample = synth.make_synthetic(yaw_deg=15, pitch_deg=5, seed=8)
    result = process(sample.frame, None, MAX_EDGE)
    assert isinstance(result, ProcessResult)
    assert result.quad_used is not None
    assert np.abs(result.quad_used - sample.quad).max() <= 3.0
    assert max(result.width, result.height) <= MAX_EDGE
    assert result.width / result.height == pytest.approx(sample.aspect, rel=0.05)
    assert _decode(result.jpeg).shape == (result.height, result.width, 3)


def test_process_uniform_gray_uses_full_frame() -> None:
    frame = np.full((800, 1200, 3), 128, dtype=np.uint8)
    assert detect_screen(frame) is None
    result = process(frame, None, MAX_EDGE)
    assert result.quad_used is None
    assert (result.width, result.height) == (1200, 800)
    assert _decode(result.jpeg).shape == (800, 1200, 3)


def test_process_full_frame_is_capped() -> None:
    frame = np.full((2160, 3840, 3), 128, dtype=np.uint8)
    result = process(frame, None, MAX_EDGE)
    assert result.quad_used is None
    assert (result.width, result.height) == (1568, 882)


def test_locked_quad_overrides_detection(synth: ModuleType) -> None:
    sample = synth.make_synthetic(yaw_deg=10, seed=9)
    detected = detect_screen(sample.frame)
    assert detected is not None
    locked = np.array([[300, 200], [1100, 200], [1100, 700], [300, 700]], dtype=np.float32)
    assert np.abs(locked - detected).max() > 50  # genuinely different from detection

    result = process(sample.frame, locked, MAX_EDGE)
    assert result.quad_used is not None
    np.testing.assert_array_equal(result.quad_used, locked)
    assert (result.width, result.height) == (800, 500)


def test_locked_quad_used_when_nothing_detected() -> None:
    frame = np.full((800, 1200, 3), 128, dtype=np.uint8)
    locked = [[100, 100], [700, 100], [700, 500], [100, 500]]
    result = process(frame, locked, MAX_EDGE)  # type: ignore[arg-type]
    assert result.quad_used is not None
    assert (result.width, result.height) == (600, 400)


def test_degenerate_locked_quad_uses_full_frame() -> None:
    frame = np.full((800, 1200, 3), 128, dtype=np.uint8)
    tiny = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.float32)
    result = process(frame, tiny, MAX_EDGE)
    assert result.quad_used is None
    assert (result.width, result.height) == (1200, 800)


# --------------------------------------------------------------------------------------------
# Synthetic generator
# --------------------------------------------------------------------------------------------


def test_synthetic_generator_is_deterministic(synth: ModuleType) -> None:
    a = synth.make_synthetic(yaw_deg=12, seed=42)
    b = synth.make_synthetic(yaw_deg=12, seed=42)
    c = synth.make_synthetic(yaw_deg=12, seed=43)
    assert np.array_equal(a.frame, b.frame)
    assert not np.array_equal(a.frame, c.frame)


# --------------------------------------------------------------------------------------------
# REQ-NFR-02: preparing a 4K frame takes <= 300 ms
# --------------------------------------------------------------------------------------------


@pytest.mark.perf
def test_process_4k_frame_is_fast(synth: ModuleType) -> None:
    sample = synth.make_synthetic(
        frame_size=(3840, 2160), screen_size=(2560, 1600), yaw_deg=15, pitch_deg=5, seed=10
    )
    process(sample.frame, None, MAX_EDGE)  # warm up
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        result = process(sample.frame, None, MAX_EDGE)
        timings.append(time.perf_counter() - start)
    assert result.quad_used is not None
    assert statistics.median(timings) <= 0.300, timings
