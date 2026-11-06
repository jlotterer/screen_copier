# TASK-10: Output (language, syntax check, writer)
**Status:** todo · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Implement language detection, syntax checking, and file writing for the final document.

## Covers
REQ-REV-07 (logic), REQ-REV-08 (logic), REQ-REV-09 (default name), REQ-REV-10

## Read first
- [requirements.md §5.5](../requirements.md#55-review--output-rev)
- [architecture.md §13 Output](../architecture.md#13-output), [§11](../architecture.md#11-gap-marker--text-format)

## Scope
- `output/language.py`:
  - `normalize_language(guess) -> str` (alias map)
  - `vote_language(guesses_in_capture_order) -> str` (ties go to the most recent)
  - `extension_for(language) -> str`
- `output/syntax.py`: `check(text, language) -> list[SyntaxIssue] | None`, with gap-marker lines replaced by blank lines before parsing.
- `output/writer.py`: `render_for_save(text, line_ending) -> bytes` and `default_filename(language, now) -> str`. Writing to disk (atomic temp + replace) raises a typed `SaveError` carrying the OS message.

## Out of scope
The UI file dialog and confirmation dialogs (TASK-17).

## Target files
`src/screen_copier/output/*.py`, `tests/output/`

## Done when
- Vote: `[python, python, text]` → python, `[]` → unknown, a tie goes to the most recent, and aliases map correctly.
- Syntax: a Python file with a missing colon on line 12 → an issue at line 12. Valid JSON/YAML/TOML/XML → `[]`. `rust` → None. A gap marker on line 3 doesn't shift reported line numbers.
- Writer: `["a  ", "b"]` with CRLF → `b"a\r\nb\r\n"`. Trailing blank lines are dropped. The output has no BOM. The gap marker is kept verbatim.
- The default filename matches `capture-\d{8}-\d{6}\.py` for Python.
- A permission-denied path raises `SaveError`.
- No PySide6 import.
