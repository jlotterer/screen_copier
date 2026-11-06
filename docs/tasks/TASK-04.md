# TASK-04: Config, secrets, data dir, logging
**Status:** todo · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Provide settings persistence, API-key resolution, data-dir paths and safe logging for the rest of the app.

## Covers
REQ-SES-01, REQ-SES-02 (storage and resolution), REQ-APP-07 (logging part), REQ-NFR-03

## Read first
- [requirements.md §5.6](../requirements.md#56-session--settings-ses)
- [architecture.md §8 Settings & Environment](../architecture.md#8-settings--environment), [§16 Errors & Logging](../architecture.md#16-errors--logging)

## Scope
- `config/paths.py`: `data_dir()` (env override, then platformdirs), plus `sessions_dir()` and `logs_dir()`. Folders are created as needed.
- `config/settings.py`: a `Settings` pydantic model with every key and default from §8.1. `load_settings()` is tolerant (unknown keys are ignored, invalid values fall back to the default per key, unreadable JSON → `settings.bak.json` and defaults). `save_settings()` writes atomically.
- `config/secrets.py`:
  - `get_api_key() -> str | None` (env var first, then keyring)
  - `set_api_key(key)`, `clear_api_key()`
  - `keyring_available() -> bool`
  - `mask_key(key) -> "••••abcd"`
  - Keyring is injectable for tests.
- `config/logging.py`: `setup_logging()` with the rotating handler and the `sk-ant-…` redaction filter.

## Out of scope
The settings UI (TASK-18). The "Test key" API call (TASK-18 uses the TASK-07 client).

## Target files
`src/screen_copier/config/*.py`, `tests/config/`

## Done when
- REQ-SES-01: the unknown key is ignored, `{"model": "claude-opus-5-5", "bogus": 1}` loads with the other keys at their defaults, and corrupt JSON produces `settings.bak.json` and defaults.
- REQ-SES-02: env-over-keyring precedence, keyring-only, neither → None, and a keyring error → `keyring_available() == False`. All of these are tested with a fake keyring backend.
- REQ-NFR-03: logging `"key=sk-ant-abc123"` writes `sk-ant-***` to the file, and `mask_key` shows only the last 4 characters.
- No module imports PySide6.
