# TASK-17: UI, review view
**Status:** todo · **Wave:** 6 · **Depends on:** TASK-14 · **Parallel:** Yes

## Goal
Build the side-by-side review: source frames on the left, editable text on the right, issue highlighting and navigation, a syntax check, and saving.

## Covers
REQ-REV-02, -03, -04, -05, -06, -07 (UI), -08 (UI), -09, -11, REQ-NFR-08

## Read first
- [requirements.md §5.5](../requirements.md#55-review--output-rev)
- [architecture.md §13 Output](../architecture.md#13-output), [§14 UI](../architecture.md#14-ui-structure), [§10.1 DocLine](../architecture.md#101-entities-modelspy-pydantic-v2--code)

## Scope
- `ui/code_editor.py`: a QPlainTextEdit with a gutter for line numbers and issue icons (a distinct shape per issue type), a monospace font, issue backgrounds and tooltips. It maps editor lines to DocLines until the first edit, then keeps highlights only on lines whose text is unchanged.
- `ui/review_view.py`:
  - left frame panel (frames for the current line)
  - editor
  - issue counter, with F8 / Shift+F8 navigation
  - conflict picker (a popup listing the alternatives)
  - language combo (vote result plus override)
  - syntax panel (click to jump)
  - Save button: a gap-count confirmation, a file dialog with the default name, the writer (TASK-10), and an error dialog on `SaveError`
  - edits call `controller.set_edited_text`
  - Save is disabled when the document is empty

## Out of scope
Mode switching and the edit-loss warning (TASK-19).

## Target files
`src/screen_copier/ui/code_editor.py`, `ui/review_view.py`, `tests/ui/test_review_view.py`

## Done when
pytest-qt with a constructed Document:
- 2 uncertain lines + 1 gap → "3 issues". F8 visits them in order. Editing one → "2 issues".
- Cursor on a line observed in frames 4 and 5 → the left panel shows 4 and 5.
- Picking alternative "a" on a conflict → the line text is "a" and the highlight is cleared.
- Python with a missing colon on line 12 → the syntax panel lists line 12, and Save stays enabled.
- 1 gap → the save confirmation appears. Cancel writes nothing (QFileDialog and QMessageBox are mocked).
- Every issue has a gutter icon (REQ-NFR-08).
