"""Tests for CaptureController (T014).

Everything is faked: a stub camera worker fed with images from ``FakeCameraSource`` (plus one test
with the real ``CameraWorker`` on the fake camera), ``FakeRecognizer`` on stitching fixtures, a
``SessionStore`` in ``tmp_path`` and a fake clock that also drives the controller's timers.
"""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal

from screen_copier.camera.fake import FakeCameraSource
from screen_copier.camera.source import CameraWorker
from screen_copier.camera.trigger import CaptureTrigger
from screen_copier.config.settings import Settings
from screen_copier.controller import CaptureController
from screen_copier.models import (
    CaptureMode,
    Document,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    RecognizedLine,
    Usage,
)
from screen_copier.preprocess.warp import ProcessResult, process
from screen_copier.recognition.base import Ok, RecognitionOutcome
from screen_copier.recognition.fake import FakeRecognizer
from screen_copier.recognition.queue import RecognitionQueue
from screen_copier.session.store import SessionStore
from screen_copier.stitching.stitcher import issue_count, render, stitch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STITCHING = FIXTURES / "stitching"
CAMERA_BASIC = FIXTURES / "camera" / "basic"
TIMEOUT_MS = 10000
SHARP = FrameQuality(sharpness=500.0, blurry=False)
BLURRY = FrameQuality(sharpness=5.0, blurry=True)


# -- fakes -------------------------------------------------------------------------------------


class FakeTime:
    """Wall clock for the controller plus its ``Timers``; time moves only with ``advance``."""

    def __init__(self) -> None:
        self.current = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
        self._timers: list[tuple[datetime, int, Callable[[], None]]] = []
        self._next_id = 0

    def now(self) -> datetime:
        return self.current

    def call_later(self, delay_s: float, callback: Callable[[], None]) -> Callable[[], None]:
        self._next_id += 1
        entry = (self.current + timedelta(seconds=delay_s), self._next_id, callback)
        self._timers.append(entry)

        def cancel() -> None:
            if entry in self._timers:
                self._timers.remove(entry)

        return cancel

    def advance(self, seconds: float) -> None:
        target = self.current + timedelta(seconds=seconds)
        while True:
            due = sorted(t for t in self._timers if t[0] <= target)
            if not due:
                break
            entry = due[0]
            self._timers.remove(entry)
            self.current = entry[0]
            entry[2]()
        self.current = target


class StubWorker(QObject):
    """Stands in for ``CameraWorker``: same signals; tests emit ``capture_requested`` directly."""

    preview_frame = Signal(object)
    metrics = Signal(float, float)
    trigger_state = Signal(str)
    capture_requested = Signal(object, object)
    resolution = Signal(int, int)
    camera_error = Signal(str, str)
    hint = Signal(str, bool)

    def __init__(self, camera_id: int) -> None:
        super().__init__()
        self.camera_id = camera_id
        self.started = False
        self.stopped = False
        self.auto: bool | None = None
        self.manual_requests = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def wait(self, ms: int) -> bool:
        return True

    def set_auto(self, enabled: bool) -> None:
        self.auto = enabled

    def capture_now(self) -> None:
        self.manual_requests += 1


class SpyRecognizer:
    """Wraps ``FakeRecognizer``; records calls, whether the JPEG was on disk, and adds usage."""

    def __init__(
        self, directory: Path, frames_dir: Callable[[], Path | None], usage: Usage | None = None
    ) -> None:
        self._inner = FakeRecognizer(directory)
        self._frames_dir = frames_dir
        self._usage = usage or Usage()
        self._lock = threading.Lock()
        self.calls: list[int] = []
        self.on_disk: dict[int, bool] = {}

    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome:
        folder = self._frames_dir()
        path = None if folder is None else folder / f"{frame_seq}.jpg"
        with self._lock:
            self.calls.append(frame_seq)
            self.on_disk[frame_seq] = path is not None and path.is_file()
        outcome = self._inner.recognize(jpeg, frame_seq=frame_seq)
        if isinstance(outcome, Ok):
            return Ok(outcome.result, self._usage)
        return outcome


def camera_images() -> list[np.ndarray]:
    """Frames from the fake camera (the fixture folder of T012)."""
    source = FakeCameraSource(CAMERA_BASIC, fps=1, hold_ms=1000, sleep=lambda s: None)
    images = []
    for _ in range(3):
        ok, image = source.read()
        assert ok
        images.append(image)
    source.release()
    return images


def frame_results(directory: Path) -> dict[int, FrameResult]:
    return {
        int(p.stem): FrameResult.model_validate_json(p.read_text(encoding="utf-8"))
        for p in directory.glob("*.json")
    }


def line(text: str, *, cut_off: bool = False) -> RecognizedLine:
    return RecognizedLine(
        text=text, line_number=None, partial=False, cut_off_right=cut_off, uncertain=False
    )


def write_results(directory: Path, results: dict[int, FrameResult]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for seq, result in results.items():
        (directory / f"{seq}.json").write_text(result.model_dump_json(), encoding="utf-8")
    return directory


# -- harness -----------------------------------------------------------------------------------


class Harness:
    def __init__(
        self,
        qtbot: Any,
        tmp_path: Path,
        recognizer_dir: Path,
        *,
        consent: bool = True,
        usage: Usage | None = None,
        worker_factory: Callable[[int, Settings], Any] | None = None,
    ) -> None:
        self.qtbot = qtbot
        self.time = FakeTime()
        self.data_dir = tmp_path / "data"
        self.store = SessionStore(self.data_dir, self.time.now)
        self.settings = Settings(consent_acknowledged=consent)
        self.persisted: list[Settings] = []
        self.process_calls: list[Any] = []
        self.workers: list[Any] = []
        self.recognizer = SpyRecognizer(recognizer_dir, self.frames_dir, usage)
        self.images = camera_images()

        def factory(camera_id: int, settings: Settings) -> Any:
            worker = (worker_factory or (lambda i, s: StubWorker(i)))(camera_id, settings)
            self.workers.append(worker)
            return worker

        def spy_process(image: Any, locked_quad: Any, max_edge: int) -> ProcessResult:
            self.process_calls.append(None if locked_quad is None else np.array(locked_quad))
            return process(image, locked_quad, max_edge)

        def queue_factory(recognizer: Any) -> RecognitionQueue:
            return RecognitionQueue(
                recognizer,
                max_concurrent=self.settings.max_concurrent_requests,
                clock=lambda: 0.0,
                sleep=lambda s: None,
                rng=random.Random(0),
            )

        self.controller = CaptureController(
            store=self.store,
            recognizer=self.recognizer,
            settings=self.settings,
            camera_worker_factory=factory,
            queue_factory=queue_factory,
            persist_settings=lambda s: self.persisted.append(s.model_copy()),
            process_fn=spy_process,
            clock=self.time.now,
            timers=self.time,
        )
        c = self.controller
        self.documents: list[tuple[Document, dict[str, int], Any]] = []
        self.warnings: list[tuple[str, str, bool]] = []
        self.log: list[tuple[Any, ...]] = []
        self.added: list[Frame] = []
        self.updated: list[Frame] = []
        self.consent_requests = 0
        c.document_changed.connect(self._on_document)
        c.warning.connect(self._on_warning)
        c.frame_added.connect(self.added.append)
        c.frame_updated.connect(self.updated.append)
        c.consent_required.connect(self._on_consent)

    def _on_document(self, document: Document, stats: dict[str, int]) -> None:
        session = self.controller.session
        snapshot = None if session is None else session.model_copy(deep=True)
        self.documents.append((document, stats, snapshot))
        self.log.append(("document", render(document)))

    def _on_warning(self, kind: str, message: str, active: bool) -> None:
        self.warnings.append((kind, message, active))
        self.log.append(("warning", kind, active))

    def _on_consent(self) -> None:
        self.consent_requests += 1

    # -- helpers -------------------------------------------------------------------------------
    def frames_dir(self) -> Path | None:
        session = self.controller.session
        return None if session is None else self.store.session_dir(session.id) / "frames"

    @property
    def worker(self) -> StubWorker:
        return self.workers[-1]

    def capture(self, quality: FrameQuality = SHARP, index: int = 0) -> None:
        self.worker.capture_requested.emit(self.images[index % len(self.images)], quality)

    def settle(self) -> None:
        """Let background disk work and its queued callbacks finish."""
        for _ in range(5):
            assert self.controller.wait_for_io(TIMEOUT_MS)
            QCoreApplication.processEvents()

    def wait_until(self, condition: Callable[[], bool]) -> None:
        self.qtbot.waitUntil(condition, timeout=TIMEOUT_MS)

    def recognized(self) -> list[int]:
        session = self.controller.session
        assert session is not None
        return [f.seq for f in session.frames if f.status is FrameStatus.RECOGNIZED]

    def capture_and_stitch(self, count: int, quality: FrameQuality = SHARP) -> None:
        """Capture one frame at a time, waiting until each is stitched."""
        for _ in range(count):
            before = len(self.recognized()) if self.controller.session else 0
            self.capture(quality)
            self.wait_until(lambda b=before: len(self.recognized()) == b + 1)
        self.settle()

    def stored(self) -> dict[str, Any]:
        session = self.controller.session
        assert session is not None
        path = self.store.session_dir(session.id) / "session.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.controller.shutdown()
        QCoreApplication.processEvents()


@pytest.fixture
def make_harness(qtbot: Any, tmp_path: Path) -> Iterator[Callable[..., Harness]]:
    harnesses: list[Harness] = []

    def make(recognizer_dir: Path, *, start: bool = True, **kwargs: Any) -> Harness:
        harness = Harness(qtbot, tmp_path, recognizer_dir, **kwargs)
        harnesses.append(harness)
        if start:
            harness.controller.start_camera()
            harness.controller.new_session()
        return harness

    yield make
    for harness in harnesses:
        harness.close()


# -- acceptance criteria -----------------------------------------------------------------------


def test_five_frames_render_the_expected_document(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    for _ in range(5):
        h.capture()
    h.wait_until(lambda: len(h.recognized()) == 5)
    h.settle()
    expected = (STITCHING / "seq_basic" / "expected.txt").read_text(encoding="utf-8")
    assert render(h.documents[-1][0]) == expected
    assert h.recognizer.calls and sorted(h.recognizer.calls) == [1, 2, 3, 4, 5]
    assert [f.seq for f in h.added] == [1, 2, 3, 4, 5]
    stored = h.stored()
    assert [f["status"] for f in stored["frames"]] == ["recognized"] * 5


def test_consent_gate_holds_frames_until_acknowledged(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames", consent=False)
    h.capture()
    h.capture()
    h.wait_until(lambda: len(h.added) == 2)
    h.settle()
    assert h.consent_requests == 1
    frames_dir = h.frames_dir()
    assert frames_dir is not None
    assert (frames_dir / "1.jpg").is_file() and (frames_dir / "2.jpg").is_file()
    assert [f["seq"] for f in h.stored()["frames"]] == [1, 2]
    h.qtbot.wait(100)
    assert h.recognizer.calls == []

    h.controller.acknowledge_consent()
    h.wait_until(lambda: len(h.recognized()) == 2)
    assert sorted(h.recognizer.calls) == [1, 2]
    assert h.settings.consent_acknowledged is True
    assert h.persisted and h.persisted[-1].consent_acknowledged is True
    h.capture()
    h.wait_until(lambda: len(h.recognized()) == 3)
    assert h.consent_requests == 1


def test_jpeg_is_on_disk_before_recognition(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    for _ in range(5):
        h.capture()
    h.wait_until(lambda: len(h.recognized()) == 5)
    assert h.recognizer.on_disk == {seq: True for seq in range(1, 6)}


def test_cut_off_line_raises_and_later_frame_clears_warning(
    make_harness: Callable[..., Harness], tmp_path: Path
) -> None:
    results = {
        1: FrameResult(
            has_line_numbers=False,
            word_wrap_detected=False,
            lines=[line("a = 1"), line("b = 2"), line("c = some_very_long_call(", cut_off=True)],
        ),
        2: FrameResult(
            has_line_numbers=False,
            word_wrap_detected=False,
            lines=[line("b = 2"), line("c = some_very_long_call("), line("d = 4")],
        ),
    }
    h = make_harness(write_results(tmp_path / "cut", results))
    h.capture_and_stitch(1)
    cut = [w for w in h.warnings if w[0] == "cut_off"]
    assert len(cut) == 1 and cut[0][2] is True and cut[0][1]
    h.capture_and_stitch(1)
    assert [w[2] for w in h.warnings if w[0] == "cut_off"] == [True, False]


def test_bridge_raises_then_clears_gap_warning(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_bridge" / "frames")
    h.capture_and_stitch(2)
    assert not [w for w in h.warnings if w[0] == "gap"]
    h.capture_and_stitch(1)
    gap = [w for w in h.warnings if w[0] == "gap"]
    assert len(gap) == 1 and gap[0][2] is True and gap[0][1]
    h.capture_and_stitch(1)
    assert [w[2] for w in h.warnings if w[0] == "gap"] == [True, False]
    h.capture_and_stitch(1)
    expected = (STITCHING / "seq_bridge" / "expected.txt").read_text(encoding="utf-8")
    assert render(h.documents[-1][0]) == expected
    # The warning is emitted before the document_changed of the frame that caused it.
    gap_true = h.log.index(("warning", "gap", True))
    assert sum(1 for entry in h.log[:gap_true] if entry[0] == "document") == 3  # new + 2 frames


def test_discard_restitches_remaining_frames(make_harness: Callable[..., Harness]) -> None:
    directory = STITCHING / "seq_basic" / "frames"
    h = make_harness(directory)
    h.capture_and_stitch(3)
    queue = h.controller.queue
    assert queue is not None
    discarded: list[int] = []
    original = queue.discard

    def spy(seq: int) -> None:
        discarded.append(seq)
        original(seq)

    queue.discard = spy  # type: ignore[method-assign]
    h.controller.discard_frame(2)
    h.settle()
    assert discarded == [2]
    session = h.controller.session
    assert session is not None
    assert session.frames[1].status is FrameStatus.DISCARDED
    assert h.updated[-1].seq == 2 and h.updated[-1].status is FrameStatus.DISCARDED
    results = frame_results(directory)
    expected = render(stitch([(1, results[1]), (3, results[3])], h.settings).document)
    assert render(h.documents[-1][0]) == expected
    assert h.documents[-1][1]["frames"] == 2
    assert h.stored()["frames"][1]["status"] == "discarded"


def test_resume_submits_queued_frame(make_harness: Callable[..., Harness], tmp_path: Path) -> None:
    directory = STITCHING / "seq_basic" / "frames"
    h = make_harness(directory, start=False)
    store = h.store
    session = store.create()
    jpeg = process(h.images[0], None, h.settings.max_image_edge_px).jpeg
    store.save_frame_image(session, 1, jpeg)
    session.frames.append(
        Frame(
            seq=1,
            captured_at=h.time.now(),
            mode=CaptureMode.AUTO,
            image_file=Frame.image_file_for(1),
            quality=SHARP,
            status=FrameStatus.QUEUED,
        )
    )
    store.save(session)

    h.controller.resume_session(session.id)
    h.wait_until(lambda: h.controller.session is not None and h.recognized() == [1])
    h.settle()
    assert h.recognizer.calls == [1]
    results = frame_results(directory)
    assert render(h.documents[-1][0]) == render(stitch([(1, results[1])], h.settings).document)
    assert h.documents[-1][1]["frames"] == 1
    assert h.stored()["frames"][0]["status"] == "recognized"
    # New captures continue after the stored frames.
    h.controller.start_camera()
    h.capture_and_stitch(1)
    assert h.recognized() == [1, 2]


def test_resume_rebuilds_document_from_recognized_frames(
    make_harness: Callable[..., Harness],
) -> None:
    directory = STITCHING / "seq_basic" / "frames"
    results = frame_results(directory)
    h = make_harness(directory, start=False)
    session = h.store.create()
    for seq in (1, 2):
        h.store.save_frame_image(session, seq, b"jpeg")
        session.frames.append(
            Frame(
                seq=seq,
                captured_at=h.time.now(),
                mode=CaptureMode.AUTO,
                image_file=Frame.image_file_for(seq),
                quality=SHARP,
                status=FrameStatus.RECOGNIZED,
                result=results[seq],
            )
        )
    h.store.save(session)
    h.controller.resume_session(session.id)
    h.wait_until(lambda: bool(h.documents))
    expected = stitch([(1, results[1]), (2, results[2])], h.settings).document
    assert render(h.documents[-1][0]) == render(expected)
    assert h.recognizer.calls == []
    assert [f.seq for f in h.added] == [1, 2]


def test_locked_region_is_passed_to_process(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    quad = [[100, 80], [1800, 90], [1790, 1000], [110, 990]]
    h.controller.lock_region(quad)
    h.capture_and_stitch(1)
    assert h.process_calls[-1] is not None
    np.testing.assert_allclose(h.process_calls[-1], np.array(quad, dtype=np.float32))
    h.controller.lock_region(None)
    h.capture_and_stitch(1)
    assert h.process_calls[-1] is None


def test_blurry_frame_is_marked_and_warning_clears_after_3_s(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    h.capture(BLURRY)
    h.wait_until(lambda: len(h.added) == 1)
    assert h.added[0].quality.blurry is True
    blurry = [w for w in h.warnings if w[0] == "blurry"]
    assert len(blurry) == 1 and blurry[0][2] is True and blurry[0][1]
    h.time.advance(2.9)
    assert [w[2] for w in h.warnings if w[0] == "blurry"] == [True]
    h.time.advance(0.1)
    assert [w[2] for w in h.warnings if w[0] == "blurry"] == [True, False]


def test_edited_text_is_saved_once_after_2_s(
    make_harness: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    h.settle()
    saves: list[str | None] = []
    original = h.store.save

    def spy(session: Any) -> None:
        saves.append(session.edited_text)
        original(session)

    monkeypatch.setattr(h.store, "save", spy)
    h.controller.set_edited_text("draft")
    h.time.advance(1.0)
    h.controller.set_edited_text("x")
    h.time.advance(1.9)
    h.settle()
    assert saves == []
    h.time.advance(0.1)
    h.settle()
    assert saves == ["x"]
    assert h.stored()["edited_text"] == "x"
    h.time.advance(10)
    h.settle()
    assert saves == ["x"]


def test_document_stats_match_session_and_document(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(
        STITCHING / "seq_bridge" / "frames", usage=Usage(input_tokens=100, output_tokens=20)
    )
    h.capture_and_stitch(5)
    h.controller.discard_frame(5)
    h.settle()
    assert len(h.documents) >= 7
    for document, stats, session in h.documents:
        assert session is not None
        assert set(stats) == {
            "frames",
            "lines",
            "gaps",
            "issues",
            "input_tokens",
            "output_tokens",
        }
        live = [f for f in session.frames if f.status is not FrameStatus.DISCARDED]
        assert stats["frames"] == len(live)
        assert stats["lines"] == sum(len(s.lines) for s in document.segments)
        assert stats["gaps"] == len(document.gaps)
        assert stats["issues"] == issue_count(document)
        assert stats["input_tokens"] == session.usage.input_tokens
        assert stats["output_tokens"] == session.usage.output_tokens
    # Frame 3 opened a gap: the stats then show it.
    assert any(stats["gaps"] == 1 for _, stats, _ in h.documents)
    assert h.documents[-1][1]["input_tokens"] == 500
    assert h.documents[-1][1]["output_tokens"] == 100
    assert h.stored()["usage"] == {"input_tokens": 500, "output_tokens": 100}


# -- further behavior --------------------------------------------------------------------------


def test_real_camera_worker_on_fake_camera_manual_capture(
    make_harness: Callable[..., Harness],
) -> None:
    def worker_factory(camera_id: int, settings: Settings) -> CameraWorker:
        return CameraWorker(
            camera_id,
            settings,
            trigger=CaptureTrigger(),
            capture_factory=lambda i, api=0: FakeCameraSource(CAMERA_BASIC),
            device_names=lambda: [],
            probe=lambda index: False,
            sleep=lambda s: None,
        )

    h = make_harness(STITCHING / "seq_basic" / "frames", worker_factory=worker_factory)
    previews: list[tuple[Any, Any]] = []
    h.controller.preview_frame.connect(lambda image, quad: previews.append((image, quad)))
    h.controller.set_auto(False)
    h.wait_until(lambda: bool(previews))
    h.controller.capture_now()
    h.wait_until(lambda: len(h.recognized()) == 1)
    assert h.added[0].mode is CaptureMode.MANUAL
    h.controller.stop_camera()
    h.settle()


def test_manual_capture_is_recorded_as_manual_in_auto_mode(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    assert h.worker.started and h.worker.auto is True
    h.capture_and_stitch(1)
    h.controller.capture_now()
    assert h.worker.manual_requests == 1
    h.capture_and_stitch(1)
    assert [f.mode for f in h.added] == [CaptureMode.AUTO, CaptureMode.MANUAL]


def test_worker_hints_and_errors_are_forwarded(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    errors: list[tuple[str, str]] = []
    resolutions: list[tuple[int, int]] = []
    h.controller.camera_error.connect(lambda k, m: errors.append((k, m)))
    h.controller.resolution.connect(lambda w, ht: resolutions.append((w, ht)))
    h.worker.hint.emit("never_settles", True)
    h.worker.hint.emit("low_resolution", True)
    h.worker.hint.emit("never_settles", False)
    h.worker.camera_error.emit("disconnected", "gone")
    h.worker.resolution.emit(1280, 720)
    kinds = [(w[0], w[2]) for w in h.warnings]
    assert ("never_settles", True) in kinds and ("never_settles", False) in kinds
    assert ("low_resolution", True) in kinds
    assert errors == [("disconnected", "gone")]
    assert resolutions == [(1280, 720)]


def test_offline_warning_mirrors_queue(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    queue = h.controller.queue
    assert queue is not None
    queue.offline_changed.emit(True)
    queue.offline_changed.emit(False)
    assert [(w[0], w[2]) for w in h.warnings if w[0] == "offline"] == [
        ("offline", True),
        ("offline", False),
    ]


def test_select_camera_restarts_worker(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(STITCHING / "seq_basic" / "frames")
    first = h.worker
    h.controller.select_camera(2)
    assert first.stopped
    assert h.worker is not first and h.worker.camera_id == 2 and h.worker.started
    assert h.settings.camera_id == 2


def test_failed_frame_retry_and_rerecognize(
    make_harness: Callable[..., Harness], tmp_path: Path
) -> None:
    directory = tmp_path / "partial"
    results = frame_results(STITCHING / "seq_basic" / "frames")
    write_results(directory, {1: results[1]})  # frame 2 has no result yet -> Malformed -> failed
    h = make_harness(directory)
    h.capture_and_stitch(1)
    h.capture()
    h.wait_until(lambda: any(f.seq == 2 and f.status is FrameStatus.FAILED for f in h.updated))
    session = h.controller.session
    assert session is not None and session.frames[1].error
    write_results(directory, {2: results[2]})
    h.controller.retry_frame(2)
    h.wait_until(lambda: h.recognized() == [1, 2])
    h.settle()
    expected = stitch([(1, results[1]), (2, results[2])], h.settings).document
    assert render(h.documents[-1][0]) == render(expected)

    calls = len(h.recognizer.calls)
    h.controller.rerecognize_frame(1)
    h.wait_until(lambda: len(h.recognizer.calls) == calls + 1)
    h.wait_until(lambda: render(h.documents[-1][0]) == render(expected) and len(h.documents) > 3)
