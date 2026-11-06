# TASK-09: Stitching, line-number mode
**Status:** todo · **Wave:** 4 · **Depends on:** TASK-08 · **Parallel:** Yes

## Goal
When the editor's line numbers are visible, use them to place lines exactly and to report missing ranges.

## Covers
REQ-STI-03, REQ-STI-04, REQ-STI-09

## Read first
- [requirements.md §5.4](../requirements.md#54-stitching-sti)
- [architecture.md §9.3 Line-number mode](../architecture.md#93-line-number-mode-stitchinglinenumpy)
- The extension point TASK-08 left in `stitching/stitcher.py`

## Scope
- `stitching/linenum.py`: place numbered lines by number, attach soft-wrap continuation lines, compute numbered gaps (including the top of the file), and emit `word_wrap` events.
- Wire it into the stitcher through TASK-08's extension point, with minimal edits to `stitcher.py`.
- Mixed documents: frames without numbers in a numbered document use overlap mode and inherit numbers (§9.3).
- Remove the `xfail` from the line-number fixtures.

## Out of scope
Changing overlap-mode behavior. Editing fixtures (raise it in the PR if one looks wrong).

## Target files
`src/screen_copier/stitching/linenum.py`, `src/screen_copier/stitching/stitcher.py` (hook only), `tests/stitching/test_linenum.py`

## Done when
- `seq_linenums_gap`, `seq_linenums_start_missing`, `seq_soft_wrap` and `seq_mixed_numbering` pass, both incrementally and in batch.
- Every fixture from TASK-08 still passes.
- Unit tests: a frame with only 1 numbered line falls back to overlap mode, `word_wrap_detected=true` emits `word_wrap`, and gap descriptions match `lines X–Y missing`.
