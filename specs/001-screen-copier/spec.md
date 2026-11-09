# Screen Copier

## Goal
Screen Copier is a Windows 11 desktop app (Python + PySide6). A laptop webcam is pointed at another computer's monitor; the app captures frames while the user scrolls through code, transcribes each frame with the Claude vision API, stitches the overlapping frames into one document, and saves it to a file after the user reviews it. It warns the user right away if they skipped part of the file and highlights anything it is unsure of, so a 300-line file is copied with no missing or duplicated lines and only a few highlighted character errors.

## Covers
- REQ-CAM-01: camera choice, remembered across launches
- REQ-CAM-02: highest resolution up to 4K, shown in the UI
- REQ-CAM-03: smooth live preview (≥15 fps), UI stays responsive
- REQ-CAM-04: manual exposure and access to driver settings
- REQ-CAM-05: Auto and Manual capture modes, manual capture in either
- REQ-CAM-06: Auto captures exactly one frame per settle, never repeatedly
- REQ-CAM-07: Auto skips a frame identical to the previous capture
- REQ-CAM-08: a blurry manual capture is kept and marked blurry
- REQ-CAM-09: visual and audible capture feedback
- REQ-CAM-10: simulated camera from a folder or video
- REQ-PRE-01: detect the screen and correct perspective
- REQ-PRE-02: the preview shows the detected outline
- REQ-PRE-03: manual outline by dragging corners, lockable
- REQ-PRE-04: no screen and no lock → whole image is used
- REQ-PRE-05: images capped to a configurable size, never upscaled
- REQ-PRE-06: preparing a frame never delays the preview or UI
- REQ-OCR-01: each frame transcribed by the Claude vision API with the configured model
- REQ-OCR-02: exact transcription with indentation, line numbers, edge, cut-off, uncertainty and word-wrap flags
- REQ-OCR-03: several frames transcribed concurrently, configurable limit
- REQ-OCR-04: results added in capture order
- REQ-OCR-05: temporary failures retried with backoff, then failed with manual retry
- REQ-OCR-06: cut-off lines warn live and are highlighted in review
- REQ-OCR-07: missing or invalid key pauses transcription and opens Settings
- REQ-OCR-08: unreadable model response retried once, then failed with an explanation
- REQ-OCR-09: offline keeps capturing and resumes on reconnect
- REQ-OCR-10: simulated recognizer from prepared files
- REQ-OCR-11: tokens used in the session are shown
- REQ-STI-01: the document depends only on frames and capture order; incremental equals batch
- REQ-STI-02: partial edge lines never appear unless a full copy exists
- REQ-STI-03: editor line numbers place lines when visible
- REQ-STI-04: missing line numbers reported as gaps, including at the start
- REQ-STI-05: overlap placement works scrolling down, up, or re-capturing
- REQ-STI-06: unplaceable or ambiguous frames mark a gap and warn, never guess
- REQ-STI-07: a frame that fills a gap removes it and lowers the gap count
- REQ-STI-08: confident over uncertain, agreement over single reading, else a conflict with alternatives
- REQ-STI-09: word wrap warns and flags the affected lines
- REQ-STI-10: discarding a frame rebuilds the document without it
- REQ-STI-11: adding a frame takes ≤200 ms on a 2,000-line document
- REQ-REV-01: thumbnails with status; enlarge, discard, retry, re-transcribe
- REQ-REV-02: Review shows source images next to editable numbered text
- REQ-REV-03: every issue highlighted with a reason on hover, not by color alone
- REQ-REV-04: cursor on a line shows the frames it came from
- REQ-REV-05: next/previous issue by keyboard, issue count, editing clears a highlight
- REQ-REV-06: pick a conflict alternative with one click
- REQ-REV-07: language guessed from frames, user can override
- REQ-REV-08: syntax check for Python, JSON, YAML, TOML, XML; errors never block saving
- REQ-REV-09: user chooses location and name; timestamped default with matching extension
- REQ-REV-10: UTF-8, chosen line ending, no trailing whitespace, one final newline, gap markers kept
- REQ-REV-11: saving with gaps asks for confirmation
- REQ-REV-12: leaving Review after edits warns and allows cancel
- REQ-SES-01: settings persist; a damaged file never blocks startup
- REQ-SES-02: API key in Credential Manager or `ANTHROPIC_API_KEY` (env wins); enter, test, remove in Settings
- REQ-SES-03: the session is saved continuously
- REQ-SES-04: offer to resume or discard an unfinished session; resume restores everything and continues transcribing
- REQ-SES-05: saved sessions cleaned up after a configurable period; discarded ones deleted at once
- REQ-SES-06: one-time consent notice before the first frame is sent
- REQ-SES-07: a session from a newer version is not loaded; discard only
- REQ-APP-01: `screen-copier` starts without a console window
- REQ-APP-02: `--create-shortcut` creates the desktop shortcut and reports the path
- REQ-APP-03: one window, Capture and Review modes, live counts
- REQ-APP-04: keyboard shortcuts for every frequent action; capture keys inert while typing
- REQ-APP-05: live warnings persist until dismissed or resolved; distinct gap sound
- REQ-APP-06: README with install, API-key setup and capture tips
- REQ-APP-07: unexpected errors logged and shown without losing the session; no key or images in logs
- REQ-NFR-01: the UI never freezes for more than 100 ms
- REQ-NFR-02: preview ≥15 fps at 1080p; 4K frame prepared in ≤300 ms; frame added in ≤200 ms
- REQ-NFR-03: the API key never appears in files or in full in the UI
- REQ-NFR-04: images are sent only to the Anthropic API
- REQ-NFR-05: runs on Windows 11 x64 with Python 3.12 and 3.13
- REQ-NFR-06: benchmark: 0 missing and 0 duplicated lines on a 300-line file, ≥99% lines exact
- REQ-NFR-07: every frame saved to disk before it is sent
- REQ-NFR-08: everything usable from the keyboard; highlights not by color alone

## Out of scope
- Horizontal scrolling. Lines wider than the screen are detected and warned about, not stitched.
- Offline or local OCR (decision 0002).
- macOS and Linux (decision 0005).
- Copy to clipboard.
- Several output files per session. Start a new session for the next file.
- Raw frames as an output. Frames are kept only for autosave and resume.
- A standalone `.exe` or installer (decision 0005).
- Tuning for printed paper or phone screens.
- Software on the source computer.
- Reconstructing tab characters. Indentation is output as spaces.
- Correcting or completing code. The output reproduces the screen, typos included.

## Contracts

### Models
Implemented by T002 in `src/screen_copier/models.py` (pydantic v2); every other task consumes it and must not add fields. The code is the source of truth; this table mirrors it. Enums (`StrEnum`): `SessionState`, `CaptureMode`, `FrameStatus`, `DocLineFlag`, `GapKind`, `StitchEventKind`. Constant `SCHEMA_VERSION = 1`; exception `UnsupportedVersion(found, supported)`. `Frame.image_file_for(seq)` returns `frames/<seq>.jpg`.

| Entity | Field | Type | Constraints / Notes |
| :--- | :--- | :--- | :--- |
| Session | schema_version | int | = `SCHEMA_VERSION` (1). Greater → `UnsupportedVersion` is raised (not a `ValidationError`). Lower → must be migrated first (SessionStore contract). |
| Session | id | str (uuid4) | Folder name. Canonical lowercase form. |
| Session | created_at, updated_at | datetime (UTC) | All datetimes must be timezone-aware; they are normalized to UTC and serialized with `Z`. |
| Session | state | `capturing` \| `reviewing` \| `saved` | |
| Session | saved_at | datetime \| null | Set when saved |
| Session | output_path | str \| null | |
| Session | language | str \| null | User override. Null means use the vote. |
| Session | frames | list[Frame] | Ordered by seq (validated strictly increasing) |
| Session | edited_text | str \| null | Non-null once the user edits. Then it is the truth for the text. |
| Session | usage | Usage | `input_tokens`, `output_tokens` ≥ 0 |
| Frame | seq | int ≥ 1 | Unique, increasing |
| Frame | captured_at | datetime | |
| Frame | mode | `auto` \| `manual` | |
| Frame | image_file | str | `frames/<seq>.jpg` |
| Frame | quality | FrameQuality `{sharpness: float ≥ 0, blurry: bool}` | |
| Frame | status | `queued` \| `recognizing` \| `recognized` \| `failed` \| `discarded` | Default `queued` |
| Frame | attempts | int ≥ 0 | |
| Frame | error | str \| null | |
| Frame | result | FrameResult \| null | Non-null iff `recognized` |
| FrameResult | has_line_numbers, word_wrap_detected | bool | Mirrors the ReportTool schema. FrameResult and RecognizedLine use strict types and ignore unknown keys, so they parse `tool_use.input` directly. |
| FrameResult | language_guess | str \| null | |
| FrameResult | lines | list[RecognizedLine] | Top to bottom, may be empty |
| RecognizedLine | text | str | No line number, no newline (CR and LF are rejected) |
| RecognizedLine | line_number | int ≥ 1 \| null | |
| RecognizedLine | partial, cut_off_right, uncertain | bool | |
| RecognizedLine | uncertain_note | str \| null | |
| Document *(derived)* | segments | list[Segment] | Never persisted |
| Document | gaps | list[Gap] | In render order |
| Segment | lines | list[DocLine] | Non-empty |
| Segment | first_line_number | int ≥ 1 \| null | |
| DocLine | text | str | Resolved |
| DocLine | observations | list[Observation] | ≥1 |
| DocLine | flags | set of `uncertain` \| `conflict` \| `cut_off` \| `soft_wrap` | |
| DocLine | alternatives | list[str] | |
| Observation | frame_seq, text, uncertain, cut_off_right | int ≥ 1, str, bool, bool | `cut_off_right` feeds the `cut_off` flag |
| Gap *(derived)* | kind | `overlap` \| `line_numbers` | Overlap gaps drive the `gap` warning (CaptureController contract) |
| Gap | description | str | See the Stitcher contract |
| Gap | before_segment | int ≥ 0 | Index of the segment the marker renders immediately before |
| StitchEvent *(derived)* | kind, frame_seq | `gap_detected` \| `gap_closed` \| `ambiguous_overlap` \| `cut_off` \| `word_wrap`, int ≥ 1 | |

JSON round trip: `Session.model_dump_json()` / `Session.model_validate_json()` with UTC datetimes.

### Recognizer
Implemented by T007 in `src/screen_copier/recognition/base.py` (`ClaudeRecognizer` in `claude.py`, `FakeRecognizer` in `fake.py`); consumed by T013 (queue) and T014 (controller). Consumers must not add outcome types.

```python
class Recognizer(Protocol):
    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome: ...

RecognitionOutcome = Ok | Retryable | AuthError | BadRequest | Malformed
# Ok(result: FrameResult, usage: Usage)
# Retryable(reason: str, connection_error: bool = False)   # True: no HTTP response (offline detection)
# AuthError(message: str)   BadRequest(message: str)   Malformed(raw: str)
```

Outcomes are frozen dataclasses. A recognizer never retries and never raises for API or response problems; the queue decides what to do with each outcome. `FakeRecognizer(dir)` returns `Ok(FrameResult from <seq>.json)` for `frame_seq`, or `Malformed` when the file is missing, and takes an optional per-call delay. The fake is selected when `SCREEN_COPIER_FAKE_RECOGNIZER` names a folder (Secrets contract). `ClaudeRecognizer(client_factory, settings)` calls the factory on every request and reads `settings.model` on every request; `client_factory_for(key_provider)` reuses one client until the key changes and raises `MissingApiKeyError` when no key is set, which maps to `AuthError`. The Anthropic client is built with `max_retries=0`, `timeout=60.0` and a pinned `base_url="https://api.anthropic.com"`.

| SDK outcome | Mapped to |
| :--- | :--- |
| `APIConnectionError`, `APITimeoutError` | `Retryable(connection_error=True)` |
| HTTP 429, 500, 502, 503, 529 (also 408, 409 and any other 5xx) | `Retryable` |
| HTTP 401, 403, or no API key | `AuthError` (the API's message; never the key) |
| HTTP 400, 404, 413 (also any other 4xx) | `BadRequest` (no retry; the frame fails with the API message) |
| No `report_transcription` `tool_use` block, `stop_reason` `max_tokens` or `refusal`, or pydantic validation of `input` fails | `Malformed` (raw truncated to 2,000 chars) |
| Valid `tool_use` | `Ok(FrameResult, Usage(input_tokens, output_tokens))` |

### ReportTool
The tool schema Claude fills. T007 ships it as `REPORT_TOOL` in `recognition/prompt.py`; T002's `FrameResult` validates exactly this shape; T003's fixture frames are JSON of this shape. `strict` is true (decision 0006), so every object has `additionalProperties: false` and every property is required; nullable properties may be `null`.

```json
{
  "name": "report_transcription",
  "description": "Report the exact transcription of the code visible on screen.",
  "strict": true,
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["has_line_numbers", "language_guess", "word_wrap_detected", "lines"],
    "properties": {
      "has_line_numbers": {"type": "boolean"},
      "language_guess": {"type": ["string", "null"]},
      "word_wrap_detected": {"type": "boolean"},
      "lines": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["text", "line_number", "partial", "cut_off_right", "uncertain", "uncertain_note"],
        "properties": {
          "text": {"type": "string"},
          "line_number": {"type": ["integer", "null"]},
          "partial": {"type": "boolean"},
          "cut_off_right": {"type": "boolean"},
          "uncertain": {"type": "boolean"},
          "uncertain_note": {"type": ["string", "null"]}
        }}}
    }
  }
}
```

The request: `messages.create(model=settings.model, max_tokens=8192, system=SYSTEM_PROMPT, tools=[REPORT_TOOL], tool_choice={"type": "auto"}, messages=[one user message with a base64 image/jpeg block and the text "Transcribe this screen."])`. No forced `tool_choice` and no `temperature` (decision 0006). The system prompt has one instruction per REQ-OCR-02 item: exact transcription, indentation as spaces, gutter numbers in `line_number` never in `text`, ignore editor and OS UI, `partial` for lines clipped top or bottom, `cut_off_right` for lines past the right edge, `uncertain` plus `uncertain_note` for ambiguous characters, `word_wrap_detected` for soft-wrapped continuation lines, and respond only through the tool.

### CaptureTrigger
Implemented by T005 in `src/screen_copier/camera/trigger.py` and `camera/quality.py`; consumed by T012 (`CameraWorker` runs it per preview frame). Pure numpy, no Qt.

```python
# camera/quality.py
def prep(frame, width=320) -> gray uint8 array    # grayscale, resized to width 320 (INTER_AREA)
def diff(a, b) -> float                            # mean(abs(a - b)) on prepped frames, 0–255
def sharpness(a) -> float                          # cv2.Laplacian(a, CV_64F).var() on a prepped frame
def is_blurry(sharpness_value, sharpness_threshold) -> bool   # sharpness < threshold

# camera/trigger.py
class TriggerState(StrEnum): MOVING, SETTLING, WAITING_FOCUS, CAPTURED
@dataclass TriggerConfig(stable_duration_ms=600, stability_threshold=2.0, motion_threshold=6.0,
                         sharpness_threshold=100.0, duplicate_threshold=1.0, never_settles_ms=5000)
TriggerConfig.from_settings(settings)              # reads the Settings keys by name
@dataclass TriggerDecision(state: TriggerState, capture: bool = False, hints: frozenset[str])
class CaptureTrigger:
    def __init__(self, config: TriggerConfig, clock: Callable[[], float]) -> None
    def update(self, diff_prev: float, sharpness: float, diff_last_capture: float) -> TriggerDecision
    def reset(self) -> None                        # back to MOVING; clears hints
    def notify_manual_capture(self) -> None        # clears the never_settles hint
NEVER_SETTLES = "never_settles"                    # the only hint
```

| State | Condition | Next | Action |
| :--- | :--- | :--- | :--- |
| MOVING | `diff_prev < stability_threshold` | SETTLING | `stable_since = now` |
| SETTLING | `diff_prev ≥ stability_threshold` | MOVING | |
| SETTLING | stable ≥ `stable_duration_ms` and `sharpness < sharpness_threshold` | WAITING_FOCUS | |
| SETTLING / WAITING_FOCUS | stable ≥ `stable_duration_ms`, sharp, and `diff_last_capture < duplicate_threshold` | CAPTURED | no capture (duplicate) |
| SETTLING / WAITING_FOCUS | stable ≥ `stable_duration_ms`, sharp, not a duplicate | CAPTURED | **emit capture** |
| WAITING_FOCUS | `diff_prev ≥ stability_threshold` | MOVING | |
| CAPTURED | `diff_prev ≥ motion_threshold` | MOVING | |

"Still" is `diff_prev < stability_threshold`, "sharp" is `sharpness ≥ sharpness_threshold`, "duplicate" is `diff_last_capture < duplicate_threshold`. In CAPTURED, diffs between `stability_threshold` and `motion_threshold` keep the state. At most one transition per frame. The `never_settles` hint is raised after `never_settles_ms` in MOVING/SETTLING without reaching CAPTURED while sharp; the timer restarts on a blurry frame or on entering WAITING_FOCUS; the hint stays until the next capture (CAPTURED, including a duplicate), `notify_manual_capture()` or `reset()`. Manual capture bypasses the state machine: the caller (`CameraWorker`) captures the current full-resolution frame with `blurry` from the metrics, owns the last-capture reference frame used for `diff_last_capture`, and then calls `notify_manual_capture()`.

### Preprocess
Implemented by T006 in `src/screen_copier/preprocess/`; consumed by T014 (runs `process` in a worker) and T015 (draws the quad and `QuadSmoother` output).

```python
Quad = np.ndarray  # shape (4, 2), float32, frame pixel coordinates, ordered TL, TR, BR, BL

# preprocess/detect.py
def detect_screen(frame) -> Quad | None          # None when no convex 4-point polygon ≥ 20% of the frame
def order_corners(points) -> Quad
class QuadSmoother:                               # display only
    def __init__(self, window: int = 5) -> None
    def update(self, quad: Quad | None) -> Quad | None   # average of the last 5; None clears the history
    def current(self) -> Quad | None
    def reset(self) -> None

# preprocess/warp.py
@dataclass ProcessResult(jpeg: bytes, quad_used: Quad | None, width: int, height: int)
def warp_to_rect(frame, quad) -> image           # degenerate quad → the full frame
def resize_cap(img, max_edge: int) -> image      # long edge ≤ max_edge, INTER_AREA, never upscales
def encode_jpeg(img, quality: int = 90) -> bytes # color JPEG; these exact bytes are stored and sent
def process(frame, locked_quad: Quad | None, max_edge: int) -> ProcessResult
```

`process` uses `locked_quad` when given, else `detect_screen(frame)`, else the full frame (`quad_used = None`). A degenerate result (non-convex quad, or quad or output area < 1% of the frame) also falls back to the full frame with `quad_used = None`. `max_edge` comes from the `max_image_edge_px` setting.

### Settings
Implemented by T004 in `src/screen_copier/config/settings.py` as the pydantic model `Settings`; read by T005 (`TriggerConfig.from_settings`), T008 (`min_overlap_lines`, `line_similarity`), T012, T013, T014, T017, T018 (edits every key) and T021 (tunes defaults). Only T004, T018 and T021 may change this file; others read keys by name.

| Key | Type | Default | Used by |
| :--- | :--- | :--- | :--- |
| `schema_version` | int | 1 | migrations |
| `camera_id` | int ≥ 0 | 0 | CameraWorker |
| `auto_exposure` | bool | true | CameraWorker |
| `exposure` | float \| null | null (driver default) | CameraWorker |
| `capture_mode` | `auto` \| `manual` | `auto` | CaptureTrigger |
| `stable_duration_ms` | int | 600 | CaptureTrigger |
| `stability_threshold` | float | 2.0 | CaptureTrigger |
| `motion_threshold` | float | 6.0 | CaptureTrigger |
| `sharpness_threshold` | float | 100.0 | CaptureTrigger |
| `duplicate_threshold` | float | 1.0 | CaptureTrigger |
| `max_image_edge_px` | int | 1568 | Preprocess |
| `model` | str | `claude-sonnet-5-5` | Recognizer |
| `max_concurrent_requests` | int ≥ 1 | 3 | RecognitionQueue |
| `min_overlap_lines` | int ≥ 1 | 2 | Stitcher |
| `line_similarity` | float in [0, 1] | 0.90 | Stitcher |
| `line_ending` | `lf` \| `crlf` (enum `LineEnding`) | `lf` | Output |
| `sound_enabled` | bool | true | UI |
| `session_retention_days` | int ≥ 0 | 7 | SessionStore |
| `consent_acknowledged` | bool | false | REQ-SES-06 |

```python
def load_settings(directory: Path | None = None) -> Settings   # tolerant, see below
def save_settings(settings: Settings, directory: Path | None = None) -> None   # atomic
def settings_path(directory: Path | None = None) -> Path       # <data_dir>/settings.json
```

Load: unknown keys are ignored; a missing or invalid key (wrong type or out of range) uses its default; a file that is not valid JSON is renamed to `settings.bak.json` and all defaults are used. Save is atomic (temp file, then replace). Tunable numbers always come from these keys, never from constants in other modules.

### Secrets
Implemented by T004 in `src/screen_copier/config/secrets.py`, `config/paths.py` and `config/logging.py`; consumed by T007 (key provider), T012 and T014 (fake camera and recognizer env vars), T018 (key UI) and T019 (startup).

```python
# config/secrets.py
def get_api_key(backend=None, env=None) -> str | None   # env ANTHROPIC_API_KEY first (non-empty), else keyring
def api_key_from_env(env=None) -> bool                  # True when the key comes from the environment
def set_api_key(key: str, backend=None) -> None         # stores the stripped key; raises on keyring errors
def clear_api_key(backend=None) -> None                 # no-op when nothing is stored
def keyring_available(backend=None) -> bool             # False on KeyringError or no backend
def mask_key(key: str | None) -> str                    # "••••" + last 4 chars; just "••••" for ≤ 4 chars or None
KEYRING_SERVICE = "screen-copier"; KEYRING_USERNAME = "anthropic_api_key"
# config/paths.py
def data_dir(env=None) -> Path        # SCREEN_COPIER_DATA_DIR, else platformdirs.user_data_dir("ScreenCopier", appauthor=False)
def sessions_dir(env=None) -> Path    # <data_dir>/sessions
def logs_dir(env=None) -> Path        # <data_dir>/logs
def atomic_write_text(path: Path, text: str, encoding="utf-8") -> None
# config/logging.py
def setup_logging(directory: Path | None = None, level=logging.INFO) -> logging.Handler
def redact(text: str) -> str          # replaces sk-ant-[A-Za-z0-9_-]+ with sk-ant-***
```

Logging: `RotatingFileHandler(<data_dir>/logs/screen-copier.log, maxBytes=1 MB, backupCount=5)` with a redaction filter. Image bytes are never logged. The key is never written to settings, session files or logs, and the UI never shows more than its last 4 characters.

| Environment variable | Purpose | Test value |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` | API key (overrides keyring) | `sk-ant-test-0000` |
| `SCREEN_COPIER_DATA_DIR` | Override the data dir | pytest `tmp_path` |
| `SCREEN_COPIER_FAKE_CAMERA` | Image folder or video file used as the camera | `tests/fixtures/camera/basic/` |
| `SCREEN_COPIER_FAKE_RECOGNIZER` | Folder of `<seq>.json` FrameResults | `tests/fixtures/recognizer/seq_basic/` |

### Stitcher
Implemented by T008 in `src/screen_copier/stitching/` (overlap mode) and extended by T009 (line-number mode through the `uses_line_numbers` hook); consumed by T014 (one `Stitcher` per session) and T017 (`render_items` maps editor lines to DocLines). Pinned by the fixtures of T003. Pure: no Qt, no clock.

```python
class Stitcher:
    def __init__(self, settings) -> None          # reads min_overlap_lines, line_similarity
    def add(self, seq: int, result: FrameResult) -> list[StitchEvent]   # seq strictly increasing, else ValueError
    def document(self) -> Document                # a fresh copy on every call
    def render(self) -> str
    def issue_count(self) -> int
    def uses_line_numbers(self, result: FrameResult, lines: Sequence[RecognizedLine]) -> bool  # T009 hook; False until T009

def stitch(results: Iterable[tuple[int, FrameResult]], settings) -> StitchOutput   # StitchOutput(document, events)
def render(document: Document) -> str
def render_items(document: Document) -> list[DocLine | Gap]   # render order; line i of render() is item i
def issue_count(document: Document) -> int
GAP_MARKER = "<<<SCREEN-COPIER GAP: {description}>>>"
```

`add` is the primitive and `stitch` is a fold over it, so incremental and batch give the same state (REQ-STI-01). A frame with no lines left after dropping `partial` lines changes nothing.

**Gap marker line:** `<<<SCREEN-COPIER GAP: {description}>>>`, exactly. It is greppable and intentionally breaks compilation. Overlap gaps: `unknown lines between capture {a} and {b}`, where `a` is the highest `frame_seq` observing the last line of the segment above and `b` the lowest `frame_seq` observing the first line of the segment below. Line-number gaps: `lines X–Y missing` (en dash). Indentation is always spaces; tabs are never produced.

**Rendering:** segments are joined in order with one gap marker line between consecutive segments, plus any line-number gaps. `render()` output is lines joined with `\n` plus a trailing `\n`.

**Events** (consumed by the controller): `gap_detected`, `gap_closed`, `ambiguous_overlap`, `cut_off`, `word_wrap`, each carrying the frame seq. A frame emits at most one event per kind. `cut_off` is emitted when any non-partial line of the frame has `cut_off_right`; `word_wrap` when the frame has `word_wrap_detected` (in any mode), and by line-number mode for continuation lines.

**Issues** (`issue_count`): every DocLine with at least one flag counts once, plus every gap.

**Discard:** re-run the batch stitch over the remaining frames (REQ-STI-10). **Performance:** ≤200 ms per `add` on a 2,000-line document (REQ-STI-11).

**Fixtures** (`tests/fixtures/stitching/<name>/`, described in `tests/fixtures/stitching/README.md`): `source.txt`, `frames/<seq>.json` (ReportTool JSON), `expected.txt` (exact `render()` output), `meta.json` with `description`, `uses_line_numbers`, `expected_events` (every event, in order), `expected_conflicts` (`line` 1-based in `expected.txt`, `alternatives` by frequency then recency), `expected_flags`, and for `seq_discard` also `discard`, `expected_after_discard` and `expected_events_after_discard`. Fixtures are generated by `make_frames.py`; never edit a fixture to make code pass, raise it in the PR instead.

### RecognitionQueue
Implemented by T013 in `src/screen_copier/recognition/queue.py` (the one recognition module that may use Qt signals); consumed by T014. Consumers must not add signals.

```python
class RecognitionQueue(QObject):
    def __init__(self, recognizer: Recognizer, *, max_concurrent: int, clock, sleep, rng) -> None
    # methods
    def submit(self, seq: int, jpeg: bytes) -> None
    def discard(self, seq: int) -> None
    def retry(self, seq: int) -> None
    def pause(self) -> None
    def resume(self) -> None
    def shutdown(self) -> None
    # signals
    frame_status = Signal(int, str, object)      # seq, FrameStatus value, error: str | None
    result_ready = Signal(int, object)           # seq, FrameResult; strictly in seq order
    auth_required = Signal()
    offline_changed = Signal(bool)
    usage = Signal(int, int)                     # input_tokens, output_tokens (totals)
```

- **Concurrency:** at most `max_concurrent` (`max_concurrent_requests` setting) calls in flight.
- **Retry schedule for `Retryable`:** delays of 1, 2, 4, 8, 16 s × (1 + U[0, 0.2]) jitter. After 5 failed attempts → `failed`.
- **`Malformed`:** 1 retry, then `failed("Unreadable model response")`.
- **`AuthError`:** pause the whole queue. Put the frame back to `queued` (attempts not incremented) and emit `auth_required` once per pause. `resume()` is called after the key changes.
- **`BadRequest`:** the frame fails at once with the API message.
- **Offline:** when 3 consecutive outcomes are connection errors, set offline and emit `offline_changed(True)`. Keep retrying the oldest frame every 15 s and hold the others. The first success emits `offline_changed(False)`.
- **In-order release:** results are buffered and `result_ready` fires only when every lower `seq` is recognized, failed or discarded.
- **Discard:** cancel the future if it has not started, otherwise ignore its result.
- **Usage:** emit token totals after each `Ok`; the controller adds them to `Session.usage`.
- Clock, sleep and RNG are injectable; no real time in tests.

### SessionStore
Implemented by T011 in `src/screen_copier/session/store.py` and `session/migrations.py`; consumed by T014 (saves) and T019 (startup discovery, cleanup, resume).

```python
class SessionStore:
    def __init__(self, data_dir: Path, clock: Callable[[], datetime]) -> None
    def create(self) -> Session
    def save_frame_image(self, session: Session, seq: int, jpeg: bytes) -> None   # before any session save
    def save(self, session: Session) -> None            # atomic: session.json.tmp, fsync, os.replace
    def load(self, session_id: str) -> Session          # applies migrations; corruption → CorruptSession
    def find_unfinished(self) -> Session | None         # newest session with state != saved
    def discard(self, session_id: str) -> None          # deletes the folder immediately
    def cleanup_saved(self, retention_days: int) -> None
    def prepare_resume(self, session: Session) -> Session   # queued/recognizing frames → queued

class CorruptSession(Exception): ...
# session/migrations.py
MIGRATIONS: dict[int, Callable[[dict], dict]]          # upgrades v → v+1
def migrate(data: dict) -> dict                         # raises UnsupportedVersion for a newer version
```

- **Layout:** `<data_dir>/sessions/<id>/session.json` and `<data_dir>/sessions/<id>/frames/<seq>.jpg`.
- **Save order:** the frame JPEG first, then `session.json` (REQ-NFR-07).
- **Save triggers** (the controller's job): every frame state change, and editor changes debounced by 2 s.
- **Discovery at startup:** a session with `state != saved` → offer to resume. If several exist, offer the newest and leave the rest for retention.
- **Retention:** at startup, delete `saved` sessions with `saved_at` older than `session_retention_days`. Unsaved sessions are never deleted by retention. Discard deletes the folder immediately.
- **Corrupt file** (JSON or validation error) → rename it to `session.corrupt-<UTC timestamp>.json` and raise `CorruptSession`; the caller tells the user and starts fresh.
- **Migrations:** on load, apply `MIGRATIONS` in sequence and then rewrite the file. A `schema_version` greater than `SCHEMA_VERSION` raises `UnsupportedVersion` (REQ-SES-07).

### CaptureController
Implemented by T014 in `src/screen_copier/controller.py` as `CaptureController(QObject)`; consumed by T015, T016, T017, T018 (through a stub with the same signals in tests) and T019. The UI talks only to the controller and never calls the camera, the API or the disk directly. UI tasks must not add signals or slots.

| Signal | Payload | When |
| :--- | :--- | :--- |
| `preview_frame` | `QImage`, `quad \| None` | Every preview frame |
| `trigger_state` | `str` (MOVING / SETTLING / WAITING_FOCUS / CAPTURED) | On change |
| `frame_added` | `Frame` | After preprocessing and save |
| `frame_updated` | `Frame` | Status change |
| `document_changed` | `Document`, `stats{frames, lines, gaps, issues, input_tokens, output_tokens}` | After each stitch |
| `warning` | `kind`, `message`, `active: bool` | Raise or clear. Kinds: `gap`, `ambiguous_overlap`, `cut_off`, `word_wrap`, `offline`, `blurry`, `never_settles`, `low_resolution` |
| `auth_required` | — | From the queue |
| `camera_error` | `kind` (`not_found` / `unavailable` / `disconnected`), `message` | From the camera worker |
| `consent_required` | — | Before the first API call if not yet acknowledged |
| `resolution` | `int`, `int` | Camera resolution in use |

**Slots:** `capture_now()`, `set_auto(bool)`, `discard_frame(seq)`, `retry_frame(seq)`, `rerecognize_frame(seq)`, `lock_region(quad | None)`, `select_camera(id)`, `acknowledge_consent()`, `new_session()`, `resume_session(id)`, `set_edited_text(str | None)`.

**Warning lifecycle:**
- `gap` stays active while the document has ≥1 gap of kind `overlap`.
- `offline` mirrors the queue's state.
- `cut_off` and `word_wrap` stay active until dismissed or until a later frame no longer shows the condition.
- `blurry` clears after 3 s.
- `never_settles` and `low_resolution` mirror the camera worker's hints.

**Consent gate:** frames are captured and saved, but nothing is submitted to the queue until consent is acknowledged; `consent_required` is emitted once when a frame is held.

**Pipeline per capture:** preprocess in a worker → allocate `seq`, save the frame JPEG, then the session → emit `frame_added` → submit to the queue (or hold for consent). Per result: update the frame → `stitcher.add` → translate events into `warning` raise/clear → emit `document_changed` with stats → save the session. Everything is injectable: camera worker factory, recognizer, store, clock.

### Output
Implemented by T010 in `src/screen_copier/output/`; consumed by T017 (language combo, syntax panel, save flow).

```python
# output/language.py
def normalize_language(guess: str | None) -> str          # alias map; unmapped → "unknown"
def vote_language(guesses_in_capture_order: Sequence[str | None]) -> str   # most common; ties → most recent; [] → "unknown"
def extension_for(language: str) -> str                    # ".py", ".json", … ; unknown → ".txt"
# output/syntax.py
@dataclass SyntaxIssue(line: int, column: int | None, message: str)
def check(text: str, language: str) -> list[SyntaxIssue] | None   # None = no checker for this language
# output/writer.py
class SaveError(Exception): ...                             # carries the OS message
def render_for_save(text: str, line_ending: LineEnding) -> bytes
def default_filename(language: str, now: datetime) -> str   # capture-YYYYMMDD-HHMMSS.<ext>, local time
def write_file(path: Path, data: bytes) -> None             # atomic temp + replace; raises SaveError
```

Alias map (examples): `py`→`python`, `js`→`javascript`, `ts`→`typescript`, `c#`/`cs`→`csharp`, `c++`→`cpp`, `yml`→`yaml`, `bash`/`sh`→`shell`, `ps1`→`powershell`, `plaintext`→`text`.

| Language | Ext | Checker |
| :--- | :--- | :--- |
| python | .py | `ast.parse` |
| json | .json | `json.loads` |
| yaml | .yaml | `yaml.safe_load` |
| toml | .toml | `tomllib.loads` |
| xml | .xml | `xml.etree.ElementTree.fromstring` |
| html | .html | — |
| javascript / typescript | .js / .ts | — |
| java / csharp / cpp / c / go / rust | .java / .cs / .cpp / .c / .go / .rs | — |
| sql / shell / powershell | .sql / .sh / .ps1 | — |
| markdown / text / unknown | .md / .txt / .txt | — |

`check` replaces gap marker lines with blank lines before parsing, so reported line numbers stay correct. `render_for_save`: split into lines and `rstrip()` each; drop trailing blank lines; join with `\n` or `\r\n` and add exactly one final line ending; encode UTF-8 without a BOM; gap markers are kept verbatim.

### CameraWorker
Implemented by T012 in `src/screen_copier/camera/source.py` and `camera/fake.py`; consumed by T014 (through an injected worker factory) and T018 (`list_cameras` for the picker).

```python
@dataclass CameraInfo(id: int, name: str)
def list_cameras() -> list[CameraInfo]        # QMediaDevices names matched to DirectShow indices 0..9; "Camera N" fallback

class CameraWorker(QThread):
    def __init__(self, camera_id: int, settings, *, trigger: CaptureTrigger, capture_factory=cv2.VideoCapture) -> None
    # signals
    preview_frame = Signal(object)           # QImage
    metrics = Signal(float, float)           # diff_prev, sharpness
    trigger_state = Signal(str)              # TriggerState value
    capture_requested = Signal(object, object)   # full-resolution ndarray (BGR), FrameQuality
    resolution = Signal(int, int)
    camera_error = Signal(str, str)          # kind (not_found / unavailable / disconnected), message
    hint = Signal(str, bool)                 # never_settles / low_resolution, active
    # slots
    def set_auto(self, enabled: bool) -> None
    def capture_now(self) -> None
    def set_exposure(self, auto: bool, value: float | None) -> None
    def open_driver_settings(self) -> None
    def stop(self) -> None

class FakeCameraSource:                       # same read interface as cv2.VideoCapture: isOpened, read, get, set, release
    def __init__(self, path: Path, *, fps: int = 30, hold_ms: int = 1000) -> None
```

- Open with `cv2.VideoCapture(index, cv2.CAP_DSHOW)`. Resolution ladder: 3840×2160, 2560×1440, 1920×1080, 1280×720; accept the first whose frame reads back at that size. Below 1280×720 → `hint("low_resolution", True)`.
- Exposure: `CAP_PROP_AUTO_EXPOSURE` (DSHOW: 0.75 auto, 0.25 manual) and `CAP_PROP_EXPOSURE`; driver settings via `CAP_PROP_SETTINGS = 1`.
- Errors: open fails and no devices → `camera_error("not_found", …)`; open fails but a device exists → `camera_error("unavailable", …)`; 30 consecutive failed reads → `camera_error("disconnected", …)` and the worker stops.
- Fake source: when `SCREEN_COPIER_FAKE_CAMERA` is a directory, cycle its images in sorted order at 30 fps, each held `hold_ms`; a file plays as a looping video. It reports its native size as the resolution.
- Manual `capture_now()` emits exactly one `capture_requested` with `blurry` from the metrics, in either mode.

### UIStructure
Shared by T015, T016, T017, T018 and T019. T019 owns the main window and shortcuts; the view tasks own their widgets and expose them as plain `QWidget` subclasses constructed with the controller.

| Component | Module | Contents | States |
| :--- | :--- | :--- | :--- |
| MainWindow (T019) | `ui/main_window.py` | Toolbar (mode toggle, Auto toggle, Capture, Save, Settings), stacked Capture/Review views, frame strip docked at the bottom | — |
| CaptureView (T015) | `ui/capture_view.py`, `ui/preview_widget.py`, `ui/banner.py` | PreviewWidget (quad overlay, 4 draggable handles, Lock/Clear), stats bar, warning banner | no-camera, camera-error, previewing, waiting-focus, captured-flash |
| FrameStrip (T016) | `ui/frame_strip.py` | Thumbnails + status badge, context menu, Delete key, double-click opens the viewer; signal `frame_selected(seq)` | empty, populated |
| ReviewView (T017) | `ui/review_view.py`, `ui/code_editor.py` | Left: frame viewer (frames for the current line). Right: CodeEditor (QPlainTextEdit with a gutter for line numbers and issue icons). Issue counter, language combo, syntax panel, Save | empty, has-issues, clean |
| SettingsDialog (T018) | `ui/settings_dialog.py` | All Settings keys, API key enter/test/clear (masked to the last 4 characters) | key-ok, key-missing, keyring-unavailable |
| Dialogs (T018) | `ui/dialogs.py` | `ConsentDialog`, `ResumeDialog` (normal / newer-version), gap-save confirm, edit-loss confirm, error | — |

**Shortcuts** (installed by T019 on the main window):

| Key | Action |
| :--- | :--- |
| Space | Capture |
| A | Toggle Auto |
| Tab | Switch mode |
| F8 / Shift+F8 | Next / previous issue (handled inside ReviewView) |
| Ctrl+S | Save |
| Delete | Discard the selected frame |
| Ctrl+N | New session |

Capture-only shortcuts (Space, A, Delete) are disabled while the CodeEditor has focus. **Issue display:** a background color plus a gutter icon with a distinct shape per issue type plus a tooltip (REQ-NFR-08). Trigger-state labels shown in the preview: "Moving…", "Settling…", "Waiting for focus", "Captured". The camera-error state offers "Open camera privacy settings" (`ms-settings:privacy-webcam`).

## Risky logic
The stitching algorithm: overlap placement with ambiguity detection, segment bridging, observation voting with conflicts, line-number placement with numbered gaps and soft wraps, and the invariant that incremental and batch stitching agree. It is pinned by the fixture folders in `tests/fixtures/stitching/` (T003, reviewed by the owner, `merge_policy: manual`), each with frame JSON, `expected.txt` and `meta.json`. T008 implemented overlap mode and made every fixture with `uses_line_numbers: false` pass, leaving the line-number fixtures marked `xfail`; T009 implements line-number mode and removes those `xfail` marks. A fixture is never edited to make code pass.
