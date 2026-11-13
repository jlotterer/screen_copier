"""Tests for the desktop shortcut command, the bundled icon and the README (T020)."""

from __future__ import annotations

import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from screen_copier import app, shortcut

README = Path(__file__).resolve().parents[1] / "README.md"
EXE = Path("C:/Users/me/.local/bin/screen-copier.exe")


class FakeRun:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        return self.result


@pytest.fixture
def which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shortcut.shutil, "which", lambda name: str(EXE))


def test_create_shortcut_success(
    which: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lnk = r"C:\Users\me\OneDrive\Desktop\Screen Copier.lnk"
    run = FakeRun(0, stdout=lnk + "\r\n")
    monkeypatch.setattr(subprocess, "run", run)

    assert shortcut.create_shortcut() == 0

    assert len(run.calls) == 1
    cmd, _ = run.calls[0]
    assert cmd[0] == "powershell"
    assert "-NoProfile" in cmd
    assert cmd[-2] == "-Command"
    script = cmd[-1]
    assert "Screen Copier.lnk" in script
    assert "WScript.Shell" in script
    assert "CreateShortcut" in script
    assert "[Environment]::GetFolderPath('Desktop')" in script
    assert str(EXE.resolve()) in script
    assert str(shortcut.icon_path()) in script
    assert lnk in capsys.readouterr().out


def test_create_shortcut_failure_prints_stderr(
    which: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(1, stderr="Access to the path is denied."))

    assert shortcut.create_shortcut() == 1

    captured = capsys.readouterr()
    assert "Access to the path is denied." in captured.err
    assert "Created shortcut" not in captured.out


def test_create_shortcut_powershell_missing(
    which: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError("powershell not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert shortcut.create_shortcut() == 1
    assert "powershell not found" in capsys.readouterr().err


def test_create_shortcut_without_executable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(shortcut.shutil, "which", lambda name: None)
    monkeypatch.setattr(shortcut.sys, "argv", ["pytest"])
    run = FakeRun(0)
    monkeypatch.setattr(subprocess, "run", run)

    assert shortcut.create_shortcut() == 1
    assert run.calls == []
    assert "screen-copier" in capsys.readouterr().err


def test_build_script_quotes_single_quotes() -> None:
    script = shortcut.build_script(Path("C:/O'Brien/screen-copier.exe"), Path("C:/x/icon.ico"))
    assert "O''Brien" in script


@pytest.mark.parametrize("code", [0, 1])
def test_main_create_shortcut_flag(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    calls: list[None] = []

    def fake() -> int:
        calls.append(None)
        return code

    monkeypatch.setattr(shortcut, "create_shortcut", fake)
    monkeypatch.setattr(app, "start_app", lambda *a, **k: pytest.fail("app must not start"))

    assert app.main(["--create-shortcut"]) == code
    assert len(calls) == 1


def test_readme_has_required_sections() -> None:
    text = README.read_text(encoding="utf-8")
    headings = {line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")}
    for heading in ("Install", "API key", "Capture tips", "Troubleshooting", "Privacy"):
        assert heading in headings, heading


def test_readme_capture_tips_cover_requirement() -> None:
    text = README.read_text(encoding="utf-8")
    tips = ("line numbers on", "word wrap off", "14 px", "40\u201360 cm", "3 lines of overlap")
    for phrase in tips:
        assert phrase.lower() in text.lower(), phrase
    assert "uv tool install ." in text
    assert "screen-copier --create-shortcut" in text
    assert "ANTHROPIC_API_KEY" in text


def test_icon_is_bundled_package_data() -> None:
    icon = files("screen_copier") / "assets" / "icon.ico"
    assert icon.is_file()
    data = icon.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"  # ICO header
    assert shortcut.icon_path().is_file()
