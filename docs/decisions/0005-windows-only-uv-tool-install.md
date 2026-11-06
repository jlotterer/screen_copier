# 0005: Windows 11 only; distribute with `uv tool install` + desktop shortcut

- **Status:** Accepted, 2026-11-06
- **Requirements affected:** REQ-APP-01, REQ-APP-02, REQ-SES-02, REQ-NFR-05

## Context
There is one user, with one Windows 11 laptop. Only the laptop runs the app, because the source computer just displays its screen. The user is comfortable with Python and wants to launch the app from a desktop icon or one command.

## Decision
- **Windows 11 x64 only.**
- **Install with `uv tool install .`.** It exposes `screen-copier` as a `gui-scripts` entry point (no console window). `screen-copier --create-shortcut` creates the desktop icon through PowerShell `WScript.Shell`.
- **API key** in Windows Credential Manager via `keyring`. The `ANTHROPIC_API_KEY` environment variable overrides it.

## Alternatives rejected
- **A PyInstaller `.exe`.** A slow extra build step, occasional antivirus false positives, and no benefit for a single Python-savvy user. Reconsider if the app is ever shared.
- **macOS/Linux support.** Not needed. It would add camera-backend and path differences to test.
- **pywin32 for the shortcut.** A heavy dependency for one call that PowerShell can make.

## Consequences
- The camera uses the DirectShow backend, and Windows privacy settings can block it. The app deep-links to the right Settings page.
- Paths go through `platformdirs`, so a future port stays possible but isn't promised.
- CI runs on `windows-latest` only.
