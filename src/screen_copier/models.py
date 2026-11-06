"""Pydantic data models shared by every module (architecture §10.1).

This module is the source of truth for the data contract. It is pure logic and must not
import PySide6.

Persisted entities: ``Session``, ``Frame``, ``FrameQuality``, ``Usage``, ``FrameResult``,
``RecognizedLine``. Derived (in-memory only) entities: ``Document``, ``Segment``, ``DocLine``,
``Observation``, ``Gap``, ``StitchEvent``.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1
"""Current version of the persisted session format (REQ-SES-07)."""


class UnsupportedVersion(Exception):
    """A persisted file was written by a newer app version and must not be loaded.

    Deliberately not a ``ValueError``: pydantic would wrap a ``ValueError`` raised inside a
    validator in a ``ValidationError``, and callers must be able to tell this case apart from a
    corrupt file.
    """

    def __init__(self, found: int, supported: int = SCHEMA_VERSION) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"File has schema version {found}, but this app supports up to version {supported}."
        )


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""A timezone-aware datetime, normalized to UTC. Naive datetimes are rejected."""


# --------------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------------


class SessionState(StrEnum):
    CAPTURING = "capturing"
    REVIEWING = "reviewing"
    SAVED = "saved"


class CaptureMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class FrameStatus(StrEnum):
    QUEUED = "queued"
    RECOGNIZING = "recognizing"
    RECOGNIZED = "recognized"
    FAILED = "failed"
    DISCARDED = "discarded"


class DocLineFlag(StrEnum):
    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"
    CUT_OFF = "cut_off"
    SOFT_WRAP = "soft_wrap"


class GapKind(StrEnum):
    OVERLAP = "overlap"
    """Unknown lines between two segments that could not be aligned (§9.2)."""
    LINE_NUMBERS = "line_numbers"
    """A known range of missing line numbers (§9.3)."""


class StitchEventKind(StrEnum):
    GAP_DETECTED = "gap_detected"
    GAP_CLOSED = "gap_closed"
    AMBIGUOUS_OVERLAP = "ambiguous_overlap"
    CUT_OFF = "cut_off"
    WORD_WRAP = "word_wrap"


# --------------------------------------------------------------------------------------------
# Recognition result (must match REPORT_TOOL in architecture §7.2)
# --------------------------------------------------------------------------------------------


class RecognizedLine(BaseModel):
    """One transcribed line, as reported by the ``report_transcription`` tool."""

    # Strict: the tool schema says boolean/integer, so "true" or 1.0 is malformed output.
    # Extra keys are ignored so a harmless extra property from the model is not fatal.
    model_config = ConfigDict(strict=True, extra="ignore")

    text: str
    line_number: Annotated[int, Field(ge=1)] | None
    partial: bool
    cut_off_right: bool
    uncertain: bool
    uncertain_note: str | None = None

    @field_validator("text")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("text must be a single line without newline characters")
        return value


class FrameResult(BaseModel):
    """Transcription of one frame: validates ``tool_use.input`` from Claude directly."""

    model_config = ConfigDict(strict=True, extra="ignore")

    has_line_numbers: bool
    language_guess: str | None = None
    word_wrap_detected: bool
    lines: list[RecognizedLine]
    """Top to bottom. May be empty."""


# --------------------------------------------------------------------------------------------
# Session (persisted)
# --------------------------------------------------------------------------------------------


class _Persisted(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(_Persisted):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class FrameQuality(_Persisted):
    sharpness: float = Field(ge=0)
    blurry: bool


class Frame(_Persisted):
    seq: int = Field(ge=1)
    captured_at: UtcDatetime
    mode: CaptureMode
    image_file: str
    """Path relative to the session folder: ``frames/<seq>.jpg``."""
    quality: FrameQuality
    status: FrameStatus = FrameStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    error: str | None = None
    result: FrameResult | None = None

    @staticmethod
    def image_file_for(seq: int) -> str:
        return f"frames/{seq}.jpg"

    @model_validator(mode="after")
    def _result_iff_recognized(self) -> Self:
        recognized = self.status is FrameStatus.RECOGNIZED
        if recognized and self.result is None:
            raise ValueError("a recognized frame must have a result")
        if not recognized and self.result is not None:
            raise ValueError(f"a frame with status {self.status.value!r} must not have a result")
        return self


class Session(_Persisted):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """uuid4; also the session folder name."""
    created_at: UtcDatetime
    updated_at: UtcDatetime
    state: SessionState = SessionState.CAPTURING
    saved_at: UtcDatetime | None = None
    output_path: str | None = None
    language: str | None = None
    """User override. Null means use the language vote."""
    frames: list[Frame] = Field(default_factory=list)
    """Ordered by seq."""
    edited_text: str | None = None
    """Non-null once the user edits; it is then the truth for the text."""
    usage: Usage = Field(default_factory=Usage)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value > SCHEMA_VERSION:
            raise UnsupportedVersion(value)
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version {value} must be migrated to {SCHEMA_VERSION} before validation"
            )
        return value

    @field_validator("id")
    @classmethod
    def _check_uuid4(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("id must be a UUID string") from exc
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("id must be a canonical lowercase uuid4 string")
        return value

    @model_validator(mode="after")
    def _frames_increasing(self) -> Self:
        seqs = [frame.seq for frame in self.frames]
        if any(b <= a for a, b in itertools.pairwise(seqs)):
            raise ValueError("frame seq values must be unique and strictly increasing")
        return self


# --------------------------------------------------------------------------------------------
# Document (derived, never persisted). Behavior lives in stitching/ (TASK-08).
# --------------------------------------------------------------------------------------------


class Observation(BaseModel):
    """One frame's reading of a document line (§9.4)."""

    frame_seq: int = Field(ge=1)
    text: str
    uncertain: bool = False
    cut_off_right: bool = False


class DocLine(BaseModel):
    text: str
    """Resolved text."""
    observations: list[Observation] = Field(min_length=1)
    flags: set[DocLineFlag] = Field(default_factory=set)
    alternatives: list[str] = Field(default_factory=list)


class Segment(BaseModel):
    lines: list[DocLine] = Field(min_length=1)
    first_line_number: int | None = Field(default=None, ge=1)


class Gap(BaseModel):
    kind: GapKind
    description: str
    """Rendered as ``<<<SCREEN-COPIER GAP: {description}>>>`` (§11)."""
    before_segment: int = Field(ge=0)
    """Index into ``Document.segments`` of the segment this gap renders immediately before."""


class Document(BaseModel):
    segments: list[Segment] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    """In render order."""


class StitchEvent(BaseModel):
    kind: StitchEventKind
    frame_seq: int = Field(ge=1)
