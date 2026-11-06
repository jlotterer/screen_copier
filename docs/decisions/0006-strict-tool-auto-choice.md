# 0006: Strict tool with `tool_choice: auto`, no forced tool call or temperature

- **Status:** Accepted, 2026-11-06
- **Requirements affected:** REQ-OCR-01, REQ-OCR-02, REQ-OCR-08
- **Amends:** [0002](0002-claude-vision-for-recognition.md). Claude vision with a structured `report_transcription` tool still stands. Only the call mechanism changes.

## Context
Decision 0002 and architecture §7.3 specified a **forced** tool call (`tool_choice: {"type": "tool", "name": "report_transcription"}`) with `temperature=0`. Neither works with the locked stack:
- `claude-sonnet-5-5` (the default model) rejects forced `tool_choice` (`tool` and `any`) with a 400: `tool_choice: type "tool" and "any" are not supported for this model.` Every frame would fail as `BadRequest`.
- The locked SDK (`anthropic` 2.6.0) sends API version `2026-10-21`. On that version `temperature`, `top_p` and `top_k` return a 400 at any value, and `messages.create` no longer accepts them.

## Decision
- Send `tool_choice: {"type": "auto"}` and mark `REPORT_TOOL` as `strict: true`. Strict mode guarantees that any `tool_use.input` matches the schema. To meet strict mode's rules, every object has `additionalProperties: false` and every property is in `required`, with the nullable ones (`language_guess`, `uncertain_note`) allowed to be `null`.
- The system prompt says to respond only through the tool, exactly once.
- A reply without a `report_transcription` call (or one stopped by `max_tokens` or `refusal`) is `Malformed`. The queue already retries `Malformed` once (REQ-OCR-08), which covers the rare missed call.
- Send no sampling parameters. Exactness comes from the prompt and the schema, not from temperature.

## Alternatives rejected
- **Structured outputs (`output_config.format`) instead of a tool.** Also guarantees schema-valid JSON, but replaces `REPORT_TOOL` and changes §7.2, the `models.py` contract comments and the brief. That is a bigger change for no gain over a strict tool.
- **Pin an older model that accepts forced tool use.** That would trade transcription quality for an API shape. The model is a user setting, so the request must work on the default.
- **Keep `temperature` through `extra_body`.** It is a 400 on API version `2026-10-21` regardless.

## Consequences
- The model can, in rare cases, answer in text instead of calling the tool. This costs one retry (one extra request) for that frame, not a failure.
- Tool input no longer needs defensive type coercion: strict mode plus the strict pydantic models in `models.py` reject anything off-schema.
- If a future model supports forced tool use again, switching back is a one-line change in `recognition/claude.py` plus a new decision record.
