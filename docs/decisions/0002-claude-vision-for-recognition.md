# 0002: Claude vision API for text recognition, no local OCR

- **Status:** Accepted, 2026-11-06. Call mechanism amended by [0006](0006-strict-tool-auto-choice.md): a strict tool with `tool_choice: auto` replaces the forced tool call.
- **Requirements affected:** REQ-OCR-01, REQ-OCR-02, REQ-SES-06, REQ-NFR-04

## Context
The main content is source code, where exact characters and indentation matter. A dropped indent breaks Python and YAML, and confusing `l/1/I`, `O/0` or `{/(` breaks the code. Classic OCR engines are tuned for prose. They often collapse leading whitespace and misread code symbols, especially through a webcam pointed at a monitor.

## Decision
Use the Claude vision API (default model `claude-sonnet-5-5`, configurable) through a forced tool call that returns structured per-line data:
- the text
- the separate line number
- `partial`, `cut_off_right` and `uncertain` flags

## Alternatives rejected
- **Tesseract, PaddleOCR or EasyOCR (local).** Fully offline and private, but noticeably worse on code formatting and symbols, with no per-line uncertainty or line-number separation.
- **Pluggable local + cloud engines.** More work and a larger test surface for an engine the user doesn't plan to use.
- **Opus 5.5 / Haiku 5.5 as the default.** Opus costs more and is slower per frame. Haiku would likely make more transcription errors. Sonnet 5.5 is the balance, and the model is a setting.

## Consequences
- An internet connection and an API key are required. Offline frames are queued (REQ-OCR-09).
- Images of the source screen leave the laptop. The user is told once and must acknowledge it (REQ-SES-06). The only network destination is the Anthropic API.
- There is a small cost per frame. Token usage is shown (REQ-OCR-11).
- Local OCR libraries are banned.
