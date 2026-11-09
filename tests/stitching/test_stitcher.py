"""Stitcher behavior beyond the fixtures: segments, gaps, bridging, events, discard, timing."""

from __future__ import annotations

import random
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence

import pytest

from screen_copier.config.settings import Settings
from screen_copier.models import (
    DocLineFlag,
    FrameResult,
    GapKind,
    RecognizedLine,
    StitchEventKind,
)
from screen_copier.stitching.normalize import lines_match
from screen_copier.stitching.stitcher import Stitcher, issue_count, render, stitch


def _distinct_source(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    words = [
        "alpha", "beta", "gamma", "delta", "items", "result", "value", "config", "parser",
        "render", "window", "buffer", "socket", "cursor", "matrix", "vector", "token", "frame",
    ]  # fmt: skip
    lines: list[str] = []
    while len(lines) < count:
        a, b, c = rng.sample(words, 3)
        candidate = f"{a} = {b}.{c}({rng.randint(0, 9999)})"
        if not any(lines_match(candidate, other, 0.90) for other in lines):
            lines.append(candidate)
    return lines


SOURCE = _distinct_source(120, seed=3)
"""Distinctive lines, no two of which match. ``src(a, b)`` is lines a..b (1-based)."""


def src(first: int, last: int) -> list[str]:
    return SOURCE[first - 1 : last]


def line(
    text: str,
    *,
    partial: bool = False,
    cut: bool = False,
    uncertain: bool = False,
    number: int | None = None,
) -> RecognizedLine:
    return RecognizedLine(
        text=text,
        line_number=number,
        partial=partial,
        cut_off_right=cut,
        uncertain=uncertain,
        uncertain_note=None,
    )


def frame(
    texts: Sequence[str | RecognizedLine],
    *,
    word_wrap: bool = False,
    numbered: bool = False,
) -> FrameResult:
    return FrameResult(
        has_line_numbers=numbered,
        language_guess="python",
        word_wrap_detected=word_wrap,
        lines=[t if isinstance(t, RecognizedLine) else line(t) for t in texts],
    )


def kinds(events: Sequence[object]) -> list[StitchEventKind]:
    return [e.kind for e in events]  # type: ignore[attr-defined]


def body(text: str) -> list[str]:
    return text.splitlines()


def marker(a: int, b: int) -> str:
    return f"<<<SCREEN-COPIER GAP: unknown lines between capture {a} and {b}>>>"


@pytest.fixture
def stitcher() -> Stitcher:
    return Stitcher(Settings())


# --------------------------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------------------------


def test_empty_document_renders_empty(stitcher: Stitcher) -> None:
    assert stitcher.render() == ""
    assert stitcher.issue_count() == 0
    assert stitcher.document().segments == []


def test_first_frame_starts_segment_zero_without_events(stitcher: Stitcher) -> None:
    assert stitcher.add(1, frame(src(1, 20))) == []
    assert body(stitcher.render()) == src(1, 20)
    assert len(stitcher.document().segments) == 1


def test_render_format(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(["a = 1", "", "    b = 2"]))
    assert stitcher.render() == "a = 1\n\n    b = 2\n"


def test_scroll_down_with_three_line_overlap(stitcher: Stitcher) -> None:
    """REQ-STI-05 acceptance: each frame overlaps the previous by 3 → no dup, no missing."""
    windows = [(1, 25), (23, 47), (45, 69), (67, 91), (89, 113)]
    for seq, (a, b) in enumerate(windows, start=1):
        assert stitcher.add(seq, frame(src(a, b))) == []
    assert body(stitcher.render()) == src(1, 113)


def test_scroll_up_head_extension(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(40, 60)))
    stitcher.add(2, frame(src(20, 42)))
    assert stitcher.add(3, frame(src(1, 22))) == []
    assert body(stitcher.render()) == src(1, 60)


def test_recapture_is_contained(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 30)))
    stitcher.add(2, frame(src(10, 20)))
    document = stitcher.document()
    assert body(render(document)) == src(1, 30)
    line_15 = document.segments[0].lines[14]
    assert [o.frame_seq for o in line_15.observations] == [1, 2]


def test_partial_lines_never_reach_the_document(stitcher: Stitcher) -> None:
    """REQ-STI-02 acceptance: half-visible `def fo` then `def foo():` in full → once."""
    stitcher.add(1, frame(["import os", "", "x = os.getcwd()", line("def fo", partial=True)]))
    stitcher.add(
        2,
        frame(
            [
                line("x = os.getcvvd()", partial=True),
                "",
                "def foo():",
                "    return x",
                "",
                "def bar():",
            ]
        ),
    )
    rendered = body(stitcher.render())
    assert rendered.count("def foo():") == 1
    assert "def fo" not in rendered


def test_frame_with_only_partial_lines_changes_nothing(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 10)))
    assert stitcher.add(2, frame([line("garbled", partial=True)])) == []
    assert stitcher.add(3, frame([])) == []
    assert body(stitcher.render()) == src(1, 10)


def test_frames_must_arrive_in_capture_order(stitcher: Stitcher) -> None:
    stitcher.add(2, frame(src(1, 10)))
    with pytest.raises(ValueError):
        stitcher.add(2, frame(src(5, 15)))
    with pytest.raises(ValueError):
        stitcher.add(1, frame(src(5, 15)))


# --------------------------------------------------------------------------------------------
# Gaps, ambiguity and bridging (REQ-STI-06, -07)
# --------------------------------------------------------------------------------------------


def test_no_overlap_creates_a_gap(stitcher: Stitcher) -> None:
    """REQ-STI-06 acceptance: a frame sharing no lines → gap marker and gap warning."""
    stitcher.add(1, frame(src(1, 20)))
    assert kinds(stitcher.add(2, frame(src(40, 60)))) == [StitchEventKind.GAP_DETECTED]
    assert body(stitcher.render()) == [*src(1, 20), marker(1, 2), *src(40, 60)]
    document = stitcher.document()
    assert [g.kind for g in document.gaps] == [GapKind.OVERLAP]
    assert document.gaps[0].before_segment == 1
    assert stitcher.issue_count() == 1


def test_unplaced_frame_goes_after_the_segment_of_the_previous_frame(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 20)))
    stitcher.add(2, frame(src(60, 80)))  # new segment 1
    stitcher.add(3, frame(src(18, 30)))  # extends segment 0
    stitcher.add(4, frame(src(100, 110)))  # unplaced: right after segment 0, not at the end
    assert body(stitcher.render()) == [
        *src(1, 30),
        marker(3, 4),
        *src(100, 110),
        marker(4, 2),
        *src(60, 80),
    ]


def test_bridge_closes_the_gap(stitcher: Stitcher) -> None:
    """REQ-STI-07 acceptance: a frame overlapping both sides → marker gone, count down."""
    stitcher.add(1, frame(src(1, 20)))
    stitcher.add(2, frame(src(30, 50)))
    assert stitcher.issue_count() == 1
    events = stitcher.add(3, frame(src(17, 34)))
    assert kinds(events) == [StitchEventKind.GAP_CLOSED]
    assert body(stitcher.render()) == src(1, 50)
    assert stitcher.document().gaps == []
    assert stitcher.issue_count() == 0


def test_bridge_from_below(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 20)))
    stitcher.add(2, frame(src(30, 50)))
    # Overlaps segment 1 (30-35, score 6) more than segment 0 (18-20, score 3).
    assert kinds(stitcher.add(3, frame(src(18, 35)))) == [StitchEventKind.GAP_CLOSED]
    assert body(stitcher.render()) == src(1, 50)


def test_one_frame_can_close_two_gaps(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 20)))
    stitcher.add(2, frame(src(30, 40)))
    stitcher.add(3, frame(src(50, 60)))  # after segment of frame 2
    assert len(stitcher.document().gaps) == 2
    # Overlaps segment 0 by 4 and segment 2 by 3, so it extends segment 0. Segment 1 is then
    # contained in it, and segment 2 a tail extension of the result: both merge.
    events = stitcher.add(4, frame(src(17, 52)))
    assert kinds(events) == [StitchEventKind.GAP_CLOSED]  # one event per kind per frame
    assert body(stitcher.render()) == src(1, 60)


def test_equal_overlap_with_two_segments_is_ambiguous(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 20)))
    stitcher.add(2, frame(src(30, 40)))
    stitcher.add(3, frame(src(50, 60)))
    # 3 lines with segment 0 and 3 with segment 2: same score, so don't guess.
    events = stitcher.add(4, frame(src(18, 52)))
    assert kinds(events) == [StitchEventKind.AMBIGUOUS_OVERLAP, StitchEventKind.GAP_DETECTED]


def test_ambiguous_frame_is_not_placed(stitcher: Stitcher) -> None:
    """REQ-STI-06 acceptance: only braces and blanks that fit two places → ambiguity."""
    block = ["def f():", "    if x:", "        y()", "        });", "    });", "", "});"]
    stitcher.add(1, frame([*block, "a = 1", *block, "b = 2"]))
    events = stitcher.add(2, frame(["        });", "    });", "", "});"]))
    assert kinds(events) == [StitchEventKind.AMBIGUOUS_OVERLAP, StitchEventKind.GAP_DETECTED]
    rendered = body(stitcher.render())
    assert rendered[-6:] == ["b = 2", marker(1, 2), "        });", "    });", "", "});"]


def test_superset_frame_is_not_placed_on_a_short_segment(stitcher: Stitcher) -> None:
    """§9.2 rule 3: a frame extending past both ends of a segment isn't a valid shape."""
    stitcher.add(1, frame(src(10, 12)))
    events = stitcher.add(2, frame(src(5, 20)))
    assert kinds(events) == [StitchEventKind.GAP_DETECTED]


# --------------------------------------------------------------------------------------------
# Observations and flags (REQ-STI-08)
# --------------------------------------------------------------------------------------------


def test_misreading_still_places_and_conflicts(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 20)))
    misread = src(15, 30)
    misread[2] = misread[2].replace("(", "{", 1)  # source line 17: one character differs
    assert lines_match(misread[2], SOURCE[16], 0.90)
    stitcher.add(2, frame(misread))
    document = stitcher.document()
    line_17 = document.segments[0].lines[16]
    assert line_17.text == misread[2]  # latest wins
    assert line_17.flags == {DocLineFlag.CONFLICT}
    assert line_17.alternatives == [misread[2], SOURCE[16]]
    assert stitcher.issue_count() == 1


def test_uncertain_line_flag_counts_as_issue(stitcher: Stitcher) -> None:
    texts: list[str | RecognizedLine] = list(src(1, 10))
    texts[3] = line(SOURCE[3], uncertain=True)
    stitcher.add(1, frame(texts))
    lines = stitcher.document().segments[0].lines
    assert lines[3].flags == {DocLineFlag.UNCERTAIN}
    assert stitcher.issue_count() == 1


def test_cut_off_flag_and_one_event_per_frame(stitcher: Stitcher) -> None:
    texts: list[str | RecognizedLine] = list(src(1, 10))
    texts[2] = line(SOURCE[2], cut=True)
    texts[5] = line(SOURCE[5], cut=True)
    assert kinds(stitcher.add(1, frame(texts))) == [StitchEventKind.CUT_OFF]
    lines = stitcher.document().segments[0].lines
    assert lines[2].flags == {DocLineFlag.CUT_OFF}
    assert lines[5].flags == {DocLineFlag.CUT_OFF}
    # A later full reading doesn't clear it: any observation with cut_off_right sets the flag.
    assert stitcher.add(2, frame(src(1, 12))) == []
    assert DocLineFlag.CUT_OFF in stitcher.document().segments[0].lines[2].flags


def test_partial_cut_off_line_emits_nothing(stitcher: Stitcher) -> None:
    events = stitcher.add(1, frame([*src(1, 5), line("garbled", partial=True, cut=True)]))
    assert events == []


def test_word_wrap_detected_emits_word_wrap(stitcher: Stitcher) -> None:
    assert kinds(stitcher.add(1, frame(src(1, 10), word_wrap=True))) == [StitchEventKind.WORD_WRAP]


# --------------------------------------------------------------------------------------------
# Determinism, discard, document copies
# --------------------------------------------------------------------------------------------


def _scenario() -> list[tuple[int, FrameResult]]:
    misread = src(15, 35)
    misread[4] = line(misread[4].replace("(", "{", 1), uncertain=True)  # type: ignore[call-overload]
    return [
        (1, frame(src(1, 20))),
        (2, frame(src(45, 70))),
        (3, frame(misread)),
        (4, frame(["$ ls -la", "total 48", "drwxr-xr-x  5 user"])),
        (5, frame(src(30, 48))),
        (6, frame(src(68, 90))),
    ]


def test_incremental_equals_batch() -> None:
    frames = _scenario()
    stitcher = Stitcher(Settings())
    events = []
    for seq, result in frames:
        events.extend(stitcher.add(seq, result))
    output = stitch(frames, Settings())
    assert stitcher.document() == output.document
    assert events == output.events
    assert stitcher.render() == render(output.document)


def test_stitch_is_deterministic() -> None:
    assert stitch(_scenario(), Settings()) == stitch(_scenario(), Settings())


@pytest.mark.parametrize("discard", [1, 2, 3, 4, 5, 6])
def test_discard_equals_never_captured(discard: int) -> None:
    """REQ-STI-10: after discarding, the document equals one built without that frame."""
    frames = _scenario()
    remaining = [(seq, r) for seq, r in frames if seq != discard]
    first = stitch(remaining, Settings())
    again = Stitcher(Settings())
    for seq, result in remaining:
        again.add(seq, result)
    assert again.document() == first.document
    assert all(
        obs.frame_seq != discard
        for segment in first.document.segments
        for doc_line in segment.lines
        for obs in doc_line.observations
    )


def test_discard_junk_frame_removes_the_gap() -> None:
    frames = [
        (1, frame(src(1, 30))),
        (2, frame(["PS C:\\> dir", "Mode  LastWriteTime", "d----  01/01/2026"])),
        (3, frame(src(27, 55))),
    ]
    assert len(stitch(frames, Settings()).document.gaps) == 1
    output = stitch([frames[0], frames[2]], Settings())
    assert body(render(output.document)) == src(1, 55)
    assert output.events == []


def test_document_is_a_fresh_copy(stitcher: Stitcher) -> None:
    stitcher.add(1, frame(src(1, 10)))
    document = stitcher.document()
    document.segments[0].lines[0].text = "tampered"
    document.segments.clear()
    assert body(stitcher.render()) == src(1, 10)
    assert stitcher.document().segments[0].lines[0].text == SOURCE[0]


def test_settings_are_used() -> None:
    a = frame(src(1, 10))
    b = frame(src(9, 20))  # 2 lines of overlap
    assert stitch([(1, a), (2, b)], Settings()).events == []
    strict = Settings(min_overlap_lines=3)
    assert kinds(stitch([(1, a), (2, b)], strict).events) == [StitchEventKind.GAP_DETECTED]


def test_issue_count_function() -> None:
    output = stitch(_scenario(), Settings())
    flagged = sum(1 for s in output.document.segments for ln in s.lines if ln.flags)
    assert issue_count(output.document) == flagged + len(output.document.gaps)


# --------------------------------------------------------------------------------------------
# Line-number extension point (TASK-09)
# --------------------------------------------------------------------------------------------


def test_numbered_frames_use_overlap_mode_until_task_09(stitcher: Stitcher) -> None:
    numbered = frame([line(t, number=i) for i, t in enumerate(src(1, 10), start=1)], numbered=True)
    assert stitcher.uses_line_numbers(numbered, numbered.lines) is False
    stitcher.add(1, numbered)
    assert body(stitcher.render()) == src(1, 10)


def test_uses_line_numbers_hook_routes_frames() -> None:
    calls: list[int] = []

    class Numbered(Stitcher):
        def uses_line_numbers(self, result, lines):  # type: ignore[no-untyped-def]
            return result.has_line_numbers

        def _add_numbered(self, seq, result, lines, kinds):  # type: ignore[no-untyped-def]
            calls.append(seq)
            kinds.append(StitchEventKind.WORD_WRAP)

    stitcher = Numbered(Settings())
    events = stitcher.add(1, frame(src(1, 5), numbered=True, word_wrap=True))
    assert calls == [1]
    assert kinds(events) == [StitchEventKind.WORD_WRAP]  # deduplicated
    stitcher.add(2, frame(src(1, 5)))
    assert calls == [1]


# --------------------------------------------------------------------------------------------
# Purity and performance
# --------------------------------------------------------------------------------------------


def test_stitching_does_not_import_pyside6() -> None:
    code = (
        "import sys\n"
        "import screen_copier.stitching.stitcher\n"
        "bad = [m for m in sys.modules if m.split('.')[0] == 'PySide6']\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def _random_source(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    words = ["alpha", "beta", "gamma", "delta", "items", "result", "value", "config", "self"]
    lines = []
    for i in range(count):
        indent = " " * (4 * rng.randint(0, 3))
        kind = rng.random()
        if kind < 0.12:
            lines.append("")
        elif kind < 0.2:
            lines.append(indent + rng.choice(["}", "});", "pass", "return None"]))
        else:
            a, b = rng.sample(words, 2)
            lines.append(f"{indent}{a}_{i} = {b}.get({rng.randint(0, 999)})")
    return lines


@pytest.mark.perf
def test_add_frame_to_2000_line_document_is_fast() -> None:
    """REQ-STI-11 / REQ-NFR-02: adding a 60-line frame to a 2,000-line doc takes ≤200 ms."""
    source = _random_source(2400, seed=11)
    stitcher = Stitcher(Settings())
    seq = 0
    start = 0
    while start + 60 < 2000:
        seq += 1
        stitcher.add(seq, frame(source[start : start + 60]))
        start += 55
    seq += 1
    stitcher.add(seq, frame(source[1940:2000]))
    assert body(stitcher.render()) == source[:2000]

    timings = []
    start = 1995
    for _ in range(5):
        seq += 1
        result = frame(source[start : start + 60])
        t0 = time.perf_counter()
        stitcher.add(seq, result)
        stitcher.document()
        timings.append(time.perf_counter() - t0)
        start += 55
    assert body(stitcher.render()) == source[: start + 5]
    assert statistics.median(timings) <= 0.200, timings
