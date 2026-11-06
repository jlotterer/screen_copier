"""Auto-capture state machine (architecture §5.3, REQ-CAM-06, REQ-CAM-07).

Pure logic with an injected clock. Must not import PySide6.

The caller (``CameraWorker``) computes the metrics for each preview frame and calls
``CaptureTrigger.update``. The caller also owns the last-capture reference frame used to compute
``diff_last_capture`` (``math.inf`` when nothing has been captured yet), and must replace it
whenever a capture happens, auto or manual.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

NEVER_SETTLES = "never_settles"
"""Hint raised when the image stays sharp but never settles (flicker, moiré)."""


class TriggerState(StrEnum):
    MOVING = "MOVING"
    SETTLING = "SETTLING"
    WAITING_FOCUS = "WAITING_FOCUS"
    CAPTURED = "CAPTURED"


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    """Thresholds for the trigger. Defaults match architecture §8.1."""

    stable_duration_ms: int = 600
    stability_threshold: float = 2.0
    motion_threshold: float = 6.0
    sharpness_threshold: float = 100.0
    duplicate_threshold: float = 1.0
    never_settles_ms: int = 5000

    @classmethod
    def from_settings(cls, settings: Any) -> Self:
        """Build from any object with matching attribute names (e.g. ``config.Settings``).

        Attributes the object lacks keep their defaults.
        """
        defaults = cls()
        values = {
            name: getattr(settings, name, getattr(defaults, name))
            for name in cls.__dataclass_fields__
        }
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    state: TriggerState
    capture: bool = False
    hints: frozenset[str] = field(default_factory=frozenset)


class CaptureTrigger:
    """Decides when Auto mode captures a frame. See the transition table in §5.3."""

    def __init__(
        self,
        config: TriggerConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``clock`` returns monotonic time in seconds."""
        self.config = config or TriggerConfig()
        self._clock = clock
        self.reset()

    @property
    def state(self) -> TriggerState:
        return self._state

    @property
    def hints(self) -> frozenset[str]:
        return frozenset({NEVER_SETTLES}) if self._never_settles else frozenset()

    def reset(self) -> None:
        """Return to MOVING and clear timers and hints. Call on entering Auto mode."""
        self._state = TriggerState.MOVING
        self._stable_since: float | None = None
        self._unsettled_since: float | None = None
        self._never_settles = False

    def notify_manual_capture(self) -> None:
        """Record that a manual capture happened.

        The trigger state is unchanged. The ``never_settles`` hint is cleared and its timer
        restarts. The caller is responsible for updating its last-capture reference frame.
        """
        self._never_settles = False
        self._unsettled_since = None

    def update(
        self, diff_prev: float, sharpness: float, diff_last_capture: float
    ) -> TriggerDecision:
        """Advance the state machine by one preview frame.

        ``diff_prev`` is against the previous preview frame, ``diff_last_capture`` against the
        last captured frame (``math.inf`` if none).
        """
        cfg = self.config
        now = self._clock()
        still = diff_prev < cfg.stability_threshold
        sharp = sharpness >= cfg.sharpness_threshold
        capture = False

        match self._state:
            case TriggerState.MOVING:
                if still:
                    self._state = TriggerState.SETTLING
                    self._stable_since = now
            case TriggerState.SETTLING | TriggerState.WAITING_FOCUS:
                if not still:
                    self._state = TriggerState.MOVING
                    self._stable_since = None
                elif self._stable_long_enough(now):
                    if not sharp:
                        self._state = TriggerState.WAITING_FOCUS
                    else:
                        self._state = TriggerState.CAPTURED
                        capture = diff_last_capture >= cfg.duplicate_threshold
            case TriggerState.CAPTURED:
                if diff_prev >= cfg.motion_threshold:
                    self._state = TriggerState.MOVING
                    self._stable_since = None

        self._update_never_settles(now, sharp)
        return TriggerDecision(state=self._state, capture=capture, hints=self.hints)

    def _stable_long_enough(self, now: float) -> bool:
        assert self._stable_since is not None
        return (now - self._stable_since) * 1000.0 >= self.config.stable_duration_ms

    def _update_never_settles(self, now: float, sharp: bool) -> None:
        if self._state is TriggerState.CAPTURED:
            # Reaching CAPTURED (duplicate or not) means the image settled.
            self._never_settles = False
            self._unsettled_since = None
            return
        if self._state in (TriggerState.MOVING, TriggerState.SETTLING) and sharp:
            if self._unsettled_since is None:
                self._unsettled_since = now
            elif (now - self._unsettled_since) * 1000.0 >= self.config.never_settles_ms:
                self._never_settles = True
        else:
            # Blurry or waiting for focus: the timer only runs while sharp and unsettled.
            self._unsettled_since = None
