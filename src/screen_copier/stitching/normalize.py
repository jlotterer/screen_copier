"""Line normalization and fuzzy matching (architecture §9.1).

Pure logic: must not import PySide6. Normalized text is used only for matching; the document
always keeps the original text.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache

MIN_DISTINCTIVE_CHARS = 3
"""A line is distinctive when it has at least this many non-space characters (§9.1)."""

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Strip the line, then collapse each internal whitespace run to one space."""
    return _WHITESPACE_RUN.sub(" ", text.strip())


def is_distinctive(text: str) -> bool:
    """True if the line has enough non-space characters to anchor a placement (§9.1)."""
    return is_distinctive_normalized(normalize(text))


def is_distinctive_normalized(norm: str) -> bool:
    """``is_distinctive`` for text that is already normalized."""
    return len(norm.replace(" ", "")) >= MIN_DISTINCTIVE_CHARS


def lines_match(a: str, b: str, similarity: float) -> bool:
    """True if both lines are blank, or their normalized similarity ratio is ≥ ``similarity``."""
    return normalized_match(normalize(a), normalize(b), similarity)


@lru_cache(maxsize=1 << 16)
def normalized_match(a: str, b: str, similarity: float) -> bool:
    """``lines_match`` for text that is already normalized.

    Cached: during stitching the same document lines are compared again on every add. Two
    cheap upper bounds on ``SequenceMatcher.ratio`` reject most pairs first; they are the
    same bounds as ``real_quick_ratio`` and ``quick_ratio``, without building a matcher, and
    never reject a pair that ``ratio`` would accept.
    """
    if a == b:
        return True  # includes both blank
    total = len(a) + len(b)
    if 2.0 * min(len(a), len(b)) / total < similarity:  # real_quick_ratio
        return False
    counts_a = _char_counts(a)
    counts_b = _char_counts(b)
    common = sum(min(n, counts_b[ch]) for ch, n in counts_a.items() if ch in counts_b)
    if 2.0 * common / total < similarity:  # quick_ratio
        return False
    return SequenceMatcher(None, a, b).ratio() >= similarity


@lru_cache(maxsize=1 << 14)
def _char_counts(text: str) -> Counter[str]:
    return Counter(text)
