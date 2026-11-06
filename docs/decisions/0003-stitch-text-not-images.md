# 0003: Stitch recognized text, not images; use line numbers when visible

- **Status:** Accepted, 2026-11-06
- **Requirements affected:** REQ-STI-01 … REQ-STI-11

## Context
Long files need many frames as the user scrolls. These must be combined with no lost or duplicated lines. When the camera points at a monitor, the images suffer from moiré, glare and refresh flicker, and the scroll distance varies between frames.

## Decision
1. Transcribe each frame on its own, then merge on the **text** by aligning overlapping lines (fuzzy line matching).
2. When the editor shows **line numbers**, use them to place lines and to detect missing ranges exactly. Fall back to text overlap when they are absent.
3. **Never guess.** An unplaceable or ambiguous frame becomes a visible gap with a live warning. A later overlapping frame closes the gap.
4. The stitcher is a **pure, deterministic** function of the ordered frame results, so it can be tested thoroughly with fixtures.

## Alternatives rejected
- **Image stitching (a tall panorama, then OCR).** Fragile with moiré and refresh artifacts, and harder to test. A single misalignment corrupts everything after it.
- **Asking the model to merge several frames.** It isn't deterministic, it's hard to test, it costs more tokens, and the model may silently "fix" or drop lines.
- **Requiring line numbers.** The most reliable option, but it wouldn't work for terminals and viewers without a gutter.

## Consequences
- The user needs about 2–3 lines of overlap between captures (stated in the README tips).
- Repetitive code (for example `}` blocks) can be ambiguous. This is handled by the distinctive-line rule and by reporting ambiguity, not by guessing.
- Fixture sequences in `tests/fixtures/stitching/` are the executable spec for this behavior.
