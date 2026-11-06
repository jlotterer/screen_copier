# TASK-16: UI, frame strip
**Status:** todo · **Wave:** 6 · **Depends on:** TASK-14 · **Parallel:** Yes

## Goal
Show captured frames as thumbnails with their status, and let the user act on them.

## Covers
REQ-REV-01

## Read first
- [requirements.md §5.5](../requirements.md#55-review--output-rev)
- [architecture.md §12](../architecture.md#12-controller-contract-controllerpy--code), [§14](../architecture.md#14-ui-structure)

## Scope
`ui/frame_strip.py`:
- A horizontal `QListView` (icon mode) backed by a model fed from `frame_added` and `frame_updated`.
- Status badges: queued, recognizing (spinner), recognized, failed, discarded (dimmed), blurry. Badges use an icon as well as color.
- Double-click opens a full-size viewer dialog.
- Context menu: Discard, Retry (failed only), Recognize again. The Delete key discards the selected frame.
- Signals `frame_selected(seq)` for the review view.

## Out of scope
The review view (TASK-17).

## Target files
`src/screen_copier/ui/frame_strip.py`, `tests/ui/test_frame_strip.py`

## Done when
pytest-qt with a stub controller:
- 3 frames (queued, recognized, failed) → 3 items with the correct badges.
- The context menu on the failed frame contains Retry, and on the recognized one it does not.
- Delete on the selected frame calls `discard_frame(seq)`.
- Double-click opens the viewer.
