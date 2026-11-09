# Tasks: Screen Copier

## Phase 1: Scaffold
- [x] T001 Project skeleton, tooling, CI, empty window

## Phase 2: Models and fixtures
- [x] T002 [P] Pydantic data models + JSON round-trip
- [x] T003 [P] Stitching fixtures: hand-built FrameResult sequences + expected outputs

## Phase 3: Domain modules
- [x] T004 [P] Settings, API-key resolution, data dir, logging
- [x] T005 [P] Quality metrics + auto-capture state machine
- [x] T006 [P] Screen detection, warp, resize/encode
- [x] T007 [P] Prompt, tool schema, Claude recognizer, fake recognizer
- [x] T008 [P] Stitching core: overlap mode, segments, gaps, voting, events
- [ ] T010 [P] Language vote, syntax check, file writer
- [ ] T011 [P] Atomic autosave, resume discovery, retention, migrations

## Phase 4: Sources and queues
- [ ] T009 [P] Line-number mode, numbered gaps, soft wrap
- [ ] T012 [P] CameraWorker thread, enumeration, exposure, fake camera
- [ ] T013 [P] Recognition queue: concurrency, retry/backoff, ordering, offline, auth pause

## Phase 5
- [ ] T014 [x] CaptureController wiring the pipeline. Brief: docs/tasks/TASK14-.md. 

## Phase 6
- [ ] T015 [P] Preview, quad handles, banner, stats. Brief: docs/tasks/TASK-15.md. Owner review required before merge.
- [ ] T016 [P] Thumbnails, badges, context menu. Brief: docs/tasks/TASK-16.md. Owner review required before merge.
- [ ] T017 [P] Editor, issue highlights/nav, syntax panel, save flow. Brief: docs/tasks/TASK-17.md. Owner review required before merge.
- [ ] T018 [P] Settings, consent, resume dialogs. Brief: docs/tasks/TASK-18.md. Owner review required before merge.

## Phase 7
- [ ] T019 [P] Main window, modes, shortcuts, startup flow, E2E test. Brief: docs/tasks/TASK-19.md. 

## Phase 8
- [ ] T020 [P] Desktop shortcut, icon, README. Brief: docs/tasks/TASK-20.md. wner review required before merge.
- [ ] T021 [P] Benchmark script, real-hardware run, tune defaults. Brief: docs/tasks/TASK-21.md. Owner review required before merge.
