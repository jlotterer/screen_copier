# TASK-01: Project scaffold
**Status:** done · **Wave:** 1 · **Depends on:** — · **Parallel:** No

## Goal
Create the project skeleton so that every later task has a working package, test runner, linter and CI to build on.

## Covers
REQ-APP-01, REQ-NFR-05

## Read first
- [CLAUDE.md](../../CLAUDE.md): stack, commands, conventions
- [architecture.md §3 Module Layout](../architecture.md#3-module-layout), [§15 CLI & Packaging](../architecture.md#15-cli--packaging)
- [decisions 0001](../decisions/0001-pyside6-desktop-app.md), [0005](../decisions/0005-windows-only-uv-tool-install.md)

## Scope
- `pyproject.toml`:
  - project metadata and version `0.1.0`
  - runtime deps with the version bounds from CLAUDE.md
  - dev deps: pytest, pytest-qt, ruff
  - `[project.gui-scripts] screen-copier = "screen_copier.app:main"`
  - ruff config (line length 100)
  - pytest config (`testpaths = ["tests"]`, `qt_api = "pyside6"`)
- Create every subpackage in the architecture §3 layout as an empty `__init__.py`. Later tasks then only add files and never edit shared `__init__`s.
- `app.py`: `main()` parses `--version` (prints the version, exits 0). Otherwise it opens an empty `QMainWindow` titled "Screen Copier".
- `.github/workflows/ci.yml`: `windows-latest`, Python 3.12 and 3.13, `uv sync`, lint, `uv run pytest` (with `QT_QPA_PLATFORM=offscreen`).
- `.gitignore` for Python, uv and IDE files.

## Out of scope
Any real feature code. `--create-shortcut` (TASK-20). README content beyond a one-line placeholder (TASK-20).

## Target files
`pyproject.toml`, `uv.lock`, `.gitignore`, `.github/workflows/ci.yml`, `src/screen_copier/**/__init__.py`, `src/screen_copier/app.py`, `tests/test_smoke.py`, `README.md` (placeholder)

## Done when
- `uv sync && uv run pytest` passes, with `tests/test_smoke.py` checking that:
  - `screen_copier.app` imports
  - `[project.gui-scripts]` contains `screen-copier`
  - `--version` prints `0.1.0`
  - the main window can be created under pytest-qt
- Lint is clean, and the CI workflow file is valid.
