"""Observation voting: pick the text of a document line from every frame's reading (§9.4).

Pure logic: must not import PySide6.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from screen_copier.models import DocLineFlag, Observation


@dataclass(frozen=True)
class Resolution:
    text: str
    flags: frozenset[DocLineFlag] = field(default_factory=frozenset)
    alternatives: tuple[str, ...] = ()
    """Set only for a conflict: the distinct texts, most frequent first, then most recent."""


def resolve(observations: Sequence[Observation]) -> Resolution:
    """Resolve one line's observations (§9.4 steps 1 to 5, plus the uncertain and cut_off flags).

    1. Group by exact text.
    2. If any observation is certain, drop the uncertain ones.
    3. If ≥3 remain and one text has a strict majority, use it (no conflict).
    4. Otherwise use the latest observation (highest ``frame_seq``).
    5. If step 4 had to choose between distinct texts, flag a conflict and keep them all as
       alternatives, ordered by frequency, then recency.
    """
    if not observations:
        raise ValueError("a document line needs at least one observation")

    certain = [obs for obs in observations if not obs.uncertain]
    remaining = certain or list(observations)

    counts = Counter(obs.text for obs in remaining)
    recency: dict[str, int] = {}
    for obs in remaining:
        recency[obs.text] = max(recency.get(obs.text, 0), obs.frame_seq)

    majority: str | None = None
    if len(remaining) >= 3:
        text, count = counts.most_common(1)[0]
        if count * 2 > len(remaining):
            majority = text

    if majority is not None:
        chosen = _latest(obs for obs in remaining if obs.text == majority)
    else:
        chosen = _latest(remaining)

    flags: set[DocLineFlag] = set()
    alternatives: tuple[str, ...] = ()
    if majority is None and len(counts) > 1:
        flags.add(DocLineFlag.CONFLICT)
        alternatives = tuple(sorted(counts, key=lambda t: (-counts[t], -recency[t])))
    if chosen.uncertain:
        flags.add(DocLineFlag.UNCERTAIN)
    if any(obs.cut_off_right for obs in observations):
        flags.add(DocLineFlag.CUT_OFF)
    return Resolution(chosen.text, frozenset(flags), alternatives)


def _latest(observations: Iterable[Observation]) -> Observation:
    """The observation with the highest ``frame_seq``; on a tie, the last one given."""
    latest: Observation | None = None
    for obs in observations:
        if latest is None or obs.frame_seq >= latest.frame_seq:
            latest = obs
    if latest is None:
        raise ValueError("no observations")
    return latest
