"""Line-number mode (architecture §9.3, REQ-STI-03, -04, -09) beyond the fixtures."""

from __future__ import annotations

import ast
import random
import statistics
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from screen_copier.config.settings import Settings
from screen_copier.models import (
    DocLine,
    DocLineFlag,
    FrameResult,
    Gap,
    GapKind,
    RecognizedLine,
    StitchEvent,
    StitchEventKind,
)
from screen_copier.stitching import linenum
from screen_copier.stitching.stitcher import GAP_MARKER, Stitcher, render_items, stitch

EN_DASH = chr(0x2013)  # ruff flags the literal


def _source(count: int, seed: int = 5) -> list[str]:
    rng = random.Random(seed)
    words = ["alpha", "beta", "gamma", "delta", "items", "result", "config", "parser", "token"]
    return [
        f"{rng.choice(words)}_{i} = {rng.choice(words)}.{rng.choice(words)}({rng.randint(0, 9999)})"
        for i in range(1, count + 1)
    ]


SOURCE = _source(120)


def src(first: int, last: int) -> list[str]:
    return SOURCE[first - 1 : last]


def line(
    text: str, number: int | None = None, *, partial: bool = False, cut: bool = False
) -> RecognizedLine:
    return RecognizedLine(
        text=text,
        line_number=number,
        partial=partial,
        cut_off_right=cut,
        uncertain=False,
        uncertain_note=None,
    )


def numbered(first: int, last: int) -> list[RecognizedLine]:
    return [line(SOURCE[n - 1], n) for n in range(first, last + 1)]


def frame(
    lines: Sequence[RecognizedLine], *, has_numbers: bool = True, word_wrap: bool = False
) -> FrameResult:
    return FrameResult(
        has_line_numbers=has_numbers,
        language_guess="python",
        word_wrap_detected=word_wrap,
        lines=list(lines),
    )


def unnumbered(first: int, last: int) -> FrameResult:
    return frame([line(t) for t in src(first, last)], has_numbers=False)


def marker(description: str) -> str:
    return GAP_MARKER.format(description=description)


def missing(first: int, last: int) -> str:
    return f"lines {first}{EN_DASH}{last} missing"


def body(stitcher: Stitcher) -> list[str]:
    return stitcher.render().splitlines()


def kinds(events: Sequence[StitchEvent]) -> list[StitchEventKind]:
    return [event.kind for event in events]


def run(frames: Sequence[FrameResult]) -> tuple[Stitcher, list[StitchEvent]]:
    stitcher = Stitcher(Settings())
    events: list[StitchEvent] = []
    for seq, result in enumerate(frames, start=1):
        events.extend(stitcher.add(seq, result))
    batch = stitch(list(enumerate(frames, start=1)), Settings())
    assert batch.document == stitcher.document()  # incremental == batch (REQ-STI-01)
    assert batch.events == events
    return stitcher, events


# --------------------------------------------------------------------------------------------
# When line-number mode applies
# --------------------------------------------------------------------------------------------


def test_one_numbered_line_is_not_enough() -> None:
    result = frame([line(t) for t in src(1, 5)] + [line(SOURCE[5], 6)])
    assert linenum.uses_line_numbers(result, result.lines) is False
    stitcher = Stitcher(Settings())
    assert stitcher.uses_line_numbers(result, result.lines) is False
    stitcher.add(1, result)
    stitcher.add(2, unnumbered(4, 10))  # placed by overlap mode: 4-6 match
    document = stitcher.document()
    assert body(stitcher) == src(1, 10)
    assert [s.first_line_number for s in document.segments] == [None]
    assert document.gaps == []


def test_two_numbered_lines_are_enough() -> None:
    result = frame([line(SOURCE[0], 1), line(SOURCE[1]), line(SOURCE[2], 3)])
    assert linenum.uses_line_numbers(result, result.lines) is True
    assert Stitcher(Settings()).uses_line_numbers(result, result.lines) is True


def test_partial_and_unflagged_frames_dont_count() -> None:
    partial = frame([line(SOURCE[0], 1, partial=True), line(SOURCE[1], 2)])
    assert linenum.uses_line_numbers(partial, partial.lines) is False
    no_flag = frame(numbered(1, 5), has_numbers=False)
    assert linenum.uses_line_numbers(no_flag, no_flag.lines) is False


# --------------------------------------------------------------------------------------------
# Placement and gaps (REQ-STI-03, -04)
# --------------------------------------------------------------------------------------------


def test_numbered_gap_between_40_and_48() -> None:
    stitcher, events = run(
        [
            frame(numbered(1, 22)),
            frame(numbered(19, 40)),
            frame(numbered(48, 70)),
            frame(numbered(66, 90)),
        ]
    )
    document = stitcher.document()
    assert events == []  # line-number gaps emit no event
    assert document.gaps == [
        Gap(kind=GapKind.LINE_NUMBERS, description=missing(41, 47), before_segment=1)
    ]
    assert [s.first_line_number for s in document.segments] == [1, 48]
    assert body(stitcher) == [*src(1, 40), marker(missing(41, 47)), *src(48, 90)]
    assert stitcher.issue_count() == 1


def test_frames_out_of_order_are_placed_by_number() -> None:
    stitcher, _ = run([frame(numbered(30, 50)), frame(numbered(1, 20)), frame(numbered(18, 32))])
    assert body(stitcher) == src(1, 50)
    assert stitcher.document().gaps == []


def test_missing_lines_at_the_top() -> None:
    stitcher, events = run([frame(numbered(5, 30)), frame(numbered(27, 55))])
    document = stitcher.document()
    assert events == []
    assert document.gaps == [
        Gap(kind=GapKind.LINE_NUMBERS, description=missing(1, 4), before_segment=0)
    ]
    assert body(stitcher) == [marker(missing(1, 4)), *src(5, 55)]
    assert isinstance(render_items(document)[0], Gap)


def test_top_gap_closes_when_the_lines_arrive() -> None:
    stitcher, _ = run([frame(numbered(5, 30)), frame(numbered(1, 8))])
    assert stitcher.document().gaps == []
    assert body(stitcher) == src(1, 30)


def test_same_number_resolves_through_voting() -> None:
    wrong = [line(SOURCE[n - 1] if n != 10 else "garbled = 1", n) for n in range(1, 21)]
    stitcher, _ = run([frame(numbered(1, 20)), frame(wrong)])
    target = stitcher.document().segments[0].lines[9]
    assert target.text == "garbled = 1"  # latest wins without a majority (§9.4)
    assert DocLineFlag.CONFLICT in target.flags
    assert target.alternatives == ["garbled = 1", SOURCE[9]]
    assert [obs.frame_seq for obs in target.observations] == [1, 2]


def test_cut_off_in_numbered_frame() -> None:
    lines = numbered(1, 10)
    lines[3] = line(SOURCE[3], 4, cut=True)
    stitcher, events = run([frame(lines)])
    assert kinds(events) == [StitchEventKind.CUT_OFF]
    assert stitcher.document().segments[0].lines[3].flags == {DocLineFlag.CUT_OFF}


# --------------------------------------------------------------------------------------------
# Soft wrap (REQ-STI-09)
# --------------------------------------------------------------------------------------------


def test_word_wrap_detected_emits_one_event() -> None:
    _, events = run([frame(numbered(1, 10), word_wrap=True)])
    assert events == [StitchEvent(kind=StitchEventKind.WORD_WRAP, frame_seq=1)]


def test_continuation_line_after_19() -> None:
    lines = [*numbered(1, 19), line("    continued text"), *numbered(20, 30)]
    stitcher, events = run([frame(lines, word_wrap=True)])
    assert events == [StitchEvent(kind=StitchEventKind.WORD_WRAP, frame_seq=1)]
    segment = stitcher.document().segments[0]
    assert segment.lines[18].text == SOURCE[18]
    assert segment.lines[18].flags == set()
    assert segment.lines[19].text == "    continued text"
    assert segment.lines[19].flags == {DocLineFlag.SOFT_WRAP}
    assert segment.lines[20].text == SOURCE[19]
    assert body(stitcher) == [*src(1, 19), "    continued text", *src(20, 30)]
    assert stitcher.issue_count() == 1


def test_continuation_without_word_wrap_detected_still_warns() -> None:
    lines = [*numbered(1, 5), line("more"), line("and more"), *numbered(6, 8)]
    stitcher, events = run([frame(lines)])
    assert kinds(events) == [StitchEventKind.WORD_WRAP]
    assert body(stitcher) == [*src(1, 5), "more", "and more", *src(6, 8)]


def test_continuation_seen_again_is_one_line() -> None:
    first = [*numbered(1, 10), line("wrapped"), *numbered(11, 20)]
    second = [*numbered(8, 10), line("wrapped"), *numbered(11, 30)]
    stitcher, events = run([frame(first), frame(second)])
    assert body(stitcher) == [*src(1, 10), "wrapped", *src(11, 30)]
    assert kinds(events) == [StitchEventKind.WORD_WRAP, StitchEventKind.WORD_WRAP]
    wrapped = stitcher.document().segments[0].lines[10]
    assert [obs.frame_seq for obs in wrapped.observations] == [1, 2]


def test_continuation_at_the_top_of_a_frame_belongs_to_the_line_above() -> None:
    later = [line("tail of 10"), *numbered(11, 20)]
    stitcher, _ = run([frame(later), frame(numbered(1, 10))])
    assert body(stitcher) == [*src(1, 10), "tail of 10", *src(11, 20)]
    assert stitcher.document().gaps == []


# --------------------------------------------------------------------------------------------
# Unnumbered frames in a numbered document (§9.3, seq_mixed_numbering)
# --------------------------------------------------------------------------------------------


def test_unnumbered_frame_inherits_numbers() -> None:
    stitcher, events = run([frame(numbered(1, 27)), unnumbered(24, 50), frame(numbered(47, 73))])
    assert events == []
    document = stitcher.document()
    assert len(document.segments) == 1
    assert document.segments[0].first_line_number == 1
    assert document.gaps == []
    assert body(stitcher) == src(1, 73)


def test_unnumbered_frame_fills_a_numbered_gap() -> None:
    stitcher, events = run([frame(numbered(1, 40)), frame(numbered(48, 70)), unnumbered(37, 50)])
    assert events == []
    assert stitcher.document().gaps == []
    assert body(stitcher) == src(1, 70)


def test_unnumbered_frame_above_line_one_keeps_its_lines() -> None:
    stitcher, _ = run(
        [
            frame(numbered(1, 20)),
            frame(
                [line("tab bar"), line("breadcrumbs")] + [line(t) for t in src(1, 5)],
                has_numbers=False,
            ),
        ]
    )
    assert body(stitcher) == ["tab bar", "breadcrumbs", *src(1, 20)]
    assert stitcher.document().gaps == []


def test_unplaced_unnumbered_frame_stays_after_its_numbered_segment() -> None:
    stitcher, events = run([frame(numbered(1, 20)), unnumbered(100, 110), frame(numbered(40, 60))])
    assert kinds(events) == [StitchEventKind.GAP_DETECTED]
    document = stitcher.document()
    assert [(g.kind, g.before_segment) for g in document.gaps] == [
        (GapKind.OVERLAP, 1),
        (GapKind.OVERLAP, 2),
        (GapKind.LINE_NUMBERS, 2),
    ]
    assert [s.first_line_number for s in document.segments] == [1, None, 40]
    items = render_items(document)
    assert [type(item) for item in items[19:24]] == [DocLine, Gap, DocLine, DocLine, DocLine]


def test_unnumbered_document_lines_up_with_a_later_numbered_frame() -> None:
    stitcher, events = run([unnumbered(1, 25), frame(numbered(20, 45))])
    assert kinds(events) == [StitchEventKind.GAP_CLOSED]
    document = stitcher.document()
    assert len(document.segments) == 1
    assert document.segments[0].first_line_number == 1
    assert document.gaps == []
    assert body(stitcher) == src(1, 45)


# --------------------------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------------------------


def test_keys_above_and_below() -> None:
    assert linenum.keys_above((5, 0), 2) == [(3, 0), (4, 0)]
    assert linenum.keys_above((5, 2), 2) == [(5, 0), (5, 1)]
    assert linenum.keys_above((2, 0), 3) == [(0, -1), (0, 0), (1, 0)]
    assert linenum.keys_below((7, 3), 2) == [(8, 0), (9, 0)]


def test_runs_and_gap_descriptions() -> None:
    keys = [(1, 0), (2, 0), (2, 1), (5, 1), (6, 0), (9, 0)]
    groups = linenum.runs(keys)
    assert groups == [[(1, 0), (2, 0), (2, 1)], [(5, 1), (6, 0)], [(9, 0)]]
    assert linenum.missing_before(groups[0], None, keys) == ""
    assert linenum.missing_before(groups[1], groups[0], keys) == missing(3, 5)
    assert linenum.missing_before(groups[2], groups[1], keys) == missing(7, 8)
    assert linenum.first_line_number(groups[1]) == 5


def test_duplicate_numbers_in_one_frame_keep_both_lines() -> None:
    lines = [line("a", 1), line("b", 2), line("b again", 2), line("c", 3)]
    placements = linenum.place_frame(lines)
    assert [p.key for p in placements] == [(1, 0), (2, 0), (2, 1), (3, 0)]
    assert [p.soft_wrap for p in placements] == [False, False, False, False]


def test_linenum_does_not_import_pyside6() -> None:
    path = Path(linenum.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [m for m in modules if m.split(".")[0] == "PySide6"]


# --------------------------------------------------------------------------------------------
# Performance (REQ-STI-11)
# --------------------------------------------------------------------------------------------


@pytest.mark.perf
def test_numbered_add_to_2000_line_document_is_fast() -> None:
    source = _source(2400, seed=11)
    stitcher = Stitcher(Settings())

    def window(first: int) -> FrameResult:
        return frame([line(source[n - 1], n) for n in range(first, first + 60)])

    seq = 0
    for first in range(1, 1950, 55):
        seq += 1
        stitcher.add(seq, window(first))
    timings = []
    for first in range(1990, 2265, 55):
        seq += 1
        t0 = time.perf_counter()
        stitcher.add(seq, window(first))
        stitcher.document()
        timings.append(time.perf_counter() - t0)
    assert statistics.median(timings) <= 0.2
