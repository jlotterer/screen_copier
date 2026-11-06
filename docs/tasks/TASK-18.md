# TASK-18: UI, settings & dialogs
**Status:** todo · **Wave:** 6 · **Depends on:** TASK-14 · **Parallel:** Yes

## Goal
Build the Settings dialog (including the API key), the first-run consent notice, and the resume dialog.

## Covers
REQ-SES-02 (UI), REQ-SES-04 (UI), REQ-SES-06 (UI), REQ-SES-07 (UI), REQ-OCR-07 (UI), REQ-NFR-03

## Read first
- [requirements.md §5.6](../requirements.md#56-session--settings-ses)
- [architecture.md §8 Settings](../architecture.md#8-settings--environment), [§14 UI](../architecture.md#14-ui-structure)

## Scope
- `ui/settings_dialog.py`:
  - Camera picker (from `list_cameras`), exposure, capture thresholds, model, concurrency, line ending, sound, retention.
  - API key section: masked display, Enter, Test (a `max_tokens=1` call through an injected client), Clear, and a "keyring unavailable" state with env-var guidance.
  - Saves through TASK-04.
  - Opened with a message when `auth_required` fires.
- `ui/dialogs.py`:
  - `ConsentDialog`: a one-time notice that images go to Anthropic. It sets `consent_acknowledged` and calls `controller.acknowledge_consent()`.
  - `ResumeDialog`: "Resume previous capture (N frames, M lines)?" with Resume/Discard. In newer-version mode it offers Discard only.
  - Helpers for the error and confirmation dialogs used elsewhere.

## Out of scope
When the dialogs appear at startup (TASK-19).

## Target files
`src/screen_copier/ui/settings_dialog.py`, `ui/dialogs.py`, `tests/ui/test_dialogs.py`

## Done when
- A key `sk-ant-…abcd` is displayed as `••••abcd`, and the full key never appears in any widget text.
- Test key: the fake client succeeds → "Key works", 401 → "Key rejected".
- Keyring unavailable → the guidance is shown and the Save Key button is disabled.
- ResumeDialog shows only Discard for an `UnsupportedVersion` session.
- ConsentDialog acceptance persists `consent_acknowledged=true`.
