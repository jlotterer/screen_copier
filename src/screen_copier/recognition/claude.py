"""Single-frame recognizer backed by the Claude vision API (architecture §7.3, decision 0006).

``anthropic.Anthropic`` is the app's only network client (REQ-NFR-04). It is built only by
``make_client`` here, with SDK retries off because the queue owns retries (§7.4).

The client is injected through a factory, so tests never touch the network.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Final, Protocol

import anthropic
from pydantic import ValidationError

from screen_copier.models import FrameResult, Usage
from screen_copier.recognition.base import (
    AuthError,
    BadRequest,
    Malformed,
    Ok,
    RecognitionOutcome,
    Retryable,
)
from screen_copier.recognition.prompt import (
    REPORT_TOOL,
    REPORT_TOOL_NAME,
    SYSTEM_PROMPT,
    USER_TEXT,
)

log = logging.getLogger(__name__)

API_BASE_URL: Final = "https://api.anthropic.com"
"""Pinned so ``ANTHROPIC_BASE_URL`` in the environment can't send images elsewhere (REQ-NFR-04)."""

REQUEST_TIMEOUT_S: Final = 60.0
"""Per-request timeout from §7.3."""

MAX_TOKENS: Final = 8192
"""Response token ceiling from §7.3."""

RETRYABLE_STATUS: Final = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
AUTH_STATUS: Final = frozenset({401, 403})
MAX_RAW_CHARS: Final = 2000
"""Malformed.raw is truncated to this many characters so logs stay readable."""


class MissingApiKeyError(Exception):
    """Raised by a client factory when no API key is configured."""


class RecognizerSettings(Protocol):
    """The part of the app settings (§8.1) this module reads. ``model`` is read on every call."""

    @property
    def model(self) -> str: ...


ClientFactory = Callable[[], anthropic.Anthropic]
"""Returns the client to use for the next request. May raise ``MissingApiKeyError``."""


def make_client(api_key: str) -> anthropic.Anthropic:
    """Build the Anthropic client exactly as §7.3 specifies."""
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=API_BASE_URL,
        max_retries=0,
        timeout=REQUEST_TIMEOUT_S,
    )


def client_factory_for(key_provider: Callable[[], str | None]) -> ClientFactory:
    """Make a thread-safe factory that reuses one client until the API key changes.

    ``key_provider`` is called on every request, so a key changed in Settings takes effect on the
    next frame without restarting the app.
    """
    lock = threading.Lock()
    cached: dict[str, anthropic.Anthropic] = {}

    def factory() -> anthropic.Anthropic:
        key = key_provider()
        if not key:
            raise MissingApiKeyError("No Anthropic API key is set.")
        with lock:
            client = cached.get(key)
            if client is None:
                # Not closed: another worker may still be mid-request on the old client.
                cached.clear()
                client = cached[key] = make_client(key)
            return client

    return factory


def build_request(jpeg: bytes, model: str) -> dict[str, Any]:
    """The ``messages.create`` keyword arguments for one frame (§7.3)."""
    data = base64.standard_b64encode(jpeg).decode("ascii")
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": [REPORT_TOOL],
        "tool_choice": {"type": "auto"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
                    },
                    {"type": "text", "text": USER_TEXT},
                ],
            }
        ],
    }


class ClaudeRecognizer:
    """Sends one JPEG to Claude and maps the reply to a ``RecognitionOutcome``. Never retries."""

    def __init__(self, client_factory: ClientFactory, settings: RecognizerSettings) -> None:
        self._client_factory = client_factory
        self._settings = settings

    def recognize(self, jpeg: bytes, *, frame_seq: int) -> RecognitionOutcome:
        try:
            client = self._client_factory()
        except MissingApiKeyError as exc:
            return AuthError(str(exc))

        try:
            message = client.messages.create(**build_request(jpeg, self._settings.model))
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            timed_out = isinstance(exc, anthropic.APITimeoutError)
            reason = "Request timed out" if timed_out else "Connection error"
            log.info("frame %d: %s", frame_seq, reason)
            return Retryable(reason, connection_error=True)
        except anthropic.APIStatusError as exc:
            return _map_status_error(exc, frame_seq)
        except anthropic.APIResponseValidationError as exc:
            log.warning("frame %d: response did not match the SDK schema", frame_seq)
            return Malformed(_truncate(str(exc)))

        return _parse_message(message, frame_seq)


def _map_status_error(exc: anthropic.APIStatusError, frame_seq: int) -> RecognitionOutcome:
    status = exc.status_code
    message = _api_error_message(exc)
    request_id = exc.request_id or "-"
    log.warning("frame %d: HTTP %d (request %s): %s", frame_seq, status, request_id, message)
    if status in AUTH_STATUS:
        return AuthError(message)
    if status in RETRYABLE_STATUS or status >= 500:
        return Retryable(f"HTTP {status}: {message}")
    # 400, 404, 413 and any other client error: the same request would fail again.
    return BadRequest(message)


def _api_error_message(exc: anthropic.APIStatusError) -> str:
    """The API's own error text (``{"error": {"message": ...}}``), else the SDK's summary."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return exc.message


def _parse_message(message: anthropic.types.Message, frame_seq: int) -> RecognitionOutcome:
    if message.stop_reason in ("max_tokens", "refusal"):
        log.warning("frame %d: stop_reason %s", frame_seq, message.stop_reason)
        return Malformed(_dump(message))

    block = next(
        (b for b in message.content if b.type == "tool_use" and b.name == REPORT_TOOL_NAME),
        None,
    )
    if block is None:
        log.warning("frame %d: no %s tool call in response", frame_seq, REPORT_TOOL_NAME)
        return Malformed(_dump(message))

    try:
        result = FrameResult.model_validate(block.input)
    except ValidationError as exc:
        log.warning("frame %d: invalid tool input: %s", frame_seq, exc.error_count())
        return Malformed(_truncate(_json(block.input)))

    usage = Usage(
        input_tokens=message.usage.input_tokens or 0,
        output_tokens=message.usage.output_tokens or 0,
    )
    return Ok(result, usage)


def _dump(message: anthropic.types.Message) -> str:
    try:
        return _truncate(message.to_json(indent=None))
    except Exception:  # a hand-built or partial message object; raw is diagnostic only
        return _truncate(repr(message))


def _json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_RAW_CHARS else text[:MAX_RAW_CHARS] + "…"
