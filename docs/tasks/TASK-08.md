# TASK-08: Stitching core (overlap mode, segments, gaps, voting)
**Status:** done · **Wave:** 3 · **Depends on:** TASK-02, TASK-03 · **Parallel:** Yes

## Goal
Implement the pure stitcher so that every non-line-number fixture from TASK-03 passes.

## Covers
REQ-STI-01, -02, -05, -06, -07, -08, -10, -11, REQ-NFR-02 (stitch timing)

## Read first
- [requirements.md §5.4](../requirements.md#54-stitching-sti)
- [architecture.md §9.1, §9.2, §9.4, §9.5](../architecture.md#9-stitching), [§11](../architecture.md#11-gap-marker--text-format)
- [decision 0003](../decisions/0003-stitch-text-not-images.md)
- `tests/fixtures/stitching/README.md`
- `src/screen_copier/models.py` (the contract) and [architecture.md §10.1](../architecture.md#101-entities-modelspy-pydantic-v2--code). TASK-02 added three things the stitcher must populate:
  - `Observation.cut_off_right`: copied from each `RecognizedLine`. It drives the `cut_off` flag (§9.4).
  - `Gap.kind`: `overlap` for unplaced-frame gaps (§9.2), `line_numbers` for numbered gaps (TASK-09). The controller's `gap` warning counts only `overlap` gaps (§12).
  - `Gap.before_segment` and `Document.gaps`: each gap records the index of the segment it renders immediately before, and `Document.gaps` is kept in render order.

## Scope
- `stitching/normalize.py`: `normalize`, `lines_match`, `is_distinctive`.
- `stitching/overlap.py`: candidate generation and validation, best-candidate choice, and ambiguity detection (§9.2).
- `stitching/resolve.py`: observation voting and conflict flags (§9.4).
- `stitching/stitcher.py`:
  - `Stitcher(settings)` with `add(seq, result) -> list[StitchEvent]`
  - `stitch(results)` as a fold over `add`
  - segment ordering, gap tracking, bridging and merge
  - `document()`, `render() -> str` (with gap markers), `issue_count()`
  - an `uses_line_numbers` hook that TASK-09 plugs into (leave a clear extension point; frames with line numbers go through overlap mode until TASK-09 lands)
- `tests/stitching/test_fixtures.py`: a generic runner that loads every fixture, stitches it incrementally and in batch, and compares the result with `expected.txt`, `expected_events` and `expected_conflicts`. Fixtures with `uses_line_numbers: true` are `xfail` until TASK-09.

## Out of scope
Line-number mode (TASK-09). Any Qt code.

## Target files
`src/screen_copier/stitching/normalize.py`, `overlap.py`, `resolve.py`, `stitcher.py`, `tests/stitching/`

## Done when
- Every non-line-number fixture passes, both incrementally and in batch (REQ-STI-01).
- Unit tests for `normalize` and `lines_match` edge cases, the distinctive-line rule, each §9.4 resolution step, and discard → re-stitch.
- Unit tests for the TASK-02 contract fields:
  - a `cut_off_right` line yields an `Observation` with `cut_off_right=True` and the `cut_off` flag;
  - an unplaced frame yields a `Gap` with `kind=overlap` and the correct `before_segment`;
  - `Document.gaps` stays consistent with `segments` after a bridge merges two segments and after discard → re-stitch.
- Performance: adding one 60-line frame to a 2,000-line document takes ≤200 ms (median of 5, `@pytest.mark.perf`).
- No PySide6 import.
- If a fixture appears wrong, do **not** edit it silently. Raise it in the PR.
