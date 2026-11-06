# 0004: Auto + manual capture; vertical scrolling only; single file per session

- **Status:** Accepted, 2026-11-06
- **Requirements affected:** REQ-CAM-05 … REQ-CAM-08, REQ-OCR-06, REQ-REV-09, non-goals

## Context
The user scrolls through a file on the source while the laptop captures it. Pressing a key for every frame is tedious for long files, but fully automatic capture can misfire on flicker. Long code lines can also run past the screen's right edge, which would make stitching two-dimensional.

## Decision
- **Both capture modes:** Auto (capture when the image is still and sharp after movement, skipping duplicates) and Manual (a hotkey, always available). The user can switch.
- **Vertical scrolling only.** Lines cut off at the right edge are detected and warned about. The user shrinks the font or widens the window instead.
- **One output file per session**, saved through a file dialog. No clipboard copy.
- **Editable review before saving**, with highlighted issues and an informational syntax check that never blocks saving.

## Alternatives rejected
- **Manual capture only.** Predictable, but slow for long files.
- **2D (horizontal) stitching.** Much more complex and error-prone for a rare case the user can avoid.
- **Clipboard copy, or several files per session.** The user didn't ask for them. They can be added later behind new REQ IDs.

## Consequences
- The auto-capture thresholds must be tuned on real hardware (TASK-21).
- The README must explain the source-side setup: line numbers on, word wrap off, font size, and overlap while scrolling.
