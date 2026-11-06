"""Tests for camera/trigger.py (architecture §5.3, REQ-CAM-06, REQ-CAM-07)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from screen_copier.camera.trigger import (
    NEVER_SETTLES,
    CaptureTrigger,
    TriggerConfig,
    TriggerDecision,
    TriggerState,
)

FRAME_S = 0.033  # ~30 fps
MOVING_DIFF = 10.0
STILL_DIFF = 0.5
SHARP = 200.0
BLURRY = 50.0
NEW_CONTENT = math.inf


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def trigger(clock: FakeClock) -> CaptureTrigger:
    return CaptureTrigger(TriggerConfig(), clock=clock)


def feed(
    trigger: CaptureTrigger,
    clock: FakeClock,
    *,
    frames: int | None = None,
    ms: float | None = None,
    diff_prev: float,
    sharpness: float = SHARP,
    diff_last_capture: float = NEW_CONTENT,
) -> list[TriggerDecision]:
    """Feed identical metrics for ``frames`` frames, or for ``ms`` of frames at ~30 fps."""
    if frames is None:
        assert ms is not None
        frames = int(ms / 1000 / FRAME_S) + 1
    out = []
    for _ in range(frames):
        out.append(trigger.update(diff_prev, sharpness, diff_last_capture))
        clock.advance(FRAME_S)
    return out


def captures(decisions: list[TriggerDecision]) -> int:
    return sum(d.capture for d in decisions)


def settle_into_waiting_focus(trigger: CaptureTrigger, clock: FakeClock) -> None:
    feed(trigger, clock, frames=1, diff_prev=STILL_DIFF, sharpness=BLURRY)
    clock.advance(0.7)
    d = trigger.update(STILL_DIFF, BLURRY, NEW_CONTENT)
    assert d.state is TriggerState.WAITING_FOCUS


# --- Done-when scenarios ---------------------------------------------------------------------


def test_move_settle_captures_once_then_again_after_motion(
    trigger: CaptureTrigger, clock: FakeClock
) -> None:
    """REQ-CAM-06: one capture per settle, none while still, one more after motion."""
    assert captures(feed(trigger, clock, frames=10, diff_prev=MOVING_DIFF)) == 0
    first = feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)
    assert captures(first) == 1
    assert first[-1].state is TriggerState.CAPTURED

    assert captures(feed(trigger, clock, ms=3000, diff_prev=STILL_DIFF)) == 0

    feed(trigger, clock, frames=10, diff_prev=MOVING_DIFF)
    assert captures(feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)) == 1


def test_capture_fires_at_stable_duration_not_before(
    trigger: CaptureTrigger, clock: FakeClock
) -> None:
    trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)  # MOVING -> SETTLING at t0
    clock.advance(0.599)
    assert not trigger.update(STILL_DIFF, SHARP, NEW_CONTENT).capture
    clock.advance(0.001)
    assert trigger.update(STILL_DIFF, SHARP, NEW_CONTENT).capture


def test_still_blurry_image_waits_for_focus(trigger: CaptureTrigger, clock: FakeClock) -> None:
    """REQ-CAM-06: a still but blurry image is not captured; state shows waiting for focus."""
    feed(trigger, clock, frames=10, diff_prev=MOVING_DIFF)
    decisions = feed(trigger, clock, ms=2000, diff_prev=STILL_DIFF, sharpness=BLURRY)
    assert captures(decisions) == 0
    assert decisions[-1].state is TriggerState.WAITING_FOCUS


def test_return_to_previous_view_is_not_captured(trigger: CaptureTrigger, clock: FakeClock) -> None:
    """REQ-CAM-07: settling on content matching the last capture does not capture."""
    feed(trigger, clock, frames=10, diff_prev=MOVING_DIFF)
    assert captures(feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)) == 1
    feed(trigger, clock, frames=10, diff_prev=MOVING_DIFF)
    decisions = feed(trigger, clock, ms=700, diff_prev=STILL_DIFF, diff_last_capture=0.5)
    assert captures(decisions) == 0
    assert decisions[-1].state is TriggerState.CAPTURED


def test_never_settles_hint_after_5s_moving_while_sharp(
    trigger: CaptureTrigger, clock: FakeClock
) -> None:
    early = feed(trigger, clock, ms=4800, diff_prev=MOVING_DIFF)
    assert all(NEVER_SETTLES not in d.hints for d in early)
    late = feed(trigger, clock, ms=400, diff_prev=MOVING_DIFF)
    assert NEVER_SETTLES in late[-1].hints
    assert late[-1].state is TriggerState.MOVING


# --- Transition table, row by row ------------------------------------------------------------


def test_row_moving_to_settling(trigger: CaptureTrigger) -> None:
    assert trigger.state is TriggerState.MOVING
    d = trigger.update(1.99, SHARP, NEW_CONTENT)
    assert d.state is TriggerState.SETTLING
    assert not d.capture


def test_moving_stays_moving_at_stability_threshold(trigger: CaptureTrigger) -> None:
    assert trigger.update(2.0, SHARP, NEW_CONTENT).state is TriggerState.MOVING


def test_row_settling_to_moving(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)
    clock.advance(0.3)
    assert trigger.update(2.0, SHARP, NEW_CONTENT).state is TriggerState.MOVING


def test_settling_motion_restarts_stable_timer(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)
    clock.advance(0.5)
    trigger.update(MOVING_DIFF, SHARP, NEW_CONTENT)  # back to MOVING
    trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)  # SETTLING, timer restarts
    clock.advance(0.5)
    d = trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)
    assert d.state is TriggerState.SETTLING
    assert not d.capture


def test_row_settling_to_waiting_focus(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, BLURRY, NEW_CONTENT)
    clock.advance(0.6)
    d = trigger.update(STILL_DIFF, 99.9, NEW_CONTENT)
    assert d.state is TriggerState.WAITING_FOCUS
    assert not d.capture


def test_row_settling_to_captured_duplicate(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, SHARP, 0.99)
    clock.advance(0.6)
    d = trigger.update(STILL_DIFF, SHARP, 0.99)
    assert d.state is TriggerState.CAPTURED
    assert not d.capture


def test_row_waiting_focus_to_captured_duplicate(trigger: CaptureTrigger, clock: FakeClock) -> None:
    settle_into_waiting_focus(trigger, clock)
    clock.advance(FRAME_S)
    d = trigger.update(STILL_DIFF, SHARP, 0.99)
    assert d.state is TriggerState.CAPTURED
    assert not d.capture


def test_row_settling_to_captured_emits_capture(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, SHARP, 1.0)
    clock.advance(0.6)
    d = trigger.update(STILL_DIFF, 100.0, 1.0)  # thresholds are inclusive for "sharp" / "new"
    assert d.state is TriggerState.CAPTURED
    assert d.capture


def test_row_waiting_focus_to_captured_emits_capture(
    trigger: CaptureTrigger, clock: FakeClock
) -> None:
    settle_into_waiting_focus(trigger, clock)
    clock.advance(FRAME_S)
    d = trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)
    assert d.state is TriggerState.CAPTURED
    assert d.capture


def test_row_waiting_focus_to_moving(trigger: CaptureTrigger, clock: FakeClock) -> None:
    settle_into_waiting_focus(trigger, clock)
    clock.advance(FRAME_S)
    assert trigger.update(2.0, BLURRY, NEW_CONTENT).state is TriggerState.MOVING


def test_waiting_focus_stays_while_blurry(trigger: CaptureTrigger, clock: FakeClock) -> None:
    settle_into_waiting_focus(trigger, clock)
    decisions = feed(trigger, clock, ms=1000, diff_prev=STILL_DIFF, sharpness=BLURRY)
    assert all(d.state is TriggerState.WAITING_FOCUS and not d.capture for d in decisions)


def test_row_captured_to_moving(trigger: CaptureTrigger, clock: FakeClock) -> None:
    feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)
    assert trigger.state is TriggerState.CAPTURED
    assert trigger.update(6.0, SHARP, NEW_CONTENT).state is TriggerState.MOVING


def test_captured_ignores_motion_below_motion_threshold(
    trigger: CaptureTrigger, clock: FakeClock
) -> None:
    """Hysteresis: diffs between stability and motion thresholds keep CAPTURED."""
    feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)
    decisions = feed(trigger, clock, frames=30, diff_prev=5.9)
    assert all(d.state is TriggerState.CAPTURED and not d.capture for d in decisions)


# --- Never-settles hint ----------------------------------------------------------------------


def test_never_settles_cleared_on_next_capture(trigger: CaptureTrigger, clock: FakeClock) -> None:
    feed(trigger, clock, ms=5100, diff_prev=MOVING_DIFF)
    assert NEVER_SETTLES in trigger.hints
    still = feed(trigger, clock, ms=700, diff_prev=STILL_DIFF)
    assert captures(still) == 1
    assert NEVER_SETTLES not in still[-1].hints


def test_never_settles_not_raised_while_blurry(trigger: CaptureTrigger, clock: FakeClock) -> None:
    decisions = feed(trigger, clock, ms=6000, diff_prev=MOVING_DIFF, sharpness=BLURRY)
    assert all(NEVER_SETTLES not in d.hints for d in decisions)


def test_never_settles_counts_settling_time(trigger: CaptureTrigger, clock: FakeClock) -> None:
    """Flicker: alternating MOVING/SETTLING without ever reaching CAPTURED raises the hint."""
    for _ in range(40):
        feed(trigger, clock, frames=3, diff_prev=MOVING_DIFF)
        feed(trigger, clock, frames=3, diff_prev=STILL_DIFF)
    assert trigger.state in (TriggerState.MOVING, TriggerState.SETTLING)
    assert NEVER_SETTLES in trigger.hints


def test_never_settles_cleared_by_manual_capture(trigger: CaptureTrigger, clock: FakeClock) -> None:
    feed(trigger, clock, ms=5100, diff_prev=MOVING_DIFF)
    assert NEVER_SETTLES in trigger.hints
    trigger.notify_manual_capture()
    assert trigger.hints == frozenset()
    d = feed(trigger, clock, frames=1, diff_prev=MOVING_DIFF)[0]
    assert NEVER_SETTLES not in d.hints


def test_never_settles_threshold_is_configurable(clock: FakeClock) -> None:
    trigger = CaptureTrigger(TriggerConfig(never_settles_ms=1000), clock=clock)
    feed(trigger, clock, ms=1100, diff_prev=MOVING_DIFF)
    assert NEVER_SETTLES in trigger.hints


# --- Manual capture, reset, config -----------------------------------------------------------


def test_manual_capture_does_not_change_state(trigger: CaptureTrigger, clock: FakeClock) -> None:
    trigger.update(STILL_DIFF, SHARP, NEW_CONTENT)
    assert trigger.state is TriggerState.SETTLING
    trigger.notify_manual_capture()
    assert trigger.state is TriggerState.SETTLING
    clock.advance(0.6)
    # The caller updated its reference, so the settled view now matches the manual capture.
    d = trigger.update(STILL_DIFF, SHARP, 0.2)
    assert d.state is TriggerState.CAPTURED
    assert not d.capture


def test_reset_returns_to_moving(trigger: CaptureTrigger, clock: FakeClock) -> None:
    feed(trigger, clock, ms=5100, diff_prev=MOVING_DIFF)
    feed(trigger, clock, frames=1, diff_prev=STILL_DIFF)
    assert trigger.state is TriggerState.SETTLING
    trigger.reset()
    assert trigger.state is TriggerState.MOVING
    assert trigger.hints == frozenset()


def test_respects_custom_thresholds(clock: FakeClock) -> None:
    cfg = TriggerConfig(stable_duration_ms=100, stability_threshold=5.0, motion_threshold=20.0)
    trigger = CaptureTrigger(cfg, clock=clock)
    trigger.update(4.0, SHARP, NEW_CONTENT)
    clock.advance(0.1)
    assert trigger.update(4.0, SHARP, NEW_CONTENT).capture
    assert trigger.update(19.0, SHARP, NEW_CONTENT).state is TriggerState.CAPTURED


def test_config_from_settings_reads_matching_attributes() -> None:
    @dataclass
    class FakeSettings:
        stable_duration_ms: int = 900
        stability_threshold: float = 3.0
        motion_threshold: float = 7.0
        sharpness_threshold: float = 80.0
        duplicate_threshold: float = 1.5
        model: str = "ignored"

    cfg = TriggerConfig.from_settings(FakeSettings())
    assert cfg == TriggerConfig(
        stable_duration_ms=900,
        stability_threshold=3.0,
        motion_threshold=7.0,
        sharpness_threshold=80.0,
        duplicate_threshold=1.5,
        never_settles_ms=5000,
    )


def test_trigger_state_values_match_controller_contract() -> None:
    assert [s.value for s in TriggerState] == ["MOVING", "SETTLING", "WAITING_FOCUS", "CAPTURED"]


def test_no_pyside6_import() -> None:
    import ast
    from pathlib import Path

    import screen_copier.camera.quality as quality
    import screen_copier.camera.trigger as trigger_mod

    for mod in (quality, trigger_mod):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = [
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(tree)
            if isinstance(n, ast.Import | ast.ImportFrom)
            for a in (n.names if isinstance(n, ast.Import) else [n])
        ]
        assert not any(name and name.startswith("PySide6") for name in names), mod.__name__
