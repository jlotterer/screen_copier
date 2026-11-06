# TASK-15: UI, capture view
**Status:** todo · **Wave:** 6 · **Depends on:** TASK-14 · **Parallel:** Yes

## Goal
Build the Capture-mode screen: the live preview with the screen outline, live stats, and warnings.

## Covers
REQ-CAM-09, REQ-PRE-02, REQ-PRE-03 (UI), REQ-APP-05

## Read first
- [requirements.md §5.1, §5.2, §5.7](../requirements.md#5-functional-requirements)
- [architecture.md §12 Controller](../architecture.md#12-controller-contract-controllerpy--code), [§14 UI Structure](../architecture.md#14-ui-structure)

## Scope
- `ui/preview_widget.py`: paints frames scaled to fit, draws the detected quad, and has 4 draggable corner handles with Lock/Clear buttons (calling `controller.lock_region`). It shows the trigger state ("Moving…", "Settling…", "Waiting for focus", "Captured") and a capture flash.
- `ui/banner.py`: a stack of warnings keyed by `kind`. It shows, updates and hides on `warning(kind, message, active)`, and has a dismiss button. Plays a capture sound and a distinct gap sound (QSoundEffect, bundled WAVs) when `sound_enabled`.
- `ui/capture_view.py`: composes the preview, a stats bar (frames, lines, gaps, issues, tokens, resolution, Auto/Manual) and the banner. Empty and error states for `camera_error` include a "Open camera privacy settings" button (`ms-settings:privacy-webcam`).

## Out of scope
Main window and shortcuts (TASK-19). Frame strip (TASK-16).

## Target files
`src/screen_copier/ui/preview_widget.py`, `ui/banner.py`, `ui/capture_view.py`, `src/screen_copier/assets/*.wav`, `tests/ui/test_capture_view.py`

## Done when
pytest-qt with a stub controller (a QObject with the same signals):
- `warning("gap", …, True)` shows the banner, and `active=False` hides it.
- Dragging the handles and pressing Lock calls `lock_region` with 4 points.
- `camera_error("unavailable")` shows the error state with the settings button.
- The stats bar updates on `document_changed`.
- With sound disabled, no sound is played (mock QSoundEffect).
