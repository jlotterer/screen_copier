"""The stitcher: joins recognized frames into one document (architecture §9).

Pure logic: must not import PySide6. ``Stitcher.add`` is the primitive; ``stitch`` is a fold over
it, so building the document one frame at a time and all at once give the same result
(REQ-STI-01). Discarding a frame is a fresh ``stitch`` of the remaining frames (REQ-STI-10).

Line-number mode (§9.3) plugs in through ``Stitcher.uses_line_numbers`` and
``Stitcher._add_numbered`` (TASK-09). Until then every frame goes through overlap mode.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from screen_copier.models import (
    DocLine,
    DocLineFlag,
    Document,
    FrameResult,
    Gap,
    GapKind,
    Observation,
    RecognizedLine,
    Segment,
    StitchEvent,
    StitchEventKind,
)
from screen_copier.stitching.overlap import Candidate, MatchLine, choose, find_candidates
from screen_copier.stitching.resolve import resolve

_BLANK = MatchLine("", False)

GAP_MARKER = "<<<SCREEN-COPIER GAP: {description}>>>"
"""The gap marker line (§11). It is greppable and intentionally breaks compilation."""


class StitchSettings(Protocol):
    """The settings the stitcher reads (§8.1). ``config.settings.Settings`` satisfies it."""

    @property
    def min_overlap_lines(self) -> int: ...

    @property
    def line_similarity(self) -> float: ...


@dataclass(frozen=True)
class StitchOutput:
    document: Document
    events: list[StitchEvent]
    """Every event emitted while adding the frames in order."""


# --------------------------------------------------------------------------------------------
# Internal mutable state. The public pydantic ``Document`` is built from it on demand.
# --------------------------------------------------------------------------------------------


@dataclass(eq=False)
class _Line:
    observations: list[Observation]
    extra_flags: set[DocLineFlag] = field(default_factory=set)
    """Flags that don't come from the observations (``soft_wrap``, set by line-number mode)."""
    text: str = ""
    flags: frozenset[DocLineFlag] = frozenset()
    alternatives: tuple[str, ...] = ()
    match: MatchLine = _BLANK

    def __post_init__(self) -> None:
        self._resolve()

    def absorb(self, other: _Line) -> None:
        """Add another reading of the same document line (a frame line or a merged line)."""
        self.observations.extend(other.observations)
        self.extra_flags |= other.extra_flags
        self._resolve()

    def _resolve(self) -> None:
        resolution = resolve(self.observations)
        if resolution.text != self.text or not self.match.norm:
            self.match = MatchLine.of(resolution.text)
        self.text = resolution.text
        self.flags = resolution.flags | self.extra_flags
        self.alternatives = resolution.alternatives

    def to_model(self) -> DocLine:
        return DocLine.model_construct(
            text=self.text,
            observations=list(self.observations),
            flags=set(self.flags),
            alternatives=list(self.alternatives),
        )


@dataclass(eq=False)
class _Segment:
    lines: list[_Line]
    first_line_number: int | None = None

    def match_lines(self) -> list[MatchLine]:
        return [line.match for line in self.lines]

    def merge(self, offset: int, incoming: Sequence[_Line]) -> None:
        """Lay ``incoming`` over this segment at ``offset`` (same convention as §9.2).

        Overlapping lines absorb the incoming readings; the rest are prepended or appended.
        """
        before: list[_Line] = []
        after: list[_Line] = []
        for i, line in enumerate(incoming):
            j = offset + i
            if j < 0:
                before.append(line)
            elif j >= len(self.lines):
                after.append(line)
            else:
                self.lines[j].absorb(line)
        self.lines[:0] = before
        self.lines.extend(after)


# --------------------------------------------------------------------------------------------
# Stitcher
# --------------------------------------------------------------------------------------------


class Stitcher:
    """Incremental stitcher. Feed recognized frames in capture order with ``add``."""

    def __init__(self, settings: StitchSettings) -> None:
        self._min_overlap_lines = settings.min_overlap_lines
        self._line_similarity = settings.line_similarity
        self._segments: list[_Segment] = []
        self._last_segment: _Segment | None = None
        """The segment that received the previous frame. An unplaced frame goes right after it."""
        self._last_seq = 0
        self._document: Document | None = None

    # -- public API -------------------------------------------------------------------------

    def add(self, seq: int, result: FrameResult) -> list[StitchEvent]:
        """Stitch one recognized frame and return the events it caused (at most one per kind)."""
        if seq <= self._last_seq:
            raise ValueError(f"frames must be added in capture order: {seq} after {self._last_seq}")
        self._last_seq = seq
        self._document = None

        lines = [line for line in result.lines if not line.partial]  # pre-filter (REQ-STI-02)
        kinds: list[StitchEventKind] = []
        if self.uses_line_numbers(result, lines):
            self._add_numbered(seq, result, lines, kinds)
        elif lines:
            self._add_overlap(seq, lines, kinds)
        if any(line.cut_off_right for line in lines):
            kinds.append(StitchEventKind.CUT_OFF)
        if result.word_wrap_detected:
            kinds.append(StitchEventKind.WORD_WRAP)
        return [StitchEvent(kind=kind, frame_seq=seq) for kind in dict.fromkeys(kinds)]

    def document(self) -> Document:
        """The current document, freshly built: mutating it doesn't affect the stitcher."""
        return self._build_document()

    def render(self) -> str:
        return render(self._current_document())

    def issue_count(self) -> int:
        return issue_count(self._current_document())

    # -- line-number mode extension point (TASK-09) -----------------------------------------

    def uses_line_numbers(self, result: FrameResult, lines: Sequence[RecognizedLine]) -> bool:
        """Whether this frame is placed by line number (§9.3) instead of by overlap (§9.2).

        ``lines`` is the frame after the partial-line pre-filter. Extension point for
        TASK-09: until line-number mode lands this returns False, so numbered frames go through
        overlap mode like any other frame.
        """
        return False

    def _add_numbered(
        self,
        seq: int,
        result: FrameResult,
        lines: Sequence[RecognizedLine],
        kinds: list[StitchEventKind],
    ) -> None:
        """Place a numbered frame (§9.3), appending event kinds to ``kinds``. TASK-09."""
        raise NotImplementedError("line-number mode is TASK-09")

    # -- overlap mode (§9.2) ----------------------------------------------------------------

    def _add_overlap(
        self, seq: int, lines: Sequence[RecognizedLine], kinds: list[StitchEventKind]
    ) -> None:
        incoming = [_Line([_observation(seq, line)]) for line in lines]
        if not self._segments:
            self._start_segment(incoming, after=None)
            return

        frame = [line.match for line in incoming]
        candidates = [
            Candidate(index, offset, score)
            for index, segment in enumerate(self._segments)
            for offset, score in self._candidates(frame, segment.match_lines())
        ]
        choice = choose(candidates)
        if choice.best is None:
            if choice.ambiguous:
                kinds.append(StitchEventKind.AMBIGUOUS_OVERLAP)
            self._start_segment(incoming, after=self._last_segment)
            kinds.append(StitchEventKind.GAP_DETECTED)
            return

        segment = self._segments[choice.best.segment]
        segment.merge(choice.best.offset, incoming)
        self._last_segment = segment
        if self._bridge(segment):
            kinds.append(StitchEventKind.GAP_CLOSED)

    def _start_segment(self, lines: list[_Line], *, after: _Segment | None) -> None:
        segment = _Segment(lines)
        index = len(self._segments) if after is None else self._index(after) + 1
        self._segments.insert(index, segment)
        self._last_segment = segment

    def _bridge(self, segment: _Segment) -> bool:
        """Merge ``segment`` with any neighbor that now validly overlaps it (§9.2 bridging).

        The neighbor is treated as the frame. An ambiguous fit doesn't merge. Repeats until no
        neighbor fits, since a merge can bring the next segment into reach.
        """
        merged_any = False
        merged = True
        while merged:
            merged = False
            index = self._index(segment)
            for neighbor_index in (index - 1, index + 1):
                if not 0 <= neighbor_index < len(self._segments):
                    continue
                neighbor = self._segments[neighbor_index]
                choice = choose(
                    Candidate(neighbor_index, offset, score)
                    for offset, score in self._candidates(
                        neighbor.match_lines(), segment.match_lines()
                    )
                )
                if choice.best is None:
                    continue
                segment.merge(choice.best.offset, neighbor.lines)
                del self._segments[neighbor_index]
                if self._last_segment is neighbor:
                    self._last_segment = segment
                merged = merged_any = True
                break
        return merged_any

    def _candidates(
        self, frame: Sequence[MatchLine], segment: Sequence[MatchLine]
    ) -> list[tuple[int, int]]:
        return find_candidates(
            frame,
            segment,
            min_overlap_lines=self._min_overlap_lines,
            line_similarity=self._line_similarity,
        )

    def _index(self, segment: _Segment) -> int:
        for index, candidate in enumerate(self._segments):
            if candidate is segment:
                return index
        raise LookupError("segment is not in the document")

    # -- document ---------------------------------------------------------------------------

    def _current_document(self) -> Document:
        if self._document is None:
            self._document = self._build_document()
        return self._document

    def _build_document(self) -> Document:
        segments: list[Segment] = []
        gaps: list[Gap] = []
        for index, segment in enumerate(self._segments):
            if index > 0:
                above = self._segments[index - 1].lines[-1]
                below = segment.lines[0]
                a = max(obs.frame_seq for obs in above.observations)
                b = min(obs.frame_seq for obs in below.observations)
                gaps.append(
                    Gap(
                        kind=GapKind.OVERLAP,
                        description=f"unknown lines between capture {a} and {b}",
                        before_segment=index,
                    )
                )
            segments.append(
                Segment.model_construct(
                    lines=[line.to_model() for line in segment.lines],
                    first_line_number=segment.first_line_number,
                )
            )
        return Document(segments=segments, gaps=gaps)


def _observation(seq: int, line: RecognizedLine) -> Observation:
    return Observation(
        frame_seq=seq,
        text=line.text,
        uncertain=line.uncertain,
        cut_off_right=line.cut_off_right,
    )


# --------------------------------------------------------------------------------------------
# Batch and document helpers
# --------------------------------------------------------------------------------------------


def stitch(results: Iterable[tuple[int, FrameResult]], settings: StitchSettings) -> StitchOutput:
    """Batch stitch: a fold of ``Stitcher.add`` over the frames in capture order."""
    stitcher = Stitcher(settings)
    events: list[StitchEvent] = []
    for seq, result in results:
        events.extend(stitcher.add(seq, result))
    return StitchOutput(document=stitcher.document(), events=events)


def render_items(document: Document) -> list[DocLine | Gap]:
    """The document in render order: every DocLine, with each Gap where its marker goes."""
    gaps_before: dict[int, list[Gap]] = {}
    for gap in document.gaps:
        gaps_before.setdefault(gap.before_segment, []).append(gap)
    items: list[DocLine | Gap] = []
    for index, segment in enumerate(document.segments):
        items.extend(gaps_before.pop(index, []))
        items.extend(segment.lines)
    for index in sorted(gaps_before):  # gaps after the last segment, if any
        items.extend(gaps_before[index])
    return items


def gap_marker(gap: Gap) -> str:
    return GAP_MARKER.format(description=gap.description)


def render(document: Document) -> str:
    """The document as text: one line per DocLine or gap marker, LF-joined, trailing LF."""
    rendered = [
        gap_marker(item) if isinstance(item, Gap) else item.text for item in render_items(document)
    ]
    return "".join(f"{line}\n" for line in rendered)


def issue_count(document: Document) -> int:
    """Issues for review: every flagged line counts once, plus every gap (REQ-REV-03)."""
    flagged = sum(1 for segment in document.segments for line in segment.lines if line.flags)
    return flagged + len(document.gaps)
