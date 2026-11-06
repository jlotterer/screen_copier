"""Generate the stitching fixtures from declarative specs (TASK-03).

Each fixture folder holds a hand-written ``source.txt``. This script slices it into simulated
``frames/<seq>.json`` FrameResults, writes ``expected.txt`` and ``meta.json``, and is the only
thing that should write those files. Change a spec here and re-run; don't hand-edit the output:

    uv run python tests/fixtures/stitching/make_frames.py

``tests/fixtures/test_fixture_schema.py`` checks that the committed files match this output.
The script uses only the standard library, so it does not depend on ``screen_copier``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent

GAP_PREFIX = "<<<SCREEN-COPIER GAP: "
GAP_SUFFIX = ">>>"
EN_DASH = chr(0x2013)  # "lines X-Y missing" uses an en dash; ruff flags the literal


# --------------------------------------------------------------------------------------------
# Spec types
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Misread:
    """How one frame transcribes one source line, when it differs from the truth."""

    text: str | None = None
    """Replacement text. None keeps the true text (useful to only mark it uncertain)."""
    uncertain: bool = False
    note: str | None = None


@dataclass(frozen=True)
class Frame:
    """A window of fully visible source lines ``first..last`` (1-based, inclusive)."""

    first: int
    last: int
    partial_top: str | None = None
    """Transcription of the clipped line just above ``first`` (``partial: true``)."""
    partial_bottom: str | None = None
    """Transcription of the clipped line just below ``last`` (``partial: true``)."""
    numbered: bool = False
    """The editor gutter is visible: every line carries its ``line_number``."""
    misreads: dict[int, Misread] = field(default_factory=dict)
    """Keyed by source line number."""
    wraps: dict[int, int] = field(default_factory=dict)
    """Source line -> column where the editor soft-wraps it. The continuation keeps the line's
    indentation and has ``line_number: null``."""
    word_wrap_detected: bool = False
    language: str | None = "python"


@dataclass(frozen=True)
class Junk:
    """A frame whose content is unrelated to ``source.txt`` (for example another window)."""

    lines: tuple[str, ...]
    language: str | None = None


@dataclass(frozen=True)
class Src:
    """Expected output: source lines ``first..last`` (1-based, inclusive)."""

    first: int
    last: int
    replace: dict[int, str] = field(default_factory=dict)
    """Source line -> the text the stitcher is expected to render instead."""
    split: dict[int, int] = field(default_factory=dict)
    """Source line -> column: rendered as two lines (a soft-wrapped line, see ``Frame.wraps``)."""


@dataclass(frozen=True)
class GapLine:
    description: str


@dataclass(frozen=True)
class Text:
    lines: tuple[str, ...]


Piece = Src | GapLine | Text


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    frames: tuple[Frame | Junk, ...]
    expected: tuple[Piece, ...]
    uses_line_numbers: bool = False
    events: tuple[tuple[str, int], ...] = ()
    conflicts: tuple[tuple[int, tuple[str, ...]], ...] = ()
    """(1-based line in expected.txt, alternatives ordered by frequency, then recency)."""
    flags: tuple[tuple[int, tuple[str, ...]], ...] = ()
    """(1-based line in expected.txt, flags). Every flagged line is listed."""
    discard: tuple[int, ...] = ()
    expected_after_discard: tuple[Piece, ...] = ()
    events_after_discard: tuple[tuple[str, int], ...] = ()


def overlap_gap(a: int, b: int) -> GapLine:
    return GapLine(f"unknown lines between capture {a} and {b}")


def numbered_gap(first: int, last: int) -> GapLine:
    return GapLine(f"lines {first}{EN_DASH}{last} missing")


# --------------------------------------------------------------------------------------------
# Fixture specs
# --------------------------------------------------------------------------------------------

FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="seq_basic",
        description=(
            "Five unnumbered frames scrolling down a 112-line Python module, each overlapping "
            "the previous one by 2 to 4 lines. No partial lines, misreadings or gaps. "
            "The output equals the source."
        ),
        frames=(
            Frame(1, 26),
            Frame(24, 49),
            Frame(46, 71),
            Frame(70, 94),
            Frame(91, 112),
        ),
        expected=(Src(1, 112),),
    ),
    Fixture(
        name="seq_partial_lines",
        description=(
            "Three unnumbered frames whose top and bottom edges clip a line (partial: true). "
            "The clipped transcriptions are garbled (frame 1 ends with 'def fo' for line 44, "
            "'def format_report(...)') and must not reach the output. Every clipped line is "
            "fully visible in a neighbouring frame (REQ-STI-02). The output equals the source."
        ),
        frames=(
            Frame(1, 43, partial_bottom="def fo"),
            Frame(
                38,
                56,
                partial_top="    retum {",
                partial_bottom='    parser.add_argumert("paths", nargs="+", type=Patn)',
            ),
            Frame(53, 70, partial_top='    retum "\\n".jom(lines)'),
        ),
        expected=(Src(1, 70),),
    ),
    Fixture(
        name="seq_no_overlap",
        description=(
            "Unnumbered frames 1-2 cover lines 1-46. The user then scrolls too far: frame 3 "
            "starts at line 60 and shares no line with the document, so it becomes a new "
            "segment after a gap marker and emits gap_detected (REQ-STI-06). Frame 4 extends "
            "that second segment. Lines 47-59 were never captured."
        ),
        frames=(
            Frame(1, 25),
            Frame(22, 46),
            Frame(60, 84),
            Frame(81, 101),
        ),
        expected=(Src(1, 46), overlap_gap(2, 3), Src(60, 101)),
        events=(("gap_detected", 3),),
    ),
    Fixture(
        name="seq_bridge",
        description=(
            "Frames 1-3 as in seq_no_overlap (a gap after line 46). The user scrolls back up: "
            "frame 4 covers lines 42-63, overlapping segment 1 by 5 lines and segment 2 by 4. "
            "It extends segment 1 (the higher score), and the bridge test then merges segment 2 "
            "into it: the gap marker disappears and gap_closed is emitted (REQ-STI-07). Frame 5 "
            "extends the merged segment. The output equals the source."
        ),
        frames=(
            Frame(1, 25),
            Frame(22, 46),
            Frame(60, 84),
            Frame(42, 63),
            Frame(81, 101),
        ),
        expected=(Src(1, 101),),
        events=(("gap_detected", 3), ("gap_closed", 4)),
    ),
    Fixture(
        name="seq_ambiguous_braces",
        description=(
            "A JavaScript test file with runs of '});' lines (a bare '}' is not distinctive, "
            "'});' is). Frames 1-3 cover the whole file. Frame 4 is a small view whose only "
            "full lines are '});' x3 and a blank line; it matches lines 28-31 and 51-54 equally "
            "well. The stitcher must not guess: it emits ambiguous_overlap, treats the frame as "
            "unplaced (gap_detected) and renders it as its own segment after a gap marker "
            "(REQ-STI-06)."
        ),
        frames=(
            Frame(1, 26, language="javascript"),
            Frame(23, 48, language="javascript"),
            Frame(45, 68, language="javascript"),
            Frame(
                51,
                54,
                partial_top="        expect(cart.totaI()).toBe(1OO5);",
                partial_bottom='  descrlbe("remove", () => {',
                language="javascript",
            ),
        ),
        expected=(
            Src(1, 68),
            overlap_gap(3, 4),
            Text(("      });", "    });", "  });", "")),
        ),
        events=(("ambiguous_overlap", 4), ("gap_detected", 4)),
    ),
    Fixture(
        name="seq_scroll_back",
        description=(
            "An 88-line JSON config. Frames 1-2 scroll down to line 45, frame 3 scrolls back up "
            "to lines 9-30 (fully contained in the document), then frames 4-5 scroll down to "
            "the end. The contained frame only adds observations: no duplicated or missing "
            "lines (REQ-STI-05). The output equals the source."
        ),
        frames=(
            Frame(1, 24, language="json"),
            Frame(21, 45, language="json"),
            Frame(9, 30, language="json"),
            Frame(42, 66, language="json"),
            Frame(63, 88, language="json"),
        ),
        expected=(Src(1, 88),),
    ),
    Fixture(
        name="seq_conflicts",
        description=(
            "Three unnumbered frames with large overlaps (lines 20-24 are seen by all three). "
            "Line 3: seen once, marked uncertain, so it keeps the uncertain flag. "
            "Line 15: frame 1 reads it correctly; frame 2 (later) reads '{Iast_error}' and marks "
            "it uncertain, so the certain reading wins (§9.4 step 2). "
            "Line 22: frames 1 and 2 agree; frame 3 (latest) misreads 'atternpts' confidently, "
            "so the majority wins (step 3). "
            "Line 38: frame 2 reads '= 5', frame 3 (latest) reads '= 6', both confident: the "
            "latest is rendered and the line is a conflict with both alternatives "
            "(steps 4-5, REQ-STI-08)."
        ),
        frames=(
            Frame(1, 24, misreads={3: Misread(uncertain=True, note="spacing")}),
            Frame(
                12,
                40,
                misreads={
                    15: Misread(
                        text='        super().__init__(f"gave up after {attempts} attempts: '
                        '{Iast_error}")',
                        uncertain=True,
                        note="l/I",
                    )
                },
            ),
            Frame(
                20,
                57,
                misreads={
                    22: Misread(text="    for attempt in range(atternpts):"),
                    38: Misread(text="    max_attempts: int = 6,"),
                },
            ),
        ),
        expected=(Src(1, 57, replace={38: "    max_attempts: int = 6,"}),),
        conflicts=((38, ("    max_attempts: int = 6,", "    max_attempts: int = 5,")),),
        flags=((3, ("uncertain",)), (38, ("conflict",))),
    ),
    Fixture(
        name="seq_linenums_gap",
        description=(
            "Four numbered frames covering lines 1-40 and 48-90 of a 90-line file. Frame 2's "
            "clipped bottom line (41) and frame 3's clipped top line (47) are partial and do "
            "not count. Line-number mode places every line by number and renders a "
            "'lines 41-47 missing' gap (REQ-STI-03, REQ-STI-04). Line-number gaps emit no "
            "event (only unplaced frames emit gap_detected)."
        ),
        uses_line_numbers=True,
        frames=(
            Frame(1, 22, numbered=True),
            Frame(19, 40, numbered=True, partial_bottom="def tokenlze(source: str) -> Iterator"),
            Frame(48, 70, numbered=True, partial_top='    yieId Token("end", "", posltion)'),
            Frame(66, 90, numbered=True),
        ),
        expected=(Src(1, 40), numbered_gap(41, 47), Src(48, 90)),
    ),
    Fixture(
        name="seq_linenums_start_missing",
        description=(
            "Two numbered frames. The first full line of frame 1 is line 5 (line 4 is clipped "
            "and partial), so lines 1-4 were never seen and a 'lines 1-4 missing' gap is "
            "rendered at the top (REQ-STI-04)."
        ),
        uses_line_numbers=True,
        frames=(
            Frame(5, 30, numbered=True, partial_top="from pathIib import Path"),
            Frame(27, 55, numbered=True),
        ),
        expected=(numbered_gap(1, 4), Src(5, 55)),
    ),
    Fixture(
        name="seq_soft_wrap",
        description=(
            "Two numbered frames. In frame 1 the editor soft-wraps line 19 (a long raise): the "
            "continuation has no line number of its own. It is kept as its own line right "
            "after line 19 with the soft_wrap flag, and word_wrap is emitted once for frame 1 "
            "(frame 1 also sets word_wrap_detected; one event per frame) (REQ-STI-09). The "
            "output equals the source except that line 19 is split in two."
        ),
        uses_line_numbers=True,
        frames=(
            Frame(1, 24, numbered=True, wraps={19: 58}, word_wrap_detected=True),
            Frame(21, 45, numbered=True),
        ),
        expected=(Src(1, 45, split={19: 58}),),
        events=(("word_wrap", 1),),
        flags=((20, ("soft_wrap",)),),
    ),
    Fixture(
        name="seq_mixed_numbering",
        description=(
            "Frames 1 and 3 show line numbers. Frame 2 was captured with the gutter cropped "
            "(has_line_numbers: false), so it is placed by overlap against the numbered "
            "document (lines 24-27) and its lines inherit numbers 28-50 (§9.3). Frame 3's "
            "numbers then agree with the inherited ones. The output equals the source."
        ),
        uses_line_numbers=True,
        frames=(
            Frame(1, 27, numbered=True),
            Frame(24, 50),
            Frame(47, 73, numbered=True),
        ),
        expected=(Src(1, 73),),
    ),
    Fixture(
        name="seq_discard",
        description=(
            "Frame 1 covers lines 1-30 and frame 3 lines 27-55. Frame 2 is junk: the user "
            "switched to a terminal. With all three frames the junk frame is an unplaced "
            "segment after a gap marker. Discarding frame 2 must give exactly the document "
            "built from frames 1 and 3: the source, no gap, no events (REQ-STI-10)."
        ),
        frames=(
            Frame(1, 30),
            Junk(
                (
                    "PS C:\\work\\billing> uv run pytest -q",
                    "..........F.....",
                    "FAILED tests/test_invoice.py::test_rounding - assert 10.04 == 10.05",
                    "1 failed, 15 passed in 0.84s",
                    "PS C:\\work\\billing>",
                )
            ),
            Frame(27, 55),
        ),
        expected=(
            Src(1, 55),
            overlap_gap(3, 2),
            Text(
                (
                    "PS C:\\work\\billing> uv run pytest -q",
                    "..........F.....",
                    "FAILED tests/test_invoice.py::test_rounding - assert 10.04 == 10.05",
                    "1 failed, 15 passed in 0.84s",
                    "PS C:\\work\\billing>",
                )
            ),
        ),
        events=(("gap_detected", 2),),
        discard=(2,),
        expected_after_discard=(Src(1, 55),),
    ),
)


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def _line(
    text: str,
    number: int | None,
    *,
    partial: bool = False,
    uncertain: bool = False,
    note: str | None = None,
) -> dict[str, object]:
    return {
        "text": text,
        "line_number": number,
        "partial": partial,
        "cut_off_right": False,
        "uncertain": uncertain,
        "uncertain_note": note,
    }


def soft_wrap(text: str, col: int) -> tuple[str, str]:
    """Split a line the way an editor soft-wraps it at ``col``: the first visual line loses its
    trailing space, the continuation keeps the line's indentation."""
    indent = text[: len(text) - len(text.lstrip(" "))]
    return text[:col].rstrip(" "), indent + text[col:].lstrip(" ")


def frame_json(source: list[str], frame: Frame | Junk) -> dict[str, object]:
    if isinstance(frame, Junk):
        return {
            "has_line_numbers": False,
            "language_guess": frame.language,
            "word_wrap_detected": False,
            "lines": [_line(text, None) for text in frame.lines],
        }
    if not 1 <= frame.first <= frame.last <= len(source):
        raise ValueError(f"window {frame.first}..{frame.last} outside source")
    unknown = set(frame.misreads) | set(frame.wraps)
    if not unknown <= set(range(frame.first, frame.last + 1)):
        raise ValueError(f"misread or wrap outside the window: {sorted(unknown)}")

    def num(n: int) -> int | None:
        return n if frame.numbered else None

    lines: list[dict[str, object]] = []
    if frame.partial_top is not None:
        if frame.first == 1:
            raise ValueError("no line above line 1 to clip")
        lines.append(_line(frame.partial_top, num(frame.first - 1), partial=True))
    for n in range(frame.first, frame.last + 1):
        truth = source[n - 1]
        misread = frame.misreads.get(n, Misread())
        text = truth if misread.text is None else misread.text
        if n in frame.wraps:
            head, tail = soft_wrap(text, frame.wraps[n])
            lines.append(_line(head, num(n), uncertain=misread.uncertain, note=misread.note))
            lines.append(_line(tail, None))
        else:
            lines.append(_line(text, num(n), uncertain=misread.uncertain, note=misread.note))
    if frame.partial_bottom is not None:
        if frame.last == len(source):
            raise ValueError("no line below the last line to clip")
        lines.append(_line(frame.partial_bottom, num(frame.last + 1), partial=True))
    return {
        "has_line_numbers": frame.numbered,
        "language_guess": frame.language,
        "word_wrap_detected": frame.word_wrap_detected,
        "lines": lines,
    }


def render_expected(source: list[str], pieces: tuple[Piece, ...]) -> str:
    out: list[str] = []
    for piece in pieces:
        if isinstance(piece, GapLine):
            out.append(f"{GAP_PREFIX}{piece.description}{GAP_SUFFIX}")
        elif isinstance(piece, Text):
            out.extend(piece.lines)
        else:
            for n in range(piece.first, piece.last + 1):
                text = piece.replace.get(n, source[n - 1])
                if n in piece.split:
                    out.extend(soft_wrap(text, piece.split[n]))
                else:
                    out.append(text)
    return "\n".join(out) + "\n"


def _events(events: tuple[tuple[str, int], ...]) -> list[dict[str, object]]:
    return [{"kind": kind, "frame_seq": seq} for kind, seq in events]


def build(fixture: Fixture) -> dict[str, str]:
    """Return {relative path: file content} for everything generated in one fixture folder."""
    source = (ROOT / fixture.name / "source.txt").read_text(encoding="utf-8").split("\n")
    if source[-1] != "":
        raise ValueError(f"{fixture.name}/source.txt must end with a newline")
    source = source[:-1]

    files: dict[str, str] = {}
    for seq, frame in enumerate(fixture.frames, start=1):
        payload = frame_json(source, frame)
        files[f"frames/{seq}.json"] = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    files["expected.txt"] = render_expected(source, fixture.expected)

    meta: dict[str, object] = {
        "description": fixture.description,
        "uses_line_numbers": fixture.uses_line_numbers,
        "expected_events": _events(fixture.events),
        "expected_conflicts": [
            {"line": line, "alternatives": list(alts)} for line, alts in fixture.conflicts
        ],
        "expected_flags": [{"line": line, "flags": list(flags)} for line, flags in fixture.flags],
    }
    if fixture.discard:
        meta["discard"] = list(fixture.discard)
        meta["expected_after_discard"] = "expected_after_discard.txt"
        meta["expected_events_after_discard"] = _events(fixture.events_after_discard)
        files["expected_after_discard.txt"] = render_expected(
            source, fixture.expected_after_discard
        )
    files["meta.json"] = json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
    return files


def write_all() -> None:
    for fixture in FIXTURES:
        folder = ROOT / fixture.name
        frames_dir = folder / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old in frames_dir.glob("*.json"):
            old.unlink()
        for rel, content in build(fixture).items():
            (folder / rel).write_bytes(content.encode("utf-8"))
        print(f"wrote {fixture.name}: {len(fixture.frames)} frames")


if __name__ == "__main__":
    write_all()
    sys.exit(0)
