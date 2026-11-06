# Screen Copier: Architecture & Design

> **Scope of this document:** *how* the requirements in [requirements.md](requirements.md) are met. It covers components, contracts, algorithms, data formats and tunable defaults. Reasons for the big choices are in [decisions/](decisions/).
>
> **Once code exists, the code is authoritative for contracts.** This applies to `models.py`, `recognition/base.py` and `controller.py`. Sections marked _(→ code)_ should then shrink to a summary plus a link. Any PR that changes a contract or default updates this file.

## 1. Design Principles
1. **Pure core, thin Qt shell.** Metrics, the capture trigger, preprocessing, stitching, output, sessions and config are plain Python and numpy with no Qt imports. Qt is used only for threads, signals and widgets. This keeps the risky logic unit-testable.
2. **The document is derived, never stored.** The stitched document is always `stitch(frames)`. Only frames, their results, and the user's edited text are persisted.
3. **Every external dependency can be replaced by a fake.** The camera, the recognizer, the clock, keyring and subprocess can all be injected. Tests never touch real hardware or the network.
4. **Never guess silently.** When the app is uncertain (alignment, conflicting readings, cut-off lines), it says so as an issue or warning rather than picking an answer quietly.

## 2. Components & Data Flow
```
 ┌──────────── Laptop: single Python process (PySide6) ───────────────────────────┐
 │                                                                                 │
 │  CameraWorker (QThread)  ──frames@≥15fps──►  PreviewWidget (GUI thread)         │
 │        │                                                                        │
 │        ▼ downscaled gray frames                                                 │
 │  quality metrics → CaptureTrigger (auto)  ◄── manual capture (Space)            │
 │        │ capture event (full-res frame)                                         │
 │        ▼                                                                        │
 │  Preprocessor (worker): detect screen quad → perspective warp → resize → JPEG   │
 │        │ Frame(status=queued) ──► SessionStore (atomic autosave)                │
 │        ▼                                                                        │
 │  RecognitionQueue (thread pool, ≤N in flight, retry/backoff, offline hold)      │
 │        │  HTTPS ──► Anthropic Messages API (vision, forced tool call)           │
 │        ▼ FrameResult, released strictly in capture order                        │
 │  Stitcher (pure, deterministic) → Document (segments, gaps, issues) + events    │
 │        │                                   │                                    │
 │        ▼ warnings                          ▼                                    │
 │  CaptureView (banner, stats)        ReviewView (editor) → syntax check          │
 │                                            │                                    │
 │                                            ▼                                    │
 │                                     OutputWriter → user-chosen file             │
 └─────────────────────────────────────────────────────────────────────────────────┘
 Persistent: <data_dir>\settings.json, <data_dir>\sessions\<id>\, <data_dir>\logs\
 Secret:     ANTHROPIC_API_KEY env var, or Windows Credential Manager via keyring
```
`CaptureController` (§12) owns the pipeline. The UI talks only to the controller and never calls the camera, the API or the disk directly.

## 3. Module Layout
```
src/screen_copier/
  app.py               main(): CLI parsing, QApplication, startup flow
  models.py            pydantic models (§10)                         (→ code)
  controller.py        CaptureController (§12)                       (→ code)
  shortcut.py          --create-shortcut (§15)
  camera/
    quality.py         diff + sharpness metrics (§5.2)               pure
    trigger.py         CaptureTrigger state machine (§5.3)           pure
    source.py          CameraWorker QThread, enumeration (§5.1)
    fake.py            FakeCameraSource (folder/video)
  preprocess/
    detect.py          screen quad detection (§6.1)                  pure
    warp.py            perspective warp, resize, encode (§6.2)       pure
  recognition/
    base.py            Recognizer protocol + outcome types (§7.1)    (→ code)
    prompt.py          system prompt + REPORT_TOOL schema (§7.2)
    claude.py          ClaudeRecognizer (§7.3)
    fake.py            FakeRecognizer
    queue.py           RecognitionQueue (§7.4)                       Qt signals allowed
  stitching/
    normalize.py       normalize + line matching (§9.1)              pure
    overlap.py         overlap-mode alignment (§9.2)                 pure
    linenum.py         line-number mode (§9.3)                       pure
    resolve.py         observation voting / conflicts (§9.4)         pure
    stitcher.py        Stitcher: segments, gaps, events, render (§9.5) pure
  output/
    language.py        vote, alias map, extension table (§13.1)      pure
    syntax.py          syntax checkers (§13.2)                       pure
    writer.py          OutputWriter, default filename (§13.3)        pure
  session/
    store.py           SessionStore: atomic save/load, discovery, retention (§10.3)
    migrations.py      schema migration registry (§10.4)
  config/
    paths.py           data_dir resolution
    settings.py        Settings model + tolerant load/save (§8)
    secrets.py         API key resolution (§8.2)
    logging.py         rotating log + redaction (§16)
  ui/
    main_window.py  capture_view.py  preview_widget.py  banner.py
    frame_strip.py  review_view.py  code_editor.py
    settings_dialog.py  dialogs.py
  assets/icon.ico
tests/  (mirrors src)   tests/fixtures/{camera,recognizer,stitching,images}/
scripts/benchmark.py    docs/benchmark.md
```

## 4. Threading Model
| Thread | Owns | Talks to GUI via |
| :--- | :--- | :--- |
| GUI (main) | Widgets, `CaptureController`, `Stitcher` calls (fast, pure) | — |
| `CameraWorker` (QThread) | `cv2.VideoCapture`, metrics, `CaptureTrigger` | `preview_frame` and `capture_requested` signals (queued connection) |
| Preprocess pool (`QThreadPool`, 1 worker) | Quad detection, warp, JPEG encode, frame JPEG write | Result signal |
| Recognition pool (`ThreadPoolExecutor`, `max_concurrent_requests`) | HTTP calls | `RecognitionQueue` signals |
| Session save (`QThreadPool`, 1 worker, serialized) | Atomic JSON writes | Done/error signal |

Rule: nothing on the GUI thread may block for more than 100 ms (REQ-NFR-01). Stitching is run on the GUI thread because it is bounded at 200 ms. If profiling shows otherwise, move it to a worker without changing its interface.

## 5. Capture
### 5.1 Camera source
- Open with `cv2.VideoCapture(index, cv2.CAP_DSHOW)`.
- **Enumeration:** names come from `QtMultimedia.QMediaDevices.videoInputs()`, matched by position to DirectShow indices 0..9. A device that cannot be matched is labeled "Camera N" (assumption A-01).
- **Resolution ladder:** try 3840×2160, then 2560×1440, 1920×1080, 1280×720. Accept the first one where the frame actually read back has that size. Below 1280×720 → raise the `low_resolution` warning.
- **Exposure:** `CAP_PROP_AUTO_EXPOSURE` (DSHOW: 0.75 = auto, 0.25 = manual) and `CAP_PROP_EXPOSURE`. "Camera driver settings…" sets `CAP_PROP_SETTINGS = 1`.
- **Errors:**
  - Open fails and no devices exist → `camera_error("not_found")`.
  - Open fails but a device exists → `camera_error("unavailable")`. The UI deep-links to `ms-settings:privacy-webcam`.
  - 30 consecutive failed reads → `camera_error("disconnected")`, and the worker stops.
- **Fake source:** if `SCREEN_COPIER_FAKE_CAMERA` is a directory, cycle through its images in sorted order (default 30 fps, each image held for `hold_ms`, default 1000). If it is a file, play it as a video, looping. The fake source reports its native size as the resolution.

### 5.2 Quality metrics (`camera/quality.py`)
- `prep(frame)`: grayscale, resize to width 320 (keeping the aspect ratio, `INTER_AREA`).
- `diff(a, b)`: `mean(abs(a - b))` on prepped frames, 0–255 scale.
- `sharpness(a)`: `cv2.Laplacian(a, CV_64F).var()` on the prepped frame.
- `blurry = sharpness < sharpness_threshold`.

### 5.3 Auto-capture state machine (`camera/trigger.py`)
Inputs per preview frame: `diff_prev` (against the previous preview frame), `sharpness`, `diff_last_capture` (against the last captured frame, or ∞), and `now` (an injected clock).

| State | Condition | Next | Action |
| :--- | :--- | :--- | :--- |
| MOVING | `diff_prev < stability_threshold` | SETTLING | `stable_since = now` |
| SETTLING | `diff_prev ≥ stability_threshold` | MOVING | |
| SETTLING | stable ≥ `stable_duration_ms` and `sharpness < sharpness_threshold` | WAITING_FOCUS | |
| SETTLING / WAITING_FOCUS | stable ≥ `stable_duration_ms`, sharp, and `diff_last_capture < duplicate_threshold` | CAPTURED | no capture (duplicate) |
| SETTLING / WAITING_FOCUS | stable ≥ `stable_duration_ms`, sharp, not a duplicate | CAPTURED | **emit capture** |
| WAITING_FOCUS | `diff_prev ≥ stability_threshold` | MOVING | |
| CAPTURED | `diff_prev ≥ motion_threshold` | MOVING | |

- Entering Auto mode resets the state to MOVING.
- **Never-settles hint:** if 5 s pass in MOVING/SETTLING without reaching CAPTURED while `sharpness ≥ threshold`, raise `never_settles`. Clear it on the next capture.
- **Manual capture** bypasses the state machine. It captures the current full-resolution frame, with `blurry` set from the metrics. It does not change the trigger state, except that it updates the last-capture reference.

## 6. Preprocessing
### 6.1 Screen detection (`preprocess/detect.py`)
1. Grayscale → Gaussian blur 5×5 → Canny (50, 150) → dilate 3×3.
2. `findContours` (external) → `approxPolyDP` (2% of the perimeter) → keep convex 4-point polygons with area ≥ 20% of the frame.
3. Choose the largest. Order the corners TL, TR, BR, BL.
4. Return `None` if none qualifies.
- A `Quad` is a `(4, 2)` float32 numpy array of frame pixel coordinates, ordered TL, TR, BR, BL.
- The preview overlay (`QuadSmoother`) averages corner positions over the last 5 detections (display only). A missed detection clears the history, so the overlay disappears rather than showing a stale outline. Capture uses detection on the captured frame itself, or the locked quad if one is set.

### 6.2 Warp & encode (`preprocess/warp.py`)
- **Aspect ratio:** estimated from the quad with Zhang & He, *Whiteboard scanning and image enhancement* (2007), assuming square pixels and the principal point at the frame center. The naive "longest edges" size is 10–15% off at a 15° tilt, which fails REQ-PRE-01.
  - The focal length is recovered from the quad when both edge pairs converge (tilt measure ≥ `MIN_TILT` = 0.01, about 0.5°) and the estimate lies within 0.3–3.0 × frame width.
  - Otherwise (tilt about one axis only, so one edge pair stays parallel) a focal length of 0.8 × frame width (~64° HFOV, a typical webcam) is assumed. In that case the result is insensitive to the guess: ≤ 2.5% error at 15° for 40–78° HFOV cameras.
- **Output size:** start from width = max(top edge, bottom edge), height = max(left edge, right edge), then enlarge the shorter side to match the estimated aspect ratio (never shrink). Apply `getPerspectiveTransform` + `warpPerspective` (`INTER_CUBIC`).
- **Degenerate result** (non-convex quad, or quad or output area < 1% of the frame) → use the full frame and log a warning. `process()` then reports `quad_used = None`.
- **Resize:** scale down so the long edge is ≤ `max_image_edge_px` (`INTER_AREA`). Never upscale.
- **Encode:** JPEG, quality 90, color (not grayscale). These exact bytes are stored as `frames/<seq>.jpg` and sent to the API.

## 7. Recognition
### 7.1 Protocol (`recognition/base.py`) _(→ code)_
```python
class Recognizer(Protocol):
    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome: ...

RecognitionOutcome = Ok | Retryable | AuthError | BadRequest | Malformed
# Ok(result: FrameResult, usage: Usage)   Retryable(reason: str)   AuthError(message: str)
# BadRequest(message: str)                Malformed(raw: str)
```

### 7.2 Prompt & tool (`recognition/prompt.py`)
The system prompt MUST contain one instruction for each item of REQ-OCR-02:
- **Exact transcription:** no corrections, completions or reformatting.
- **Indentation:** expressed as spaces, counted from the monospace character grid.
- **Line numbers:** gutter numbers go in `line_number`, never in `text`.
- **Ignore UI:** tabs, menus, breadcrumbs, minimap, status bar, scrollbars, line-change markers.
- **Edge flags:** `partial` for lines clipped at the top or bottom; `cut_off_right` for lines that run past the right edge.
- **Uncertainty:** `uncertain` plus `uncertain_note` for ambiguous characters (`l/1/I`, `O/0`, `{/(`, `;/:`, `` `/' ``, and the count of spaces).
- **Word wrap:** `word_wrap_detected` when soft-wrapped continuation lines are seen.
- **Output:** respond only through the tool.

`REPORT_TOOL`:
```json
{
  "name": "report_transcription",
  "description": "Report the exact transcription of the code visible on screen.",
  "input_schema": {
    "type": "object",
    "required": ["has_line_numbers", "word_wrap_detected", "lines"],
    "properties": {
      "has_line_numbers": {"type": "boolean"},
      "language_guess": {"type": ["string", "null"]},
      "word_wrap_detected": {"type": "boolean"},
      "lines": {"type": "array", "items": {
        "type": "object",
        "required": ["text", "line_number", "partial", "cut_off_right", "uncertain"],
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

### 7.3 API call (`recognition/claude.py`)
```python
client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=60.0)  # the queue owns retries
client.messages.create(
    model=settings.model, max_tokens=8192, temperature=0, system=SYSTEM_PROMPT,
    tools=[REPORT_TOOL], tool_choice={"type": "tool", "name": "report_transcription"},
    messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": "Transcribe this screen."}]}])
```
| SDK outcome | Mapped to |
| :--- | :--- |
| `APIConnectionError`, `APITimeoutError`, HTTP 429, 500, 502, 503, 529 | `Retryable` |
| HTTP 401, 403 | `AuthError` |
| HTTP 400, 404, 413 | `BadRequest` (no retry; the frame fails with the API message) |
| No `tool_use` block, or pydantic validation of `input` fails | `Malformed` |
| Valid `tool_use` | `Ok(FrameResult, Usage(input_tokens, output_tokens))` |

### 7.4 Queue (`recognition/queue.py`)
- **Concurrency:** at most `max_concurrent_requests` calls in flight.
- **Retry schedule for `Retryable`:** delays of 1, 2, 4, 8, 16 s × (1 + U[0, 0.2]) jitter. After 5 failed attempts → `failed`.
- **`Malformed`:** 1 retry, then `failed("Unreadable model response")`.
- **`AuthError`:** pause the whole queue. Put the frame back to `queued` (attempts not incremented) and emit `auth_required` once per pause. `resume()` is called after the key changes.
- **Offline:** when 3 consecutive outcomes are connection errors, set `offline = True` and emit `offline_changed`. Keep retrying the oldest frame every 15 s, and hold the others. The first success clears offline.
- **In-order release:** results are buffered and released to the stitcher only when every lower `seq` is recognized, failed, or discarded.
- **Discard:** cancel the future if it hasn't started, otherwise ignore its result.
- **Usage:** the token totals are added to `Session.usage`.
- Clock, sleep and RNG are injectable.

## 8. Settings & Environment
### 8.1 Settings (`<data_dir>/settings.json`, `config/settings.py`)
| Key | Type | Default | Used by |
| :--- | :--- | :--- | :--- |
| `schema_version` | int | 1 | §10.4 |
| `camera_id` | int | 0 | §5.1 |
| `auto_exposure` | bool | true | §5.1 |
| `exposure` | float \| null | null (driver default) | §5.1 |
| `capture_mode` | `auto` \| `manual` | `auto` | §5.3 |
| `stable_duration_ms` | int | 600 | §5.3 |
| `stability_threshold` | float | 2.0 | §5.3 |
| `motion_threshold` | float | 6.0 | §5.3 |
| `sharpness_threshold` | float | 100.0 | §5.2 |
| `duplicate_threshold` | float | 1.0 | §5.3 |
| `max_image_edge_px` | int | 1568 | §6.2 |
| `model` | str | `claude-sonnet-5-5` | §7.3 |
| `max_concurrent_requests` | int | 3 | §7.4 |
| `min_overlap_lines` | int | 2 | §9.2 |
| `line_similarity` | float | 0.90 | §9.1 |
| `line_ending` | `lf` \| `crlf` | `lf` | §13.3 |
| `sound_enabled` | bool | true | UI |
| `session_retention_days` | int | 7 | §10.3 |
| `consent_acknowledged` | bool | false | REQ-SES-06 |

- Load: parse JSON. Unknown keys are ignored, and missing or invalid keys use their default.
- If the file is not valid JSON → rename it to `settings.bak.json` and use all defaults.
- Save: atomic, the same way as sessions.

### 8.2 Secrets & paths
- **API key:** `os.environ["ANTHROPIC_API_KEY"]` if set and non-empty, else `keyring.get_password("screen-copier", "anthropic_api_key")`. `keyring.errors.KeyringError` or no backend → the key is unavailable and the UI shows the "set ANTHROPIC_API_KEY" hint.
- **"Test key":** sends a `messages.create` call with `max_tokens=1` and the text "ping" to the configured model.
- **Data dir:** `SCREEN_COPIER_DATA_DIR`, else `platformdirs.user_data_dir("ScreenCopier", appauthor=False)`.

### 8.3 Environment variables
| Variable | Purpose | Test value |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` | API key (overrides keyring) | `sk-ant-test-0000` |
| `SCREEN_COPIER_DATA_DIR` | Override the data dir | pytest `tmp_path` |
| `SCREEN_COPIER_FAKE_CAMERA` | Image folder or video file used as the camera | `tests/fixtures/camera/basic/` |
| `SCREEN_COPIER_FAKE_RECOGNIZER` | Folder of `<seq>.json` FrameResults | `tests/fixtures/recognizer/seq_basic/` |

## 9. Stitching
`Stitcher` is pure: `stitch(results: list[tuple[int, FrameResult]]) -> StitchOutput(document, events)`. An incremental `add(seq, result)` must produce the same state as a batch run (REQ-STI-01). Recommended: implement `add` as the primitive and make batch a fold over it.

### 9.1 Normalization & matching (`stitching/normalize.py`)
- `normalize(s)`: strip, then collapse internal whitespace runs to one space. Used for matching only. Output keeps the original text.
- `match(a, b)`: true if both are blank, or if `SequenceMatcher(None, normalize(a), normalize(b)).ratio() ≥ line_similarity`.
- **Distinctive line:** `len(normalize(s).replace(" ", "")) ≥ 3`.
- **Pre-filter:** drop lines with `partial = true` from the frame before anything else (REQ-STI-02).

### 9.2 Overlap mode (`stitching/overlap.py`)
The frame lines F (after the pre-filter) are compared with each segment S. A **candidate** is an offset `o` (which can be negative or extend past the end) aligning `F[i]` with `S[o+i]`. The overlap is the set of indices where both exist. The candidate is valid when:
1. the overlap has ≥ `min_overlap_lines` matching **distinctive** lines,
2. ≥ 90% of the overlapped lines match, and
3. it is a tail extension (`o + len(F) > len(S)` and `o ≥ 0`), a head extension (`o < 0` and `o + len(F) ≤ len(S)`), or containment (`0 ≤ o` and `o + len(F) ≤ len(S)`).

Choosing:
- Score = the number of matching lines.
- The best candidate across all segments wins.
- If another valid candidate, at a different segment or offset, has the same score → **ambiguous**. Emit `ambiguous_overlap` and treat the frame as unplaced.

Applying:
- Overlapping lines add observations (§9.4).
- Non-overlapping lines are appended or prepended as new DocLines.

**Unplaced frame:** becomes a new segment, inserted right after the segment that received the previous frame. Emit `gap_detected`.

**Bridging:** after applying, test the modified segment against its neighbors with the same validity rule, treating the neighbor as the frame. If valid → merge the two segments and emit `gap_closed`.

### 9.3 Line-number mode (`stitching/linenum.py`)
- **When it applies:** to a frame with `has_line_numbers = true` and ≥2 non-partial lines with a `line_number`.
- **Placement:** each numbered line is an observation for document line `n`. Line-number segments are kept as a map from `n` to DocLine. Contiguous runs of numbers form segments, ordered by `n`.
- **Unnumbered lines in a numbered frame:** a non-partial line with `line_number = null` is attached immediately after the preceding numbered line as a DocLine with the `soft_wrap` flag (keyed `n + 0.1`, `n + 0.2`, …) and emits `word_wrap`. `word_wrap_detected = true` also emits `word_wrap`.
- **Gaps:** every missing range between the minimum and maximum numbers → a gap, described as `lines X–Y missing`. If the minimum is > 1 → a gap `lines 1–(min−1) missing` at the top.
- **Frames without numbers in a numbered document:** use overlap mode (§9.2) against the rendered numbered segments. Lines placed this way inherit numbers from their alignment.

### 9.4 Observation resolution (`stitching/resolve.py`)
For each DocLine with its observations:
1. Group the observations by exact text.
2. If any observation is not uncertain, discard the uncertain ones.
3. If ≥3 observations remain and one text has a strict majority → use it.
4. Otherwise → use the latest observation (highest `frame_seq`).
5. If the remaining set has more than one distinct text after steps 2–3 → set the `conflict` flag and set `alternatives` to the distinct texts ordered by frequency, then recency.

Other flags:
- `uncertain`: set if the chosen observation is uncertain.
- `cut_off`: set if any observation had `cut_off_right` (also emit `cut_off`).

### 9.5 Rendering, events, discard
- **Rendering:** segments are joined in order with one gap marker line between consecutive segments, plus any line-number gaps. Gap marker text is `<<<SCREEN-COPIER GAP: {description}>>>`. For overlap gaps the description is `unknown lines between capture {a} and {b}`.
- **Events** (consumed by the controller): `gap_detected`, `gap_closed`, `ambiguous_overlap`, `cut_off`, `word_wrap`. Each carries the frame seq.
- **Discard:** re-run the batch stitch over the remaining frames (REQ-STI-10).
- **Performance:** matching only checks offsets near segment ends first; use a full scan only for containment. Target ≤200 ms per add on a 2,000-line document (REQ-STI-11).

## 10. Data Model & Persistence
### 10.1 Entities (`models.py`, pydantic v2) _(→ code)_
| Entity | Field | Type | Constraints / Notes |
| :--- | :--- | :--- | :--- |
| Session | schema_version | int | = `SCHEMA_VERSION` (1). Greater → `UnsupportedVersion` is raised (not a `ValidationError`). Lower → must be migrated first (§10.4). |
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
| Frame | quality | `{sharpness: float, blurry: bool}` | |
| Frame | status | `queued` \| `recognizing` \| `recognized` \| `failed` \| `discarded` | |
| Frame | attempts | int ≥ 0 | |
| Frame | error | str \| null | |
| Frame | result | FrameResult \| null | Non-null iff `recognized` |
| FrameResult | has_line_numbers, word_wrap_detected | bool | §7.2. FrameResult and RecognizedLine use strict types and ignore unknown keys, so they parse `tool_use.input` directly. |
| FrameResult | language_guess | str \| null | |
| FrameResult | lines | list[RecognizedLine] | Top to bottom, may be empty |
| RecognizedLine | text | str | No line number, no newline (CR and LF are rejected) |
| RecognizedLine | line_number | int ≥ 1 \| null | |
| RecognizedLine | partial, cut_off_right, uncertain | bool | |
| RecognizedLine | uncertain_note | str \| null | |
| Document *(derived)* | segments | list[Segment] | Never persisted |
| Document | gaps | list[Gap] | In render order |
| Segment | lines | list[DocLine] | Non-empty |
| Segment | first_line_number | int \| null | |
| DocLine | text | str | Resolved |
| DocLine | observations | list[Observation] | ≥1 |
| DocLine | flags | set of `uncertain` \| `conflict` \| `cut_off` \| `soft_wrap` | |
| DocLine | alternatives | list[str] | |
| Observation | frame_seq, text, uncertain, cut_off_right | int ≥ 1, str, bool, bool | `cut_off_right` feeds the `cut_off` flag (§9.4) |
| Gap *(derived)* | kind | `overlap` \| `line_numbers` | Overlap gaps drive the `gap` warning (§12) |
| Gap | description | str | §9.5 |
| Gap | before_segment | int ≥ 0 | Index of the segment the marker renders immediately before |
| StitchEvent *(derived)* | kind, frame_seq | `gap_detected` \| `gap_closed` \| `ambiguous_overlap` \| `cut_off` \| `word_wrap`, int ≥ 1 | §9.5 |

### 10.2 State ownership
| State | Where | Survives restart |
| :--- | :--- | :--- |
| Settings | `settings.json` | Yes |
| API key | Env var, or Credential Manager | Yes (keyring) |
| Frames and results | `sessions/<id>/session.json` + `frames/*.jpg` | Yes, until retention or discard |
| Document | Derived from frames in memory | Rebuilt |
| Edited text | `Session.edited_text` | Yes |
| Camera, metrics, trigger state | Memory | No |
| In-flight requests | Memory | No. Re-queued on resume. |
| Locked screen quad | Memory | No (the user re-locks) |

### 10.3 Session store (`session/store.py`)
- **Layout:** `<data_dir>/sessions/<id>/session.json` and `<data_dir>/sessions/<id>/frames/<seq>.jpg`.
- **Save order:** write the frame JPEG first, then `session.json` (REQ-NFR-07).
- **Atomic write:** write to `session.json.tmp` in the same folder, `fsync`, then `os.replace`.
- **Save triggers:** every frame state change, and editor changes debounced by 2 s.
- **Discovery at startup:** a session with `state != saved` → offer to resume. If several exist, offer the newest and leave the rest for retention.
- **Retention:** at startup, delete `saved` sessions with `saved_at` older than `session_retention_days`. Discard deletes the folder immediately.
- **Corrupt file** (JSON or validation error) → rename it to `session.corrupt-<UTC timestamp>.json`, notify the user, and start fresh.
- **Resume:** frames with status `queued` or `recognizing` → reset to `queued` and re-submit.

### 10.4 Migrations
`session/migrations.py` holds `MIGRATIONS: dict[int, Callable[[dict], dict]]`, which upgrades v → v+1. On load, apply them in sequence and then rewrite the file. A version greater than the supported one raises `UnsupportedVersion` (REQ-SES-07). Settings use the same mechanism.

## 11. Gap Marker & Text Format
- **Gap marker line:** `<<<SCREEN-COPIER GAP: {description}>>>`, exactly. It is greppable and intentionally breaks compilation.
- **Indentation:** always spaces. Tabs are never produced (a non-goal).

## 12. Controller Contract (`controller.py`) _(→ code)_
| Signal | Payload | When |
| :--- | :--- | :--- |
| `preview_frame` | `QImage`, `quad \| None` | Every preview frame |
| `trigger_state` | `str` (MOVING / SETTLING / WAITING_FOCUS / CAPTURED) | On change |
| `frame_added` | `Frame` | After preprocessing and save |
| `frame_updated` | `Frame` | Status change |
| `document_changed` | `Document`, `stats{frames, lines, gaps, issues, input_tokens, output_tokens}` | After each stitch |
| `warning` | `kind`, `message`, `active: bool` | Raise or clear. Kinds: `gap`, `ambiguous_overlap`, `cut_off`, `word_wrap`, `offline`, `blurry`, `never_settles`, `low_resolution` |
| `auth_required` | — | §7.4 |
| `camera_error` | `kind` (`not_found` / `unavailable` / `disconnected`), `message` | §5.1 |
| `consent_required` | — | Before the first API call if not yet acknowledged |

**Slots:** `capture_now()`, `set_auto(bool)`, `discard_frame(seq)`, `retry_frame(seq)`, `rerecognize_frame(seq)`, `lock_region(quad | None)`, `select_camera(id)`, `acknowledge_consent()`, `new_session()`, `resume_session(id)`, `set_edited_text(str | None)`.

**Warning lifecycle:**
- `gap` stays active while the document has ≥1 overlap gap.
- `offline` mirrors the queue's state.
- `cut_off` and `word_wrap` stay active until dismissed or until a later frame no longer shows the condition.
- `blurry` clears after 3 s.

**Consent gate:** frames are captured and saved, but the queue isn't started until consent is acknowledged.

## 13. Output
### 13.1 Language (`output/language.py`)
- **Vote:** the most common normalized `language_guess` among recognized frames. Ties go to the most recent. None → `unknown`.
- **Alias map (examples):** `py`→`python`, `js`→`javascript`, `ts`→`typescript`, `c#`/`cs`→`csharp`, `c++`→`cpp`, `yml`→`yaml`, `bash`/`sh`→`shell`, `ps1`→`powershell`, `plaintext`→`text`. Unmapped → `unknown`.

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

### 13.2 Syntax check (`output/syntax.py`)
`check(text, language) -> list[SyntaxIssue(line: int, column: int | None, message: str)] | None`. `None` means there is no checker. Gap marker lines are replaced with blank lines before checking, so line numbers stay correct.

### 13.3 Writer (`output/writer.py`)
1. Split into lines and `rstrip()` each.
2. Drop trailing blank lines.
3. Join with `\n` or `\r\n`, and add exactly one final line ending.
4. Encode as UTF-8 without a BOM.

The default filename is `capture-YYYYMMDD-HHMMSS.<ext>`, in local time.

## 14. UI Structure
| Component | Contents | States |
| :--- | :--- | :--- |
| MainWindow | Toolbar (mode toggle, Auto toggle, Capture, Save, Settings), stacked Capture/Review views, frame strip docked at the bottom | — |
| CaptureView | PreviewWidget (quad overlay, 4 draggable handles, Lock/Clear), stats bar, warning banner | no-camera, camera-error, previewing, waiting-focus, captured-flash |
| FrameStrip | Thumbnails + status badge, context menu, Delete key, double-click opens the viewer | empty, populated |
| ReviewView | Left: frame viewer (frames for the current line). Right: CodeEditor (QPlainTextEdit with a gutter for line numbers and issue icons). Issue counter, language combo, syntax panel. | empty, has-issues, clean |
| SettingsDialog | All §8.1 settings, API key enter/test/clear (masked to the last 4 characters) | key-ok, key-missing, keyring-unavailable |
| Dialogs | Consent, Resume (normal / newer-version), Gap-save confirm, Edit-loss confirm, Error | — |

**Shortcuts:**
| Key | Action |
| :--- | :--- |
| Space | Capture |
| A | Toggle Auto |
| Tab | Switch mode |
| F8 / Shift+F8 | Next / previous issue |
| Ctrl+S | Save |
| Delete | Discard the selected frame |
| Ctrl+N | New session |

Capture-only shortcuts (Space, A, Delete) are disabled while the CodeEditor has focus.

**Issue display:** a background color plus a gutter icon (with a distinct shape per issue type) plus a tooltip (REQ-NFR-08).

## 15. CLI & Packaging
- `pyproject.toml`: `[project.gui-scripts] screen-copier = "screen_copier.app:main"`. Installed with `uv tool install .`.
- `screen-copier --version` prints the version. `screen-copier --create-shortcut` runs `shortcut.py`.
- The shortcut is created by calling `powershell -NoProfile -Command` with a `WScript.Shell` `CreateShortcut` script:
  - Target: the resolved path of the `screen-copier` executable (`shutil.which`).
  - Icon: `assets/icon.ico`.
  - Location: `[Environment]::GetFolderPath('Desktop')\Screen Copier.lnk`.
  - Exit code 0 on success, 1 on failure, with stderr printed.

## 16. Errors & Logging
- `config/logging.py`: `RotatingFileHandler(<data_dir>/logs/screen-copier.log, maxBytes=1 MB, backupCount=5)`, INFO level.
- A redaction filter replaces any `sk-ant-[A-Za-z0-9_-]+` with `sk-ant-***`.
- Image bytes are never logged.
- `sys.excepthook` and Qt's message handler → log the traceback and show the error dialog. The session continues, because it is already saved.

## 17. Testing Architecture
- **Unit (no Qt):** `tests/camera/` (synthetic numpy frames + a fake clock), `tests/preprocess/` (synthetic images from `tests/fixtures/images/make_synthetic.py`), `tests/recognition/` (an injected fake client), `tests/stitching/`, `tests/output/`, `tests/session/`, `tests/config/`.
- **Stitching fixtures** (`tests/fixtures/stitching/<name>/`): `frames/<seq>.json` (FrameResult JSON), `expected.txt` (rendered output including gap markers), `meta.json` (`{"description", "expected_events": [{"kind", "frame_seq"}], "expected_conflicts": [{"line", "alternatives"}]}`). A generic test runs every fixture both incrementally and in batch.
- **UI (pytest-qt):** with the fake camera, the fake recognizer and `tmp_path` as the data dir.
- **Timing tests** are marked `@pytest.mark.perf` (registered in `pyproject.toml`). They run by default; skip them on slow machines with `uv run pytest -m "not perf"`.
- **Manual / hardware:** `scripts/benchmark.py` (TASK-21).

## 18. Design Assumptions
| ID | Statement | Impact if wrong | Resolved by |
| :--- | :--- | :--- | :--- |
| A-01 | QMediaDevices order matches DirectShow indices. | Wrong camera names. The "Camera N" fallback exists. | TASK-12 manual check |
| A-02 | Default capture thresholds (§8.1) suit a typical 1080p webcam. | Auto-capture is too eager or never fires. | TASK-21 tuning |
| A-03 | A 1568 px long edge is enough for code at ≥14 px. | Accuracy drops. Raise `max_image_edge_px`. | TASK-21 benchmark |
| A-04 | Claude reliably fills the tool schema, including the edge flags. | Worse stitching. The Malformed retry mitigates it. | TASK-21 benchmark |
