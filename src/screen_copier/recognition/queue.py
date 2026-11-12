"""RecognitionQueue: concurrent, ordered, fault-tolerant frame recognition (architecture §7.4).

This is the one recognition module allowed to use Qt. Recognizer calls run on a
``ThreadPoolExecutor``; waits (backoff and offline probes) run ``sleep`` on short-lived daemon
threads. Both report back through internal signals with queued connections, so every state change
and every public signal happens on the thread the queue lives on (the GUI thread).

Policy (spec.md, RecognitionQueue contract):

- At most ``max_concurrent`` recognizer calls in flight.
- ``Retryable``: wait 1, 2, 4, 8, 16 s x (1 + U[0, 0.2]); after 5 failed attempts the frame fails.
- ``Malformed``: one retry, then ``failed("Unreadable model response")``.
- ``AuthError``: pause the queue, put the frame back to ``queued`` without counting the attempt,
  and emit ``auth_required`` once per pause.
- ``BadRequest``: fail at once with the API message.
- Offline: 3 consecutive connection errors set offline. Only the oldest unfinished frame is tried,
  every 15 s; the others are held. Any outcome that got an HTTP response clears offline.
- ``result_ready`` fires strictly in seq order: a result is released only once every lower known
  seq is recognized, failed or discarded.
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal, SignalInstance, Slot

from screen_copier.models import FrameResult, FrameStatus
from screen_copier.recognition.base import (
    AuthError,
    BadRequest,
    Malformed,
    Ok,
    RecognitionOutcome,
    Recognizer,
    Retryable,
)

log = logging.getLogger(__name__)

# Policy numbers fixed by the RecognitionQueue contract (spec.md); they are not user settings.
BACKOFF_DELAYS_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
BACKOFF_JITTER = 0.2
MAX_RETRYABLE_ATTEMPTS = 5
MAX_MALFORMED_ATTEMPTS = 2
OFFLINE_AFTER_CONNECTION_ERRORS = 3
OFFLINE_PROBE_INTERVAL_S = 15.0
UNREADABLE_RESPONSE = "Unreadable model response"

_TERMINAL = (FrameStatus.RECOGNIZED, FrameStatus.FAILED, FrameStatus.DISCARDED)


@dataclass
class _Entry:
    seq: int
    jpeg: bytes | None
    status: FrameStatus = FrameStatus.QUEUED
    attempts: int = 0
    retryable_failures: int = 0
    malformed_failures: int = 0
    ready: bool = True
    """False while a backoff or offline-probe wait is running."""
    token: int = 0
    """Bumped on every dispatch, wait, retry and discard; stale callbacks carry an old token."""
    call_id: int | None = None
    result: FrameResult | None = None
    released: bool = False
    last_started: float | None = None


class RecognitionQueue(QObject):
    """Runs a ``Recognizer`` over submitted frames. Create and use it on the GUI thread."""

    frame_status = Signal(int, str, object)  # seq, FrameStatus value, error: str | None
    result_ready = Signal(int, object)  # seq, FrameResult; strictly in seq order
    auth_required = Signal()
    offline_changed = Signal(bool)
    usage = Signal(int, int)  # input_tokens, output_tokens (running totals)

    # Internal: worker and timer threads -> queue thread.
    _call_done = Signal(int, object)  # call_id, RecognitionOutcome
    _wait_done = Signal(int, int)  # seq, token

    def __init__(
        self,
        recognizer: Recognizer,
        *,
        max_concurrent: int,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        rng: random.Random,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._recognizer = recognizer
        self._max_concurrent = max_concurrent
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent, thread_name_prefix="recognition"
        )
        self._entries: dict[int, _Entry] = {}
        self._inflight: dict[int, tuple[int, Future[None]]] = {}  # call_id -> (seq, future)
        self._next_call_id = 1
        self._paused = False
        self._auth_notified = False
        self._offline = False
        self._consecutive_connection_errors = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._shut_down = False
        self._call_done.connect(self._on_call_done, Qt.ConnectionType.QueuedConnection)
        self._wait_done.connect(self._on_wait_done, Qt.ConnectionType.QueuedConnection)

    # ----------------------------------------------------------------- read-only state
    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def offline(self) -> bool:
        return self._offline

    @property
    def in_flight(self) -> int:
        """Recognizer calls currently running (including ones whose frame was discarded)."""
        return len(self._inflight)

    def status(self, seq: int) -> FrameStatus:
        return self._entries[seq].status

    def attempts(self, seq: int) -> int:
        """Recognizer calls counted for ``seq``. Calls that ended in ``AuthError`` do not count."""
        return self._entries[seq].attempts

    # ----------------------------------------------------------------- public methods
    def submit(self, seq: int, jpeg: bytes) -> None:
        """Queue a frame. ``seq`` must be new to this queue."""
        if self._shut_down:
            return
        if seq in self._entries:
            raise ValueError(f"frame {seq} was already submitted")
        self._entries[seq] = _Entry(seq=seq, jpeg=jpeg)
        self._set_status(self._entries[seq], FrameStatus.QUEUED)
        self._pump()

    def discard(self, seq: int) -> None:
        """Drop a frame: cancel it if not started, else ignore its result when it arrives."""
        entry = self._entries.get(seq)
        if entry is None or entry.status is FrameStatus.DISCARDED:
            return
        if entry.call_id is not None:
            call = self._inflight.get(entry.call_id)
            if call is not None:
                call[1].cancel()
        entry.token += 1
        entry.call_id = None
        entry.jpeg = None
        entry.result = None
        self._set_status(entry, FrameStatus.DISCARDED)
        self._release()
        self._pump()

    def retry(self, seq: int) -> None:
        """Submit a ``failed`` frame again with a fresh retry budget."""
        entry = self._entries.get(seq)
        if self._shut_down or entry is None or entry.status is not FrameStatus.FAILED:
            return
        entry.token += 1
        entry.retryable_failures = 0
        entry.malformed_failures = 0
        entry.ready = True
        self._set_status(entry, FrameStatus.QUEUED)
        self._pump()

    def pause(self) -> None:
        """Stop starting new calls. Calls in flight finish and their outcomes are handled."""
        self._paused = True

    def resume(self) -> None:
        """Start calls again, e.g. after the API key changed."""
        if self._shut_down:
            return
        self._paused = False
        self._auth_notified = False
        self._pump()

    def shutdown(self) -> None:
        """Stop for good without blocking: pending calls are cancelled, late outcomes ignored."""
        if self._shut_down:
            return
        self._shut_down = True
        self._paused = True
        for entry in self._entries.values():
            entry.token += 1
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------------- scheduling
    def _pump(self) -> None:
        if self._paused or self._shut_down:
            return
        if self._offline:
            oldest = self._oldest_unfinished()
            if (
                not self._inflight
                and oldest is not None
                and oldest.status is FrameStatus.QUEUED
                and oldest.ready
            ):
                self._dispatch(oldest)
            return
        for seq in sorted(self._entries):
            if len(self._inflight) >= self._max_concurrent:
                return
            entry = self._entries[seq]
            if entry.status is FrameStatus.QUEUED and entry.ready:
                self._dispatch(entry)

    def _oldest_unfinished(self) -> _Entry | None:
        for seq in sorted(self._entries):
            entry = self._entries[seq]
            if entry.status not in _TERMINAL:
                return entry
        return None

    def _dispatch(self, entry: _Entry) -> None:
        assert entry.jpeg is not None
        call_id = self._next_call_id
        self._next_call_id += 1
        entry.token += 1
        entry.call_id = call_id
        entry.last_started = self._clock()
        self._set_status(entry, FrameStatus.RECOGNIZING)
        jpeg = entry.jpeg
        seq = entry.seq
        future = self._executor.submit(self._run_call, call_id, jpeg, seq)
        self._inflight[call_id] = (seq, future)
        future.add_done_callback(lambda f, cid=call_id: self._on_future_done(cid, f))

    def _run_call(self, call_id: int, jpeg: bytes, seq: int) -> None:
        # Worker thread: never touch queue state here.
        try:
            outcome: RecognitionOutcome = self._recognizer.recognize(jpeg, frame_seq=seq)
        except Exception as exc:  # a recognizer should not raise; treat it as temporary
            log.exception("Recognizer raised for frame %d", seq)
            outcome = Retryable(f"Unexpected error: {type(exc).__name__}")
        self._emit_safely(self._call_done, call_id, outcome)

    def _on_future_done(self, call_id: int, future: Future[None]) -> None:
        # A cancelled future never ran _run_call; report it so its slot is freed.
        if future.cancelled():
            self._emit_safely(self._call_done, call_id, None)

    def _wait_then_wake(self, seq: int, token: int, delay: float) -> None:
        def run() -> None:
            self._sleep(delay)
            self._emit_safely(self._wait_done, seq, token)

        threading.Thread(target=run, name=f"recognition-wait-{seq}", daemon=True).start()

    @staticmethod
    def _emit_safely(signal: SignalInstance, *args: object) -> None:
        with contextlib.suppress(RuntimeError):  # the queue object was already deleted
            signal.emit(*args)

    def _schedule_wait(self, entry: _Entry, delay: float) -> None:
        entry.token += 1
        entry.ready = False
        self._wait_then_wake(entry.seq, entry.token, delay)

    @Slot(int, int)
    def _on_wait_done(self, seq: int, token: int) -> None:
        entry = self._entries.get(seq)
        if self._shut_down or entry is None or entry.token != token:
            return
        entry.ready = True
        self._pump()

    # ----------------------------------------------------------------- outcomes
    @Slot(int, object)
    def _on_call_done(self, call_id: int, outcome: RecognitionOutcome | None) -> None:
        call = self._inflight.pop(call_id, None)
        if call is None or self._shut_down:
            return
        seq = call[0]
        if outcome is None:  # cancelled before it started
            self._pump()
            return
        entry = self._entries.get(seq)
        current = entry is not None and entry.call_id == call_id
        if entry is not None and current:
            entry.call_id = None

        self._track_connectivity(outcome)
        if isinstance(outcome, Ok):
            self._input_tokens += outcome.usage.input_tokens
            self._output_tokens += outcome.usage.output_tokens
            self.usage.emit(self._input_tokens, self._output_tokens)
        if isinstance(outcome, AuthError):
            self._pause_for_auth()

        if entry is not None and current and entry.status is FrameStatus.RECOGNIZING:
            self._apply(entry, outcome)
        self._release()
        self._pump()

    def _track_connectivity(self, outcome: RecognitionOutcome) -> None:
        if isinstance(outcome, Retryable) and outcome.connection_error:
            self._consecutive_connection_errors += 1
            threshold = OFFLINE_AFTER_CONNECTION_ERRORS
            if not self._offline and self._consecutive_connection_errors >= threshold:
                self._offline = True
                log.info("Recognition is offline")
                self.offline_changed.emit(True)
                self._schedule_offline_probe_for_oldest()
            return
        # Any outcome that got an HTTP response proves the connection is back.
        self._consecutive_connection_errors = 0
        if self._offline:
            self._offline = False
            log.info("Recognition is back online")
            self.offline_changed.emit(False)

    def _pause_for_auth(self) -> None:
        self._paused = True
        if not self._auth_notified:
            self._auth_notified = True
            self.auth_required.emit()

    def _apply(self, entry: _Entry, outcome: RecognitionOutcome) -> None:
        if isinstance(outcome, AuthError):
            # Not counted as an attempt; the frame waits for resume().
            self._set_status(entry, FrameStatus.QUEUED)
            return
        entry.attempts += 1
        if isinstance(outcome, Ok):
            entry.result = outcome.result
            entry.jpeg = None
            self._set_status(entry, FrameStatus.RECOGNIZED)
        elif isinstance(outcome, BadRequest):
            self._fail(entry, outcome.message)
        elif isinstance(outcome, Malformed):
            entry.malformed_failures += 1
            if entry.malformed_failures >= MAX_MALFORMED_ATTEMPTS:
                self._fail(entry, UNREADABLE_RESPONSE)
            else:
                self._set_status(entry, FrameStatus.QUEUED)
        elif isinstance(outcome, Retryable):
            self._on_retryable(entry, outcome)

    def _on_retryable(self, entry: _Entry, outcome: Retryable) -> None:
        if self._offline and outcome.connection_error:
            # Offline attempts do not use up the retry budget: the frame waits for the network.
            # Held frames stay ready; _pump only dispatches the oldest one while offline.
            self._set_status(entry, FrameStatus.QUEUED)
            entry.ready = True
            if entry is self._oldest_unfinished():
                self._schedule_offline_probe(entry)
            return
        entry.retryable_failures += 1
        if entry.retryable_failures >= MAX_RETRYABLE_ATTEMPTS:
            self._fail(entry, outcome.reason)
            return
        base = BACKOFF_DELAYS_S[min(entry.retryable_failures, len(BACKOFF_DELAYS_S)) - 1]
        delay = base * (1.0 + self._rng.uniform(0.0, BACKOFF_JITTER))
        self._set_status(entry, FrameStatus.QUEUED)
        self._schedule_wait(entry, delay)

    def _schedule_offline_probe(self, entry: _Entry) -> None:
        """Wait until 15 s after the frame's last attempt started (no wait if never tried)."""
        if entry.last_started is None:
            entry.token += 1  # drop any pending backoff wait
            entry.ready = True
            return
        elapsed = self._clock() - entry.last_started
        self._schedule_wait(entry, max(0.0, OFFLINE_PROBE_INTERVAL_S - elapsed))

    def _schedule_offline_probe_for_oldest(self) -> None:
        """On going offline, switch the oldest waiting frame from its backoff to the probe timer.

        A frame still in flight gets its probe timer when its outcome arrives (``_on_retryable``).
        """
        oldest = self._oldest_unfinished()
        if oldest is not None and oldest.status is FrameStatus.QUEUED:
            self._schedule_offline_probe(oldest)

    def _fail(self, entry: _Entry, error: str) -> None:
        self._set_status(entry, FrameStatus.FAILED, error)

    # ----------------------------------------------------------------- release & status
    def _release(self) -> None:
        for seq in sorted(self._entries):
            entry = self._entries[seq]
            if entry.status in (FrameStatus.FAILED, FrameStatus.DISCARDED):
                continue
            if entry.status is not FrameStatus.RECOGNIZED:
                return
            if not entry.released and entry.result is not None:
                entry.released = True
                result = entry.result
                entry.result = None
                self.result_ready.emit(seq, result)

    def _set_status(self, entry: _Entry, status: FrameStatus, error: str | None = None) -> None:
        entry.status = status
        if status is not FrameStatus.RECOGNIZED:
            entry.released = False
        self.frame_status.emit(entry.seq, status.value, error)
