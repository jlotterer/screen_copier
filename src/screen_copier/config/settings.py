"""User settings: model, tolerant load and atomic save (architecture §8.1, REQ-SES-01).

Pure logic: must not import PySide6. The API key is never stored here (see ``secrets.py``).
"""

from __future__ import annotations

import json
import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from screen_copier.config.paths import atomic_write_text, data_dir
from screen_copier.models import CaptureMode

logger = logging.getLogger(__name__)

SETTINGS_FILE_NAME = "settings.json"
SETTINGS_BACKUP_NAME = "settings.bak.json"
SETTINGS_SCHEMA_VERSION = 1
DEFAULT_MODEL = "claude-sonnet-5-5"


class LineEnding(StrEnum):
    LF = "lf"
    CRLF = "crlf"


class Settings(BaseModel):
    """Every user-tunable setting with its default (architecture §8.1)."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    schema_version: int = Field(default=SETTINGS_SCHEMA_VERSION, ge=1)
    camera_id: int = Field(default=0, ge=0)
    auto_exposure: bool = True
    exposure: float | None = None
    capture_mode: CaptureMode = CaptureMode.AUTO
    stable_duration_ms: int = Field(default=600, ge=0)
    stability_threshold: float = Field(default=2.0, ge=0)
    motion_threshold: float = Field(default=6.0, ge=0)
    sharpness_threshold: float = Field(default=100.0, ge=0)
    duplicate_threshold: float = Field(default=1.0, ge=0)
    max_image_edge_px: int = Field(default=1568, gt=0)
    model: str = Field(default=DEFAULT_MODEL, min_length=1)
    max_concurrent_requests: int = Field(default=3, ge=1)
    min_overlap_lines: int = Field(default=2, ge=1)
    line_similarity: float = Field(default=0.90, ge=0, le=1)
    line_ending: LineEnding = LineEnding.LF
    sound_enabled: bool = True
    session_retention_days: int = Field(default=7, ge=0)
    consent_acknowledged: bool = False


def settings_path(directory: Path | None = None) -> Path:
    """Return ``<data_dir>/settings.json``."""
    return (directory if directory is not None else data_dir()) / SETTINGS_FILE_NAME


def _valid_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known keys whose value validates on its own; drop the rest with a warning."""
    kept: dict[str, Any] = {}
    for name, value in raw.items():
        if name not in Settings.model_fields:
            logger.info("Ignoring unknown setting %r", name)
            continue
        try:
            Settings.model_validate({name: value})
        except ValidationError:
            logger.warning("Invalid value for setting %r; using the default", name)
            continue
        kept[name] = value
    return kept


def load_settings(directory: Path | None = None) -> Settings:
    """Load settings, never raising for bad content.

    Unknown keys are ignored and each invalid value falls back to its default. A file that is not
    a JSON object is renamed to ``settings.bak.json`` and all defaults are used.
    """
    path = settings_path(directory)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Settings()
    except OSError:
        logger.exception("Could not read %s; using default settings", path)
        return Settings()

    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        backup = path.with_name(SETTINGS_BACKUP_NAME)
        logger.warning("Settings file is not valid JSON; moving it to %s", backup.name)
        try:
            os.replace(path, backup)
        except OSError:
            logger.exception("Could not move the damaged settings file aside")
        return Settings()

    return Settings.model_validate(_valid_fields(raw))


def save_settings(settings: Settings, directory: Path | None = None) -> None:
    """Write settings to ``<data_dir>/settings.json`` atomically."""
    text = json.dumps(settings.model_dump(mode="json"), indent=2) + "\n"
    atomic_write_text(settings_path(directory), text)
