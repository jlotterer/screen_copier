"""Tests for RecognitionQueue (TASK-13).

Covers REQ-OCR-03, -04, -05, -07, -08, -09 and -11. No network and no real waits: the recognizer is
scripted, and the queue gets a fake sleep that records each wait and returns at once.
"""

from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from screen_copier.models import FrameResult, FrameStatus, RecognizedLine, Usage
from screen_copier.recognition.base import (
    AuthError,
    BadRequest,
    Malformed,
    Ok,
    RecognitionOutcome,
    Retryable,
)
from screen_copier.recognition.queue import (
    OFFLINE_PROBE_INTERVAL_S,
    UNREADABLE_RESPONSE,
    RecognitionQueue,
)

TIMEOUT_MS = 5000


def result(seq: int) -> FrameResult:
    return FrameResult(
        has_line_numbers=False,
        word_wrap_detected=False,
        lines=[
            RecognizedLine(
                text=f"line of frame {seq}",
                line_number=None,
                partial=False,
                cut_off_right=False,
                uncertain=False,
                uncertain_note=None,
            )
        ],
    )


def ok(seq: int, usage: Usage | None = None) -> Ok:
    return Ok(result(seq), usage or Usage())


class ScriptedRecognizer:
    """Returns scripted outcomes per seq (default ``Ok``); can block calls on events."""

    def __init__(self, script: dict[int, list[RecognitionOutcome | Blocked]] | None = None) -> None:
        self.script = {seq: list(outcomes) for seq, outcomes in (script or {}).items()}
        self.calls: list[int] = []
        self.gates: dict[int, threading.Event] = {}
        self.delay_s = 0.0
        self.started: dict[int, threading.Event] = defaultdict(threading.Event)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome:
        with self._lock:
            self.calls.append(frame_seq)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            outcomes = self.script.get(frame_seq)
            outcome = outcomes.pop(0) if outcomes else ok(frame_seq)
        self.started[frame_seq].set()
        try:
            gate = self.gates.get(frame_seq)
            if gate is not None:
                assert gate.wait(5), f"gate for frame {frame_seq} never opened"
            if self.delay_s:
                time.sleep(self.delay_s)
            if isinstance(outcome, Blocked):
                assert outcome.event.wait(5), f"blocked call for frame {frame_seq} never released"
                return outcome.outcome
            return outcome
        finally:
            with self._lock:
                self.in_flight -= 1


@dataclass(frozen=True)
class Blocked:
    """Script entry: wait for ``event`` before returning ``outcome``."""

    event: threading.Event
    outcome: RecognitionOutcome


class FakeSleep:
    """Records each wait. Returns at once, or blocks until ``release`` is set if one is given."""

    def __init__(self) -> None:
        self.waits: list[float] = []
        self.release: threading.Event | None = None
        self._lock = threading.Lock()

    def __call__(self, seconds: float) -> None:
        with self._lock:
            self.waits.append(seconds)
        if self.release is not None:
            assert self.release.wait(5), "fake sleep was never released"


class Recorder:
    """Collects every public signal of a queue."""

    def __init__(self, queue: RecognitionQueue) -> None:
        self.statuses: list[tuple[int, str, str | None]] = []
        self.results: list[int] = []
        self.auth = 0
        self.offline: list[bool] = []
        self.usage: list[tuple[int, int]] = []
        queue.frame_status.connect(lambda s, st, e: self.statuses.append((s, st, e)))
        queue.result_ready.connect(lambda s, r: self.results.append(s))
        queue.auth_required.connect(self._on_auth)
        queue.offline_changed.connect(self.offline.append)
        queue.usage.connect(lambda i, o: self.usage.append((i, o)))

    def _on_auth(self) -> None:
        self.auth += 1

    def last_status(self, seq: int) -> tuple[str, str | None]:
        for s, status, error in reversed(self.statuses):
            if s == seq:
                return status, error
        raise AssertionError(f"no status for frame {seq}")


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def make_queue(qtbot, sleep: FakeSleep) -> Iterator:  # type: ignore[no-untyped-def]
    created: list[RecognitionQueue] = []

    def factory(
        recognizer: ScriptedRecognizer, max_concurrent: int = 3
    ) -> tuple[RecognitionQueue, Recorder]:
        queue = RecognitionQueue(
            recognizer,
            max_concurrent=max_concurrent,
            clock=time.monotonic,
            sleep=sleep,
            rng=random.Random(1234),
        )
        created.append(queue)
        return queue, Recorder(queue)

    yield factory
    for queue in created:
        queue.shutdown()


def submit_all(queue: RecognitionQueue, seqs: range | list[int]) -> None:
    for seq in seqs:
        queue.submit(seq, f"jpeg {seq}".encode())


# --------------------------------------------------------------------------- REQ-OCR-03
def test_never_more_than_max_concurrent_calls_in_flight(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    recognizer.delay_s = 0.2
    queue, rec = make_queue(recognizer, max_concurrent=3)
    peak = 0

    def track(*_: object) -> None:
        nonlocal peak
        peak = max(peak, queue.in_flight)

    queue.frame_status.connect(track)
    submit_all(queue, range(1, 11))
    qtbot.waitUntil(lambda: len(rec.results) == 10, timeout=TIMEOUT_MS)

    assert recognizer.max_in_flight == 3
    assert peak <= 3
    assert rec.results == list(range(1, 11))


# --------------------------------------------------------------------------- REQ-OCR-04
def test_results_released_in_capture_order(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    gates = {seq: threading.Event() for seq in (1, 2, 3)}
    recognizer.gates.update(gates)
    queue, rec = make_queue(recognizer)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: all(recognizer.started[s].is_set() for s in (1, 2, 3)))

    for seq in (3, 1, 2):
        gates[seq].set()
        qtbot.waitUntil(lambda s=seq: queue.status(s) is FrameStatus.RECOGNIZED)
        if seq == 3:
            assert rec.results == []
        if seq == 1:
            assert rec.results == [1]

    assert rec.results == [1, 2, 3]


def test_failed_lower_frame_unblocks_later_results(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [BadRequest("bad image")]})
    gate = threading.Event()
    recognizer.gates[1] = gate
    queue, rec = make_queue(recognizer)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: queue.status(2) is FrameStatus.RECOGNIZED)
    qtbot.waitUntil(lambda: queue.status(3) is FrameStatus.RECOGNIZED)
    assert rec.results == []

    gate.set()
    qtbot.waitUntil(lambda: rec.results == [2, 3])
    assert queue.status(1) is FrameStatus.FAILED


# --------------------------------------------------------------------------- REQ-OCR-05
def test_retryable_twice_then_ok_backs_off(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [Retryable("429"), Retryable("529"), ok(1)]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.results == [1], timeout=TIMEOUT_MS)

    assert queue.status(1) is FrameStatus.RECOGNIZED
    assert len(sleep.waits) == 2
    assert 1.0 <= sleep.waits[0] <= 1.2
    assert 2.0 <= sleep.waits[1] <= 2.4
    assert queue.attempts(1) == 3
    states = [st for s, st, _ in rec.statuses if s == 1]
    assert states == [
        "queued",
        "recognizing",
        "queued",
        "recognizing",
        "queued",
        "recognizing",
        "recognized",
    ]


def test_five_retryable_failures_fail_then_retry_resubmits(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [Retryable("server error 500")] * 5})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: queue.status(1) is FrameStatus.FAILED, timeout=TIMEOUT_MS)

    assert rec.last_status(1) == ("failed", "server error 500")
    assert recognizer.calls == [1] * 5
    assert len(sleep.waits) == 4
    for wait, base in zip(sleep.waits, [1, 2, 4, 8], strict=True):
        assert base <= wait <= base * 1.2

    queue.retry(1)
    qtbot.waitUntil(lambda: rec.results == [1], timeout=TIMEOUT_MS)
    assert recognizer.calls == [1] * 6
    assert queue.status(1) is FrameStatus.RECOGNIZED


def test_retry_ignores_frames_that_are_not_failed(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.results == [1])
    queue.retry(1)
    queue.retry(99)
    qtbot.wait(50)
    assert recognizer.calls == [1]


# --------------------------------------------------------------------------- REQ-OCR-07
def test_auth_error_pauses_and_resume_recovers(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [AuthError("invalid x-api-key")]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.auth == 1)

    assert queue.paused
    assert queue.status(1) is FrameStatus.QUEUED
    assert rec.last_status(1) == ("queued", None)
    assert queue.attempts(1) == 0

    # A frame submitted while paused waits too, and no second auth_required fires.
    queue.submit(2, b"jpeg")
    qtbot.wait(50)
    assert recognizer.calls == [1]
    assert rec.auth == 1

    queue.resume()
    qtbot.waitUntil(lambda: rec.results == [1, 2])
    assert queue.attempts(1) == 1
    assert rec.auth == 1


def test_concurrent_auth_errors_emit_auth_required_once(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({seq: [AuthError("401")] for seq in (1, 2, 3)})
    queue, rec = make_queue(recognizer)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: all(queue.status(s) is FrameStatus.QUEUED for s in (1, 2, 3)))
    qtbot.waitUntil(lambda: len(recognizer.calls) == 3)
    qtbot.wait(50)
    assert rec.auth == 1

    queue.resume()
    qtbot.waitUntil(lambda: rec.results == [1, 2, 3])


def test_auth_required_fires_again_in_a_new_pause(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [AuthError("401"), AuthError("401")]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.auth == 1)
    queue.resume()
    qtbot.waitUntil(lambda: rec.auth == 2)
    queue.resume()
    qtbot.waitUntil(lambda: rec.results == [1])


# --------------------------------------------------------------------------- REQ-OCR-08
def test_malformed_twice_fails_with_unreadable_response(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [Malformed("junk"), Malformed("junk")]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: queue.status(1) is FrameStatus.FAILED)

    assert UNREADABLE_RESPONSE == "Unreadable model response"
    assert rec.last_status(1) == ("failed", "Unreadable model response")
    assert recognizer.calls == [1, 1]


def test_malformed_once_then_ok_recognizes(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [Malformed("junk")]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.results == [1])


# --------------------------------------------------------------------------- REQ-OCR-09
def test_offline_after_three_connection_errors_and_back_online(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    conn = Retryable("connection refused", connection_error=True)
    recognizer = ScriptedRecognizer({1: [conn, conn, conn, ok(1)]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.offline == [True, False], timeout=TIMEOUT_MS)
    qtbot.waitUntil(lambda: rec.results == [1])

    assert not queue.offline
    # Two normal backoffs, then the offline probe interval.
    assert len(sleep.waits) == 3
    assert sleep.waits[2] <= OFFLINE_PROBE_INTERVAL_S
    assert sleep.waits[2] >= OFFLINE_PROBE_INTERVAL_S - 1


def test_offline_holds_other_frames_and_probes_oldest(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    conn = Retryable("timeout", connection_error=True)
    probe = threading.Event()
    # Waits block until released, so no frame is re-sent before the queue goes offline.
    sleep.release = threading.Event()
    # Frames 1-3 each fail once with a connection error, in any order: 3 in a row -> offline.
    # Frame 1 (the oldest) is then probed: blocked then a connection error, another one, then Ok.
    recognizer = ScriptedRecognizer({1: [conn, Blocked(probe, conn), conn], 2: [conn], 3: [conn]})
    queue, rec = make_queue(recognizer)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: rec.offline == [True])
    assert sorted(recognizer.calls) == [1, 2, 3]
    assert queue.offline
    # Going offline switched the oldest frame to the probe interval, whatever order 1-3 failed in.
    qtbot.waitUntil(lambda: any(w > 10 for w in sleep.waits))
    assert [queue.status(s) for s in (1, 2, 3)] == [FrameStatus.QUEUED] * 3

    # While offline, a new frame and frames 2 and 3 are held; only the oldest frame is tried.
    queue.submit(4, b"jpeg")
    sleep.release.set()
    qtbot.waitUntil(lambda: len(recognizer.calls) == 4)
    qtbot.wait(100)
    assert recognizer.calls[3:] == [1]
    assert [queue.status(s) for s in (2, 3, 4)] == [FrameStatus.QUEUED] * 3

    probe.set()
    qtbot.waitUntil(lambda: rec.offline == [True, False], timeout=TIMEOUT_MS)
    qtbot.waitUntil(lambda: rec.results == [1, 2, 3, 4], timeout=TIMEOUT_MS)
    assert recognizer.calls[3:6] == [1, 1, 1]
    assert sorted(recognizer.calls[6:]) == [2, 3, 4]
    # One probe wait on going offline, then one after each failed probe.
    probes = [w for w in sleep.waits if w > 10]
    assert len(probes) == 3
    assert all(OFFLINE_PROBE_INTERVAL_S - 1 <= w <= OFFLINE_PROBE_INTERVAL_S for w in probes)
    # Offline attempts do not use up the retry budget: nothing failed.
    assert all(queue.status(s) is FrameStatus.RECOGNIZED for s in (1, 2, 3, 4))


def test_connection_errors_while_offline_do_not_fail_the_frame(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    conn = Retryable("offline", connection_error=True)
    recognizer = ScriptedRecognizer({1: [conn] * 8})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: rec.results == [1], timeout=TIMEOUT_MS)
    assert rec.offline == [True, False]
    assert "failed" not in [st for _, st, _ in rec.statuses]


# --------------------------------------------------------------------------- discard
def test_discard_in_flight_frame_ignores_its_result(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    gate = threading.Event()
    recognizer.gates[1] = gate
    queue, rec = make_queue(recognizer)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: recognizer.started[1].is_set())
    qtbot.waitUntil(lambda: queue.status(3) is FrameStatus.RECOGNIZED)
    assert rec.results == []

    queue.discard(1)
    qtbot.waitUntil(lambda: rec.results == [2, 3])
    gate.set()
    qtbot.waitUntil(lambda: queue.in_flight == 0)
    qtbot.wait(50)

    assert rec.results == [2, 3]
    assert queue.status(1) is FrameStatus.DISCARDED
    assert rec.last_status(1) == ("discarded", None)


def test_discard_frame_not_started_cancels_it(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    gate = threading.Event()
    recognizer.gates[1] = gate
    queue, rec = make_queue(recognizer, max_concurrent=1)
    submit_all(queue, [1, 2, 3])
    qtbot.waitUntil(lambda: recognizer.started[1].is_set())
    queue.discard(2)
    gate.set()
    qtbot.waitUntil(lambda: rec.results == [1, 3])
    assert recognizer.calls == [1, 3]


# --------------------------------------------------------------------------- REQ-OCR-11
def test_usage_totals_are_running(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [ok(1, Usage(input_tokens=10, output_tokens=5))]})
    recognizer.script[2] = [ok(2, Usage(input_tokens=20, output_tokens=7))]
    queue, rec = make_queue(recognizer, max_concurrent=1)
    submit_all(queue, [1, 2])
    qtbot.waitUntil(lambda: len(rec.usage) == 2)
    assert rec.usage == [(10, 5), (30, 12)]


# --------------------------------------------------------------------------- BadRequest
def test_bad_request_fails_at_once_without_retry(qtbot, make_queue, sleep) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer({1: [BadRequest("image exceeds 5 MB maximum")]})
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: queue.status(1) is FrameStatus.FAILED)
    qtbot.wait(50)

    assert rec.last_status(1) == ("failed", "image exceeds 5 MB maximum")
    assert recognizer.calls == [1]
    assert sleep.waits == []


# --------------------------------------------------------------------------- misc
def test_manual_pause_holds_new_calls(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    queue, rec = make_queue(recognizer)
    queue.pause()
    queue.submit(1, b"jpeg")
    qtbot.wait(50)
    assert recognizer.calls == []
    queue.resume()
    qtbot.waitUntil(lambda: rec.results == [1])
    assert rec.auth == 0


def test_shutdown_ignores_late_outcomes(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    recognizer = ScriptedRecognizer()
    gate = threading.Event()
    recognizer.gates[1] = gate
    queue, rec = make_queue(recognizer)
    queue.submit(1, b"jpeg")
    qtbot.waitUntil(lambda: recognizer.started[1].is_set())
    queue.shutdown()
    gate.set()
    qtbot.wait(100)
    assert rec.results == []
    queue.submit(2, b"jpeg")
    assert recognizer.calls == [1]


def test_duplicate_submit_is_rejected(qtbot, make_queue) -> None:  # type: ignore[no-untyped-def]
    queue, _ = make_queue(ScriptedRecognizer())
    queue.submit(1, b"jpeg")
    with pytest.raises(ValueError):
        queue.submit(1, b"jpeg")


def test_queue_module_is_the_only_recognition_module_with_qt() -> None:
    import ast
    from pathlib import Path

    import screen_copier.recognition as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        if path.name == "queue.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("PySide6"), path.name
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("PySide6") for a in node.names), path.name
