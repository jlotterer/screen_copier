# Decision Records

Short records of significant decisions and **why** they were made. Agents: read the relevant record before proposing a change to the stack or the approach. Never reverse a decision silently. Add a new record that supersedes the old one, and set the old one's status to `Superseded by NNNN`.

| # | Decision | Status |
| :--- | :--- | :--- |
| [0001](0001-pyside6-desktop-app.md) | Python desktop app with PySide6, not a local web app | Accepted |
| [0002](0002-claude-vision-for-recognition.md) | Claude vision API for recognition, no local OCR | Accepted |
| [0003](0003-stitch-text-not-images.md) | Stitch recognized text, not images; use line numbers when visible | Accepted |
| [0004](0004-capture-modes-and-scope.md) | Auto + manual capture; vertical scrolling only; one file per session | Accepted |
| [0005](0005-windows-only-uv-tool-install.md) | Windows 11 only; `uv tool install` + desktop shortcut | Accepted |
| [0006](0006-strict-tool-auto-choice.md) | Strict tool with `tool_choice: auto`; no forced tool call or temperature (amends 0002) | Accepted |

## Template
```markdown
# NNNN: <Decision title>
- **Status:** Proposed | Accepted | Superseded by NNNN, <date>
- **Requirements affected:** REQ-…
## Context
## Decision
## Alternatives rejected
## Consequences
```
