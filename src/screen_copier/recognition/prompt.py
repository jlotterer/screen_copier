"""System prompt and the ``report_transcription`` tool (architecture §7.2, REQ-OCR-02).

The tool's input schema must stay in step with ``FrameResult`` / ``RecognizedLine`` in
``models.py``. The tool is ``strict`` so the API guarantees schema-valid input; strict mode needs
``additionalProperties: false`` on every object and every property listed in ``required``
(nullable properties are required and may be ``null``). See decision 0006.
"""

from __future__ import annotations

from typing import Any, Final

REPORT_TOOL_NAME: Final = "report_transcription"

SYSTEM_PROMPT: Final = """\
You transcribe source code from a photo of a computer monitor taken with a webcam. \
Your transcription is pasted straight into a file, so it must be exact.

Always respond by calling the report_transcription tool exactly once. \
Do not write any other text. Respond only through the tool.

Rules:
1. Exact transcription. Copy every character exactly as shown. Do not correct typos, \
fix syntax, complete partial code, rename anything, or reformat. If the code on screen is \
wrong, transcribe it wrong.
2. Indentation. Express all leading indentation as spaces, never tabs. Count the spaces from \
the monospace character grid: compare where each line starts with the character columns of \
the lines above and below.
3. Line numbers. If the editor shows line numbers in a gutter, set has_line_numbers to true \
and put each number in that line's line_number field. Never include the line number in text. \
If there are no line numbers, set has_line_numbers to false and line_number to null.
4. Ignore UI. Transcribe only the code. Ignore everything around it: editor tabs, menus, \
title bars, breadcrumbs, the minimap, the status bar, scrollbars, line-change markers in the \
gutter, fold arrows, and the operating system's taskbar and windows.
5. Edge flags. Set partial to true for a line that is clipped at the top or bottom edge of \
the visible code area, so that only part of its height is visible. Set cut_off_right to true \
for a line that continues past the right edge of the window or screen.
6. Uncertainty. When you cannot tell which character is shown, give your best reading, set \
uncertain to true, and explain in uncertain_note (for example "l vs 1 vs I", "O vs 0", \
"{ vs (", "; vs :", "` vs '", or "unsure of the number of spaces"). Otherwise set uncertain \
to false and uncertain_note to null.
7. Word wrap. If the editor soft-wraps long lines, so that one logical line continues on the \
next visual row without its own line number, set word_wrap_detected to true. Report each \
visual row as its own entry in lines, top to bottom, and give continuation rows a \
line_number of null.

List lines top to bottom, including blank lines inside the code (as empty text). \
If no code is visible, report an empty lines list. Set language_guess to the programming \
language's lowercase name (for example "python"), or null if you cannot tell.
"""

_NULLABLE_STRING: Final = {"type": ["string", "null"]}

REPORT_TOOL: Final[dict[str, Any]] = {
    "name": REPORT_TOOL_NAME,
    "description": "Report the exact transcription of the code visible on screen.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["has_line_numbers", "language_guess", "word_wrap_detected", "lines"],
        "properties": {
            "has_line_numbers": {"type": "boolean"},
            "language_guess": _NULLABLE_STRING,
            "word_wrap_detected": {"type": "boolean"},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "text",
                        "line_number",
                        "partial",
                        "cut_off_right",
                        "uncertain",
                        "uncertain_note",
                    ],
                    "properties": {
                        "text": {"type": "string"},
                        "line_number": {"type": ["integer", "null"]},
                        "partial": {"type": "boolean"},
                        "cut_off_right": {"type": "boolean"},
                        "uncertain": {"type": "boolean"},
                        "uncertain_note": _NULLABLE_STRING,
                    },
                },
            },
        },
    },
}

USER_TEXT: Final = "Transcribe this screen."
