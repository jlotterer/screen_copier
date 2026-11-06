"""Recognizer protocol and outcome types (architecture §7.1).

This module is the source of truth for the recognition contract. It is pure logic and must not
import PySide6.

A recognizer turns one JPEG into exactly one ``RecognitionOutcome``. It never retries and never
raises for API or response problems: the queue (TASK-13) decides what to do with each outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from screen_copier.models import FrameResult, Usage


@dataclass(frozen=True, slots=True)
class Ok:
    """The frame was transcribed."""

    result: FrameResult
    usage: Usage


@dataclass(frozen=True, slots=True)
class Retryable:
    """A temporary failure: rate limit, overload, server error, timeout or lost connection."""

    reason: str
    connection_error: bool = False
    """True when the request never got an HTTP response (connection error or timeout).

    The queue counts consecutive connection errors to detect that the app is offline (§7.4).
    """


@dataclass(frozen=True, slots=True)
class AuthError:
    """The API key is missing, invalid, or lacks permission. Never contains the key itself."""

    message: str


@dataclass(frozen=True, slots=True)
class BadRequest:
    """The API rejected the request. Retrying the same request will not help."""

    message: str


@dataclass(frozen=True, slots=True)
class Malformed:
    """The model answered, but not with a valid ``report_transcription`` tool call."""

    raw: str
    """What came back instead, for logs and debugging."""


RecognitionOutcome = Ok | Retryable | AuthError | BadRequest | Malformed


class Recognizer(Protocol):
    """Transcribes a single frame. Implementations must be safe to call from worker threads."""

    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome: ...
