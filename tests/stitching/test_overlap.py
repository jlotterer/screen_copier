"""Candidate validation, scoring and choice (§9.2)."""

from __future__ import annotations

from screen_copier.stitching.overlap import (
    Candidate,
    MatchLine,
    choose,
    find_candidates,
    has_valid_shape,
    score_offset,
)

KW = {"min_overlap_lines": 2, "line_similarity": 0.90}


def ml(*texts: str) -> list[MatchLine]:
    return [MatchLine.of(t) for t in texts]


SEG = ml(*(f"line_{i:02d} = value_{i}" for i in range(10)))


def test_shapes() -> None:
    assert has_valid_shape(0, 3, 10)  # containment at the top
    assert has_valid_shape(7, 3, 10)  # containment at the bottom
    assert has_valid_shape(8, 3, 10)  # tail extension
    assert has_valid_shape(9, 3, 10)
    assert not has_valid_shape(10, 3, 10)  # no overlap
    assert has_valid_shape(-2, 3, 10)  # head extension
    assert not has_valid_shape(-3, 3, 10)  # no overlap
    assert not has_valid_shape(-1, 12, 10)  # covers the segment and extends both ways


def test_tail_extension() -> None:
    frame = [*SEG[7:], *ml("new_a = 1", "new_b = 2")]
    assert find_candidates(frame, SEG, **KW) == [(7, 3)]


def test_head_extension() -> None:
    frame = [*ml("new_a = 1", "new_b = 2"), *SEG[:3]]
    assert find_candidates(frame, SEG, **KW) == [(-2, 3)]


def test_containment() -> None:
    assert find_candidates(SEG[3:6], SEG, **KW) == [(3, 3)]


def test_min_overlap_counts_only_distinctive_matches() -> None:
    seg = ml("alpha = 1", "beta = 2", "}", "", "}")
    frame = ml("}", "", "}", "gamma = 3")
    # Offset 2 overlaps 3 matching lines, but none is distinctive.
    assert score_offset(frame, seg, 2, **KW) is None
    frame2 = ml("beta = 2", "}", "", "}", "gamma = 3")
    assert score_offset(frame2, seg, 1, **KW) is None  # 4 matches, only 1 distinctive
    assert score_offset(frame2, seg, 1, min_overlap_lines=1, line_similarity=0.9) == 4


def test_ninety_percent_rule() -> None:
    seg = ml(*(f"statement_{i} = compute({i})" for i in range(20)))
    frame = list(seg[:10])
    frame[4] = MatchLine.of("something entirely different")
    assert score_offset(frame, seg, 0, **KW) == 9  # 9 of 10 = 90%: valid
    frame[5] = MatchLine.of("another unrelated line here")
    assert score_offset(frame, seg, 0, **KW) is None  # 8 of 10: invalid


def test_overlap_shorter_than_min_is_invalid() -> None:
    frame = [SEG[9], *ml("new = 1")]
    assert score_offset(frame, SEG, 9, **KW) is None


def test_fuzzy_matches_count() -> None:
    frame = ml("line_07 = vaIue_7", "line_08 = value_8", "line_09 = value_9", "next = 1")
    assert find_candidates(frame, SEG, **KW) == [(7, 3)]


def test_empty_inputs() -> None:
    assert find_candidates([], SEG, **KW) == []
    assert find_candidates(SEG, [], **KW) == []


def test_choose_best() -> None:
    choice = choose([Candidate(0, 5, 3), Candidate(1, 0, 4)])
    assert choice.best == Candidate(1, 0, 4)
    assert not choice.ambiguous


def test_choose_tie_is_ambiguous() -> None:
    choice = choose([Candidate(0, 5, 4), Candidate(0, 20, 4), Candidate(1, 0, 2)])
    assert choice.best is None
    assert choice.ambiguous


def test_choose_tie_below_best_is_not_ambiguous() -> None:
    choice = choose([Candidate(0, 5, 2), Candidate(0, 20, 2), Candidate(1, 0, 4)])
    assert choice.best == Candidate(1, 0, 4)


def test_choose_nothing() -> None:
    choice = choose([])
    assert choice.best is None
    assert not choice.ambiguous
