# 0001: Python desktop app with PySide6, not a local web app

- **Status:** Accepted, 2026-11-06
- **Requirements affected:** REQ-CAM-02, REQ-CAM-04, REQ-APP-01, REQ-APP-03

## Context
The user wants a simple experience: launch by clicking a desktop icon or typing one command, a live camera preview, a strip of captured frames, and an editable side-by-side review. Two options were seriously considered:
- a Python desktop app (PySide6 / Qt Widgets)
- a local web app (FastAPI backend plus a browser UI using `getUserMedia`)

Reading small code text off a glowing monitor depends heavily on camera resolution and on fixed (not automatic) exposure.

## Decision
Build a single-process Python desktop app with PySide6 Widgets. Use OpenCV (DirectShow backend) for the camera.

## Alternatives rejected
- **Local web app.** The browser picks the camera settings, often 720p with auto-exposure, and offers little control over exposure and focus. Launching means starting a server and opening a tab. Closing the tab can leave the server running, and the port can clash with something else. That's two moving parts instead of one.
- **Electron or Tauri.** A heavier stack, and image processing would need JavaScript or a Python sidecar. There's no benefit for a one-user tool.
- **QML / Qt Quick.** Widgets give us split panes, a code editor and lists for free, with less code.

## Consequences
- Full camera control: the resolution ladder, manual exposure, and the driver settings dialog.
- One process, launched from a gui-script (no console window) and a desktop shortcut.
- The UI is less easy to style than HTML. That's acceptable.
- Agents MUST NOT introduce web servers, browser UIs, Electron, or QML.
