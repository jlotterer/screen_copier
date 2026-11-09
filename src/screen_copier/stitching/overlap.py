"""Overlap mode: place a frame by matching its lines with a segment's lines (§9.2).

Pure logic: must not import PySide6.

A candidate is an offset ``o`` aligning frame line ``F[i]`` with segment line ``S[o + i]``. The
overlap is the set of indices where both exist. A candidate is valid when:

1. the overlap has ≥ ``min_overlap_lines`` matching lines that are distinctive on both sides,
2. ≥ ``MIN_MATCH_PERCENT`` of the overlapped lines match, and
3. it is a tail extension, a head extension or a containment (the frame never covers the whole
   segment *and* extends past both of its ends).

Its score is the number of matching lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from screen_copier.stitching.normalize import (
    is_distinctive_normalized,
    normalize,
    normalized_match,
)

MIN_MATCH_PERCENT = 90
"""Rule 2 of §9.2: at least this share of the overlapped lines must match. A fixed part of the
validity rule, not a user setting (§8.1 has no key for it)."""


@dataclass(frozen=True, slots=True)
class MatchLine:
    """The parts of a line that matching needs, computed once."""

    norm: str
    distinctive: bool

    @classmethod
    def of(cls, text: str) -> MatchLine:
        norm = normalize(text)
        return cls(norm, is_distinctive_normalized(norm))


@dataclass(frozen=True, slots=True)
class Candidate:
    segment: int
    """Index of the segment in the document."""
    offset: int
    score: int


@dataclass(frozen=True, slots=True)
class Choice:
    best: Candidate | None
    """The winning candidate; None if there is no valid candidate or the choice is ambiguous."""
    ambiguous: bool = False


def has_valid_shape(offset: int, frame_len: int, segment_len: int) -> bool:
    """Rule 3 of §9.2: tail extension, head extension or containment."""
    end = offset + frame_len
    if offset >= 0:
        return offset < segment_len  # tail extension or containment; must overlap
    return 0 < end <= segment_len  # head extension


def score_offset(
    frame: Sequence[MatchLine],
    segment: Sequence[MatchLine],
    offset: int,
    *,
    min_overlap_lines: int,
    line_similarity: float,
) -> int | None:
    """The candidate's score if ``offset`` is valid, else None."""
    if not has_valid_shape(offset, len(frame), len(segment)):
        return None
    lo = max(0, -offset)
    hi = min(len(frame), len(segment) - offset)
    overlap = hi - lo
    if overlap < min_overlap_lines:
        return None
    # Largest number of mismatches that still leaves ≥ MIN_MATCH_PERCENT matching (integer math).
    required_matches = -(-overlap * MIN_MATCH_PERCENT // 100)  # ceil(overlap * 90 / 100)
    allowed_misses = overlap - required_matches
    misses = 0
    matches = 0
    anchors = 0
    for i in range(lo, hi):
        f = frame[i]
        s = segment[offset + i]
        if normalized_match(f.norm, s.norm, line_similarity):
            matches += 1
            if f.distinctive and s.distinctive:
                anchors += 1
        else:
            misses += 1
            if misses > allowed_misses:
                return None
    if anchors < min_overlap_lines:
        return None
    return matches


def find_candidates(
    frame: Sequence[MatchLine],
    segment: Sequence[MatchLine],
    *,
    min_overlap_lines: int,
    line_similarity: float,
) -> list[tuple[int, int]]:
    """Every valid ``(offset, score)`` of ``frame`` against ``segment``, by offset.

    Every offset with a non-empty overlap is checked, because ambiguity detection needs all
    valid candidates, not just the first. Most offsets are rejected after one or two line
    comparisons, so this stays fast on long documents (§9.5, REQ-STI-11).
    """
    if not frame or not segment:
        return []
    found: list[tuple[int, int]] = []
    for offset in range(1 - len(frame), len(segment)):
        score = score_offset(
            frame,
            segment,
            offset,
            min_overlap_lines=min_overlap_lines,
            line_similarity=line_similarity,
        )
        if score is not None:
            found.append((offset, score))
    return found


def choose(candidates: Iterable[Candidate]) -> Choice:
    """Pick the highest-scoring candidate. A tie for the top score is ambiguous (§9.2)."""
    best: Candidate | None = None
    tied = False
    for candidate in candidates:
        if best is None or candidate.score > best.score:
            best = candidate
            tied = False
        elif candidate.score == best.score:
            tied = True
    if best is None:
        return Choice(None)
    if tied:
        return Choice(None, ambiguous=True)
    return Choice(best)
