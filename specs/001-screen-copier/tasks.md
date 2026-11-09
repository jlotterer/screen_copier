# Tasks: Screen Copier

Feature folder with one brief per task under `tasks/`. `[P]` = may run in parallel with siblings. Converted from the wave plan in `docs/tasks/README.md`; TASK-NN is T0NN.

## Phase 1: Scaffold
- [x] T001 Project skeleton, tooling, CI, empty window

## Phase 2: Models and fixtures
- [x] T002 [P] Pydantic data models + JSON round-trip
- [x] T003 [P] Hand-built FrameResult sequences + expected outputs (executable spec)

## Phase 3: Domain modules
- [x] T004 [P] Settings, API-key resolution, data dir, logging
- [x] T005 [P] Quality metrics + auto-capture state machine
- [x] T006 [P] Screen detection, warp, resize/encode
- [x] T007 [P] Prompt, tool schema, Claude recognizer, fake recognizer
- [x] T008 [P] Overlap mode, segments, gaps, voting, events
- [ ] T010 [P] Language vote, syntax check, file writer
- [ ] T011 [P] Atomic autosave, resume discovery, retention, migrations

## Phase 4: Line numbers, camera source, recognition queue
- [ ] T009 [P] Line-number mode, numbered gaps, soft wrap
- [ ] T012 [P] CameraWorker thread, enumeration, exposure, fake camera
- [ ] T013 [P] Concurrency, retry/backoff, ordering, offline, auth pause

## Phase 5: Controller
- [ ] T014 CaptureController wiring the pipeline

## Phase 6: UI views
- [ ] T015 [P] Preview, quad handles, banner, stats
- [ ] T016 [P] Thumbnails, badges, context menu
- [ ] T017 [P] Editor, issue highlights/nav, syntax panel, save flow
- [ ] T018 [P] Settings, consent, resume dialogs

## Phase 7: UI integration
- [ ] T019 Main window, modes, shortcuts, startup flow, E2E test

## Phase 8: Packaging and benchmark
- [ ] T020 [P] Desktop shortcut, icon, README
- [ ] T021 [P] Benchmark script, real-hardware run, tune defaults
