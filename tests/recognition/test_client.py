"""Tests for the recognition client (TASK-07).

Covers REQ-OCR-01, REQ-OCR-02, REQ-OCR-08 (classification), REQ-OCR-10 and REQ-NFR-04.
No test touches the network: the Anthropic client is replaced by a recording fake.
"""

from __future__ import annotations

import ast
import base64
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from screen_copier.models import FrameResult, RecognizedLine, Usage
from screen_copier.recognition import claude
from screen_copier.recognition.base import (
    AuthError,
    BadRequest,
    Malformed,
    Ok,
    RecognitionOutcome,
    Retryable,
)
from screen_copier.recognition.claude import (
    ClaudeRecognizer,
    MissingApiKeyError,
    client_factory_for,
    make_client,
)
from screen_copier.recognition.fake import FakeRecognizer
from screen_copier.recognition.prompt import REPORT_TOOL, REPORT_TOOL_NAME, SYSTEM_PROMPT

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "screen_copier"
SEQ_BASIC = REPO / "tests" / "fixtures" / "recognizer" / "seq_basic"

DEFAULT_MODEL = "claude-sonnet-5-5"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"
TEST_KEY = "sk-ant-test-0000"

VALID_INPUT: dict[str, Any] = {
    "has_line_numbers": True,
    "language_guess": "python",
    "word_wrap_detected": False,
    "lines": [
        {
            "text": "def f():",
            "line_number": 1,
            "partial": False,
            "cut_off_right": False,
            "uncertain": False,
            "uncertain_note": None,
        },
        {
            "text": "    return 1",
            "line_number": 2,
            "partial": False,
            "cut_off_right": True,
            "uncertain": True,
            "uncertain_note": "1 vs l",
        },
    ],
}


# --------------------------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------------------------


@dataclass
class StubSettings:
    model: str = DEFAULT_MODEL


class FakeMessages:
    def __init__(self, reply: Message | BaseException) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class FakeClient:
    def __init__(self, reply: Message | BaseException) -> None:
        self.messages = FakeMessages(reply)


def make_message(
    content: list[dict[str, Any]],
    *,
    stop_reason: str = "tool_use",
    input_tokens: int = 1200,
    output_tokens: int = 340,
) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": DEFAULT_MODEL,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def non_object_tool_use_message() -> Message:
    """The SDK parses responses leniently, so a non-object ``input`` can reach us unvalidated."""
    message = make_message([tool_use(VALID_INPUT)])
    block = message.content[0].model_copy(update={"input": ["not", "an", "object"]})
    return message.model_copy(update={"content": [block]})


def tool_use(tool_input: Any, name: str = REPORT_TOOL_NAME) -> dict[str, Any]:
    return {"type": "tool_use", "id": "toolu_test", "name": name, "input": tool_input}


def run(
    reply: Message | BaseException, settings: StubSettings | None = None
) -> tuple[RecognitionOutcome, FakeClient]:
    client = FakeClient(reply)
    recognizer = ClaudeRecognizer(lambda: client, settings or StubSettings())  # type: ignore[arg-type,return-value]
    return recognizer.recognize(JPEG, frame_seq=7), client


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(status: int, message: str = "api says no") -> anthropic.APIStatusError:
    body = {"type": "error", "error": {"type": "some_error", "message": message}}
    response = httpx2.Response(status, request=REQUEST, json=body)
    return anthropic.APIStatusError(f"Error code: {status}", response=response, body=body)


# --------------------------------------------------------------------------------------------
# Request shape (REQ-OCR-01, §7.3, decision 0006)
# --------------------------------------------------------------------------------------------


def test_request_shape() -> None:
    _, client = run(make_message([tool_use(VALID_INPUT)]))

    [kwargs] = client.messages.calls
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["max_tokens"] == 8192
    assert kwargs["system"] == SYSTEM_PROMPT
    assert kwargs["tools"] == [REPORT_TOOL]
    assert kwargs["tools"][0]["strict"] is True
    # Sonnet 5.5 rejects forced tool_choice, and API version 2026-10-21 rejects sampling params.
    assert kwargs["tool_choice"] == {"type": "auto"}
    assert not {"temperature", "top_p", "top_k"} & kwargs.keys()

    [message] = kwargs["messages"]
    assert message["role"] == "user"
    images = [block for block in message["content"] if block["type"] == "image"]
    assert len(images) == 1
    source = images[0]["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(source["data"]) == JPEG
    assert {"type": "text", "text": "Transcribe this screen."} in message["content"]


def test_request_uses_model_from_settings_on_each_call() -> None:
    settings = StubSettings()
    client = FakeClient(make_message([tool_use(VALID_INPUT)]))
    recognizer = ClaudeRecognizer(lambda: client, settings)  # type: ignore[arg-type,return-value]

    recognizer.recognize(JPEG, frame_seq=1)
    settings.model = "claude-opus-5-5"
    recognizer.recognize(JPEG, frame_seq=2)

    assert [call["model"] for call in client.messages.calls] == [DEFAULT_MODEL, "claude-opus-5-5"]


def test_make_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")

    client = make_client(TEST_KEY)

    assert isinstance(client, anthropic.Anthropic)
    assert client.max_retries == 0
    assert client.timeout == 60.0
    assert client.api_key == TEST_KEY
    # REQ-NFR-04: the environment cannot redirect frames away from the Anthropic API.
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"


def test_client_factory_reuses_client_until_key_changes() -> None:
    keys = iter([TEST_KEY, TEST_KEY, "sk-ant-test-1111"])
    factory = client_factory_for(lambda: next(keys))

    first, second, third = factory(), factory(), factory()

    assert first is second
    assert third is not first
    assert third.api_key == "sk-ant-test-1111"
    assert third.max_retries == 0


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key_is_auth_error(key: str | None) -> None:
    factory = client_factory_for(lambda: key)
    with pytest.raises(MissingApiKeyError):
        factory()

    outcome = ClaudeRecognizer(factory, StubSettings()).recognize(JPEG, frame_seq=1)

    assert isinstance(outcome, AuthError)


# --------------------------------------------------------------------------------------------
# Prompt and tool (REQ-OCR-02, §7.2)
# --------------------------------------------------------------------------------------------

PROMPT_RULES = {
    "exact text": [r"exact", r"do not correct", r"reformat"],
    "indentation as spaces": [r"indentation", r"as spaces", r"character grid"],
    "line numbers separate": [r"line_number", r"never include the line number in text"],
    "ignore UI": [
        r"ignore",
        r"tabs",
        r"menus",
        r"breadcrumbs",
        r"minimap",
        r"status bar",
        r"scrollbars",
        r"line-change markers",
    ],
    "partial": [r"partial", r"clipped at the top or bottom"],
    "cut_off_right": [r"cut_off_right", r"past the right edge"],
    "uncertain + note": [
        r"\buncertain\b",
        r"uncertain_note",
        r"l vs 1 vs I",
        r"O vs 0",
        r"\{ vs \(",
        r"; vs :",
        r"` vs '",
        r"number of spaces",
    ],
    "word wrap": [r"word_wrap_detected", r"soft-wraps"],
    "tool only": [r"report_transcription", r"respond only through the tool"],
}


@pytest.mark.parametrize("rule", PROMPT_RULES)
def test_system_prompt_covers_req_ocr_02(rule: str) -> None:
    text = " ".join(SYSTEM_PROMPT.split())  # collapse line breaks inside sentences
    missing = [p for p in PROMPT_RULES[rule] if not re.search(p, text, re.IGNORECASE)]
    assert not missing, f"system prompt lacks {rule!r} instruction: {missing}"


def _schema_matches_model(schema: dict[str, Any], model: type) -> None:
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(model.model_fields)  # type: ignore[attr-defined]
    assert set(schema["required"]) == set(schema["properties"])  # strict: all listed


def test_report_tool_is_strict_and_matches_models() -> None:
    assert REPORT_TOOL["name"] == "report_transcription"
    assert REPORT_TOOL["strict"] is True
    schema = REPORT_TOOL["input_schema"]
    _schema_matches_model(schema, FrameResult)
    _schema_matches_model(schema["properties"]["lines"]["items"], RecognizedLine)


def test_report_tool_is_json_serializable() -> None:
    assert json.loads(json.dumps(REPORT_TOOL)) == REPORT_TOOL


# --------------------------------------------------------------------------------------------
# Successful response
# --------------------------------------------------------------------------------------------


def test_valid_tool_use_is_ok_with_usage() -> None:
    reply = make_message(
        [{"type": "text", "text": "Here you go."}, tool_use(VALID_INPUT)],
        input_tokens=1500,
        output_tokens=420,
    )

    outcome, _ = run(reply)

    expected = Ok(
        FrameResult.model_validate(VALID_INPUT), Usage(input_tokens=1500, output_tokens=420)
    )
    assert outcome == expected


def test_extra_tool_properties_are_ignored() -> None:
    tool_input = {**copy.deepcopy(VALID_INPUT), "confidence": 0.9}

    outcome, _ = run(make_message([tool_use(tool_input)]))

    assert isinstance(outcome, Ok)


def test_blank_screen_is_ok_with_no_lines() -> None:
    tool_input = {**VALID_INPUT, "lines": [], "language_guess": None}

    outcome, _ = run(make_message([tool_use(tool_input)]))

    assert isinstance(outcome, Ok)
    assert outcome.result.lines == []


# --------------------------------------------------------------------------------------------
# Error mapping (§7.3 table)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_retryable_status(status: int) -> None:
    outcome, _ = run(status_error(status))

    assert isinstance(outcome, Retryable)
    assert outcome.connection_error is False
    assert str(status) in outcome.reason


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=REQUEST),
        anthropic.APITimeoutError(request=REQUEST),
    ],
    ids=["connection", "timeout"],
)
def test_connection_errors_are_retryable(error: Exception) -> None:
    outcome, _ = run(error)

    assert isinstance(outcome, Retryable)
    assert outcome.connection_error is True


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status(status: int) -> None:
    outcome, _ = run(status_error(status, "invalid x-api-key"))

    assert outcome == AuthError("invalid x-api-key")


@pytest.mark.parametrize("status", [400, 404, 413])
def test_bad_request_status(status: int) -> None:
    outcome, _ = run(status_error(status, "image exceeds 5 MB maximum"))

    assert outcome == BadRequest("image exceeds 5 MB maximum")


def test_sdk_specific_error_classes_map_by_status() -> None:
    def response(status: int) -> httpx2.Response:
        return httpx2.Response(status, request=REQUEST, json={})

    rate = anthropic.RateLimitError("rate limited", response=response(429), body=None)
    overloaded = anthropic.OverloadedError("overloaded", response=response(529), body=None)
    auth = anthropic.AuthenticationError("bad key", response=response(401), body=None)
    not_found = anthropic.NotFoundError("no model", response=response(404), body=None)

    assert isinstance(run(rate)[0], Retryable)
    assert isinstance(run(overloaded)[0], Retryable)
    assert run(auth)[0] == AuthError("bad key")  # falls back to the SDK message
    assert run(not_found)[0] == BadRequest("no model")


# --------------------------------------------------------------------------------------------
# Malformed responses (REQ-OCR-08 classification)
# --------------------------------------------------------------------------------------------


def _without(key: str) -> dict[str, Any]:
    data = copy.deepcopy(VALID_INPUT)
    del data[key]
    return data


def _line_with(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID_INPUT)
    data["lines"][0].update(changes)
    return data


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            make_message([{"type": "text", "text": "def f():"}], stop_reason="end_turn"),
            id="text-only",
        ),
        pytest.param(make_message([], stop_reason="end_turn"), id="empty"),
        pytest.param(make_message([tool_use(VALID_INPUT, name="other_tool")]), id="wrong-tool"),
        pytest.param(make_message([tool_use(_without("lines"))]), id="missing-field"),
        pytest.param(make_message([tool_use(_line_with(partial="true"))]), id="string-bool"),
        pytest.param(make_message([tool_use(_line_with(line_number=0))]), id="line-number-0"),
        pytest.param(make_message([tool_use(_line_with(text="a\nb"))]), id="newline-in-text"),
        pytest.param(non_object_tool_use_message(), id="non-object"),
        pytest.param(
            make_message([tool_use(VALID_INPUT)], stop_reason="max_tokens"), id="max-tokens"
        ),
        pytest.param(
            make_message([{"type": "text", "text": "No."}], stop_reason="refusal"), id="refusal"
        ),
    ],
)
def test_malformed(reply: Message) -> None:
    outcome, _ = run(reply)

    assert isinstance(outcome, Malformed)
    assert outcome.raw


def test_malformed_raw_is_truncated() -> None:
    huge = make_message([{"type": "text", "text": "x" * 50_000}], stop_reason="end_turn")

    outcome, _ = run(huge)

    assert isinstance(outcome, Malformed)
    assert len(outcome.raw) <= claude.MAX_RAW_CHARS + 1


# --------------------------------------------------------------------------------------------
# Fake recognizer (REQ-OCR-10)
# --------------------------------------------------------------------------------------------


def test_fake_returns_fixture_for_seq_1() -> None:
    expected = FrameResult.model_validate_json((SEQ_BASIC / "1.json").read_text("utf-8"))

    outcome = FakeRecognizer(SEQ_BASIC).recognize(JPEG, frame_seq=1)

    assert outcome == Ok(expected, Usage())


@pytest.mark.parametrize("seq", [1, 2, 3])
def test_seq_basic_fixtures_are_valid(seq: int) -> None:
    assert isinstance(FakeRecognizer(SEQ_BASIC).recognize(b"", frame_seq=seq), Ok)


def test_fake_missing_file_is_malformed() -> None:
    outcome = FakeRecognizer(SEQ_BASIC).recognize(JPEG, frame_seq=99)

    assert isinstance(outcome, Malformed)
    assert "99" in outcome.raw


def test_fake_invalid_file_is_malformed(tmp_path: Path) -> None:
    (tmp_path / "1.json").write_text("{not json", encoding="utf-8")

    assert isinstance(FakeRecognizer(tmp_path).recognize(JPEG, frame_seq=1), Malformed)


def test_fake_delay_uses_injected_sleep() -> None:
    slept: list[float] = []

    FakeRecognizer(SEQ_BASIC, delay_s=0.75, sleep=slept.append).recognize(JPEG, frame_seq=1)
    FakeRecognizer(SEQ_BASIC, sleep=slept.append).recognize(JPEG, frame_seq=1)

    assert slept == [0.75]


def test_fake_rejects_negative_delay() -> None:
    with pytest.raises(ValueError):
        FakeRecognizer(SEQ_BASIC, delay_s=-1)


# --------------------------------------------------------------------------------------------
# Privacy and layering (REQ-NFR-04, CLAUDE.md conventions)
# --------------------------------------------------------------------------------------------

NETWORK_MODULES = {
    "socket", "ssl", "http", "urllib", "httpx", "httpx2", "requests", "aiohttp", "urllib3",
    "websocket", "websockets", "ftplib", "smtplib", "telnetlib", "xmlrpc", "grpc",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return {name.split(".")[0] for name in names}


def test_only_network_client_is_anthropic() -> None:
    sources = sorted(SRC.rglob("*.py"))
    assert sources

    offenders = {
        str(path.relative_to(SRC)): sorted(_imports(path) & NETWORK_MODULES) for path in sources
    }
    assert {path: mods for path, mods in offenders.items() if mods} == {}

    # Cloud-provider clients would send frames to AWS, Google or Azure instead of Anthropic.
    third_party = re.compile(r"\bAnthropic(Bedrock|Vertex|Foundry)\w*|\bBedrockMantle\w*")
    users = [p.relative_to(SRC).as_posix() for p in sources if third_party.search(p.read_text())]
    assert users == []

    # The one recognition client is anthropic.Anthropic, pinned to the Anthropic API.
    assert claude.API_BASE_URL == "https://api.anthropic.com"
    assert type(make_client(TEST_KEY)) is anthropic.Anthropic


@pytest.mark.parametrize("module", ["base.py", "prompt.py", "claude.py", "fake.py"])
def test_recognition_modules_do_not_import_qt(module: str) -> None:
    assert "PySide6" not in _imports(SRC / "recognition" / module)
