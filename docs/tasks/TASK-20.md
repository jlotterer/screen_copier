# TASK-20: Packaging, shortcut, README
**Status:** todo · **Wave:** 8 · **Depends on:** TASK-19 · **Parallel:** Yes

## Goal
Make the app installable and launchable from a desktop icon, and document how to use it well.

## Covers
REQ-APP-02, REQ-APP-06

## Read first
- [architecture.md §15 CLI & Packaging](../architecture.md#15-cli--packaging)
- [decision 0005](../decisions/0005-windows-only-uv-tool-install.md)

## Scope
- `shortcut.py`: `create_shortcut() -> int`.
  - Resolve the `screen-copier` executable (`shutil.which`) and the packaged icon path.
  - Run a PowerShell `WScript.Shell` script that creates `Desktop\Screen Copier.lnk`. The Desktop path comes from `[Environment]::GetFolderPath('Desktop')`, which handles OneDrive-redirected Desktops.
  - Print the path, and return 0 on success or 1 with stderr.
- Wire `--create-shortcut` in `app.py`.
- `assets/icon.ico` (simple and original), included as package data.
- `README.md`:
  - what it is
  - requirements (Windows 11, Python 3.12+, uv, an Anthropic API key)
  - install (`uv tool install .`, `screen-copier --create-shortcut`)
  - API-key setup (Settings or the env var)
  - capture tips: line numbers on, word wrap off, font ≥14 px, dim the room or turn off glare, camera 40–60 cm and square to the screen, lock exposure, scroll with 3+ lines of overlap
  - a troubleshooting section (camera blocked, gaps, cut-off lines)
  - a privacy note
  - a link to `docs/`

## Out of scope
A PyInstaller `.exe`.

## Target files
`src/screen_copier/shortcut.py`, `src/screen_copier/app.py` (flag wiring only), `src/screen_copier/assets/icon.ico`, `pyproject.toml` (package data only), `README.md`, `tests/test_shortcut.py`

## Done when
- With `subprocess.run` mocked to succeed, the PowerShell script references `Screen Copier.lnk` and the icon path, and the exit code is 0. Mocked to fail → exit code 1, with the error printed.
- A README test checks that the headings Install, API key, Capture tips, Troubleshooting and Privacy exist.
- **Manual:** `uv tool install .` → `screen-copier --create-shortcut` → double-click the icon → the app opens with no console window. Note the result in the PR.
