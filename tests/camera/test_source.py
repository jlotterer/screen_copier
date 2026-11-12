"""Tests for CameraWorker, list_cameras and FakeCameraSource (T012)."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage

from screen_copier.camera.fake import FakeCameraSource
from screen_copier.camera.source import (
    FAKE_CAMERA_ENV,
    CameraInfo,
    CameraWorker,
    list_cameras,
    select_capture_factory,
)
from screen_copier.camera.trigger import CaptureTrigger, TriggerConfig, TriggerState
from screen_copier.config.settings import Settings
from screen_copier.models import CaptureMode, FrameQuality

BASIC = Path(__file__).resolve().parents[1] / "fixtures" / "camera" / "basic"
BASIC_FILES = sorted(BASIC.glob("*.png"))


# -- helpers -----------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


def text_frame(seed: int, size: tuple[int, int] = (640, 360), offset: int = 0) -> np.ndarray:
    """A sharp, code-like frame. ``offset`` shifts the text to simulate scrolling."""
    w, h = size
    img = np.full((h, w, 3), 255, np.uint8)
    for k in range(12):
        y = 30 + k * 28 - offset
        cv2.putText(img, f"line {seed}-{k} x = {k * seed}", (10, y), 0, 0.8, (0, 0, 0), 2)
    return img


class ScriptedSource:
    """A cv2.VideoCapture look-alike that plays a fixed list of frames, then fails every read."""

    def __init__(
        self,
        frames: list[np.ndarray],
        *,
        size: tuple[int, int] | None = None,
        clock: FakeClock | None = None,
        fps: int = 30,
        opened: bool = True,
    ) -> None:
        self._frames = iter(frames)
        h, w = frames[0].shape[:2] if frames else (0, 0)
        self.size = size or (w, h)
        self._clock = clock
        self._fps = fps
        self._opened = opened
        self.props: dict[int, float] = {}
        self.set_calls: list[tuple[int, float]] = []
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, Any]:
        if self._clock is not None:
            self._clock.sleep(1 / self._fps)
        else:
            time.sleep(0.002)  # don't flood the GUI event queue
        frame = next(self._frames, None)
        return (frame is not None), frame

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.size[0])
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.size[1])
        return self.props.get(prop_id, 0.0)

    def set(self, prop_id: int, value: float) -> bool:
        self.props[prop_id] = value
        self.set_calls.append((prop_id, value))
        return True

    def release(self) -> None:
        self.released = True


def make_worker(
    factory: Callable[[int, int], Any],
    *,
    mode: CaptureMode = CaptureMode.MANUAL,
    clock: Callable[[], float] | None = None,
    device_names: Callable[[], list[str]] = lambda: [],
    **settings_kw: Any,
) -> CameraWorker:
    settings = Settings(capture_mode=mode, **settings_kw)
    trigger_kw = {"clock": clock} if clock is not None else {}
    trigger = CaptureTrigger(TriggerConfig.from_settings(settings), **trigger_kw)
    return CameraWorker(
        0,
        settings,
        trigger=trigger,
        capture_factory=factory,
        device_names=device_names,
        probe=lambda index: False,
        sleep=lambda s: None,
    )


@pytest.fixture
def run_worker(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[CameraWorker], CameraWorker]]:
    """Start workers and stop them after the test.

    Signals from the worker thread are queued, so a queued call can still be pending for a
    ``waitSignal`` blocker after the ``with`` block ends. With PySide6 that call crashes if the
    blocker has been freed, so every blocker is kept alive until the workers have stopped and the
    event queue is drained.
    """
    workers: list[CameraWorker] = []
    blockers: list[Any] = []
    wait_signal, wait_signals = qtbot.waitSignal, qtbot.waitSignals

    def keep(factory: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            blocker = factory(*args, **kwargs)
            blockers.append(blocker)
            return blocker

        return wrapper

    monkeypatch.setattr(qtbot, "waitSignal", keep(wait_signal))
    monkeypatch.setattr(qtbot, "waitSignals", keep(wait_signals))

    def start(worker: CameraWorker) -> CameraWorker:
        workers.append(worker)
        worker.start()
        return worker

    yield start
    for worker in workers:
        worker.stop()
        assert worker.wait(5000)
    QCoreApplication.processEvents()
    blockers.clear()


def image_id(image: QImage) -> int:
    """Which basic fixture image a preview shows, from its background colour."""
    color = image.pixelColor(5, 5)
    bgr = (color.blue(), color.green(), color.red())
    for i, path in enumerate(BASIC_FILES):
        ref = cv2.imread(str(path))
        if tuple(int(v) for v in ref[5, 5]) == bgr:
            return i
    raise AssertionError(f"unknown preview image {bgr}")


# -- fixture folder ----------------------------------------------------------------------------


def test_basic_fixture_has_three_distinct_pngs() -> None:
    assert len(BASIC_FILES) == 3
    corners = {tuple(cv2.imread(str(p))[5, 5]) for p in BASIC_FILES}
    assert len(corners) == 3


# -- FakeCameraSource --------------------------------------------------------------------------


def test_fake_source_cycles_folder_in_sorted_order_holding_each_image() -> None:
    clock = FakeClock()
    src = FakeCameraSource(BASIC, fps=30, hold_ms=100, clock=clock, sleep=clock.sleep)
    assert src.isOpened()
    assert (src.get(cv2.CAP_PROP_FRAME_WIDTH), src.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (1920, 1080)
    refs = [cv2.imread(str(p)) for p in BASIC_FILES]
    seen = []
    for _ in range(12):  # 3 frames per image at 30 fps and 100 ms
        ok, frame = src.read()
        assert ok
        seen.append(next(i for i, ref in enumerate(refs) if np.array_equal(ref, frame)))
    assert seen == [0, 0, 0, 1, 1, 1, 2, 2, 2, 0, 0, 0]
    # Reads are paced at 30 fps.
    assert clock.now == pytest.approx(11 / 30)


def test_fake_source_keeps_native_size_and_records_props() -> None:
    src = FakeCameraSource(BASIC)
    assert src.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
    assert src.get(cv2.CAP_PROP_FRAME_WIDTH) == 1920
    assert src.set(cv2.CAP_PROP_EXPOSURE, -5)
    assert src.get(cv2.CAP_PROP_EXPOSURE) == -5
    assert src.get(cv2.CAP_PROP_FPS) == 30
    src.release()
    assert not src.isOpened()
    assert src.read() == (False, None)


def test_fake_source_missing_or_empty_path_is_not_opened(tmp_path: Path) -> None:
    assert not FakeCameraSource(tmp_path / "missing").isOpened()
    assert not FakeCameraSource(tmp_path).isOpened()


def test_fake_source_loops_a_video_file(tmp_path: Path) -> None:
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 30, (320, 240))
    if not writer.isOpened():
        pytest.skip("no MJPG video writer in this OpenCV build")
    for i in range(3):
        writer.write(np.full((240, 320, 3), 60 * (i + 1), np.uint8))
    writer.release()

    clock = FakeClock()
    src = FakeCameraSource(path, clock=clock, sleep=clock.sleep)
    assert src.isOpened()
    assert (src.get(cv2.CAP_PROP_FRAME_WIDTH), src.get(cv2.CAP_PROP_FRAME_HEIGHT)) == (320, 240)
    levels = []
    for _ in range(7):
        ok, frame = src.read()
        assert ok
        levels.append(round(float(frame.mean()) / 60))
    assert levels == [1, 2, 3, 1, 2, 3, 1]


# -- source selection --------------------------------------------------------------------------


def test_fake_camera_env_selects_fake_source() -> None:
    factory = select_capture_factory({FAKE_CAMERA_ENV: str(BASIC)})
    source = factory(0, cv2.CAP_DSHOW)
    assert isinstance(source, FakeCameraSource)
    assert source.isOpened()


def test_without_env_the_real_capture_is_selected() -> None:
    assert select_capture_factory({}) is cv2.VideoCapture
    assert select_capture_factory({FAKE_CAMERA_ENV: "  "}) is cv2.VideoCapture


def test_worker_default_factory_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FAKE_CAMERA_ENV, str(BASIC))
    settings = Settings()
    worker = CameraWorker(0, settings, trigger=CaptureTrigger(TriggerConfig()))
    assert isinstance(worker._factory(0, cv2.CAP_DSHOW), FakeCameraSource)


# -- list_cameras ------------------------------------------------------------------------------


def test_list_cameras_matches_names_by_position_with_fallback() -> None:
    cameras = list_cameras(
        names=lambda: ["Integrated Webcam", "USB Camera"],
        probe=lambda i: i in (0, 1, 2),
        env={},
    )
    assert cameras == [
        CameraInfo(0, "Integrated Webcam"),
        CameraInfo(1, "USB Camera"),
        CameraInfo(2, "Camera 2"),
    ]


def test_list_cameras_probes_indices_0_to_9() -> None:
    probed: list[int] = []
    list_cameras(names=lambda: [], probe=lambda i: probed.append(i) or False, env={})
    assert probed == list(range(10))


def test_list_cameras_with_fake_env_lists_the_simulated_camera() -> None:
    cameras = list_cameras(names=lambda: [], probe=lambda i: False, env={FAKE_CAMERA_ENV: "x/y"})
    assert cameras == [CameraInfo(0, "Simulated camera (y)")]


# -- CameraWorker ------------------------------------------------------------------------------


def test_preview_shows_each_basic_image_in_turn(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: FakeCameraSource(BASIC, hold_ms=200))
    seen: list[int] = []
    worker.preview_frame.connect(lambda img: seen.append(image_id(img)))
    run_worker(worker)
    for expected in (0, 1, 2):
        with qtbot.waitSignal(
            worker.preview_frame,
            timeout=3000,
            check_params_cb=lambda img, e=expected: image_id(img) == e,
        ):
            pass
    # Collapse repeats: the images follow the sorted file order, cycling. The resolution ladder
    # reads a few frames first, so the preview may join the cycle part-way through image 0.
    order = [i for k, i in enumerate(seen) if k == 0 or seen[k - 1] != i]
    assert len(order) >= 3
    for prev, nxt in itertools.pairwise(order):
        assert nxt == (prev + 1) % 3


def test_resolution_emitted_once_for_1080p_source(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: FakeCameraSource(BASIC))
    sizes: list[tuple[int, int]] = []
    hints: list[tuple[str, bool]] = []
    worker.resolution.connect(lambda w, h: sizes.append((w, h)))
    worker.hint.connect(lambda name, active: hints.append((name, active)))
    with qtbot.waitSignal(worker.resolution, timeout=3000) as blocker:
        run_worker(worker)
    assert blocker.args == [1920, 1080]
    qtbot.wait(300)
    assert sizes == [(1920, 1080)]
    assert ("low_resolution", True) not in hints


def test_resolution_ladder_accepts_first_size_that_reads_back(qtbot: Any, run_worker: Any) -> None:
    class LadderSource(ScriptedSource):
        """Supports up to 2560x1440: frames come back at the requested size if allowed."""

        def read(self) -> tuple[bool, Any]:
            w = int(self.props.get(cv2.CAP_PROP_FRAME_WIDTH, 640))
            h = int(self.props.get(cv2.CAP_PROP_FRAME_HEIGHT, 480))
            if w > 2560:
                w, h = 1920, 1080
            return True, np.zeros((h, w, 3), np.uint8)

    source = LadderSource([np.zeros((480, 640, 3), np.uint8)])
    worker = make_worker(lambda i, api: source)
    with qtbot.waitSignal(worker.resolution, timeout=3000) as blocker:
        run_worker(worker)
    worker.stop()
    assert worker.wait(3000)
    assert blocker.args == [2560, 1440]
    widths = [v for p, v in source.set_calls if p == cv2.CAP_PROP_FRAME_WIDTH]
    assert widths == [3840, 2560]


def test_low_resolution_hint_for_640x480(qtbot: Any, run_worker: Any, tmp_path: Path) -> None:
    for i in range(2):
        cv2.imwrite(str(tmp_path / f"{i}.png"), text_frame(i, (640, 480)))
    worker = make_worker(lambda i, api: FakeCameraSource(tmp_path))
    with qtbot.waitSignals(
        [worker.resolution, worker.hint],
        timeout=3000,
        check_params_cbs=[
            lambda w, h: (w, h) == (640, 480),
            lambda name, active: (name, active) == ("low_resolution", True),
        ],
    ):
        run_worker(worker)


def test_30fps_source_gives_at_least_45_previews_in_3_seconds(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: FakeCameraSource(BASIC, fps=30))
    count = 0

    def on_frame(_: object) -> None:
        nonlocal count
        count += 1

    worker.preview_frame.connect(on_frame)
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        run_worker(worker)
    count = 0  # count from the first preview frame on
    qtbot.wait(3000)
    assert count >= 45


def test_30_failed_reads_emit_disconnected_and_stop(qtbot: Any, run_worker: Any) -> None:
    frames = [text_frame(1)] * 5  # 4 ladder reads + 1 preview, then every read fails
    worker = make_worker(lambda i, api: ScriptedSource(frames))
    errors: list[tuple[str, str]] = []
    worker.camera_error.connect(lambda kind, msg: errors.append((kind, msg)))
    with qtbot.waitSignal(worker.finished, timeout=5000):
        run_worker(worker)
    assert worker.wait(1000)
    assert worker.isFinished()
    assert [kind for kind, _ in errors] == ["disconnected"]


def test_29_failed_reads_do_not_disconnect(qtbot: Any, run_worker: Any) -> None:
    class Flaky(ScriptedSource):
        def __init__(self) -> None:
            super().__init__([text_frame(1)])
            self.n = 0

        def read(self) -> tuple[bool, Any]:
            self.n += 1
            if self.n % 30 == 0:  # 29 failures, then one good frame, repeated
                return True, text_frame(1)
            return False, None

    worker = make_worker(lambda i, api: Flaky())
    errors: list[str] = []
    worker.camera_error.connect(lambda kind, msg: errors.append(kind))
    run_worker(worker)
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        pass
    qtbot.wait(100)
    assert errors == []
    assert worker.isRunning()


def test_open_fails_without_devices_is_not_found(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: ScriptedSource([], opened=False), device_names=lambda: [])
    with qtbot.waitSignal(worker.camera_error, timeout=3000) as blocker:
        run_worker(worker)
    assert worker.wait(3000)
    assert blocker.args[0] == "not_found"


def test_open_fails_with_a_device_is_unavailable(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(
        lambda i, api: ScriptedSource([], opened=False), device_names=lambda: ["Webcam"]
    )
    with qtbot.waitSignal(worker.camera_error, timeout=3000) as blocker:
        run_worker(worker)
    assert worker.wait(3000)
    assert blocker.args[0] == "unavailable"


def test_capture_now_in_manual_mode_emits_exactly_one_capture(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: FakeCameraSource(BASIC), mode=CaptureMode.MANUAL)
    captures: list[tuple[Any, Any]] = []
    worker.capture_requested.connect(lambda f, q: captures.append((f, q)))
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        run_worker(worker)
    with qtbot.waitSignal(worker.capture_requested, timeout=3000):
        worker.capture_now()
    qtbot.wait(300)
    assert len(captures) == 1
    frame, quality = captures[0]
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (1080, 1920, 3)
    assert isinstance(quality, FrameQuality)
    assert quality.sharpness > 0
    assert quality.blurry is False


def test_manual_capture_of_blurry_frame_is_flagged(qtbot: Any, run_worker: Any) -> None:
    flat = np.full((360, 640, 3), 128, np.uint8)
    source = ScriptedSource([flat] * 2000)
    worker = make_worker(lambda i, api: source)
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        run_worker(worker)
    with qtbot.waitSignal(worker.capture_requested, timeout=3000) as blocker:
        worker.capture_now()
    assert blocker.args[1].blurry is True


def test_auto_mode_captures_once_after_motion_then_stillness(qtbot: Any, run_worker: Any) -> None:
    clock = FakeClock()
    moving = [text_frame(1, offset=8 * k) for k in range(10)]
    still = [text_frame(9)] * 60  # 2 s held still and sharp at 30 fps
    frames = [moving[0]] * 4 + moving + still  # 4 ladder reads first
    worker = make_worker(
        lambda i, api: ScriptedSource(frames, clock=clock), mode=CaptureMode.AUTO, clock=clock
    )
    captures: list[FrameQuality] = []
    states: list[str] = []
    worker.capture_requested.connect(lambda f, q: captures.append(q))
    worker.trigger_state.connect(states.append)
    with qtbot.waitSignal(worker.finished, timeout=5000):  # script ends -> disconnected
        run_worker(worker)
    qtbot.wait(50)
    assert len(captures) == 1
    assert captures[0].blurry is False
    assert TriggerState.SETTLING in states
    assert states[-1] == TriggerState.CAPTURED


def test_manual_mode_never_auto_captures(qtbot: Any, run_worker: Any) -> None:
    clock = FakeClock()
    frames = [text_frame(9)] * 70
    worker = make_worker(
        lambda i, api: ScriptedSource(frames, clock=clock), mode=CaptureMode.MANUAL, clock=clock
    )
    captures: list[Any] = []
    worker.capture_requested.connect(lambda f, q: captures.append(q))
    with qtbot.waitSignal(worker.finished, timeout=5000):
        run_worker(worker)
    qtbot.wait(50)
    assert captures == []


def test_set_auto_resets_trigger_and_metrics_are_emitted(qtbot: Any, run_worker: Any) -> None:
    worker = make_worker(lambda i, api: FakeCameraSource(BASIC), mode=CaptureMode.MANUAL)
    resets: list[int] = []
    original_reset = worker.trigger.reset

    def counting_reset() -> None:
        resets.append(1)
        original_reset()

    worker.trigger.reset = counting_reset  # type: ignore[method-assign]
    with qtbot.waitSignal(worker.metrics, timeout=3000):
        run_worker(worker)
    with qtbot.waitSignal(
        worker.trigger_state, timeout=3000, check_params_cb=lambda s: s == "MOVING"
    ):
        worker.set_auto(True)
    assert resets == [1]


def test_exposure_and_driver_settings_are_applied_on_worker_thread(
    qtbot: Any, run_worker: Any
) -> None:
    source = ScriptedSource([text_frame(1)] * 5000)
    worker = make_worker(lambda i, api: source, auto_exposure=False, exposure=-6.0)
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        run_worker(worker)
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.25) in source.set_calls
    assert (cv2.CAP_PROP_EXPOSURE, -6.0) in source.set_calls

    source.set_calls.clear()
    worker.set_exposure(True, None)
    worker.open_driver_settings()
    qtbot.waitUntil(lambda: len(source.set_calls) >= 2, timeout=3000)
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 0.75) in source.set_calls
    assert (cv2.CAP_PROP_SETTINGS, 1) in source.set_calls
    assert not any(p == cv2.CAP_PROP_EXPOSURE for p, _ in source.set_calls)


def test_stop_ends_the_thread_and_releases_the_device(qtbot: Any, run_worker: Any) -> None:
    source = ScriptedSource([text_frame(1)] * 100000)
    worker = make_worker(lambda i, api: source)
    with qtbot.waitSignal(worker.preview_frame, timeout=3000):
        run_worker(worker)
    worker.stop()
    assert worker.wait(3000)
    assert source.released
