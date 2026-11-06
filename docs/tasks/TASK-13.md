# TASK-13: Recognition queue
**Status:** todo · **Wave:** 4 · **Depends on:** TASK-07 · **Parallel:** Yes

## Goal
Run recognition concurrently and resiliently, and release results strictly in capture order.

## Covers
REQ-OCR-03, -04, -05, -07, -08 (retry once), -09, -11

## Read first
- [requirements.md §5.3](../requirements.md#53-recognition-ocr)
- [architecture.md §7.4 Queue](../architecture.md#74-queue-recognitionqueuepy), [§4 Threading](../architecture.md#4-threading-model)

## Scope
`recognition/queue.py`: `RecognitionQueue(QObject)` with an injected `Recognizer`, clock, sleep, RNG and `max_concurrent`.
- **Methods:** `submit(seq, jpeg)`, `discard(seq)`, `retry(seq)`, `pause()`, `resume()`, `shutdown()`.
- **Signals:** `frame_status(seq, status, error)`, `result_ready(seq, FrameResult)` (in order only), `auth_required()`, `offline_changed(bool)`, `usage(input_tokens, output_tokens)`.
- **Behavior:** the backoff schedule, the Malformed retry once, the auth pause, offline detection and recovery, in-order buffering, and discard handling, all exactly as in §7.4.

## Out of scope
Persisting statuses (TASK-14 listens and saves). The settings UI for the key (TASK-18).

## Target files
`src/screen_copier/recognition/queue.py`, `tests/recognition/test_queue.py`

## Done when
- 10 frames with a 200 ms fake → never more than 3 in flight.
- Completion order 3, 1, 2 → `result_ready` order 1, 2, 3. A failed frame 1 doesn't block 2 and 3 once it is marked failed.
- 429, 429, Ok → recognized after waits of ≥1 s and ≥2 s (fake sleep). 5 Retryables → failed, and `retry()` re-submits.
- AuthError → paused, the frame stays queued, `auth_required` fires once, and `resume()` continues.
- Malformed twice → failed with "Unreadable model response".
- 3 connection errors → `offline_changed(True)`, then a success → `offline_changed(False)`.
- Discarding an in-flight frame → its result is ignored, and later frames are not blocked.
- Usage totals are emitted.
