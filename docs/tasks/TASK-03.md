# TASK-03: Stitching fixtures (executable spec)
**Status:** todo · **Wave:** 2 · **Depends on:** TASK-01 · **Parallel:** Yes (with TASK-02)

## Goal
Write the stitching test fixtures **before** the stitcher exists. Each fixture is a sequence of frame transcriptions plus the exact expected output. The stitching tasks (TASK-08, TASK-09) are then "make these pass." This is the most important spec artifact in the project. Correctness here matters more than speed.

## Covers
Acceptance criteria for REQ-STI-01 … REQ-STI-10 (as data; the code that runs them is in TASK-08)

## Read first
- [requirements.md §5.4 Stitching](../requirements.md#54-stitching-sti)
- [architecture.md §9 Stitching](../architecture.md#9-stitching), [§11 Gap Marker](../architecture.md#11-gap-marker--text-format), [§17 fixture format](../architecture.md#17-testing-architecture)
- [decision 0003](../decisions/0003-stitch-text-not-images.md)

## Scope
Create `tests/fixtures/stitching/<name>/` for each sequence below. Each folder holds:
- `source.txt`: the true file being "scrolled". Use realistic code of 40–150 lines, mostly Python, with one JSON and one with many `}` lines.
- `frames/<seq>.json`: FrameResult JSON (architecture §7.2), simulating what Claude returns for a window onto `source.txt`. Include `partial` top/bottom lines where noted.
- `expected.txt`: the exact rendered document, including gap markers.
- `meta.json`: `{"description", "uses_line_numbers": bool, "expected_events": [{"kind","frame_seq"}], "expected_conflicts": [{"line": <1-based line in expected.txt>, "alternatives": [...]}]}`.

| Fixture | What it exercises |
| :--- | :--- |
| `seq_basic` | 5 unnumbered frames, 2–4 lines of overlap, no issues. Expected = source. |
| `seq_partial_lines` | Partial lines at the top/bottom edges must not leak into the output (REQ-STI-02). |
| `seq_no_overlap` | Frame 3 jumps ahead: a gap marker plus a `gap_detected` event (REQ-STI-06). |
| `seq_bridge` | Like `seq_no_overlap`, plus a frame 4 that bridges the gap, which is removed, with a `gap_closed` event (REQ-STI-07). |
| `seq_ambiguous_braces` | A frame of only `}`/blank lines that fits 2 places → `ambiguous_overlap` (REQ-STI-06). |
| `seq_scroll_back` | The user scrolls down, back up (contained frame), then down again. No duplicates (REQ-STI-05). |
| `seq_conflicts` | The same lines read differently across frames: uncertain-vs-certain, majority, and an unresolved conflict (REQ-STI-08). |
| `seq_linenums_gap` | Numbered frames covering 1–40 and 48–90 → a `lines 41–47 missing` gap (REQ-STI-03/04). |
| `seq_linenums_start_missing` | The first numbered frame starts at line 5 → a `lines 1–4 missing` gap at the top (REQ-STI-04). |
| `seq_soft_wrap` | A numbered frame with an unnumbered continuation line → a soft_wrap flag and `word_wrap` event (REQ-STI-09). |
| `seq_mixed_numbering` | Numbered frames plus one frame with the gutter cropped (no numbers), placed by overlap. |
| `seq_discard` | 3 frames where frame 2 is junk. `meta.json` adds `"discard": [2]` and `"expected_after_discard"` (REQ-STI-10). |

Also add `tests/fixtures/stitching/README.md` describing the format and each fixture. Optionally add `tests/fixtures/stitching/make_frames.py`, a helper that slices `source.txt` into frames from a spec (window ranges, partial flags, injected misreadings), so fixtures are reproducible. Commit the generated JSON.

## Out of scope
Any stitcher code. The test runner that loads fixtures (TASK-08 writes it).

## Target files
`tests/fixtures/stitching/**`

## Done when
- All 12 fixture folders exist. `tests/fixtures/test_fixture_schema.py` checks, with plain `json` (no dependency on TASK-02, which runs in parallel), that every frame file has the required keys and types from architecture §7.2. TASK-08 later validates them through the pydantic models as well.
- For fixtures with no gaps or conflicts, `expected.txt` == `source.txt`, and a test asserts this.
- The README describes every fixture.
- **A person has reviewed it.** Flag the PR for the user to review, because these fixtures define correct behavior.
