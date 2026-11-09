"""normalize, lines_match and the distinctive-line rule (§9.1)."""

from __future__ import annotations

import random
from difflib import SequenceMatcher

import pytest

from screen_copier.stitching.normalize import is_distinctive, lines_match, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("x", "x"),
        ("    return x", "return x"),
        ("a  =   b", "a = b"),
        ("a\t=\tb  ", "a = b"),
        ("\t  foo(a,   b)\t", "foo(a, b)"),
        ("a \u00a0 b", "a b"),  # non-breaking space is whitespace too
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("", "", True),  # both blank
        ("   ", "", True),  # whitespace-only is blank
        ("", "x", False),
        ("x", "", False),
        ("def foo():", "def foo():", True),
        ("    def foo():", "def foo():", True),  # indentation is ignored for matching
        ("x  =  1", "x = 1", True),  # whitespace runs collapse
        ("total = compute(items)", "totaI = compute(items)", True),  # one char in 22: 0.95
        ("x = 1", "x = l", False),  # one char in 5: 0.80
        ("def fo", "def format_report(rows):", False),  # a clipped line
        ("return a", "return b", False),
    ],
)
def test_lines_match(a: str, b: str, expected: bool) -> None:
    assert lines_match(a, b, 0.90) is expected
    assert lines_match(b, a, 0.90) is expected


def test_lines_match_threshold_is_inclusive() -> None:
    # 10 vs 10 characters with 9 in common: ratio = 2*9/20 = 0.90 exactly.
    assert lines_match("abcdefghij", "abcdefghiX", 0.90)
    assert not lines_match("abcdefghij", "abcdefghiX", 0.91)


def test_lines_match_uses_the_threshold() -> None:
    assert lines_match("x = 1", "x = l", 0.80)
    assert lines_match("abc", "xyz", 0.0)
    assert not lines_match("abc", "abd", 1.0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", False),
        ("    ", False),
        ("}", False),
        ("  })", False),
        ("} )", False),  # spaces don't count
        ("});", True),
        ("    });", True),
        ("x = 1", True),
        ("a b c", True),
        ("ab", False),
    ],
)
def test_is_distinctive(text: str, expected: bool) -> None:
    assert is_distinctive(text) is expected


def test_fast_path_agrees_with_sequence_matcher() -> None:
    """The cheap pre-checks never change the answer of the §9.1 rule."""
    rng = random.Random(7)
    alphabet = "abc de(){}=_;:.,1lI0O"
    for _ in range(3000):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        b = list(a)
        for _ in range(rng.randint(0, 4)):  # a few edits, so many pairs are near the threshold
            if b and rng.random() < 0.5:
                b[rng.randrange(len(b))] = rng.choice(alphabet)
            else:
                b.insert(rng.randint(0, len(b)), rng.choice(alphabet))
        b_text = "".join(b)
        threshold = rng.choice([0.0, 0.5, 0.8, 0.9, 0.95, 1.0])
        na, nb = normalize(a), normalize(b_text)
        expected = (not na and not nb) or SequenceMatcher(None, na, nb).ratio() >= threshold
        assert lines_match(a, b_text, threshold) is expected, (a, b_text, threshold)
