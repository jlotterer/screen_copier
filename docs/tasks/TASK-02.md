# TASK-02: Data models
**Status:** done · **Wave:** 2 · **Depends on:** TASK-01 · **Parallel:** Yes (with TASK-03)

## Goal
Define the pydantic models that every other module exchanges. After this task, `models.py` is the source of truth for the data contract.

## Covers
REQ-SES-07 (version field and the `UnsupportedVersion` error type)

## Read first
- [architecture.md §10.1 Entities](../architecture.md#101-entities-modelspy-pydantic-v2--code), [§9.5](../architecture.md#95-rendering-events-discard) (events, gap marker)

## Scope
- Models:
  - `Session`, `Frame`, `FrameQuality`, `Usage`, `FrameResult`, `RecognizedLine`
  - `Observation`, `DocLine`, `Segment`, `Gap`, `Document`
  - `StitchEvent(kind, frame_seq)` and the event-kind enum
- Enums for frame status, capture mode, session state, and DocLine flags.
- `Document.render() -> str` and `Document.issue_count()` are **not** in scope (TASK-08). Only the data shape is.
- `SCHEMA_VERSION = 1` constant and `UnsupportedVersion` exception.
- JSON round-trip helpers: `Session.model_dump_json()` / `model_validate_json()` with UTC datetimes.
- `FrameResult` must validate exactly the tool schema in architecture §7.2, so it can parse Claude's `tool_use.input`.

## Out of scope
Loading and saving files (TASK-11). Stitching logic (TASK-08).

## Target files
`src/screen_copier/models.py`, `tests/test_models.py`

## Done when
- A round-trip test passes for each entity with every field populated, including null-able fields set to null.
- `FrameResult.model_validate` accepts a sample tool input and rejects one that is missing `lines`.
- The constraint tests pass: `Frame.seq` ≥ 1, `RecognizedLine.line_number` ≥ 1 or null.
- `models.py` does not import PySide6.
- architecture.md §10.1 is updated if any field name differs.
