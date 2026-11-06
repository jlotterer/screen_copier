# Task Index

Each task is a self-contained brief for one agent session and one PR. Start with [CLAUDE.md](../../CLAUDE.md), then read the brief, then only the sections it links to.

**Parallel = Yes** means the task shares no files with the other Yes tasks in the same wave, so it can run in its own worktree at the same time.

**Status values:** `todo` · `in-progress` · `done`. Update the status line in the brief in your PR.

| Task | Wave | Area | Summary | Depends on | Parallel |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [TASK-01](TASK-01.md) | 1 | Scaffold | Project skeleton, tooling, CI, empty window | — | No |
| [TASK-02](TASK-02.md) | 2 | Models | Pydantic data models + JSON round-trip | 01 | Yes |
| [TASK-03](TASK-03.md) | 2 | Stitching fixtures | Hand-built FrameResult sequences + expected outputs (executable spec) | 01 | Yes |
| [TASK-04](TASK-04.md) | 3 | Config | Settings, API-key resolution, data dir, logging | 02 | Yes |
| [TASK-05](TASK-05.md) | 3 | Capture logic | Quality metrics + auto-capture state machine | 02 | Yes |
| [TASK-06](TASK-06.md) | 3 | Preprocess | Screen detection, warp, resize/encode | 02 | Yes |
| [TASK-07](TASK-07.md) | 3 | Recognition client | Prompt, tool schema, Claude recognizer, fake recognizer | 02 | Yes |
| [TASK-08](TASK-08.md) | 3 | Stitching core | Overlap mode, segments, gaps, voting, events | 02, 03 | Yes |
| [TASK-09](TASK-09.md) | 4 | Stitching line numbers | Line-number mode, numbered gaps, soft wrap | 08 | Yes |
| [TASK-10](TASK-10.md) | 3 | Output | Language vote, syntax check, file writer | 02 | Yes |
| [TASK-11](TASK-11.md) | 3 | Session store | Atomic autosave, resume discovery, retention, migrations | 02 | Yes |
| [TASK-12](TASK-12.md) | 4 | Camera source | CameraWorker thread, enumeration, exposure, fake camera | 04, 05 | Yes |
| [TASK-13](TASK-13.md) | 4 | Recognition queue | Concurrency, retry/backoff, ordering, offline, auth pause | 07 | Yes |
| [TASK-14](TASK-14.md) | 5 | Controller | CaptureController wiring the pipeline | 06, 09, 10, 11, 12, 13 | No |
| [TASK-15](TASK-15.md) | 6 | UI capture view | Preview, quad handles, banner, stats | 14 | Yes |
| [TASK-16](TASK-16.md) | 6 | UI frame strip | Thumbnails, badges, context menu | 14 | Yes |
| [TASK-17](TASK-17.md) | 6 | UI review view | Editor, issue highlights/nav, syntax panel, save flow | 14 | Yes |
| [TASK-18](TASK-18.md) | 6 | UI settings & dialogs | Settings, consent, resume dialogs | 14 | Yes |
| [TASK-19](TASK-19.md) | 7 | UI integration | Main window, modes, shortcuts, startup flow, E2E test | 15, 16, 17, 18 | No |
| [TASK-20](TASK-20.md) | 8 | Packaging & docs | Desktop shortcut, icon, README | 19 | Yes |
| [TASK-21](TASK-21.md) | 8 | Benchmark & tuning | Benchmark script, real-hardware run, tune defaults | 19 | Yes |

## Waves at a glance
```
W1  01
W2  02 ─ 03
W3  04 05 06 07 08 10 11         (08 also needs 03)
W4  09 12 13
W5  14
W6  15 16 17 18
W7  19
W8  20 21
```

## Requirement Coverage
| Requirement | Covered by |
| :--- | :--- |
| REQ-CAM-01, -02, -03, -04, -10 | TASK-12 |
| REQ-CAM-05 | TASK-14 |
| REQ-CAM-06, -07 | TASK-05 |
| REQ-CAM-08 | TASK-05, TASK-14 |
| REQ-CAM-09 | TASK-15 |
| REQ-PRE-01, -04, -05, -06 | TASK-06 |
| REQ-PRE-02 | TASK-15 |
| REQ-PRE-03 | TASK-06, TASK-14, TASK-15 |
| REQ-OCR-01, -02, -10 | TASK-07 |
| REQ-OCR-03, -04, -05, -09, -11 | TASK-13 |
| REQ-OCR-06 | TASK-14 |
| REQ-OCR-07 | TASK-13, TASK-18 |
| REQ-OCR-08 | TASK-07, TASK-13 |
| REQ-STI-01, -02, -05, -06, -07, -08, -10, -11 | TASK-03 (fixtures), TASK-08 |
| REQ-STI-03, -04, -09 | TASK-03 (fixtures), TASK-09 |
| REQ-REV-01 | TASK-16 |
| REQ-REV-02, -03, -04, -05, -06, -11 | TASK-17 |
| REQ-REV-07, -08, -09 | TASK-10, TASK-17 |
| REQ-REV-10 | TASK-10 |
| REQ-REV-12 | TASK-19 |
| REQ-SES-01 | TASK-04 |
| REQ-SES-02 | TASK-04, TASK-18 |
| REQ-SES-03 | TASK-11 |
| REQ-SES-04 | TASK-14, TASK-18, TASK-19 |
| REQ-SES-05 | TASK-11, TASK-19 |
| REQ-SES-06 | TASK-14, TASK-18 |
| REQ-SES-07 | TASK-02, TASK-11, TASK-18 |
| REQ-APP-01 | TASK-01 |
| REQ-APP-02, -06 | TASK-20 |
| REQ-APP-03, -04 | TASK-19 |
| REQ-APP-05 | TASK-15 |
| REQ-APP-07 | TASK-04, TASK-19 |
| REQ-NFR-01 | TASK-19 |
| REQ-NFR-02 | TASK-06, TASK-08, TASK-12, TASK-21 |
| REQ-NFR-03 | TASK-04, TASK-18 |
| REQ-NFR-04 | TASK-07 |
| REQ-NFR-05 | TASK-01 |
| REQ-NFR-06 | TASK-21 |
| REQ-NFR-07 | TASK-11, TASK-14 |
| REQ-NFR-08 | TASK-17, TASK-19 |
