import os
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from screen_copier.config.settings import LineEnding
from screen_copier.output.writer import SaveError, default_filename, render_for_save, write_file
from screen_copier.stitching.stitcher import GAP_MARKER


def test_crlf_and_trailing_spaces() -> None:
    assert render_for_save("a  \nb", LineEnding.CRLF) == b"a\r\nb\r\n"


def test_lf_default() -> None:
    assert render_for_save("a  \nb", LineEnding.LF) == b"a\nb\n"


def test_input_crlf_is_normalized() -> None:
    assert render_for_save("a\r\nb\r\n", LineEnding.LF) == b"a\nb\n"


def test_trailing_blank_lines_dropped() -> None:
    assert render_for_save("x = 1\n\n\n\n", LineEnding.LF) == b"x = 1\n"
    assert render_for_save("x = 1\n  \n\t\n\n", LineEnding.CRLF) == b"x = 1\r\n"


def test_inner_blank_lines_kept() -> None:
    assert render_for_save("a\n\n\nb\n", LineEnding.LF) == b"a\n\n\nb\n"


@pytest.mark.parametrize("text", ["", "héllo wörld ✓ 日本\n", "﻿bom start\n", "x"])
def test_utf8_without_bom(text: str) -> None:
    data = render_for_save(text, LineEnding.LF)
    assert not data.startswith(b"\xef\xbb\xbf")
    data.decode("utf-8")
    assert data.endswith(b"\n")


def test_gap_marker_kept_verbatim() -> None:
    marker = GAP_MARKER.format(description="unknown lines between capture 2 and 3")
    data = render_for_save(f"a\n{marker}\nb\n", LineEnding.CRLF)
    assert f"\r\n{marker}\r\n".encode() in data


def test_default_filename_python() -> None:
    name = default_filename("python", datetime(2026, 3, 4, 5, 6, 7))
    assert re.fullmatch(r"capture-\d{8}-\d{6}\.py", name)
    assert name == "capture-20260304-050607.py"


def test_default_filename_unknown_language() -> None:
    assert default_filename("unknown", datetime(2026, 1, 2, 3, 4, 5)) == (
        "capture-20260102-030405.txt"
    )


def test_default_filename_aware_datetime_uses_local_time() -> None:
    now = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone(timedelta(hours=3)))
    expected = now.astimezone().strftime("capture-%Y%m%d-%H%M%S.json")
    assert default_filename("json", now) == expected
    assert re.fullmatch(r"capture-\d{8}-\d{6}\.json", default_filename("json", datetime.now(UTC)))


def test_write_file_writes_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "out.py"
    target.write_bytes(b"old")
    write_file(target, b"new\n")
    assert target.read_bytes() == b"new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["out.py"]


def test_write_file_missing_directory_raises_save_error(tmp_path: Path) -> None:
    with pytest.raises(SaveError) as info:
        write_file(tmp_path / "nope" / "out.py", b"x")
    assert info.value.os_message
    assert str(info.value) == info.value.os_message


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission bits are not enforced on Windows or for root",
)
def test_write_file_permission_denied_raises_save_error(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(SaveError) as info:
            write_file(locked / "out.py", b"x")
        assert info.value.os_message == os.strerror(13)  # "Permission denied"
        assert isinstance(info.value.__cause__, PermissionError)
        assert list(locked.iterdir()) == []
    finally:
        locked.chmod(0o700)


def test_write_file_permission_denied_simulated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Platform-independent version of the permission test: the OS refuses the temp file."""

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied", str(tmp_path))

    monkeypatch.setattr("screen_copier.output.writer.tempfile.mkstemp", deny)
    with pytest.raises(SaveError, match="Access is denied") as info:
        write_file(tmp_path / "out.py", b"x")
    assert info.value.path == tmp_path / "out.py"


def test_write_file_replace_failure_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("screen_copier.output.writer.os.replace", fail)
    with pytest.raises(SaveError):
        write_file(tmp_path / "out.py", b"x")
    assert list(tmp_path.iterdir()) == []
