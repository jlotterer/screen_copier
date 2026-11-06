"""Tests for the shared data models (TASK-02, REQ-SES-07)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from screen_copier import models
from screen_copier.models import (
    SCHEMA_VERSION,
    CaptureMode,
    DocLine,
    DocLineFlag,
    Document,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
    Gap,
    GapKind,
    Observation,
    RecognizedLine,
    Segment,
    Session,
    SessionState,
    StitchEvent,
    StitchEventKind,
    UnsupportedVersion,
    Usage,
)

T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
SESSION_ID = "0b1d6a52-7f5c-4c3e-9a2b-6f1e2d3c4b5a"


def sample_tool_input() -> dict[str, Any]:
    """A `tool_use.input` payload as Claude returns it for REPORT_TOOL (§7.2)."""
    return {
        "has_line_numbers": True,
        "language_guess": "python",
        "word_wrap_detected": False,
        "lines": [
            {
                "text": "def main() -> None:",
                "line_number": 1,
                "partial": False,
                "cut_off_right": False,
                "uncertain": False,
                "uncertain_note": None,
            },
            {
                "text": "    print('hel1o')",
                "line_number": 2,
                "partial": False,
                "cut_off_right": True,
                "uncertain": True,
                "uncertain_note": "l/1 ambiguous",
            },
            {
                "text": "    return",
                "line_number": None,
                "partial": True,
                "cut_off_right": False,
                "uncertain": False,
            },
        ],
    }


def full_frame_result() -> FrameResult:
    return FrameResult.model_validate(sample_tool_input())


def full_session() -> Session:
    return Session(
        schema_version=SCHEMA_VERSION,
        id=SESSION_ID,
        created_at=T0,
        updated_at=T0 + timedelta(minutes=5),
        state=SessionState.SAVED,
        saved_at=T0 + timedelta(minutes=6),
        output_path=r"C:\Users\me\code\main.py",
        language="python",
        frames=[
            Frame(
                seq=1,
                captured_at=T0 + timedelta(seconds=1),
                mode=CaptureMode.AUTO,
                image_file="frames/1.jpg",
                quality=FrameQuality(sharpness=123.5, blurry=False),
                status=FrameStatus.RECOGNIZED,
                attempts=1,
                error=None,
                result=full_frame_result(),
            ),
            Frame(
                seq=2,
                captured_at=T0 + timedelta(seconds=2),
                mode=CaptureMode.MANUAL,
                image_file="frames/2.jpg",
                quality=FrameQuality(sharpness=12.0, blurry=True),
                status=FrameStatus.FAILED,
                attempts=3,
                error="HTTP 529 overloaded",
                result=None,
            ),
        ],
        edited_text="def main() -> None:\n    print('hello')\n",
        usage=Usage(input_tokens=1500, output_tokens=200),
    )


def full_document() -> Document:
    line = DocLine(
        text="x = 1",
        observations=[
            Observation(frame_seq=1, text="x = 1", uncertain=False, cut_off_right=False),
            Observation(frame_seq=2, text="x = l", uncertain=True, cut_off_right=True),
        ],
        flags={DocLineFlag.CONFLICT, DocLineFlag.CUT_OFF, DocLineFlag.UNCERTAIN},
        alternatives=["x = 1", "x = l"],
    )
    wrapped = DocLine(
        text="continued",
        observations=[Observation(frame_seq=2, text="continued")],
        flags={DocLineFlag.SOFT_WRAP},
    )
    return Document(
        segments=[
            Segment(lines=[line, wrapped], first_line_number=5),
            Segment(lines=[line], first_line_number=None),
        ],
        gaps=[
            Gap(kind=GapKind.LINE_NUMBERS, description="lines 1–4 missing", before_segment=0),  # noqa: RUF001
            Gap(
                kind=GapKind.OVERLAP,
                description="unknown lines between capture 1 and 2",
                before_segment=1,
            ),
        ],
    )


# --- round trips


ROUND_TRIP_CASES: list[BaseModel] = [
    full_session(),
    full_session().frames[0],
    full_session().frames[1],  # null-able fields (result, error) set to null
    FrameQuality(sharpness=0.0, blurry=True),
    Usage(input_tokens=0, output_tokens=7),
    full_frame_result(),
    FrameResult(has_line_numbers=False, language_guess=None, word_wrap_detected=True, lines=[]),
    full_frame_result().lines[0],
    full_frame_result().lines[2],
    Observation(frame_seq=3, text="  y", uncertain=True, cut_off_right=True),
    full_document().segments[0].lines[0],
    full_document().segments[0],
    full_document().gaps[1],
    full_document(),
    Document(),
    StitchEvent(kind=StitchEventKind.GAP_DETECTED, frame_seq=4),
]


@pytest.mark.parametrize("instance", ROUND_TRIP_CASES, ids=lambda m: type(m).__name__)
def test_json_round_trip(instance: BaseModel) -> None:
    restored = type(instance).model_validate_json(instance.model_dump_json())
    assert restored == instance


def test_session_with_null_fields_round_trips() -> None:
    session = Session(
        id=SESSION_ID,
        created_at=T0,
        updated_at=T0,
        saved_at=None,
        output_path=None,
        language=None,
        edited_text=None,
    )
    restored = Session.model_validate_json(session.model_dump_json())
    assert restored == session
    assert restored.saved_at is None and restored.edited_text is None


def test_round_trip_covers_every_entity() -> None:
    covered = {type(m) for m in ROUND_TRIP_CASES}
    expected = {
        Session, Frame, FrameQuality, Usage, FrameResult, RecognizedLine,
        Observation, DocLine, Segment, Gap, Document, StitchEvent,
    }  # fmt: skip
    assert expected <= covered


def test_every_event_kind_round_trips() -> None:
    for kind in StitchEventKind:
        event = StitchEvent(kind=kind, frame_seq=1)
        assert StitchEvent.model_validate_json(event.model_dump_json()) == event


# --- datetimes


def test_datetimes_serialize_as_utc() -> None:
    data = json.loads(full_session().model_dump_json())
    assert data["created_at"] == "2026-11-06T12:00:00Z"
    assert data["frames"][0]["captured_at"] == "2026-11-06T12:00:01Z"


def test_non_utc_datetimes_are_normalized_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    session = Session(
        id=SESSION_ID,
        created_at=datetime(2026, 11, 6, 7, 0, tzinfo=eastern),
        updated_at="2026-11-06T07:00:00-05:00",
    )
    assert session.created_at == T0 and session.created_at.tzinfo is UTC
    assert session.updated_at == T0 and session.updated_at.tzinfo is UTC


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Session(id=SESSION_ID, created_at=datetime(2026, 11, 6), updated_at=T0)


# --- FrameResult / tool schema


def test_frame_result_accepts_sample_tool_input() -> None:
    result = FrameResult.model_validate(sample_tool_input())
    assert result.has_line_numbers is True
    assert result.language_guess == "python"
    assert [line.line_number for line in result.lines] == [1, 2, None]
    assert result.lines[1].uncertain_note == "l/1 ambiguous"
    assert result.lines[2].uncertain_note is None  # optional in the schema


def test_frame_result_accepts_tool_input_as_json() -> None:
    result = FrameResult.model_validate_json(json.dumps(sample_tool_input()))
    assert result == full_frame_result()


def test_frame_result_rejects_missing_lines() -> None:
    payload = sample_tool_input()
    del payload["lines"]
    with pytest.raises(ValidationError, match="lines"):
        FrameResult.model_validate(payload)


@pytest.mark.parametrize("field", ["has_line_numbers", "word_wrap_detected"])
def test_frame_result_rejects_missing_required_fields(field: str) -> None:
    payload = sample_tool_input()
    del payload[field]
    with pytest.raises(ValidationError):
        FrameResult.model_validate(payload)


def test_language_guess_is_optional() -> None:
    payload = sample_tool_input()
    del payload["language_guess"]
    assert FrameResult.model_validate(payload).language_guess is None


@pytest.mark.parametrize("field", ["text", "line_number", "partial", "cut_off_right", "uncertain"])
def test_recognized_line_rejects_missing_required_fields(field: str) -> None:
    payload = sample_tool_input()
    del payload["lines"][0][field]
    with pytest.raises(ValidationError):
        FrameResult.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("partial", "true"), ("uncertain", 1), ("line_number", "3"), ("line_number", 2.5)],
)
def test_recognized_line_rejects_wrong_types(field: str, bad_value: object) -> None:
    payload = sample_tool_input()
    payload["lines"][0][field] = bad_value
    with pytest.raises(ValidationError):
        FrameResult.model_validate(payload)


def test_frame_result_ignores_extra_properties() -> None:
    payload = sample_tool_input()
    payload["confidence"] = 0.9
    payload["lines"][0]["color"] = "blue"
    assert FrameResult.model_validate(payload) == full_frame_result()


@pytest.mark.parametrize("text", ["a\nb", "a\r\nb", "trailing\n"])
def test_recognized_line_text_rejects_newlines(text: str) -> None:
    payload = sample_tool_input()
    payload["lines"][0]["text"] = text
    with pytest.raises(ValidationError):
        FrameResult.model_validate(payload)


# --- constraints


@pytest.mark.parametrize("seq", [0, -1])
def test_frame_seq_must_be_at_least_one(seq: int) -> None:
    with pytest.raises(ValidationError):
        Frame(
            seq=seq,
            captured_at=T0,
            mode=CaptureMode.AUTO,
            image_file="frames/0.jpg",
            quality=FrameQuality(sharpness=1.0, blurry=False),
        )


def test_frame_seq_one_is_valid() -> None:
    frame = Frame(
        seq=1,
        captured_at=T0,
        mode=CaptureMode.AUTO,
        image_file=Frame.image_file_for(1),
        quality=FrameQuality(sharpness=1.0, blurry=False),
    )
    assert frame.image_file == "frames/1.jpg"
    assert frame.status is FrameStatus.QUEUED and frame.attempts == 0


@pytest.mark.parametrize("line_number", [0, -3])
def test_recognized_line_number_must_be_at_least_one(line_number: int) -> None:
    with pytest.raises(ValidationError):
        RecognizedLine(
            text="x", line_number=line_number, partial=False, cut_off_right=False, uncertain=False
        )


@pytest.mark.parametrize("line_number", [1, 9999, None])
def test_recognized_line_number_valid_values(line_number: int | None) -> None:
    line = RecognizedLine(
        text="x", line_number=line_number, partial=False, cut_off_right=False, uncertain=False
    )
    assert line.line_number == line_number


def test_recognized_frame_requires_result() -> None:
    with pytest.raises(ValidationError, match="must have a result"):
        Frame(
            seq=1,
            captured_at=T0,
            mode=CaptureMode.AUTO,
            image_file="frames/1.jpg",
            quality=FrameQuality(sharpness=1.0, blurry=False),
            status=FrameStatus.RECOGNIZED,
        )


@pytest.mark.parametrize("status", [s for s in FrameStatus if s is not FrameStatus.RECOGNIZED])
def test_non_recognized_frame_must_not_have_result(status: FrameStatus) -> None:
    with pytest.raises(ValidationError, match="must not have a result"):
        Frame(
            seq=1,
            captured_at=T0,
            mode=CaptureMode.AUTO,
            image_file="frames/1.jpg",
            quality=FrameQuality(sharpness=1.0, blurry=False),
            status=status,
            result=full_frame_result(),
        )


def test_attempts_and_usage_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)
    with pytest.raises(ValidationError):
        Frame(
            seq=1,
            captured_at=T0,
            mode=CaptureMode.AUTO,
            image_file="frames/1.jpg",
            quality=FrameQuality(sharpness=1.0, blurry=False),
            attempts=-1,
        )


def test_session_frames_must_have_increasing_unique_seq() -> None:
    session = full_session()
    data = json.loads(session.model_dump_json())
    data["frames"][1]["seq"] = 1
    with pytest.raises(ValidationError, match="strictly increasing"):
        Session.model_validate(data)
    data["frames"] = list(reversed(json.loads(session.model_dump_json())["frames"]))
    with pytest.raises(ValidationError, match="strictly increasing"):
        Session.model_validate(data)


def test_session_id_defaults_to_uuid4() -> None:
    a = Session(created_at=T0, updated_at=T0)
    b = Session(created_at=T0, updated_at=T0)
    assert a.id != b.id
    assert Session(id=a.id, created_at=T0, updated_at=T0).id == a.id


@pytest.mark.parametrize(
    "bad_id",
    ["not-a-uuid", "6ba7b810-9dad-11d1-80b4-00c04fd430c8", SESSION_ID.upper()],
    ids=["garbage", "uuid1", "uppercase"],
)
def test_session_id_must_be_canonical_uuid4(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Session(id=bad_id, created_at=T0, updated_at=T0)


def test_session_defaults() -> None:
    session = Session(created_at=T0, updated_at=T0)
    assert session.schema_version == SCHEMA_VERSION == 1
    assert session.state is SessionState.CAPTURING
    assert session.frames == []
    assert session.usage == Usage(input_tokens=0, output_tokens=0)


def test_persisted_models_reject_unknown_fields() -> None:
    data = json.loads(full_session().model_dump_json())
    data["surprise"] = True
    with pytest.raises(ValidationError):
        Session.model_validate(data)


def test_segment_and_docline_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        Segment(lines=[])
    with pytest.raises(ValidationError):
        DocLine(text="x", observations=[])


def test_enum_values_match_architecture() -> None:
    assert {s.value for s in SessionState} == {"capturing", "reviewing", "saved"}
    assert {m.value for m in CaptureMode} == {"auto", "manual"}
    assert {s.value for s in FrameStatus} == {
        "queued", "recognizing", "recognized", "failed", "discarded",
    }  # fmt: skip
    assert {f.value for f in DocLineFlag} == {"uncertain", "conflict", "cut_off", "soft_wrap"}
    assert {k.value for k in StitchEventKind} == {
        "gap_detected", "gap_closed", "ambiguous_overlap", "cut_off", "word_wrap",
    }  # fmt: skip


# --- versioning (REQ-SES-07)


def test_newer_schema_version_raises_unsupported_version() -> None:
    data = json.loads(full_session().model_dump_json())
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(UnsupportedVersion) as excinfo:
        Session.model_validate_json(json.dumps(data))
    assert excinfo.value.found == SCHEMA_VERSION + 1
    assert excinfo.value.supported == SCHEMA_VERSION
    assert not isinstance(excinfo.value, ValidationError | ValueError)


def test_older_schema_version_is_a_validation_error() -> None:
    data = json.loads(full_session().model_dump_json())
    data["schema_version"] = 0
    with pytest.raises(ValidationError, match="migrated"):
        Session.model_validate(data)


# --- purity


def test_models_does_not_import_pyside6() -> None:
    source = Path(models.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "PySide6" not in imported
