# TASK-14: Capture controller
**Status:** todo · **Wave:** 5 · **Depends on:** TASK-06, TASK-09, TASK-10, TASK-11, TASK-12, TASK-13 · **Parallel:** No

## Goal
Wire camera → preprocessing → session save → recognition → stitching behind one Qt-facing object, so the UI only talks to the controller.

## Covers
REQ-CAM-05, REQ-CAM-08, REQ-PRE-03, REQ-OCR-06, REQ-SES-04 (logic), REQ-SES-06 (gate), REQ-NFR-07

## Read first
- [architecture.md §2 Components](../architecture.md#2-components--data-flow), [§4 Threading](../architecture.md#4-threading-model), [§12 Controller Contract](../architecture.md#12-controller-contract-controllerpy--code)
- The public APIs of TASK-06, 09, 10, 11, 12 and 13 (read the code, not just the docs)

## Scope
`controller.py`: `CaptureController(QObject)`, with every signal and slot in §12.
- **On `capture_requested`:**
  1. Preprocess in a worker.
  2. Allocate a seq and save the frame JPEG, then the session (REQ-NFR-07).
  3. Emit `frame_added`.
  4. Submit to the queue, unless consent is pending, in which case emit `consent_required` and hold.
- **On `result_ready`:** update the frame → `stitcher.add` → translate stitch events into `warning` raise/clear → emit `document_changed` with stats → save the session.
- **Discard:** queue discard, mark the frame discarded, re-stitch, save.
- **Editor edits:** `set_edited_text` with a 2 s debounced save.
- **Session life cycle:** `new_session()`, `resume_session(id)` (load, `prepare_resume`, re-submit queued frames, rebuild the document).
- **Warning lifecycle** exactly as in §12.
- Everything injectable: camera worker factory, recognizer, store, clock.

## Out of scope
Widgets and dialogs (TASK-15 to 18). The startup flow ordering (TASK-19).

## Target files
`src/screen_copier/controller.py`, `tests/test_controller.py`

## Done when
Integration tests with pytest-qt, using the fake camera, the fake recognizer (`seq_basic`) and a temporary data dir:
- Capturing 5 frames → `document_changed` text equals `seq_basic/expected.txt`.
- The consent gate: no recognizer call until `acknowledge_consent()`.
- Each frame JPEG exists on disk before its recognizer call.
- A recognizer returning a `cut_off_right` line → `warning("cut_off", active=True)`.
- A no-overlap fixture → `warning("gap", active=True)`, and a later bridging frame clears it.
- Discarding a frame → re-stitched text equals the expected output without that frame.
- Resume: a session with one `queued` frame → the frame is recognized after `resume_session`.
- A locked region is passed through to preprocessing.
