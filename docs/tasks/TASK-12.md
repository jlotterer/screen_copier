# TASK-12: Camera source (worker thread, enumeration, exposure, fake)
**Status:** todo · **Wave:** 4 · **Depends on:** TASK-04, TASK-05 · **Parallel:** Yes

## Goal
Deliver camera frames to the app off the GUI thread, together with quality metrics and auto-capture decisions. Provide a fake camera for tests.

## Covers
REQ-CAM-01, REQ-CAM-02, REQ-CAM-03, REQ-CAM-04, REQ-CAM-10, REQ-NFR-02 (preview fps)

## Read first
- [requirements.md §5.1](../requirements.md#51-camera--capture-cam)
- [architecture.md §4 Threading](../architecture.md#4-threading-model), [§5.1 Camera source](../architecture.md#51-camera-source)
- [decision 0001](../decisions/0001-pyside6-desktop-app.md)

## Scope
- `camera/source.py`:
  - `list_cameras() -> list[CameraInfo(id, name)]` (QMediaDevices names, "Camera N" fallback)
  - `CameraWorker(QThread)`, which:
    - opens with DSHOW and walks the resolution ladder
    - applies auto/manual exposure and can open the driver settings
    - computes metrics (TASK-05) and runs `CaptureTrigger` when Auto mode is on
    - emits `preview_frame(QImage)`, `metrics(...)`, `trigger_state(str)`, `capture_requested(ndarray, FrameQuality)`, `resolution(int, int)`, `camera_error(kind, message)`
    - accepts slots `set_auto(bool)`, `capture_now()`, `set_exposure(auto, value)`, `open_driver_settings()`, `stop()`
- `camera/fake.py`: `FakeCameraSource` (folder or video), with the same read interface as `cv2.VideoCapture`, so the worker can use either. Selected automatically when `SCREEN_COPIER_FAKE_CAMERA` is set.

## Out of scope
Preprocessing, saving and recognition (TASK-14). Widgets (TASK-15).

## Target files
`src/screen_copier/camera/source.py`, `src/screen_copier/camera/fake.py`, `tests/camera/test_source.py`, `tests/fixtures/camera/basic/`

## Done when
- The fake camera folder of 3 PNGs cycles in the preview (pytest-qt `waitSignal`).
- A fake camera that reports 1920×1080 → `resolution` is emitted as (1920, 1080).
- 30 fps fake for 3 s → ≥45 `preview_frame` signals.
- 30 consecutive read failures → `camera_error("disconnected")`.
- `capture_now()` emits exactly one `capture_requested`.
- In Auto mode with a fake sequence of moving → still images, exactly one `capture_requested` is emitted.
- **Manual (device-only):** a real webcam reaches ≥1080p and ≥15 fps preview, and the camera names match (assumption A-01). Record the results in the PR.
