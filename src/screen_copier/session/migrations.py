"""Schema migrations for persisted sessions (architecture §10.4).

``MIGRATIONS[v]`` upgrades a raw session dict from schema version ``v`` to ``v + 1``. ``migrate``
applies them in sequence up to ``SCHEMA_VERSION``. A file from a newer app version raises
``UnsupportedVersion`` (REQ-SES-07).

Pure logic: must not import PySide6.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from screen_copier.models import SCHEMA_VERSION, UnsupportedVersion

Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: dict[int, Migration] = {}
"""Upgrades v → v+1. Empty while ``SCHEMA_VERSION`` is 1 (the first persisted format)."""


class MigrationError(ValueError):
    """The data cannot be migrated: missing or invalid version, or no migration step."""


def schema_version_of(data: Mapping[str, Any]) -> int:
    """Return ``data["schema_version"]``, or raise ``MigrationError`` if it is not an int."""
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise MigrationError(f"schema_version must be an integer, got {version!r}")
    return version


def migrate(
    data: dict[str, Any],
    *,
    migrations: Mapping[int, Migration] | None = None,
    target: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Upgrade ``data`` to schema version ``target`` and return the upgraded dict.

    The input dict is not modified. Raises ``UnsupportedVersion`` if the data is newer than
    ``target`` and ``MigrationError`` if the version is invalid or a step is missing.
    ``migrations`` defaults to ``MIGRATIONS``, looked up at call time.
    """
    registry = MIGRATIONS if migrations is None else migrations
    version = schema_version_of(data)
    if version > target:
        raise UnsupportedVersion(version, target)
    result = dict(data)
    while version < target:
        step = registry.get(version)
        if step is None:
            raise MigrationError(f"no migration from schema_version {version} to {version + 1}")
        result = dict(step(result))
        version += 1
        result["schema_version"] = version
    return result
