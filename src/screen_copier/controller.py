"""CaptureController: the one Qt-facing object that wires the pipeline (architecture §2, §4, §12).

camera worker -> preprocess (thread pool) -> frame JPEG + session save -> recognition queue ->
stitcher -> ``document_changed``. The UI talks only to this object; it never calls the camera, the
API or the disk directly.

Threading: the controller lives on the GUI thread. Preprocessing and every disk write or read run
on one private ``QThreadPool`` with a single thread, so they happen in submission order (a frame's
JPEG is always written before the ``session.json`` that refers to it, REQ-NFR-07). Results come
back to the GUI thread through a queued signal. Session saves write a deep copy, so the GUI thread
can keep changing the live session. Stitching runs on the GUI thread (it is pure, bounded work).

Everything is injectable: camera worker factory, recognizer, recognition queue factory, session
store, settings (and how to persist them), preprocessing function, wall clock and timers.
"""

from __future__ import annotations

import contextlib
import logging
import random
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

from screen_copier.camera.source import CameraWorker
from screen_copier.camera.trigger import CaptureTrigger, TriggerConfig
from screen_copier.config.settings import Settings, save_settings
from screen_copier.models import (
    CaptureMode,
    Document,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    GapKind,
    Session,
    StitchEvent,
    StitchEventKind,
    Usage,
)
from screen_copier.preprocess.detect import as_quad
from screen_copier.preprocess.warp import ProcessResult, process
from screen_copier.recognition.base import Recognizer
from screen_copier.recognition.queue import RecognitionQueue
from screen_copier.session.store import SessionStore
from screen_copier.stitching.stitcher import Stitcher, issue_count

log = logging.getLogger(__name__)

# Warning lifecycle constants fixed by the CaptureController contract (spec.md); not settings.
BLURRY_CLEAR_S = 3.0
EDIT_SAVE_DEBOUNCE_S = 2.0

WARNING_MESSAGES: dict[str, str] = {
    "gap": "Some lines were missed between captures. Scroll back to cover the gap.",
    "ambiguous_overlap": (
        "A capture matched more than one place in the document. Check the highlighted lines."
    ),
    "cut_off": ("Some lines are cut off at the right edge. Shrink the font or widen the window."),
    "word_wrap": "The editor seems to wrap long lines. Turn word wrap off on the other computer.",
    "offline": "No connection to the Anthropic API. Frames will be sent when it is back.",
    "blurry": "The last capture is blurry.",
    "never_settles": "The image never settles. Check for flicker or reduce camera motion.",
    "low_resolution": "The camera resolution is low; small text may not be readable.",
}

_EVENT_WARNINGS: dict[StitchEventKind, str] = {
    StitchEventKind.AMBIGUOUS_OVERLAP: "ambiguous_overlap",
    StitchEventKind.CUT_OFF: "cut_off",
    StitchEventKind.WORD_WRAP: "word_wrap",
}


class Timers(Protocol):
    """Schedules a callback on the GUI thread; returns a function that cancels it."""

    def call_later(self, delay_s: float, callback: Callable[[], None]) -> Callable[[], None]: ...


class QtTimers:
    """Production ``Timers`` backed by single-shot ``QTimer``s (GUI thread only)."""

    def __init__(self) -> None:
        self._timers: set[QTimer] = set()

    def call_later(self, delay_s: float, callback: Callable[[], None]) -> Callable[[], None]:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(max(0, round(delay_s * 1000)))
        self._timers.add(timer)

        def cancel() -> None:
            timer.stop()
            self._timers.discard(timer)

        timer.timeout.connect(lambda: self._timers.discard(timer))
        return cancel


class CameraWorkerLike(Protocol):
    """What the controller needs from ``CameraWorker`` (signals plus slots)."""

    preview_frame: Any
    trigger_state: Any
    capture_requested: Any
    resolution: Any
    camera_error: Any
    hint: Any

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def wait(self, ms: int) -> bool: ...
    def set_auto(self, enabled: bool) -> None: ...
    def capture_now(self) -> None: ...


CameraWorkerFactory = Callable[[int, Settings], CameraWorkerLike]
QueueFactory = Callable[[Recognizer], RecognitionQueue]
ProcessFn = Callable[[Any, Any, int], ProcessResult]


def default_camera_worker(camera_id: int, settings: Settings) -> CameraWorker:
    trigger = CaptureTrigger(TriggerConfig.from_settings(settings))
    return CameraWorker(camera_id, settings, trigger=trigger)


def default_queue_factory(settings: Settings) -> QueueFactory:
    def make(recognizer: Recognizer) -> RecognitionQueue:
        return RecognitionQueue(
            recognizer,
            max_concurrent=settings.max_concurrent_requests,
            clock=time.monotonic,
            sleep=time.sleep,
            rng=random.Random(),
        )

    return make


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_stats(session: Session | None, document: Document, issues: int) -> dict[str, int]:
    """Stats carried by ``document_changed``."""
    frames = 0
    usage = Usage()
    if session is not None:
        frames = sum(1 for f in session.frames if f.status is not FrameStatus.DISCARDED)
        usage = session.usage
    return {
        "frames": frames,
        "lines": sum(len(segment.lines) for segment in document.segments),
        "gaps": len(document.gaps),
        "issues": issues,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


class _Invoker(QObject):
    """Delivers callables from pool threads to the GUI thread."""

    call = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        fn()


class _Task(QRunnable):
    def __init__(
        self,
        invoker: _Invoker,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[Exception], None] | None,
    ) -> None:
        super().__init__()
        self._invoker = invoker
        self._fn = fn
        self._on_done = on_done
        self._on_error = on_error

    def run(self) -> None:
        try:
            value = self._fn()
        except Exception as exc:
            log.exception("Background task failed")
            if self._on_error is not None:
                on_error, error = self._on_error, exc
                self._emit(lambda: on_error(error))
            return
        if self._on_done is not None:
            on_done = self._on_done
            self._emit(lambda: on_done(value))

    def _emit(self, fn: Callable[[], None]) -> None:
        with contextlib.suppress(RuntimeError):  # the controller was deleted
            self._invoker.call.emit(fn)


class CaptureController(QObject):
    """Wires camera, preprocessing, persistence, recognition and stitching for the UI."""

    preview_frame = Signal(object, object)  # QImage, quad (4x2 float32 ndarray) | None
    trigger_state = Signal(str)
    frame_added = Signal(object)  # Frame
    frame_updated = Signal(object)  # Frame
    document_changed = Signal(object, object)  # Document, stats dict
    warning = Signal(str, str, bool)  # kind, message, active
    auth_required = Signal()
    camera_error = Signal(str, str)  # kind, message
    consent_required = Signal()
    resolution = Signal(int, int)

    def __init__(
        self,
        *,
        store: SessionStore,
        recognizer: Recognizer,
        settings: Settings,
        camera_worker_factory: CameraWorkerFactory = default_camera_worker,
        queue_factory: QueueFactory | None = None,
        persist_settings: Callable[[Settings], None] = save_settings,
        process_fn: ProcessFn = process,
        clock: Callable[[], datetime] = _utc_now,
        timers: Timers | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._recognizer = recognizer
        self._settings = settings
        self._worker_factory = camera_worker_factory
        self._queue_factory = queue_factory or default_queue_factory(settings)
        self._persist_settings = persist_settings
        self._process = process_fn
        self._clock = clock
        self._timers: Timers = timers or QtTimers()

        self._invoker = _Invoker(self)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)  # serial: preserves frame and save order

        self._worker: CameraWorkerLike | None = None
        self._camera_id = settings.camera_id
        self._auto = settings.capture_mode is CaptureMode.AUTO
        self._manual_pending = 0
        self._locked_quad: np.ndarray | None = None

        self._session: Session | None = None
        self._generation = 0
        """Bumped when the session changes; late callbacks for an old session are dropped."""
        self._loading = False
        self._deferred_captures: list[tuple[Any, FrameQuality]] = []
        self._next_seq = 1
        self._stitcher = Stitcher(settings)
        self._last_stitched = 0
        self._queue: RecognitionQueue | None = None
        self._rerecognize_queue: RecognitionQueue | None = None
        self._submitted: set[int] = set()
        self._held: list[tuple[int, bytes]] = []
        self._consent_notified = False
        self._usage_base = Usage()
        self._queue_usage = (0, 0)
        self._rerecognize_usage = (0, 0)
        self._rerecognize_counter = 0
        self._rerecognize_seqs: dict[int, int] = {}
        self._queue_connections: list[tuple[Any, Any]] = []

        self._active: dict[str, bool] = {}
        self._cancel_blurry: Callable[[], None] | None = None
        self._cancel_edit_save: Callable[[], None] | None = None

    # ===================================================================== read-only state
    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def queue(self) -> RecognitionQueue | None:
        return self._queue

    @property
    def locked_quad(self) -> np.ndarray | None:
        return self._locked_quad

    def is_warning_active(self, kind: str) -> bool:
        return self._active.get(kind, False)

    def document(self) -> Document:
        return self._stitcher.document()

    # ===================================================================== lifecycle
    def start_camera(self) -> None:
        """Start the camera worker for the selected camera (no-op if already running)."""
        if self._worker is not None:
            return
        worker = self._worker_factory(self._camera_id, self._settings)
        worker.preview_frame.connect(self._on_preview)
        worker.trigger_state.connect(self.trigger_state)
        worker.capture_requested.connect(self._on_capture_requested)
        worker.resolution.connect(self.resolution)
        worker.camera_error.connect(self.camera_error)
        worker.hint.connect(self._on_hint)
        worker.set_auto(self._auto)
        self._worker = worker
        worker.start()

    def stop_camera(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        for signal in (
            worker.preview_frame,
            worker.trigger_state,
            worker.capture_requested,
            worker.resolution,
            worker.camera_error,
            worker.hint,
        ):
            with contextlib.suppress(RuntimeError, TypeError):
                signal.disconnect()
        worker.stop()
        worker.wait(5000)

    def wait_for_io(self, timeout_ms: int = 10000) -> bool:
        """Block until queued preprocessing and disk work is done (for shutdown and tests).

        Completion callbacks are delivered to this thread later, through its event loop.
        """
        return self._pool.waitForDone(timeout_ms)

    def shutdown(self, timeout_ms: int = 10000) -> None:
        """Stop the camera and the queues and wait for pending disk writes."""
        self.stop_camera()
        self._flush_edit_save()
        self._drop_queues()
        self._pool.waitForDone(timeout_ms)

    # ===================================================================== slots: camera
    @Slot()
    def capture_now(self) -> None:
        """Manual capture, in either mode (REQ-CAM-05)."""
        if self._worker is None:
            return
        self._manual_pending += 1
        self._worker.capture_now()

    @Slot(bool)
    def set_auto(self, enabled: bool) -> None:
        self._auto = enabled
        self._settings.capture_mode = CaptureMode.AUTO if enabled else CaptureMode.MANUAL
        if self._worker is not None:
            self._worker.set_auto(enabled)

    @Slot(object)
    def lock_region(self, quad: Any) -> None:
        """Lock the screen outline (TL, TR, BR, BL), or clear it with ``None`` (REQ-PRE-03)."""
        self._locked_quad = None if quad is None else as_quad(quad)

    @Slot(int)
    def select_camera(self, camera_id: int) -> None:
        self._camera_id = camera_id
        self._settings.camera_id = camera_id
        if self._worker is not None:
            self.stop_camera()
            self.start_camera()

    # ===================================================================== slots: session
    @Slot()
    def new_session(self) -> None:
        """Start a fresh session with an empty document. Creating it writes a tiny file."""
        self._reset_session_state()
        self._session = self._store.create()
        self._make_queue()
        self._emit_document()
        self._replay_deferred()

    @Slot(str)
    def resume_session(self, session_id: str) -> None:
        """Load a stored session, rebuild its document and resubmit frames still waiting."""
        self._reset_session_state()
        self._loading = True
        generation = self._generation
        store = self._store

        def load() -> tuple[Session, dict[int, bytes]]:
            session = store.prepare_resume(store.load(session_id))
            folder = store.session_dir(session.id)
            images: dict[int, bytes] = {}
            for frame in session.frames:
                if frame.status is FrameStatus.QUEUED:
                    path = folder / frame.image_file
                    if path.is_file():
                        images[frame.seq] = path.read_bytes()
            return session, images

        def loaded(value: tuple[Session, dict[int, bytes]]) -> None:
            if generation == self._generation:
                self._finish_resume(*value)

        def failed(exc: Exception) -> None:
            if generation == self._generation:
                self._loading = False
                log.error("Could not resume session %s: %s", session_id, exc)

        self._run_io(load, loaded, failed)

    def _finish_resume(self, session: Session, images: dict[int, bytes]) -> None:
        self._loading = False
        self._session = session
        self._usage_base = session.usage.model_copy()
        self._next_seq = max((f.seq for f in session.frames), default=0) + 1
        self._rebuild_stitcher()
        self._make_queue()
        to_submit: list[tuple[int, bytes]] = []
        for frame in session.frames:
            if frame.status is FrameStatus.QUEUED:
                jpeg = images.get(frame.seq)
                if jpeg is None:
                    frame.status = FrameStatus.FAILED
                    frame.error = "The frame image is missing"
                else:
                    to_submit.append((frame.seq, jpeg))
            self.frame_added.emit(frame.model_copy(deep=True))
        session.updated_at = self._clock()
        self._save()
        self._refresh_gap_warning(self._stitcher.document())
        self._emit_document()
        for seq, jpeg in to_submit:
            self._submit_or_hold(seq, jpeg)
        self._replay_deferred()

    @Slot(object)
    def set_edited_text(self, text: str | None) -> None:
        """Record the user's edits; saved after a 2 s debounce."""
        if self._session is None:
            return
        self._session.edited_text = text
        self._session.updated_at = self._clock()
        if self._cancel_edit_save is not None:
            self._cancel_edit_save()
        self._cancel_edit_save = self._timers.call_later(EDIT_SAVE_DEBOUNCE_S, self._edit_save_due)

    def _edit_save_due(self) -> None:
        self._cancel_edit_save = None
        self._save()

    def _flush_edit_save(self) -> None:
        if self._cancel_edit_save is not None:
            self._cancel_edit_save()
            self._cancel_edit_save = None
            self._save()

    @Slot()
    def acknowledge_consent(self) -> None:
        """Remember the consent (REQ-SES-06) and send the frames that were held."""
        self._settings.consent_acknowledged = True
        try:
            self._persist_settings(self._settings)
        except OSError:
            log.exception("Could not save the consent acknowledgment")
        held, self._held = self._held, []
        for seq, jpeg in held:
            self._submit(seq, jpeg)

    # ===================================================================== slots: frames
    @Slot(int)
    def discard_frame(self, seq: int) -> None:
        frame = self._frame(seq)
        if frame is None or frame.status is FrameStatus.DISCARDED:
            return
        self._held = [(s, j) for s, j in self._held if s != seq]
        if self._queue is not None and seq in self._submitted:
            self._queue.discard(seq)
        if self._rerecognize_queue is not None:
            self._rerecognize_queue.discard(seq)
        was_recognized = frame.status is FrameStatus.RECOGNIZED
        frame.status = FrameStatus.DISCARDED
        frame.result = None
        frame.error = None
        self._touch_and_save()
        self.frame_updated.emit(frame.model_copy(deep=True))
        if was_recognized:
            self._rebuild_stitcher()
            self._after_stitch(None, self._stitcher.document())

    @Slot(int)
    def retry_frame(self, seq: int) -> None:
        """Send a failed frame again."""
        frame = self._frame(seq)
        if frame is None or frame.status is not FrameStatus.FAILED or self._queue is None:
            return
        if seq in self._submitted:
            self._queue.retry(seq)
            return
        # Failed in an earlier run of the app: the queue has never seen it.
        self._read_image_then(seq, lambda jpeg: self._submit_or_hold(seq, jpeg))

    @Slot(int)
    def rerecognize_frame(self, seq: int) -> None:
        """Transcribe a frame again; a recognized frame keeps its result until the new one."""
        frame = self._frame(seq)
        if frame is None:
            return
        if frame.status is FrameStatus.FAILED:
            self.retry_frame(seq)
            return
        if frame.status is not FrameStatus.RECOGNIZED:
            return
        if self._rerecognize_queue is None:
            rq = self._queue_factory(self._recognizer)
            self._connect(rq.result_ready, self._on_rerecognized)
            self._connect(rq.auth_required, self.auth_required)
            self._connect(rq.usage, self._on_rerecognize_usage)
            self._rerecognize_queue = rq
        rq = self._rerecognize_queue
        generation = self._generation

        def submit(jpeg: bytes) -> None:
            if generation != self._generation or rq is not self._rerecognize_queue:
                return
            rq.discard(seq)  # an earlier request for the same frame
            # Each request needs a fresh queue key; the original seq maps back through it.
            key = self._rerecognize_key(seq)
            rq.submit(key, jpeg)

        self._read_image_then(seq, submit)

    def _rerecognize_key(self, seq: int) -> int:
        self._rerecognize_counter += 1
        key = self._rerecognize_counter
        self._rerecognize_seqs[key] = seq
        return key

    def _on_rerecognized(self, key: int, result: FrameResult) -> None:
        seq = self._rerecognize_seqs.pop(key, None)
        frame = self._frame(seq) if seq is not None else None
        if frame is None or frame.status is not FrameStatus.RECOGNIZED:
            return
        frame.result = result
        self._touch_and_save()
        self.frame_updated.emit(frame.model_copy(deep=True))
        events = self._rebuild_stitcher()
        self._after_stitch([e for e in events if e.frame_seq == seq], self._stitcher.document())

    def _on_rerecognize_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._rerecognize_usage = (input_tokens, output_tokens)
        self._update_usage()

    # ===================================================================== camera signals
    def _on_preview(self, image: Any) -> None:
        self.preview_frame.emit(image, self._locked_quad)

    def _on_hint(self, kind: str, active: bool) -> None:
        self._set_warning(kind, active, force=True)

    def _on_capture_requested(self, image: Any, quality: FrameQuality) -> None:
        if self._loading:
            self._deferred_captures.append((image, quality))
            return
        if self._session is None:
            self.new_session()
        self._capture(image, quality)

    def _replay_deferred(self) -> None:
        deferred, self._deferred_captures = self._deferred_captures, []
        for image, quality in deferred:
            self._capture(image, quality)

    def _capture(self, image: Any, quality: FrameQuality) -> None:
        session = self._session
        assert session is not None
        mode = CaptureMode.AUTO if self._auto else CaptureMode.MANUAL
        if self._manual_pending > 0:
            self._manual_pending -= 1
            mode = CaptureMode.MANUAL
        seq = self._next_seq
        self._next_seq += 1
        frame = Frame(
            seq=seq,
            captured_at=self._clock(),
            mode=mode,
            image_file=Frame.image_file_for(seq),
            quality=quality,
        )
        locked = None if self._locked_quad is None else self._locked_quad.copy()
        max_edge = self._settings.max_image_edge_px
        store, process_fn, generation = self._store, self._process, self._generation

        def work() -> bytes:
            result = process_fn(image, locked, max_edge)
            store.save_frame_image(session, seq, result.jpeg)
            return result.jpeg

        def saved_image(jpeg: bytes) -> None:
            if generation != self._generation:
                return
            session.frames.append(frame)
            session.updated_at = self._clock()
            self._save(then=lambda: self._frame_saved(generation, frame, jpeg))

        self._run_io(work, saved_image)

    def _frame_saved(self, generation: int, frame: Frame, jpeg: bytes) -> None:
        if generation != self._generation:
            return
        self.frame_added.emit(frame.model_copy(deep=True))
        if frame.quality.blurry:
            self._raise_blurry()
        if frame.status is FrameStatus.QUEUED:  # not discarded in the meantime
            self._submit_or_hold(frame.seq, jpeg)

    def _raise_blurry(self) -> None:
        if self._cancel_blurry is not None:
            self._cancel_blurry()
        self._set_warning("blurry", True, force=True)
        self._cancel_blurry = self._timers.call_later(BLURRY_CLEAR_S, self._clear_blurry)

    def _clear_blurry(self) -> None:
        self._cancel_blurry = None
        self._set_warning("blurry", False)

    # ===================================================================== recognition
    def _make_queue(self) -> None:
        queue = self._queue_factory(self._recognizer)
        self._connect(queue.frame_status, self._on_frame_status)
        self._connect(queue.result_ready, self._on_result_ready)
        self._connect(queue.auth_required, self.auth_required)
        self._connect(queue.offline_changed, self._on_offline_changed)
        self._connect(queue.usage, self._on_usage)
        self._queue = queue

    def _connect(self, signal: Any, slot: Any) -> None:
        signal.connect(slot)
        self._queue_connections.append((signal, slot))

    def _drop_queues(self) -> None:
        for queue in (self._queue, self._rerecognize_queue):
            if queue is not None:
                queue.shutdown()
        connections, self._queue_connections = self._queue_connections, []
        for signal, slot in connections:
            with contextlib.suppress(RuntimeError, TypeError):
                signal.disconnect(slot)
        self._queue = None
        self._rerecognize_queue = None

    def _submit_or_hold(self, seq: int, jpeg: bytes) -> None:
        if not self._settings.consent_acknowledged:
            self._held.append((seq, jpeg))
            if not self._consent_notified:
                self._consent_notified = True
                self.consent_required.emit()
            return
        self._submit(seq, jpeg)

    def _submit(self, seq: int, jpeg: bytes) -> None:
        frame = self._frame(seq)
        if self._queue is None or frame is None or frame.status is FrameStatus.DISCARDED:
            return
        if seq in self._submitted:
            self._queue.retry(seq)
        else:
            self._submitted.add(seq)
            self._queue.submit(seq, jpeg)

    def _on_frame_status(self, seq: int, status_value: str, error: str | None) -> None:
        frame = self._frame(seq)
        if frame is None or frame.status in (FrameStatus.DISCARDED, FrameStatus.RECOGNIZED):
            return
        status = FrameStatus(status_value)
        if status is FrameStatus.DISCARDED:
            return  # the controller marks discards itself
        if status is FrameStatus.RECOGNIZED:
            # A recognized frame must carry its result; it arrives in order via result_ready.
            status = FrameStatus.RECOGNIZING
        if self._queue is not None and seq in self._submitted:
            with contextlib.suppress(KeyError):
                frame.attempts = self._queue.attempts(seq)
        changed = frame.status is not status or frame.error != error
        frame.status = status
        frame.error = error
        if changed:
            self._touch_and_save()
            self.frame_updated.emit(frame.model_copy(deep=True))

    def _on_result_ready(self, seq: int, result: FrameResult) -> None:
        frame = self._frame(seq)
        if frame is None or frame.status is FrameStatus.DISCARDED:
            return
        frame.status = FrameStatus.RECOGNIZED
        frame.error = None
        frame.result = result
        if self._queue is not None:
            with contextlib.suppress(KeyError):
                frame.attempts = self._queue.attempts(seq)
        self.frame_updated.emit(frame.model_copy(deep=True))
        if seq > self._last_stitched:
            events = self._stitcher.add(seq, result)
            self._last_stitched = seq
        else:  # a retried frame arrived after later frames were stitched
            events = [e for e in self._rebuild_stitcher() if e.frame_seq == seq]
        self._after_stitch(events, self._stitcher.document())
        self._touch_and_save()

    def _on_offline_changed(self, offline: bool) -> None:
        self._set_warning("offline", offline)

    def _on_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._queue_usage = (input_tokens, output_tokens)
        self._update_usage()

    def _update_usage(self) -> None:
        if self._session is None:
            return
        qi, qo = self._queue_usage
        ri, ro = self._rerecognize_usage
        self._session.usage = Usage(
            input_tokens=self._usage_base.input_tokens + qi + ri,
            output_tokens=self._usage_base.output_tokens + qo + ro,
        )

    # ===================================================================== stitching
    def _rebuild_stitcher(self) -> list[StitchEvent]:
        """Re-stitch every recognized frame in seq order (REQ-STI-10). Returns all events."""
        self._stitcher = Stitcher(self._settings)
        self._last_stitched = 0
        events: list[StitchEvent] = []
        if self._session is None:
            return events
        for frame in sorted(self._session.frames, key=lambda f: f.seq):
            if frame.status is FrameStatus.RECOGNIZED and frame.result is not None:
                events.extend(self._stitcher.add(frame.seq, frame.result))
                self._last_stitched = frame.seq
        return events

    def _after_stitch(self, events: Sequence[StitchEvent] | None, document: Document) -> None:
        """Translate a frame's events into warnings and emit the document.

        ``events`` is ``None`` when no new frame was stitched (a discard): the per-frame warnings
        then keep their state, and only the document-derived ``gap`` warning is refreshed.
        """
        if events is not None:
            kinds = {event.kind for event in events}
            for event_kind, warning_kind in _EVENT_WARNINGS.items():
                # Raised on each occurrence (it may have been dismissed); cleared when a later
                # frame no longer shows the condition.
                self._set_warning(warning_kind, event_kind in kinds, force=True)
        self._refresh_gap_warning(document)
        self._emit_document(document)

    def _refresh_gap_warning(self, document: Document) -> None:
        has_gap = any(gap.kind is GapKind.OVERLAP for gap in document.gaps)
        self._set_warning("gap", has_gap)

    def _emit_document(self, document: Document | None = None) -> None:
        if document is None:
            document = self._stitcher.document()
        stats = make_stats(self._session, document, issue_count(document))
        self.document_changed.emit(document, stats)

    # ===================================================================== helpers
    def _set_warning(self, kind: str, active: bool, *, force: bool = False) -> None:
        """Emit a raise or clear. Clears only on change; raises on change or when forced."""
        was = self._active.get(kind, False)
        if active == was and not (active and force):
            return
        self._active[kind] = active
        self.warning.emit(kind, WARNING_MESSAGES.get(kind, kind), active)

    def _frame(self, seq: int | None) -> Frame | None:
        if self._session is None or seq is None:
            return None
        for frame in self._session.frames:
            if frame.seq == seq:
                return frame
        return None

    def _reset_session_state(self) -> None:
        self._flush_edit_save()
        self._generation += 1
        self._drop_queues()
        self._session = None
        self._loading = False
        self._next_seq = 1
        self._stitcher = Stitcher(self._settings)
        self._last_stitched = 0
        self._submitted = set()
        self._held = []
        self._consent_notified = False
        self._usage_base = Usage()
        self._queue_usage = (0, 0)
        self._rerecognize_usage = (0, 0)
        self._rerecognize_seqs = {}
        for kind in ("gap", "ambiguous_overlap", "cut_off", "word_wrap", "offline", "blurry"):
            self._set_warning(kind, False)
        if self._cancel_blurry is not None:
            self._cancel_blurry()
            self._cancel_blurry = None

    def _touch_and_save(self) -> None:
        if self._session is not None:
            self._session.updated_at = self._clock()
            self._save()

    def _save(self, then: Callable[[], None] | None = None) -> None:
        """Write a snapshot of the session on the I/O thread, then call ``then`` on this one."""
        if self._session is None:
            return
        snapshot = self._session.model_copy(deep=True)
        store = self._store
        self._run_io(lambda: store.save(snapshot), None if then is None else (lambda _: then()))

    def _read_image_then(self, seq: int, callback: Callable[[bytes], None]) -> None:
        session = self._session
        if session is None:
            return
        path: Path = self._store.session_dir(session.id) / Frame.image_file_for(seq)
        generation = self._generation

        def done(jpeg: bytes) -> None:
            if generation == self._generation:
                callback(jpeg)

        self._run_io(path.read_bytes, done)

    def _run_io(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._pool.start(_Task(self._invoker, fn, on_done, on_error))
