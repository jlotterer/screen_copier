"""Tests for the session schema migration registry (T011, architecture §10.4)."""

from __future__ import annotations

from typing import Any

import pytest

from screen_copier.models import SCHEMA_VERSION, UnsupportedVersion
from screen_copier.session import migrations
from screen_copier.session.migrations import MIGRATIONS, MigrationError, migrate


def test_current_version_is_unchanged() -> None:
    data = {"schema_version": SCHEMA_VERSION, "x": 1}
    assert migrate(data) == data


def test_newer_version_raises_unsupported() -> None:
    with pytest.raises(UnsupportedVersion) as info:
        migrate({"schema_version": 99})
    assert info.value.found == 99
    assert info.value.supported == SCHEMA_VERSION


@pytest.mark.parametrize("version", [None, "1", 1.0, True])
def test_invalid_version_raises(version: object) -> None:
    with pytest.raises(MigrationError):
        migrate({"schema_version": version})


def test_missing_version_raises() -> None:
    with pytest.raises(MigrationError):
        migrate({})


def test_missing_step_raises() -> None:
    with pytest.raises(MigrationError):
        migrate({"schema_version": 0}, migrations={}, target=1)


def test_steps_apply_in_sequence_without_mutating_input() -> None:
    calls: list[int] = []

    def step(version: int) -> Any:
        def run(data: dict[str, Any]) -> dict[str, Any]:
            calls.append(version)
            assert data["schema_version"] == version
            return {**data, "trail": [*data.get("trail", []), version]}

        return run

    original = {"schema_version": 1}
    result = migrate(original, migrations={1: step(1), 2: step(2), 3: step(3)}, target=4)
    assert calls == [1, 2, 3]
    assert result == {"schema_version": 4, "trail": [1, 2, 3]}
    assert original == {"schema_version": 1}


def test_default_registry_is_looked_up_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(MIGRATIONS, 0, lambda d: {**d, "upgraded": True})
    assert migrate({"schema_version": 0}) == {"schema_version": 1, "upgraded": True}


def test_registry_covers_every_version_below_current() -> None:
    first = min([*MIGRATIONS, SCHEMA_VERSION])
    assert all(v in MIGRATIONS for v in range(first, SCHEMA_VERSION))
    assert all(v < SCHEMA_VERSION for v in migrations.MIGRATIONS)
