# TASK-21: Benchmark & tuning on real hardware
**Status:** todo · **Wave:** 8 · **Depends on:** TASK-19 · **Parallel:** Yes

## Goal
Measure real-world accuracy and performance, tune the default thresholds, and confirm or revise the design assumptions.

## Covers
REQ-NFR-06, REQ-NFR-02 (manual part). Resolves assumptions A-01 to A-04 (architecture §18) and A-07 (requirements §7).

## Read first
- [requirements.md §6](../requirements.md#6-non-functional-requirements)
- [architecture.md §8.1 Settings](../architecture.md#81-settings-data_dirsettingsjson-configsettingspy), [§18 Design Assumptions](../architecture.md#18-design-assumptions)

## Scope
- `scripts/benchmark.py <session_dir> <ground_truth_file> [--output report.md]`:
  - Render the session's document.
  - Align it with the ground truth (difflib).
  - Report missing lines, duplicated lines, exact-match %, character error rate, gap and issue counts, frames, tokens, and the estimated cost.
  - Also report the share of real errors that were flagged as issues (are mistakes highlighted?).
- **Hardware runs** (the user runs these; the agent prepares the instructions and the script). Copy a 300-line Python file from a real monitor:
  - (a) line numbers on
  - (b) line numbers off
  - (c) a 2nd camera or lighting condition if available
- Tune `stable_duration_ms`, `stability_threshold`, `motion_threshold`, `sharpness_threshold` and `max_image_edge_px` from the results. Update the §8.1 defaults.
- Write up the results in `docs/benchmark.md`.

## Out of scope
New features. Changing the stitching algorithm. If results show it is needed, propose it in a decision record instead.

## Target files
`scripts/benchmark.py`, `tests/test_benchmark.py`, `docs/benchmark.md`, `src/screen_copier/config/settings.py` (defaults only), `docs/architecture.md` (§8.1, §18)

## Done when
- A unit test of the metrics on fixtures: a known missing line, a duplicate, and a character error are each counted correctly.
- `docs/benchmark.md` shows whether REQ-NFR-06 is met. If not, it proposes revised targets or follow-up work with a rationale.
- architecture.md §18 marks each assumption as confirmed or revised.
