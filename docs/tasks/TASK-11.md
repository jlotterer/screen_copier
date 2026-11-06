# TASK-11: Session store (autosave, resume, retention, migrations)
**Status:** todo · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Make sure a crash never loses a capture: persist sessions atomically, find unfinished sessions, and clean up old ones.

## Covers
REQ-SES-03, REQ-SES-05, REQ-SES-07, REQ-NFR-07

## Read first
- [requirements.md §5.6](../requirements.md#56-session--settings-ses)
- [architecture.md §10.2–10.4](../architecture.md#102-state-ownership)

## Scope
- `session/store.py`: `SessionStore(data_dir, clock)` with:
  - `create() -> Session`
  - `save_frame_image(session, seq, jpeg)`, which writes the image before any session save
  - `save(session)` (atomic: tmp file + fsync + `os.replace`)
  - `load(id) -> Session` (applies migrations; on corruption, renames the file and raises `CorruptSession`)
  - `find_unfinished() -> Session | None` (the newest one)
  - `discard(id)`
  - `cleanup_saved(retention_days)`
  - `prepare_resume(session)` (resets `recognizing` frames to `queued`)
- `session/migrations.py`: `MIGRATIONS` registry and `migrate(data: dict) -> dict`, raising `UnsupportedVersion` for newer versions.

## Out of scope
The debounce timer for editor saves (TASK-14 owns timing). The resume dialog (TASK-18).

## Target files
`src/screen_copier/session/*.py`, `tests/session/`

## Done when
- Round trip: a session with 3 frames, results and `edited_text` → save → load gives an equal session, with the frame JPEGs present.
- Atomicity: patching `os.replace` to raise mid-save leaves the previous `session.json` loadable.
- Corruption: invalid JSON → renamed to `session.corrupt-*.json`, and `CorruptSession` is raised.
- `schema_version: 99` → `UnsupportedVersion`.
- Retention with a fake clock: a saved session 8 days old is deleted, one 6 days old is kept, and unsaved ones are never deleted.
- `prepare_resume` turns `recognizing` into `queued`.
- No PySide6 import.
