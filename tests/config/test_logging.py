"""Tests for logging setup and redaction (TASK-04, REQ-APP-07, REQ-NFR-03)."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from screen_copier.config import logging as log_config
from screen_copier.config.secrets import set_api_key
from screen_copier.config.settings import Settings, save_settings

TEST_KEY = "sk-ant-test-0000"


@pytest.fixture
def handler(tmp_path: Path) -> Iterator[logging.Handler]:
    root = logging.getLogger()
    old_level = root.level
    h = log_config.setup_logging(tmp_path / "logs")
    yield h
    root.removeHandler(h)
    h.close()
    root.setLevel(old_level)


def _log_text(handler: logging.Handler) -> str:
    handler.flush()
    assert isinstance(handler, RotatingFileHandler)
    return Path(handler.baseFilename).read_text(encoding="utf-8")


def test_handler_configuration(tmp_path: Path, handler: logging.Handler) -> None:
    assert isinstance(handler, RotatingFileHandler)
    assert Path(handler.baseFilename) == tmp_path / "logs" / "screen-copier.log"
    assert handler.maxBytes == 1_000_000
    assert handler.backupCount == 5
    assert handler.level == logging.INFO
    assert handler in logging.getLogger().handlers


def test_req_nfr_03_key_in_message_is_redacted(handler: logging.Handler) -> None:
    logging.getLogger("t").info("key=sk-ant-abc123")
    text = _log_text(handler)
    assert "key=sk-ant-***" in text
    assert "abc123" not in text


def test_key_in_args_and_traceback_is_redacted(handler: logging.Handler) -> None:
    log = logging.getLogger("t")
    log.warning("auth header %s", f"Bearer {TEST_KEY}")
    try:
        raise RuntimeError(f"bad key {TEST_KEY}")
    except RuntimeError:
        log.exception("request failed")
    log.info("stack", stack_info=True, extra={"x": TEST_KEY})
    text = _log_text(handler)
    assert TEST_KEY not in text
    assert "Bearer sk-ant-***" in text
    assert "RuntimeError: bad key sk-ant-***" in text


def test_redact_handles_underscores_and_multiple_keys() -> None:
    assert log_config.redact("a sk-ant-api03_X-y b sk-ant-Z") == "a sk-ant-*** b sk-ant-***"


def test_setup_twice_does_not_duplicate(tmp_path: Path, handler: logging.Handler) -> None:
    second = log_config.setup_logging(tmp_path / "logs")
    try:
        ours = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
        assert ours == [second]
    finally:
        logging.getLogger().removeHandler(second)
        second.close()


def test_req_nfr_03_data_folder_never_contains_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scan everything the config layer writes for the test key."""
    monkeypatch.setenv("SCREEN_COPIER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    h = log_config.setup_logging()
    try:

        class MemKeyring:
            def set_password(self, *args: str) -> None:
                pass

        set_api_key(TEST_KEY, MemKeyring())  # type: ignore[arg-type]
        logging.getLogger("t").info("using %s", TEST_KEY)
        save_settings(Settings(model="claude-opus-5-5"))
        h.flush()
    finally:
        logging.getLogger().removeHandler(h)
        h.close()
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert TEST_KEY.encode() not in path.read_bytes(), path


def test_config_modules_do_not_import_pyside6() -> None:
    code = (
        "import sys\n"
        "import screen_copier.config.paths, screen_copier.config.settings\n"
        "import screen_copier.config.secrets, screen_copier.config.logging\n"
        "assert not any(m.split('.')[0] == 'PySide6' for m in sys.modules), 'PySide6 imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
