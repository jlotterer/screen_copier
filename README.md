# Screen Copier

Screen Copier copies code off another computer's screen when copy and paste is not an option.
Point your laptop's webcam at the other monitor and scroll through the code. The app captures
sharp frames as you go, transcribes each one with the Claude vision API, stitches the
overlapping frames into a single document and lets you review it before saving it to a file.

## Requirements

- Windows 11 with a webcam (built-in or USB)
- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com/))

## Install

From a checkout of this repository:

```powershell
uv tool install .
screen-copier --create-shortcut
```

`uv tool install .` puts the `screen-copier` command on your `PATH`. If the command is not
found, run `uv tool update-shell` and open a new terminal.

`screen-copier --create-shortcut` adds a **Screen Copier** shortcut with the app icon to your
desktop (OneDrive-redirected desktops included) and prints where it put it. Double-click it to
start the app without a console window. You can also start the app with `screen-copier`.

To upgrade after pulling new code, run `uv tool install --reinstall .`. To remove the app, run
`uv tool uninstall screen-copier` and delete the shortcut.

## API key

The app needs an Anthropic API key to transcribe frames. Set it up in one of two ways:

- **Settings (recommended):** open **Settings**, paste the key and click **Test**. The key is
  stored in Windows Credential Manager, never in the settings file, session files or logs, and
  the app only ever shows its last 4 characters.
- **Environment variable:** set `ANTHROPIC_API_KEY` before starting the app. When it is set,
  it takes precedence over the key saved in Settings.

## Capture tips

Recognition quality depends mostly on the picture. Before you start:

- **Turn line numbers on** in the editor on the other computer. They make stitching exact and
  show you precisely which lines are missing.
- **Turn word wrap off**, so each source line is one line on screen.
- **Use a font size of 14 px or larger**; zoom the editor in if needed.
- **Remove glare:** dim the room or close the blinds, and avoid lamps reflecting in the screen.
- **Place the camera 40–60 cm from the screen**, square to it and level, so the code fills most
  of the preview.
- **Lock the exposure:** once the picture looks right, untick **Automatic exposure** in
  Settings and set a fixed value, so brightness does not drift while you scroll.
- **Scroll with at least 3 lines of overlap** between screens, and pause briefly after each
  scroll so a sharp frame is captured.

## Troubleshooting

- **The camera preview is black or "camera blocked":** close other apps that use the camera
  (Teams, Zoom, the Camera app), then check *Settings → Privacy & security → Camera* in Windows
  and allow desktop apps to use it. Pick the right camera in the app's Settings if you have
  more than one.
- **Gaps in the document:** you scrolled too far between captures. Scroll back to the lines the
  review view marks as missing and capture them again; keep 3+ lines of overlap.
- **Lines cut off at the edges:** the screen outline was not detected well. Drag the corner
  handles in the capture view to fit the code area, move the camera so the whole screen is in
  view, or turn word wrap off so long lines are not wrapped off screen.
- **Many wrong characters:** increase the font size, reduce glare and move the camera closer.
- **Recognition stops with an authentication error:** the API key is missing or invalid. Fix
  it in Settings; the app resumes the queued frames.
- **Something else:** the log file is in the app's data folder
  (`%LOCALAPPDATA%\ScreenCopier\logs`).

## Privacy

Captured frames are sent to Anthropic's API for transcription, so anything visible in the
camera image, including the code on screen, leaves your computer. Only capture code you are
allowed to share with Anthropic. The app makes no other network calls. Sessions and frame
images are stored locally in the app's data folder (`%LOCALAPPDATA%\ScreenCopier`); saved
sessions are deleted automatically after the retention period (7 days by default).

## More documentation

See [docs/](docs/) for the requirements, the architecture and the decision records.
