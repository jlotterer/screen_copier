"""Line-number mode (architecture §9.3): place lines by the editor line numbers Claude reports.

Pure logic: must not import PySide6. The stitcher keeps every line placed by number in a map
from a *key* to its DocLine. A key is ``(n, j)``:

- ``(n, 0)`` is editor line ``n`` (``n >= 1``).
- ``(n, j)`` with ``j >= 1`` is the ``j``-th soft-wrap continuation after line ``n`` (the
  architecture's ``n + 0.1``, ``n + 0.2``, ... without float rounding or a limit of 9).
- ``(0, j)`` holds the rare lines an unnumbered frame aligns *above* line 1; ``j`` may be zero
  or negative so they keep their order.

Keys sort in document order. Contiguous runs of line numbers form segments; every missing range
of numbers between them, and before the first one, is a ``line_numbers`` gap.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from screen_copier.models import FrameResult, RecognizedLine

Key = tuple[int, int]
"""``(line number, continuation index)``; see the module docstring."""

EN_DASH = chr(0x2013)  # ruff flags the literal


def uses_line_numbers(result: FrameResult, lines: Sequence[RecognizedLine]) -> bool:
    """Whether a frame is placed by line number (§9.3) rather than by overlap (§9.2).

    ``lines`` is the frame after the partial-line pre-filter. It needs ``has_line_numbers``
    and at least two of those lines carrying a ``line_number``.
    """
    if not result.has_line_numbers:
        return False
    numbered = 0
    for line in lines:
        if not line.partial and line.line_number is not None:
            numbered += 1
            if numbered >= 2:
                return True
    return False


@dataclass(frozen=True)
class Placement:
    key: Key
    line: RecognizedLine
    soft_wrap: bool
    """True for a line without its own number: a continuation of the line above."""


def place_frame(lines: Sequence[RecognizedLine]) -> list[Placement]:
    """Key every non-partial line of a numbered frame, top to bottom.

    A line with ``line_number = null`` continues the preceding numbered line. Unnumbered lines
    above the frame's first numbered line ``m`` continue line ``m - 1`` (scrolled out of view).
    Two lines of one frame claiming the same number are both kept: the second becomes a
    continuation, so no reading is silently merged into another.
    """
    full = [line for line in lines if not line.partial]
    first = next((line.line_number for line in full if line.line_number is not None), None)
    if first is None:
        return []
    base = first - 1
    used: set[Key] = set()
    placements: list[Placement] = []
    j = 0
    for line in full:
        if line.line_number is not None and (line.line_number, 0) not in used:
            base, j = line.line_number, 0
            soft_wrap = False
        else:
            j += 1
            soft_wrap = line.line_number is None
        key = (base, j)
        used.add(key)
        placements.append(Placement(key, line, soft_wrap))
    return placements


def keys_below(last: Key, count: int) -> list[Key]:
    """Keys for ``count`` lines aligned directly below the line keyed ``last``."""
    return [(last[0] + i, 0) for i in range(1, count + 1)]


def keys_above(first: Key, count: int) -> list[Key]:
    """Keys for ``count`` lines aligned directly above the line keyed ``first``, top to bottom."""
    keys: list[Key] = []
    n, j = first
    for _ in range(count):
        if n == 0 or j > 0:
            j -= 1  # the line itself (j == 1 -> j == 0), or the next slot above line 1
        elif n > 1:
            n -= 1
        else:  # above line 1
            n, j = 0, 0
        keys.append((n, j))
    keys.reverse()
    return keys


def anchor(key: Key, numbers: Collection[int]) -> int:
    """The editor line a key belongs to for grouping and gaps.

    A continuation belongs to its line ``n`` if that line was seen; otherwise (an orphaned
    continuation at the top of a frame, or a line above line 1) it sits just before ``n + 1``.
    """
    n, j = key
    if j == 0 or n in numbers:
        return max(n, 1)
    return n + 1


def runs(keys: Iterable[Key]) -> list[list[Key]]:
    """Group keys into runs of contiguous line numbers, in document order."""
    ordered = sorted(keys)
    numbers = {n for n, j in ordered if j == 0 and n >= 1}
    groups: list[list[Key]] = []
    previous: int | None = None
    for key in ordered:
        current = anchor(key, numbers)
        if previous is None or current > previous + 1:
            groups.append([])
        groups[-1].append(key)
        previous = current
    return groups


def first_line_number(run: Sequence[Key]) -> int:
    """The editor line of a run's first DocLine (continuations then follow that number)."""
    return max(run[0][0], 1)


def missing_before(run: Sequence[Key], previous: Sequence[Key] | None, keys: Iterable[Key]) -> str:
    """The gap description for the numbers missing just above ``run``, or "" if none are.

    ``previous`` is the numbered run rendered above it (None for the first one, where the gap
    starts at line 1). ``keys`` is every numbered key, to tell orphaned continuations apart.
    """
    numbers = {n for n, j in keys if j == 0 and n >= 1}
    first = 1 if previous is None else anchor(previous[-1], numbers) + 1
    last = anchor(run[0], numbers) - 1
    return gap_description(first, last) if first <= last else ""


def gap_description(first: int, last: int) -> str:
    """``lines X-Y missing``, joined with an en dash (§11)."""
    return f"lines {first}{EN_DASH}{last} missing"
