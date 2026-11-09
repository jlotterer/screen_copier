"""Observation voting, one test per §9.4 step (REQ-STI-08)."""

from __future__ import annotations

import pytest

from screen_copier.models import DocLineFlag, Observation
from screen_copier.stitching.resolve import resolve


def obs(seq: int, text: str, *, uncertain: bool = False, cut: bool = False) -> Observation:
    return Observation(frame_seq=seq, text=text, uncertain=uncertain, cut_off_right=cut)


def test_single_observation() -> None:
    r = resolve([obs(1, "x = 1")])
    assert r.text == "x = 1"
    assert r.flags == frozenset()
    assert r.alternatives == ()


def test_no_observations_is_an_error() -> None:
    with pytest.raises(ValueError):
        resolve([])


def test_step1_identical_readings_agree() -> None:
    r = resolve([obs(1, "x = 1"), obs(2, "x = 1")])
    assert r.text == "x = 1"
    assert r.flags == frozenset()


def test_step2_certain_beats_uncertain_even_if_older() -> None:
    """REQ-STI-08 acceptance: uncertain "x = 1" and confident "x = l" → "x = l"."""
    r = resolve([obs(1, "x = l"), obs(2, "x = 1", uncertain=True)])
    assert r.text == "x = l"
    assert r.flags == frozenset()
    assert r.alternatives == ()


def test_step2_all_uncertain_keeps_them_and_flags_uncertain() -> None:
    r = resolve([obs(1, "x = 1", uncertain=True)])
    assert r.text == "x = 1"
    assert r.flags == {DocLineFlag.UNCERTAIN}


def test_step2_all_uncertain_and_differing_is_a_conflict() -> None:
    r = resolve([obs(1, "x = 1", uncertain=True), obs(2, "x = l", uncertain=True)])
    assert r.text == "x = l"
    assert r.flags == {DocLineFlag.UNCERTAIN, DocLineFlag.CONFLICT}
    assert r.alternatives == ("x = l", "x = 1")


def test_step3_strict_majority_beats_recency() -> None:
    r = resolve([obs(1, "a = 1"), obs(2, "a = 1"), obs(3, "a = 7")])
    assert r.text == "a = 1"
    assert r.flags == frozenset()  # a majority settles the line: no conflict
    assert r.alternatives == ()


def test_step3_needs_three_observations() -> None:
    # 2 remain after step 2, so no majority vote: the latest wins and it's a conflict.
    r = resolve([obs(1, "a = 1"), obs(2, "a = 7"), obs(3, "a = 1", uncertain=True)])
    assert r.text == "a = 7"
    assert DocLineFlag.CONFLICT in r.flags


def test_step3_needs_a_strict_majority() -> None:
    r = resolve([obs(1, "a"), obs(2, "a"), obs(3, "b"), obs(4, "b")])
    assert r.text == "b"
    assert r.flags == {DocLineFlag.CONFLICT}
    assert r.alternatives == ("b", "a")  # frequency tie, so recency decides


def test_step4_latest_wins_without_majority() -> None:
    """REQ-STI-08 acceptance: two confident readings that differ → a conflict with both."""
    r = resolve([obs(2, "n = 5"), obs(3, "n = 6")])
    assert r.text == "n = 6"
    assert r.flags == {DocLineFlag.CONFLICT}
    assert r.alternatives == ("n = 6", "n = 5")


def test_step4_latest_is_by_frame_seq_not_list_order() -> None:
    r = resolve([obs(5, "late"), obs(2, "early")])
    assert r.text == "late"


def test_step5_alternatives_by_frequency_then_recency() -> None:
    r = resolve([obs(1, "a"), obs(2, "b"), obs(3, "a"), obs(4, "c"), obs(5, "b")])
    # a: 2 (last 3), b: 2 (last 5), c: 1. No strict majority (2 of 5).
    assert r.text == "b"
    assert r.alternatives == ("b", "a", "c")


def test_step5_alternatives_exclude_dropped_uncertain_readings() -> None:
    r = resolve([obs(1, "a"), obs(2, "b"), obs(3, "zzz", uncertain=True)])
    assert r.alternatives == ("b", "a")


def test_cut_off_flag_from_any_observation() -> None:
    r = resolve([obs(1, "long_line(", cut=True), obs(2, "long_line(")])
    assert r.flags == {DocLineFlag.CUT_OFF}


def test_cut_off_flag_even_from_a_dropped_uncertain_observation() -> None:
    r = resolve([obs(1, "abc"), obs(2, "abc", uncertain=True, cut=True)])
    assert DocLineFlag.CUT_OFF in r.flags
