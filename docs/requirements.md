# Screen Copier: Requirements

> **Scope of this document:** *what* the app must do and how we'll know it does it. *How* it is built (algorithms, thresholds, data formats, libraries) lives in [architecture.md](architecture.md). *Why* key choices were made lives in [decisions/](decisions/). Requirement IDs are stable: never renumber; retire an ID instead of reusing it.

## 1. Objective
A single developer needs to copy source code from one computer (the **source**) to their laptop when there is no other way to transfer it. They point the laptop's webcam at the source's monitor, scroll through the file, and Screen Copier produces a text file with the code. It warns them right away if they skipped part of the file, and it highlights anything it's unsure of so they can fix it before saving.

**Success:** a 300-line code file is copied with no missing or duplicated lines and only a few character errors, all of them highlighted for review (REQ-NFR-06).

## 2. Users & Context
- **One user**, a developer, using their own Windows 11 laptop with a built-in or USB webcam.
- **The source** is a monitor or laptop screen showing code in an editor or terminal. Nothing can be installed on it.
- **The user controls the source display:** they can turn on line numbers, turn off word wrap, and change the font size.
- **An internet connection is available on the laptop.** The user accepts that images of the screen are sent to Anthropic's API.

## 3. Core Workflow
1. Open the app (desktop icon or `screen-copier` command). A live camera preview appears.
2. Point the camera at the source screen. The app finds and straightens the screen area.
3. Scroll through the file on the source. Frames are captured **automatically** each time the image settles, or **manually** by keypress.
4. Each frame is transcribed in the background and stitched into the growing document. The app warns live about gaps, cut-off lines, and word wrap.
5. Switch to **Review**: each line of text sits next to the image(s) it came from, with uncertain lines highlighted. Fix them and check the syntax.
6. Save to a file.

## 4. Non-Goals
- **Horizontal scrolling.** Lines wider than the screen are detected and warned about, not stitched. The user shrinks the font or turns off wrap instead.
- **Offline or local OCR.**
- **macOS and Linux.**
- **Copy to clipboard.**
- **Several output files per session.** Start a new session for the next file.
- **Raw frames as an output.** Frames are kept only for autosave and resume.
- **A standalone `.exe` or installer.**
- **Tuning for printed paper or phone screens.** These may work but are not tuned or tested.
- **Software on the source computer.**
- **Reconstructing tab characters.** Indentation is output as spaces.
- **Correcting or completing code.** The output reproduces the screen, typos included.

## 5. Functional Requirements

Keywords MUST / SHOULD / MAY follow RFC 2119.

### 5.1 Camera & Capture (CAM)
**User intent:** As the user, I want a live preview and frames captured with as little effort as possible while I scroll.

| ID | Requirement |
| :--- | :--- |
| REQ-CAM-01 | The user MUST be able to choose which camera to use, and the choice MUST be remembered across launches. |
| REQ-CAM-02 | The app MUST use the highest resolution the camera supports, up to 4K, and MUST show the resolution in use. |
| REQ-CAM-03 | The app MUST show a smooth live preview (at least 15 fps), and the UI MUST stay responsive while it does. |
| REQ-CAM-04 | The user MUST be able to set exposure manually when the camera supports it, because bright monitors wash out under auto-exposure. The app SHOULD offer access to the camera driver's own settings. |
| REQ-CAM-05 | The app MUST offer an **Auto** and a **Manual** capture mode that the user can switch between. A manual capture MUST be possible in either mode. |
| REQ-CAM-06 | In Auto mode, the app MUST capture exactly one frame each time the image becomes still and sharp after movement. It MUST NOT capture repeatedly while the image stays still. |
| REQ-CAM-07 | Auto mode MUST NOT capture a frame that shows the same content as the previous capture. |
| REQ-CAM-08 | A manual capture of a blurry image MUST still be captured, and MUST be marked as blurry. |
| REQ-CAM-09 | Every capture MUST give visual feedback and, unless sound is turned off, audible feedback. |
| REQ-CAM-10 | The app MUST be runnable with a simulated camera (a folder of images or a video file) for testing and demos. |

**Edge cases:**
- **No camera:** the app shows a clear empty state with Retry.
- **Camera blocked by Windows privacy settings, or in use by another app:** the app explains the cause and links to the Windows camera privacy settings.
- **Camera disconnected mid-session:** auto-capture stops, the app offers Reconnect, and the session is untouched.
- **Only low resolutions available (below 720p):** the app warns that accuracy will suffer.
- **Image never settles** (flicker or moiré): after a few seconds, the app suggests manual capture or adjusting sensitivity.

**Acceptance criteria:**
- `REQ-CAM-01`: Given two cameras, When the user selects the second and restarts, Then the second camera is selected.
- `REQ-CAM-02`: Given a camera that supports at most 1080p, When it opens, Then the UI shows 1920×1080.
- `REQ-CAM-03`: Given a 30 fps camera, When previewing for 3 s, Then at least 45 frames are displayed, and the UI responds to input within 100 ms throughout.
- `REQ-CAM-05`: Given either mode, When the user presses the capture key, Then exactly one frame is captured.
- `REQ-CAM-06`: Given Auto mode, When the image moves, then is still and sharp for the configured settle time, then stays still, Then exactly one frame is captured. When it moves and settles again, Then exactly one more is captured.
- `REQ-CAM-06`: Given Auto mode and a still but blurry image, When time passes, Then nothing is captured and the UI shows it is waiting for focus.
- `REQ-CAM-07`: Given Auto mode, When the user scrolls away and back to exactly the previous view, Then no new frame is captured.
- `REQ-CAM-08`: Given a blurry image, When the user captures manually, Then a frame is captured and shown as "Blurry".
- `REQ-CAM-10`: Given the simulated camera points at a folder of 3 images, When the app starts, Then the preview shows those images.

### 5.2 Screen Detection (PRE)
**User intent:** As the user, I want only the source screen used, straightened and cropped, so clutter around it doesn't confuse recognition.

| ID | Requirement |
| :--- | :--- |
| REQ-PRE-01 | The app MUST detect the source screen in the camera image and correct for perspective, so the text is straight and rectangular. |
| REQ-PRE-02 | The live preview MUST show the detected screen outline. |
| REQ-PRE-03 | The user MUST be able to set the screen outline manually by dragging its corners, and lock it so it overrides detection until cleared. |
| REQ-PRE-04 | If no screen is detected and none is locked, the app MUST use the whole camera image. |
| REQ-PRE-05 | Images sent for recognition MUST be no larger than needed for accurate reading (the size limit is configurable), so cost and time stay low. Images MUST NEVER be upscaled. |
| REQ-PRE-06 | Preparing a captured frame MUST NOT delay the preview or the UI. |

**Acceptance criteria:**
- `REQ-PRE-01`: Given a photo of a code screen taken at an angle of up to 15°, When it is processed, Then straight vertical text edges stay straight (within 2 px) and the aspect ratio is within 5% of the true screen's.
- `REQ-PRE-03`: Given a locked outline, When a frame is captured where detection finds a different outline, Then the locked outline is used.
- `REQ-PRE-04`: Given an image with no screen in it, When it is processed, Then the whole image is used.
- `REQ-PRE-05`: Given a screen region larger than the size limit, Then the result fits the limit with the aspect ratio kept. Given a region smaller than the limit, Then it is not enlarged.

### 5.3 Recognition (OCR)
**User intent:** As the user, I want each frame transcribed exactly, with indentation, symbols and line numbers kept, so the copied code works without retyping.

| ID | Requirement |
| :--- | :--- |
| REQ-OCR-01 | Each captured frame MUST be transcribed by the Claude vision API, using the model chosen in settings (default `claude-sonnet-5-5`). |
| REQ-OCR-02 | Transcription MUST: reproduce the text exactly, with no corrections; keep indentation; report editor line numbers separately from the code; ignore editor and OS UI around the code; and report, for each line, whether it is partly hidden at the top or bottom edge, cut off at the right edge, or uncertain (with the reason). It MUST also report whether visual word wrap is present. |
| REQ-OCR-03 | Several frames MUST be transcribed at the same time (with a configurable limit), so capture never waits for recognition. |
| REQ-OCR-04 | Transcribed frames MUST be added to the document in the order they were captured, whatever order transcription finishes in. |
| REQ-OCR-05 | Temporary failures (rate limits, server errors, timeouts, dropped connections) MUST be retried on their own with increasing delays. After repeated failure, the frame MUST be marked failed, and the user MUST be able to retry it. |
| REQ-OCR-06 | Lines cut off at the right edge MUST produce a live warning telling the user to shrink the font or widen the window, and MUST be highlighted in review. |
| REQ-OCR-07 | A missing or invalid API key MUST pause transcription (keeping the frames) and take the user to Settings with an explanation. |
| REQ-OCR-08 | An unreadable model response MUST be retried once, then the frame marked failed with an explanation. |
| REQ-OCR-09 | Without internet, the app MUST keep capturing, show how many frames are waiting, and continue on its own when the connection returns. |
| REQ-OCR-10 | The app MUST be runnable with a simulated recognizer (prepared transcription files) for testing and demos. |
| REQ-OCR-11 | The app SHOULD show the API tokens used in the current session. |

**Edge cases:**
- **Blank screen:** produces no lines, no gap, no error.
- **A frame discarded while it is being transcribed:** its result is ignored.
- **App closed with frames still waiting:** they are transcribed after the session is resumed.

**Acceptance criteria:**
- `REQ-OCR-01`: Given the default settings, When a frame is transcribed, Then the request uses `claude-sonnet-5-5` and contains that frame's image.
- `REQ-OCR-03`: Given 10 waiting frames and a concurrency limit of 3, When transcribing, Then no more than 3 requests run at once.
- `REQ-OCR-04`: Given frames 1–3 finishing in the order 3, 1, 2, Then they are added to the document in the order 1, 2, 3.
- `REQ-OCR-05`: Given a rate-limit error twice and then success, Then the frame ends up transcribed with no user action. Given failures on every attempt, Then the frame is marked failed and Retry transcribes it again.
- `REQ-OCR-07`: Given an invalid key, When a frame is sent, Then transcription pauses, the frame stays waiting, and Settings opens once.
- `REQ-OCR-08`: Given two unreadable responses, Then the frame is marked failed with the reason "Unreadable model response".
- `REQ-OCR-09`: Given the connection drops and returns, Then an offline banner appears, then disappears, and waiting frames are transcribed.
- `REQ-OCR-10`: Given the simulated recognizer has a file for frame 1, When frame 1 is transcribed, Then the result equals that file.

### 5.4 Stitching (STI)
**User intent:** As the user, I want the frames joined into one document in the right order, with no lost or doubled lines, and to know right away when I've skipped part of the file.

| ID | Requirement |
| :--- | :--- |
| REQ-STI-01 | The document MUST depend only on the transcribed frames and their capture order. Building it one frame at a time and all at once MUST give the same result. |
| REQ-STI-02 | Lines partly hidden at the top or bottom edge MUST NOT appear in the document unless a fully visible copy exists. |
| REQ-STI-03 | When editor line numbers are visible, the app MUST use them to place lines. |
| REQ-STI-04 | When line numbers are used, missing line numbers MUST be reported as gaps ("lines 41–47 missing"), including missing lines at the start of the file. |
| REQ-STI-05 | Without line numbers, the app MUST place each frame by matching its lines with lines already captured. This must work whether the user scrolls down, scrolls back up, or re-captures a region already seen. |
| REQ-STI-06 | If a frame can't be placed, or could fit in more than one place, the app MUST NOT guess. It MUST mark a gap in the document and warn the user live to scroll back and capture an overlapping view. |
| REQ-STI-07 | When a later frame fills a gap, the gap MUST disappear from the document and from the live gap count. |
| REQ-STI-08 | When frames disagree about a line, the app MUST prefer confident readings over uncertain ones and agreement over a single reading. If it still can't decide, it MUST mark the line as a conflict and keep every alternative for the user to choose from. |
| REQ-STI-09 | When word wrap appears to be on at the source, the app MUST warn the user, and MUST flag the affected lines in review. |
| REQ-STI-10 | Discarding a frame MUST rebuild the document as if that frame had never been captured. |
| REQ-STI-11 | Adding a frame MUST update the document fast enough to feel instant (≤200 ms for a 2,000-line document). |

**Edge cases:**
- **Repetitive code** (many identical lines such as `}`): the app reports ambiguity and does not guess (REQ-STI-06).
- **End of file:** the app cannot detect it. The user decides when capture is complete.

**Acceptance criteria:**
- `REQ-STI-01`: Given any fixture sequence in `tests/fixtures/stitching/`, When stitched frame by frame and all at once, Then both results equal the fixture's `expected.txt`.
- `REQ-STI-02`: Given a frame ending with a half-visible `def fo` and the next frame showing `def foo():` in full, Then the output contains `def foo():` once and no `def fo`.
- `REQ-STI-03`/`-04`: Given numbered frames covering lines 1–40 and 48–90, Then the document has lines 1–40, a gap marker for "lines 41–47 missing", and lines 48–90.
- `REQ-STI-04`: Given the first numbered frame starts at line 5, Then a gap for "lines 1–4 missing" appears at the top.
- `REQ-STI-05`: Given unnumbered frames that each overlap the previous by 3 lines, Then the output has no duplicated or missing lines.
- `REQ-STI-06`: Given a frame that shares no lines with the document, Then a gap marker separates it and a gap warning is shown.
- `REQ-STI-06`: Given a frame of only `}` and blank lines that fits two places, Then the app reports ambiguity instead of placing it.
- `REQ-STI-07`: Given a gap, When a frame overlapping both sides of it arrives, Then the gap marker disappears and the gap count decreases.
- `REQ-STI-08`: Given one uncertain reading "x = 1" and one confident "x = l", Then "x = l" is used. Given two confident readings that differ, Then the line is marked a conflict with both alternatives.
- `REQ-STI-09`: Given a numbered frame with a line that has no number of its own, Then a word-wrap warning appears and the line is flagged.
- `REQ-STI-10`: Given frames 1–3, When frame 2 is discarded, Then the document equals one built from frames 1 and 3 only.
- `REQ-STI-11`: Given a 2,000-line document, When one frame is added, Then the update takes ≤200 ms.

### 5.5 Review & Output (REV)
**User intent:** As the user, I want to compare the text with the images, fix highlighted problems, check the syntax, and save to a file.

| ID | Requirement |
| :--- | :--- |
| REQ-REV-01 | Captured frames MUST be shown as thumbnails in capture order, each showing its status (waiting, transcribing, done, failed, discarded, blurry). The user MUST be able to enlarge a frame, discard it, retry it if failed, and transcribe it again. |
| REQ-REV-02 | A Review mode MUST show the source image(s) next to editable text with line numbers. |
| REQ-REV-03 | Review MUST highlight every issue (uncertain line, conflict, cut-off line, possible word-wrap line, gap) and explain the reason on hover. Highlighting MUST NOT rely on color alone. |
| REQ-REV-04 | Placing the cursor on a line MUST show the frame(s) that line came from. |
| REQ-REV-05 | The user MUST be able to jump to the next and previous issue by keyboard, and see how many issues remain. Editing a highlighted line MUST clear its highlight. |
| REQ-REV-06 | For a conflict, the user MUST be able to pick one of the alternatives with one click. |
| REQ-REV-07 | The app MUST guess the programming language from the frames, and the user MUST be able to override the guess. |
| REQ-REV-08 | For Python, JSON, YAML, TOML and XML, the app MUST check syntax and list errors with clickable line numbers. Syntax errors MUST NOT prevent saving. |
| REQ-REV-09 | Saving MUST let the user choose the file location and name. The default name is timestamped, with an extension matching the language. |
| REQ-REV-10 | Saved files MUST be UTF-8, MUST use the line ending chosen in settings (default LF), MUST have no trailing whitespace, MUST end with one newline, and MUST keep any unresolved gap markers visible in the text. |
| REQ-REV-11 | If gaps remain, saving MUST ask for confirmation first. |
| REQ-REV-12 | Going back to capture after editing in Review MUST warn that capturing more will discard the edits, and MUST let the user cancel. |

**Edge cases:**
- **Save fails** (permission denied, disk full): an error explains why, and the user can choose another location.
- **Nothing captured:** Save is disabled, with an explanation.

**Acceptance criteria:**
- `REQ-REV-01`: Given frames that are waiting, done and failed, Then each thumbnail shows its status, and only the failed one offers Retry.
- `REQ-REV-03`/`-05`: Given 2 uncertain lines and 1 gap, Then "3 issues" is shown. Next-issue visits each in order. Editing one shows "2 issues".
- `REQ-REV-04`: Given line 50 came from frames 4 and 5, When the cursor is on line 50, Then frames 4 and 5 are shown.
- `REQ-REV-06`: Given a conflict with alternatives "a" and "b", When the user picks "a", Then the line reads "a" and is no longer highlighted.
- `REQ-REV-07`: Given frames mostly guessed as Python, Then the language is Python. When the user picks JSON, Then the syntax check uses JSON.
- `REQ-REV-08`: Given Python with a missing colon on line 12, Then a syntax error at line 12 is listed, and Save stays enabled.
- `REQ-REV-10`: Given the lines `a␠␠` and `b` with CRLF chosen, Then the file bytes are `a\r\nb\r\n`.
- `REQ-REV-11`: Given 1 gap, When the user saves and cancels the confirmation, Then no file is written. When they confirm, Then the file is written with the gap marker in it.
- `REQ-REV-12`: Given edits in Review, When the user switches to capture and cancels, Then the edits are kept.

### 5.6 Session & Settings (SES)
**User intent:** As the user, I want settings and my API key remembered securely, and a crash not to lose a long capture.

| ID | Requirement |
| :--- | :--- |
| REQ-SES-01 | Settings MUST persist across launches. A damaged or outdated settings file MUST NOT prevent the app from starting. |
| REQ-SES-02 | The API key MUST be stored securely (in Windows Credential Manager), or provided through the `ANTHROPIC_API_KEY` environment variable, which takes priority. The user MUST be able to enter, test and remove the stored key in Settings. |
| REQ-SES-03 | The capture session MUST be saved continuously, so that a crash or forced close loses nothing that was captured or edited (edits within the last few seconds excepted). |
| REQ-SES-04 | On launch after an unfinished session, the app MUST offer to resume it (showing its size) or discard it. Resuming MUST restore frames, text, edits and mode, and MUST continue transcribing frames that were still waiting. |
| REQ-SES-05 | Saved sessions MUST be cleaned up automatically after a configurable period (default 7 days). Discarded sessions MUST be deleted immediately. |
| REQ-SES-06 | Before the first frame is ever sent, the app MUST tell the user that images go to Anthropic's API, and MUST wait for acknowledgment. The acknowledgment MUST be remembered. |
| REQ-SES-07 | A session file from a newer app version MUST NOT be loaded. The user MUST be told why, and offered discard only. |

**Edge cases:**
- **Damaged session file:** it is set aside (not deleted), the user is told, and a fresh session starts.
- **Secure storage unavailable:** the environment variable still works, and Settings explains how to use it.

**Acceptance criteria:**
- `REQ-SES-01`: Given a settings file with one valid setting and one unknown entry, When the app starts, Then the valid setting is applied, everything else uses defaults, and no error occurs.
- `REQ-SES-02`: Given both the environment variable and a stored key, Then the environment variable is used.
- `REQ-SES-03`: Given 3 captured frames, When the process is killed and restarted, Then the resumed session has all 3 frames and the same text.
- `REQ-SES-04`: Given a frame still waiting at the time of a crash, When the user resumes, Then that frame is transcribed.
- `REQ-SES-05`: Given saved sessions 8 days old and 6 days old, When the app starts, Then the 8-day-old one is deleted and the 6-day-old one is kept.
- `REQ-SES-06`: Given a first launch, When the first frame is captured, Then nothing is sent until the notice is acknowledged.
- `REQ-SES-07`: Given a session from a newer version, Then the only option is discard.

### 5.7 App Shell & Launch (APP)
**User intent:** As the user, I want to launch the app with one click or one command and use a simple single window.

| ID | Requirement |
| :--- | :--- |
| REQ-APP-01 | The `screen-copier` command MUST start the app without opening a console window. |
| REQ-APP-02 | `screen-copier --create-shortcut` MUST create a "Screen Copier" desktop shortcut with the app icon, report where it put it, and exit with a non-zero code on failure. |
| REQ-APP-03 | The app MUST use one main window with two modes, **Capture** and **Review**, switchable at any time. Capture mode MUST show live counts of frames, lines, gaps, issues and tokens. |
| REQ-APP-04 | Every frequent action MUST have a keyboard shortcut: capture, toggle Auto, switch mode, next/previous issue, save, discard frame, new session. Capture shortcuts MUST NOT fire while the user is typing in the editor. |
| REQ-APP-05 | Live warnings (gap, ambiguous overlap, cut-off lines, word wrap, offline, blurry) MUST stay visible until dismissed or resolved. A gap warning MUST also play a distinct sound when sound is on. |
| REQ-APP-06 | The repository MUST include a README with install steps, API-key setup, and capture tips (line numbers on, word wrap off, font ≥14 px, camera 40–60 cm away, 3+ lines of overlap when scrolling). |
| REQ-APP-07 | Unexpected errors MUST be logged and shown to the user without losing the session. Logs MUST NOT contain the API key or images. |

**Acceptance criteria:**
- `REQ-APP-01`: Given an installed app, When `screen-copier` runs, Then no console window appears.
- `REQ-APP-02`: Given the shortcut command succeeds, Then it prints the shortcut path and exits 0. Given it fails, Then it exits 1 with an error message.
- `REQ-APP-03`/`-04`: Given Capture mode, When the switch-mode key is pressed, Then Review is shown. When the capture key is pressed while typing in the editor, Then nothing is captured.
- `REQ-APP-05`: Given a gap warning, When a later frame fills the gap, Then the warning disappears.
- `REQ-APP-07`: Given an internal error, Then an error dialog appears, the log records it, and the session can still be resumed.

## 6. Non-Functional Requirements
| ID | Category | Requirement | How Verified |
| :--- | :--- | :--- | :--- |
| REQ-NFR-01 | Responsiveness | The UI MUST never freeze for more than 100 ms during capture or transcription. | Automated UI latency test with simulated camera and recognizer |
| REQ-NFR-02 | Performance | Preview ≥15 fps at 1080p. Preparing a 4K frame ≤300 ms. Adding a frame to the document ≤200 ms. | Timing tests, plus a manual check on the target laptop |
| REQ-NFR-03 | Privacy | The API key MUST never appear in files the app writes, or in full in the UI. | Automated scan of the data folder for a test key |
| REQ-NFR-04 | Privacy | Images MUST be sent only to the Anthropic API. | Code review, plus a test |
| REQ-NFR-05 | Compatibility | MUST run on Windows 11 x64 with Python 3.12 and 3.13. | CI on Windows |
| REQ-NFR-06 | Accuracy | Benchmark: a 300-line Python file, 14 px font, 1080p webcam at 40–60 cm, line numbers on. The output MUST have 0 missing and 0 duplicated lines, and SHOULD have ≥99% of lines exactly right before review. | Benchmark script on real hardware (TASK-21) |
| REQ-NFR-07 | Reliability | Every captured frame MUST be saved to disk before it is sent for transcription. | Crash-and-resume test |
| REQ-NFR-08 | Accessibility | Everything MUST be usable from the keyboard. Issue highlights MUST NOT rely on color alone. | Keyboard UI tests, plus a manual check |

## 7. Assumptions & Open Questions
| ID | Type | Statement | Impact if Wrong |
| :--- | :--- | :--- | :--- |
| A-05 | Assumption | LF is the right default line ending. | The user changes a setting. |
| A-06 | Assumption | 7-day retention for saved sessions is enough. | A lost session. The period is configurable. |
| A-07 | Assumption | The accuracy target in REQ-NFR-06 is realistic. | The target is reset after the TASK-21 benchmark. |

Design-level assumptions (A-01 to A-04) are tracked in [architecture.md §18](architecture.md#18-design-assumptions). There are no blocking open questions.

## 8. Glossary
| Term | Meaning |
| :--- | :--- |
| Source | The computer whose screen is being copied. |
| Frame | One captured image, plus its transcription. |
| Partial line | A line partly hidden at the top or bottom edge of the screen area. |
| Cut-off line | A line that runs past the right edge of the screen area. |
| Gap | A part of the file known or suspected to be missing from the document. It is shown in the text as a gap marker. |
| Conflict | A line that frames read differently, where the app couldn't decide which reading is right. |
| Issue | Anything highlighted for review: an uncertain line, conflict, cut-off line, word-wrap line, or gap. |
