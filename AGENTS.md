# Screen Copier: Agent Rules

Rules every worker and validator follows. The Commands, Conventions and Banned sections are kept identical to [CLAUDE.md](CLAUDE.md).

## Commands
- Install deps: `uv sync`
- Run: `uv run screen-copier`
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- End-user install: `uv tool install .` then `screen-copier --create-shortcut`

## Conventions
- `src/` layout. Package at `src/screen_copier/`, with one subpackage per domain: `camera/`, `preprocess/`, `recognition/`, `stitching/`, `output/`, `session/`, `config/`, `ui/`. Tests mirror the package under `tests/`.
- **Pure-logic modules must not import PySide6.** That covers `camera/quality.py`, `camera/trigger.py`, `preprocess/`, `stitching/`, `output/`, `session/`, `config/`, `recognition/` (except `queue.py`, which may use Qt signals), and `models.py`.
- Type hints everywhere. ruff, line length 100.
- Time-dependent code takes an injectable clock and sleep. Randomness is seeded.
- Tunable numbers come from settings (architecture.md §8), never magic constants.
- Commits use Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`).

## Banned
- Local OCR engines (Tesseract, PaddleOCR, EasyOCR): see decision 0002.
- QML/Qt Quick, Electron, web servers, browser UIs: see decision 0001.
- pywin32: create shortcuts through PowerShell `WScript.Shell` called with `subprocess`.
- PyInstaller or other freezers: see decision 0005.
- Network calls to anything other than the Anthropic API.
- Writing the API key to settings, session files, or logs, or showing more than its last 4 characters.
- Blocking work (camera reads, image processing, HTTP, disk I/O larger than a few KB) on the Qt GUI thread.
- Real camera or real Anthropic API in automated tests. Use the fakes (`SCREEN_COPIER_FAKE_CAMERA`, `SCREEN_COPIER_FAKE_RECOGNIZER`, injected clients).

