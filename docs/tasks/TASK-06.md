# TASK-06: Preprocessing (screen detection, warp, encode)
**Status:** done · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Turn a full camera frame into a straightened, cropped, size-capped JPEG of just the source screen.

## Covers
REQ-PRE-01, REQ-PRE-03 (override logic), REQ-PRE-04, REQ-PRE-05, REQ-PRE-06, REQ-NFR-02 (preprocess timing)

## Read first
- [requirements.md §5.2](../requirements.md#52-screen-detection-pre)
- [architecture.md §6 Preprocessing](../architecture.md#6-preprocessing)

## Scope
- `preprocess/detect.py`: `detect_screen(frame) -> Quad | None` (corners ordered TL, TR, BR, BL) and `QuadSmoother` (average of the last 5, for display).
- `preprocess/warp.py`:
  - `warp_to_rect(frame, quad) -> ndarray`, with the degenerate fallback
  - `resize_cap(img, max_edge)`, which never upscales
  - `encode_jpeg(img, quality=90) -> bytes`
  - `process(frame, locked_quad, max_edge) -> ProcessResult(jpeg, quad_used, width, height)`
- `tests/fixtures/images/make_synthetic.py`: renders code text onto a white rectangle with OpenCV, places it on a dark background with a perspective skew (0–15°), and adds Gaussian noise. It is deterministic with a seed.

## Out of scope
Threading (TASK-14 runs `process` in a worker). The UI overlay (TASK-15).

## Target files
`src/screen_copier/preprocess/*.py`, `tests/preprocess/`, `tests/fixtures/images/make_synthetic.py`

## Done when
- A synthetic screen skewed 15° → detected, the warped aspect ratio is within 5% of the true one, and a rendered vertical line is straight within 2 px.
- A uniform gray image → `detect_screen` returns None and `process` uses the full frame.
- A locked quad overrides detection.
- Resize: 3000×2000 → 1568×1045, and 800×600 is unchanged.
- `process` on a synthetic 3840×2160 frame takes ≤300 ms (median of 5; mark it `@pytest.mark.perf`, so it can be skipped on slow CI).
- No PySide6 import.
