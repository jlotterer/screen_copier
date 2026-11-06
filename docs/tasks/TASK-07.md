# TASK-07: Recognition client (Claude + fake)
**Status:** done · **Wave:** 3 · **Depends on:** TASK-02 · **Parallel:** Yes

## Goal
Implement a single-frame recognizer that sends one JPEG to Claude and returns a typed outcome, plus a fake recognizer for tests and demos.

## Covers
REQ-OCR-01, REQ-OCR-02, REQ-OCR-08 (classification), REQ-OCR-10, REQ-NFR-04

## Read first
- [requirements.md §5.3](../requirements.md#53-recognition-ocr)
- [architecture.md §7.1–7.3](../architecture.md#7-recognition)
- [decision 0002](../decisions/0002-claude-vision-for-recognition.md)
- Before writing API code, check the current `anthropic` SDK docs for `messages.create`, tool use, and error classes.

## Scope
- `recognition/base.py`: the `Recognizer` protocol and the outcome types (`Ok`, `Retryable`, `AuthError`, `BadRequest`, `Malformed`).
- `recognition/prompt.py`: `SYSTEM_PROMPT` and `REPORT_TOOL`, exactly as in §7.2.
- `recognition/claude.py`: `ClaudeRecognizer(client_factory, settings)`. It builds the request in §7.3, maps errors with the §7.3 table, parses `tool_use.input` into `FrameResult`, and returns `Usage`. The Anthropic client is injected, so tests never hit the network.
- `recognition/fake.py`: `FakeRecognizer(dir)` returns `Ok(FrameResult from <seq>.json)`, or `Malformed` if the file is missing. It has an optional per-call delay for UI demos.

## Out of scope
Retries, concurrency, ordering and offline handling (TASK-13).

## Target files
`src/screen_copier/recognition/base.py`, `prompt.py`, `claude.py`, `fake.py`, `tests/recognition/test_client.py`, `tests/fixtures/recognizer/seq_basic/`

## Done when
- Request-shape test: model `claude-sonnet-5-5`, no sampling parameters, `tool_choice` `auto` with a `strict` `report_transcription` tool, one base64 `image/jpeg` block, and the client built with `max_retries=0`. (Amended by [decision 0006](../decisions/0006-strict-tool-auto-choice.md): Sonnet 5.5 rejects forced `tool_choice`, and API version `2026-10-21` rejects `temperature`.)
- Prompt test: the system prompt contains an instruction for each REQ-OCR-02 item (exact text, indentation as spaces, line numbers separate, ignore UI, partial, cut_off_right, uncertain + note, word wrap).
- Error-mapping test: each §7.3 row is mapped correctly, using fake SDK exceptions or responses.
- A response without `tool_use`, or with invalid input → `Malformed`.
- The fake recognizer returns the fixture `1.json` for seq 1.
- The only network client is `anthropic.Anthropic` (REQ-NFR-04).
