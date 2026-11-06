"""Simulated recognizer that replays prepared transcriptions (REQ-OCR-10).

Selected at runtime with ``SCREEN_COPIER_FAKE_RECOGNIZER=<dir>`` (architecture §8.3). The folder
holds one ``<seq>.json`` file per frame, each a ``FrameResult``. No network is used.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from screen_copier.models import FrameResult, Usage
from screen_copier.recognition.base import Malformed, Ok, RecognitionOutcome


class FakeRecognizer:
    """Returns ``Ok(<seq>.json)``, or ``Malformed`` when that file is missing or invalid."""

    def __init__(
        self,
        directory: str | Path,
        *,
        delay_s: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``delay_s`` simulates API latency for UI demos; ``sleep`` is injectable for tests."""
        if delay_s < 0:
            raise ValueError("delay_s must not be negative")
        self._directory = Path(directory)
        self._delay_s = delay_s
        self._sleep = sleep

    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome:
        if self._delay_s:
            self._sleep(self._delay_s)
        path = self._directory / f"{frame_seq}.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Malformed(f"No simulated result for frame {frame_seq}: {path} not found")
        except OSError as exc:
            return Malformed(f"Could not read {path}: {exc}")
        try:
            result = FrameResult.model_validate_json(text)
        except ValidationError as exc:
            return Malformed(f"{path} is not a valid FrameResult: {exc}")
        return Ok(result, Usage())
