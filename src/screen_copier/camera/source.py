"""Camera enumeration and the camera worker thread (architecture §4, §5.1).

``CameraWorker`` owns the capture device. All reads, metrics and trigger decisions happen inside
``run()`` on the worker thread; results reach the GUI through signals, which Qt queues across
threads. The slots (``set_auto``, ``capture_now``, ``set_exposure``, ``open_driver_settings``,
``stop``) only record a request under a lock and return at once; the run loop applies it.

Set ``SCREEN_COPIER_FAKE_CAMERA`` to a folder of images or a video file to use
``FakeCameraSource`` instead of a real camera (REQ-CAM-10).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from screen_copier.camera.fake import FakeCameraSource
from screen_copier.camera.quality import diff, is_blurry, prep, sharpness
from screen_copier.camera.trigger import NEVER_SETTLES, CaptureTrigger, TriggerState
from screen_copier.models import CaptureMode, FrameQuality

logger = logging.getLogger(__name__)

FAKE_CAMERA_ENV = "SCREEN_COPIER_FAKE_CAMERA"

MAX_DSHOW_INDEX = 10
"""DirectShow indices 0..9 are probed during enumeration (§5.1)."""

RESOLUTION_LADDER: tuple[tuple[int, int], ...] = (
    (3840, 2160),
    (2560, 1440),
    (1920, 1080),
    (1280, 720),
)
"""Resolutions tried in order on open; the first that reads back at that size wins (§5.1)."""

MIN_RESOLUTION = (1280, 720)
"""Below this the ``low_resolution`` hint is raised."""

DISCONNECT_AFTER_FAILED_READS = 30
"""Consecutive failed reads after which the camera counts as disconnected (§5.1)."""

READ_RETRY_DELAY_S = 0.01
"""Pause after a failed read so a dead device doesn't spin the CPU."""

AUTO_EXPOSURE_ON = 0.75
AUTO_EXPOSURE_OFF = 0.25
"""DirectShow values for ``CAP_PROP_AUTO_EXPOSURE``."""

ERROR_NOT_FOUND = "not_found"
ERROR_UNAVAILABLE = "unavailable"
ERROR_DISCONNECTED = "disconnected"
HINT_LOW_RESOLUTION = "low_resolution"
HINT_NEVER_SETTLES = NEVER_SETTLES

MAX_DIFF = 255.0
"""``diff_prev`` used when there is no previous frame to compare with."""


class CaptureDevice(Protocol):
    """The subset of ``cv2.VideoCapture`` the worker uses."""

    def isOpened(self) -> bool: ...
    def read(self) -> tuple[bool, Any]: ...
    def get(self, prop_id: int) -> float: ...
    def set(self, prop_id: int, value: float) -> bool: ...
    def release(self) -> None: ...


CaptureFactory = Callable[[int, int], CaptureDevice]
"""Called as ``factory(camera_index, cv2.CAP_DSHOW)``, like ``cv2.VideoCapture``."""


@dataclass(frozen=True, slots=True)
class CameraInfo:
    id: int
    name: str


# -- source selection --------------------------------------------------------------------------


def fake_camera_path(env: Mapping[str, str] | None = None) -> Path | None:
    """The ``SCREEN_COPIER_FAKE_CAMERA`` path, or ``None`` when unset or empty."""
    value = (os.environ if env is None else env).get(FAKE_CAMERA_ENV, "").strip()
    return Path(value) if value else None


def select_capture_factory(env: Mapping[str, str] | None = None) -> CaptureFactory:
    """``FakeCameraSource`` when ``SCREEN_COPIER_FAKE_CAMERA`` is set, else ``cv2.VideoCapture``.

    The fake ignores the camera index and plays the configured folder or video file.
    """
    path = fake_camera_path(env)
    if path is None:
        return cv2.VideoCapture
    if not path.exists():
        logger.warning("%s points to a missing path: %s", FAKE_CAMERA_ENV, path)

    def fake_factory(index: int, api: int = 0) -> CaptureDevice:
        return FakeCameraSource(path)

    return fake_factory


# -- enumeration -------------------------------------------------------------------------------


def qt_camera_names() -> list[str]:
    """Device names from ``QMediaDevices.videoInputs()``; empty when QtMultimedia is unusable."""
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except Exception:  # missing backend libraries, e.g. on a headless CI box
        logger.debug("QtMultimedia unavailable; camera names fall back to 'Camera N'")
        return []
    try:
        return [device.description() for device in QMediaDevices.videoInputs()]
    except Exception:
        logger.exception("QMediaDevices.videoInputs() failed")
        return []


def probe_dshow(index: int) -> bool:
    """True when DirectShow device ``index`` opens."""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    try:
        return bool(cap.isOpened())
    finally:
        cap.release()


def list_cameras(
    *,
    names: Callable[[], Sequence[str]] = qt_camera_names,
    probe: Callable[[int], bool] = probe_dshow,
    env: Mapping[str, str] | None = None,
) -> list[CameraInfo]:
    """Cameras that open through DirectShow (indices 0..9), named from QtMultimedia.

    Names are matched by position: DirectShow index ``i`` gets the ``i``-th QMediaDevices name.
    An index with no matching name is labeled ``"Camera N"`` with ``N`` the index (assumption
    A-01). With ``SCREEN_COPIER_FAKE_CAMERA`` set, the simulated camera is the only entry.

    Probing opens each device, so this blocks; call it off the GUI thread.
    """
    path = fake_camera_path(env)
    if path is not None:
        return [CameraInfo(0, f"Simulated camera ({path.name})")]
    labels = list(names())
    cameras: list[CameraInfo] = []
    for index in range(MAX_DSHOW_INDEX):
        if not probe(index):
            continue
        name = labels[index] if index < len(labels) and labels[index] else f"Camera {index}"
        cameras.append(CameraInfo(index, name))
    return cameras


# -- worker ------------------------------------------------------------------------------------


def to_qimage(frame: NDArray[np.uint8]) -> QImage:
    """A deep-copied ``QImage`` of a BGR (or grayscale) frame."""
    frame = np.ascontiguousarray(frame)
    h, w = frame.shape[:2]
    if frame.ndim == 2:
        image = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_Grayscale8)
    else:
        if frame.shape[2] == 4:
            frame = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR))
        image = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888)
    return image.copy()


def _safe_diff(a: NDArray[np.uint8] | None, b: NDArray[np.uint8] | None, missing: float) -> float:
    if a is None or b is None or a.shape != b.shape:
        return missing
    return diff(a, b)


class CameraWorker(QThread):
    """Reads frames on its own thread and reports preview, metrics, captures and errors."""

    preview_frame = Signal(object)  # QImage
    metrics = Signal(float, float)  # diff_prev, sharpness
    trigger_state = Signal(str)  # TriggerState value
    capture_requested = Signal(object, object)  # full-resolution BGR ndarray, FrameQuality
    resolution = Signal(int, int)
    camera_error = Signal(str, str)  # not_found / unavailable / disconnected, message
    hint = Signal(str, bool)  # never_settles / low_resolution, active

    def __init__(
        self,
        camera_id: int,
        settings: Any,
        *,
        trigger: CaptureTrigger,
        capture_factory: CaptureFactory | None = None,
        device_names: Callable[[], Sequence[str]] = qt_camera_names,
        probe: Callable[[int], bool] = probe_dshow,
        sleep: Callable[[float], None] = time.sleep,
        parent: Any = None,
    ) -> None:
        """``capture_factory`` defaults to ``select_capture_factory()``: ``cv2.VideoCapture``,
        or ``FakeCameraSource`` when ``SCREEN_COPIER_FAKE_CAMERA`` is set.

        ``device_names`` and ``probe`` are only used to tell ``not_found`` from ``unavailable``
        when opening fails. The trigger carries its own injected clock.
        """
        super().__init__(parent)
        self.camera_id = camera_id
        self.settings = settings
        self.trigger = trigger
        self._factory = capture_factory or select_capture_factory()
        self._device_names = device_names
        self._probe = probe
        self._sleep = sleep

        self._lock = threading.Lock()
        self._running = False
        self._auto = str(getattr(settings, "capture_mode", CaptureMode.AUTO)) == CaptureMode.AUTO
        self._auto_changed = True
        self._capture_pending = False
        self._exposure_request: tuple[bool, float | None] | None = (
            bool(getattr(settings, "auto_exposure", True)),
            getattr(settings, "exposure", None),
        )
        self._driver_settings_pending = False

        # Worker-thread state.
        self._prev_small: NDArray[np.uint8] | None = None
        self._last_capture_small: NDArray[np.uint8] | None = None
        self._last_state: TriggerState | None = None
        self._never_settles = False

    # -- slots (any thread; never block) -------------------------------------------------------

    def set_auto(self, enabled: bool) -> None:
        with self._lock:
            if enabled != self._auto:
                self._auto = enabled
                self._auto_changed = True

    def capture_now(self) -> None:
        with self._lock:
            self._capture_pending = True

    def set_exposure(self, auto: bool, value: float | None) -> None:
        with self._lock:
            self._exposure_request = (auto, value)

    def open_driver_settings(self) -> None:
        with self._lock:
            self._driver_settings_pending = True

    def stop(self) -> None:
        """Ask the loop to end. Returns at once; call ``wait()`` to join the thread."""
        with self._lock:
            self._running = False

    # -- thread body ---------------------------------------------------------------------------

    def run(self) -> None:
        with self._lock:
            self._running = True
        cap = self._open()
        if cap is None:
            return
        try:
            self._configure_resolution(cap)
            failures = 0
            while self._is_running():
                self._apply_requests(cap)
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= DISCONNECT_AFTER_FAILED_READS:
                        self.camera_error.emit(
                            ERROR_DISCONNECTED,
                            f"The camera stopped sending frames ({failures} failed reads).",
                        )
                        break
                    self._sleep(READ_RETRY_DELAY_S)
                    continue
                failures = 0
                self._handle_frame(frame)
        except Exception as exc:  # keep the thread from dying silently
            logger.exception("camera worker failed")
            self.camera_error.emit(ERROR_DISCONNECTED, f"Camera error: {exc}")
        finally:
            cap.release()
            with self._lock:
                self._running = False

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _open(self) -> CaptureDevice | None:
        try:
            cap = self._factory(self.camera_id, cv2.CAP_DSHOW)
        except Exception:
            logger.exception("opening camera %s raised", self.camera_id)
            cap = None
        if cap is not None and cap.isOpened():
            return cap
        if cap is not None:
            cap.release()
        if self._any_device_exists():
            self.camera_error.emit(
                ERROR_UNAVAILABLE,
                f"Camera {self.camera_id} could not be opened. It may be in use by another app "
                "or blocked by Windows camera privacy settings.",
            )
        else:
            self.camera_error.emit(ERROR_NOT_FOUND, "No camera was found.")
        return None

    def _any_device_exists(self) -> bool:
        try:
            if self._device_names():
                return True
        except Exception:
            logger.exception("listing camera names failed")
        return any(
            self._probe(index) for index in range(MAX_DSHOW_INDEX) if index != self.camera_id
        )

    def _configure_resolution(self, cap: CaptureDevice) -> None:
        """Walk the ladder, accept the first size that reads back, and report the size in use."""
        size: tuple[int, int] | None = None
        last_read: tuple[int, int] | None = None
        for width, height in RESOLUTION_LADDER:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            last_read = (int(frame.shape[1]), int(frame.shape[0]))
            if last_read == (width, height):
                size = last_read
                break
        if size is None:
            size = last_read or (
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
        width, height = size
        self.resolution.emit(width, height)
        low = width < MIN_RESOLUTION[0] or height < MIN_RESOLUTION[1]
        self.hint.emit(HINT_LOW_RESOLUTION, low)

    def _apply_requests(self, cap: CaptureDevice) -> None:
        with self._lock:
            exposure, self._exposure_request = self._exposure_request, None
            driver, self._driver_settings_pending = self._driver_settings_pending, False
            auto_changed, self._auto_changed = self._auto_changed, False
            auto = self._auto
        if exposure is not None:
            auto_exposure, value = exposure
            cap.set(
                cv2.CAP_PROP_AUTO_EXPOSURE,
                AUTO_EXPOSURE_ON if auto_exposure else AUTO_EXPOSURE_OFF,
            )
            if not auto_exposure and value is not None:
                cap.set(cv2.CAP_PROP_EXPOSURE, float(value))
        if driver:
            cap.set(cv2.CAP_PROP_SETTINGS, 1)
        if auto_changed:
            if auto:
                self.trigger.reset()
                self._last_state = None
            else:
                self._last_state = None
                self._set_never_settles(False)

    def _handle_frame(self, frame: NDArray[np.uint8]) -> None:
        small = prep(frame)
        diff_prev = _safe_diff(small, self._prev_small, MAX_DIFF)
        sharp = sharpness(small)
        self._prev_small = small
        self.preview_frame.emit(to_qimage(frame))
        self.metrics.emit(diff_prev, sharp)

        with self._lock:
            auto = self._auto
            manual, self._capture_pending = self._capture_pending, False

        capture = manual
        if auto:
            diff_last = _safe_diff(small, self._last_capture_small, math.inf)
            decision = self.trigger.update(diff_prev, sharp, diff_last)
            if decision.state != self._last_state:
                self._last_state = decision.state
                self.trigger_state.emit(str(decision.state))
            self._set_never_settles(NEVER_SETTLES in decision.hints)
            capture = capture or decision.capture

        if not capture:
            return
        threshold = self.trigger.config.sharpness_threshold
        quality = FrameQuality(sharpness=max(0.0, sharp), blurry=is_blurry(sharp, threshold))
        self._last_capture_small = small
        self.capture_requested.emit(frame.copy(), quality)
        if manual:
            self.trigger.notify_manual_capture()
            self._set_never_settles(False)

    def _set_never_settles(self, active: bool) -> None:
        if active != self._never_settles:
            self._never_settles = active
            self.hint.emit(HINT_NEVER_SETTLES, active)
