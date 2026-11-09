# Screen Copier: Agent Guide

Screen Copier is a Windows 11 desktop app (Python + PySide6). A laptop webcam is pointed at another computer's monitor. The app captures frames while the user scrolls through code, transcribes each frame with the Claude vision API, stitches the overlapping frames into one document, and saves it to a file after the user reviews it.

## Where things are
| Doc | Use it for |
| :--- | :--- |
| [docs/requirements.md](docs/requirements.md) | **What** the app must do: REQ IDs, acceptance criteria, non-goals. It changes rarely. |
| [docs/architecture.md](docs/architecture.md) | **How** it is built: components, data model, contracts, algorithms, tunable defaults. |
| [docs/decisions/](docs/decisions/) | **Why** key choices were made. Read these before proposing to change the stack or the approach. |
| [docs/tasks/](docs/tasks/) | One brief per task, plus [the index](docs/tasks/README.md) with waves and dependencies. |
| [specs/001-screen-copier/](specs/001-screen-copier/) | **The live briefs** orch runs: `tasks.md` (phases), one brief per task under `tasks/`, and `spec.md` with the shared contracts. `docs/tasks/` is the original plan and stays for reference. |
| `tests/fixtures/stitching/` | Executable spec for stitching. Expected outputs live here. |

Once code exists, **code is the source of truth for contracts** (`src/screen_copier/models.py`, `recognition/base.py`, `controller.py`). If code and architecture.md disagree, follow the code and fix the doc in the same PR.

Agents follow [AGENTS.md](AGENTS.md).

## Stack (locked; see decision records 0001–0005)
- Python `>=3.12,<3.14`, managed with **uv**
- UI: PySide6 `>=6.8,<7` (Widgets)
- Camera and imaging: opencv-python `>=4.10,<5`, numpy `>=2`
- Recognition: anthropic SDK `>=0.70`, default model `claude-sonnet-5-5`
- Libraries: pydantic `>=2.7,<3`, keyring `>=25`, platformdirs `>=4`, PyYAML `>=6`
- Tests and lint: pytest `>=8`, pytest-qt `>=4.4`, ruff `>=0.6`

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

## Working on a task
1. Read your task brief in `docs/tasks/TASK-NN.md`, then only the requirement and architecture sections it links to.
2. Stay inside the brief's scope and target files. If you need to change something outside them, stop and say so in the PR instead of doing it.
3. Write tests for every acceptance criterion the task lists.
4. Don't reverse a decision record silently. If one seems wrong, say so in the PR and propose a new decision record.

## Definition of done
- `uv run pytest` passes, and lint is clean.
- Every acceptance criterion cited by the task has an automated test, or a documented manual step if the brief marks it as device-only.
- No banned dependency or pattern is introduced.
- **Docs are updated in the same PR** if behavior, contracts, or defaults changed (requirements.md, architecture.md, the task brief's status line).
