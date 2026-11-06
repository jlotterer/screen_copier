# TASK-05: Quality metrics + auto-capture state machine
**Status:** done · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Implement the pure logic that decides when Auto mode captures a frame.

## Covers
REQ-CAM-06, REQ-CAM-07, REQ-CAM-08 (blurry flag)

## Read first
- [requirements.md §5.1](../requirements.md#51-camera--capture-cam)
- [architecture.md §5.2 Quality metrics](../architecture.md#52-quality-metrics-cameraqualitypy), [§5.3 State machine](../architecture.md#53-auto-capture-state-machine-cameratriggerpy)

## Scope
- `camera/quality.py`: `prep`, `diff`, `sharpness`, `is_blurry`, all on numpy arrays.
- `camera/trigger.py`: a `CaptureTrigger` class with an injected clock and thresholds (from a `Settings` or a plain dataclass).
  - `update(diff_prev, sharpness, diff_last_capture) -> TriggerDecision(state, capture: bool, hints: set[str])`
  - `reset()` and `notify_manual_capture()`
  - It must implement the transition table in architecture §5.3 exactly, including the `never_settles` hint.

## Out of scope
Camera I/O and threads (TASK-12).

## Target files
`src/screen_copier/camera/quality.py`, `src/screen_copier/camera/trigger.py`, `tests/camera/test_quality.py`, `tests/camera/test_trigger.py`

## Done when
- Quality: a sharp synthetic text image has sharpness above a Gaussian-blurred copy, `diff` of identical frames is 0, and `diff` of a shifted text image is > `motion_threshold`.
- Trigger, with a fake clock:
  - Moving ×10 (diff 10), then stable (diff 0.5, sharpness 200) for 700 ms → exactly one capture. More stable frames → no capture. Motion then stability → a second capture.
  - Stable with sharpness 50 for 2 s → no capture, state `WAITING_FOCUS`.
  - Returning to near-identical content (`diff_last_capture` 0.5) → no capture (REQ-CAM-07).
  - 5 s of MOVING while sharp → the `never_settles` hint.
- Every row of the transition table has a test.
- No PySide6 import.
