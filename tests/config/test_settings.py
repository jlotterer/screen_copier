"""Tests for settings load/save (TASK-04, REQ-SES-01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from screen_copier.config import settings as settings_mod
from screen_copier.config.settings import (
    LineEnding,
    Settings,
    load_settings,
    save_settings,
)
from screen_copier.models import CaptureMode

EXPECTED_DEFAULTS = {
    "schema_version": 1,
    "camera_id": 0,
    "auto_exposure": True,
    "exposure": None,
    "capture_mode": "auto",
    "stable_duration_ms": 600,
    "stability_threshold": 2.0,
    "motion_threshold": 6.0,
    "sharpness_threshold": 100.0,
    "duplicate_threshold": 1.0,
    "max_image_edge_px": 1568,
    "model": "claude-sonnet-5-5",
    "max_concurrent_requests": 3,
    "min_overlap_lines": 2,
    "line_similarity": 0.90,
    "line_ending": "lf",
    "sound_enabled": True,
    "session_retention_days": 7,
    "consent_acknowledged": False,
}


def _write(directory: Path, content: str) -> Path:
    path = directory / "settings.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_match_architecture_table() -> None:
    assert Settings().model_dump(mode="json") == EXPECTED_DEFAULTS


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path) == Settings()


def test_req_ses_01_unknown_key_ignored_valid_key_applied(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"model": "claude-opus-5-5", "bogus": 1}))
    loaded = load_settings(tmp_path)
    assert loaded.model == "claude-opus-5-5"
    assert loaded.model_dump(mode="json") == {**EXPECTED_DEFAULTS, "model": "claude-opus-5-5"}
    assert not hasattr(loaded, "bogus")


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("camera_id", "front"),
        ("camera_id", -1),
        ("capture_mode", "sometimes"),
        ("line_similarity", 1.5),
        ("max_concurrent_requests", 0),
        ("line_ending", "cr"),
        ("model", ""),
        ("exposure", "bright"),
    ],
)
def test_invalid_value_falls_back_per_key(tmp_path: Path, key: str, bad_value: object) -> None:
    _write(tmp_path, json.dumps({key: bad_value, "sound_enabled": False}))
    loaded = load_settings(tmp_path)
    assert getattr(loaded, key) == getattr(Settings(), key)
    assert loaded.sound_enabled is False


@pytest.mark.parametrize("content", ["{not json", "", "[1, 2, 3]", '"just a string"'])
def test_corrupt_file_is_backed_up_and_defaults_used(tmp_path: Path, content: str) -> None:
    _write(tmp_path, content)
    assert load_settings(tmp_path) == Settings()
    backup = tmp_path / "settings.bak.json"
    assert backup.read_text(encoding="utf-8") == content
    assert not (tmp_path / "settings.json").exists()


def test_corrupt_file_replaces_older_backup(tmp_path: Path) -> None:
    (tmp_path / "settings.bak.json").write_text("older", encoding="utf-8")
    _write(tmp_path, "{broken")
    load_settings(tmp_path)
    assert (tmp_path / "settings.bak.json").read_text(encoding="utf-8") == "{broken"


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    original = Settings(
        camera_id=2,
        exposure=-5.0,
        capture_mode=CaptureMode.MANUAL,
        line_ending=LineEnding.CRLF,
        consent_acknowledged=True,
    )
    save_settings(original, tmp_path)
    assert load_settings(tmp_path) == original
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_save_uses_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(settings_mod, "atomic_write_text", lambda path, text: calls.append(path))
    save_settings(Settings(), tmp_path)
    assert calls == [tmp_path / "settings.json"]


def test_default_directory_comes_from_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCREEN_COPIER_DATA_DIR", str(tmp_path))
    save_settings(Settings(camera_id=4))
    assert (tmp_path / "settings.json").exists()
    assert load_settings().camera_id == 4


def test_assignment_is_validated() -> None:
    s = Settings()
    with pytest.raises(ValueError):
        s.line_similarity = 2.0
