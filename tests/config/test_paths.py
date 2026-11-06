"""Tests for data-dir resolution and atomic writes (TASK-04)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from screen_copier.config import paths


def test_env_override_wins_and_creates_folder(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data"
    result = paths.data_dir({paths.DATA_DIR_ENV: str(target)})
    assert result == target
    assert target.is_dir()


def test_falls_back_to_platformdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_user_data_dir(name: str, appauthor: object) -> str:
        calls.append((name, appauthor))
        return str(tmp_path / "platform")

    monkeypatch.setattr(paths.platformdirs, "user_data_dir", fake_user_data_dir)
    assert paths.data_dir({paths.DATA_DIR_ENV: "  "}) == tmp_path / "platform"
    assert calls == [("ScreenCopier", False)]


def test_reads_os_environ_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_sessions_and_logs_dirs_are_created(tmp_path: Path) -> None:
    env = {paths.DATA_DIR_ENV: str(tmp_path)}
    assert paths.sessions_dir(env) == tmp_path / "sessions"
    assert paths.logs_dir(env) == tmp_path / "logs"
    assert (tmp_path / "sessions").is_dir()
    assert (tmp_path / "logs").is_dir()


def test_atomic_write_replaces_content_and_leaves_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.json"
    paths.atomic_write_text(target, "one")
    paths.atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["file.json"]


def test_atomic_write_failure_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.json"
    target.write_text("old", encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        paths.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "file.json.tmp").exists()
