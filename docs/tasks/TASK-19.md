# TASK-19: UI integration, main window, startup, E2E
**Status:** todo · **Wave:** 7 · **Depends on:** TASK-15, TASK-16, TASK-17, TASK-18 · **Parallel:** No

## Goal
Assemble the app into one window with two modes, keyboard shortcuts, the startup flow and error handling, and prove the full flow end to end with fakes.

## Covers
REQ-APP-03, REQ-APP-04, REQ-APP-07, REQ-REV-12, REQ-SES-04, REQ-SES-05 (startup call), REQ-NFR-01, REQ-NFR-08

## Read first
- [requirements.md §5.7](../requirements.md#57-app-shell--launch-app), [§6 NFRs](../requirements.md#6-non-functional-requirements)
- [architecture.md §14 UI](../architecture.md#14-ui-structure), [§16 Errors](../architecture.md#16-errors--logging)

## Scope
- `ui/main_window.py`:
  - Toolbar, stacked Capture/Review views, frame strip dock, and the shortcuts from §14 (capture-only shortcuts disabled while the editor has focus).
  - Leaving Review after edits → the edit-loss confirmation (REQ-REV-12).
  - Ctrl+N new session (with a confirmation if unsaved).
- `app.py` startup:
  1. Logging.
  2. Settings.
  3. Session cleanup (retention).
  4. Discover an unfinished session → ResumeDialog.
  5. Create the controller and the window.
  6. Show consent on the first capture if needed.
  - Install `sys.excepthook` → log + error dialog.
- An end-to-end test: the whole app with `SCREEN_COPIER_FAKE_CAMERA`, `SCREEN_COPIER_FAKE_RECOGNIZER` (`seq_basic`) and a temporary data dir.

## Out of scope
The shortcut command and README (TASK-20).

## Target files
`src/screen_copier/ui/main_window.py`, `src/screen_copier/app.py`, `tests/ui/test_main_window.py`, `tests/e2e/test_full_flow.py`

## Done when
- E2E: start → acknowledge consent → capture 5 frames (Space) → Tab into Review → Ctrl+S (dialog mocked to a temporary path) → the file equals `seq_basic/expected.txt`.
- Tab switches modes. Space while the editor has focus doesn't capture.
- Edits followed by switching to Capture, then Cancel → the edits are kept and the mode stays Review.
- Kill-and-restart simulation (a new app instance on the same data dir) → the resume dialog appears, and resuming restores the frames and text.
- An exception raised in a slot → an error dialog is shown, the traceback is in the log, and the session file is still valid.
- Event-loop latency stays ≤100 ms during the E2E run (a QTimer probe). REQ-NFR-01.
- Every toolbar action is reachable from the keyboard (REQ-NFR-08).
