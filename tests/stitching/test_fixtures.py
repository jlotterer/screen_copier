"""Generic runner for the stitching fixtures (TASK-03 spec, REQ-STI-01).

Every fixture in ``tests/fixtures/stitching/`` is stitched incrementally and in batch, and the
result is compared with ``expected.txt``, ``expected_events``, ``expected_conflicts`` and
``expected_flags``. Overlap-mode and line-number-mode fixtures (``uses_line_numbers: true``)
run the same way; none is ``xfail``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from screen_copier.config.settings import Settings
from screen_copier.models import DocLine, DocLineFlag, Document, FrameResult, StitchEvent
from screen_copier.stitching.stitcher import Stitcher, render, render_items, stitch

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stitching"


@dataclass(frozen=True)
class Fixture:
    name: str
    path: Path
    meta: dict[str, Any]
    frames: list[tuple[int, FrameResult]]

    def text(self, name: str) -> str:
        return (self.path / name).read_text(encoding="utf-8")


def _load(path: Path) -> Fixture:
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    frame_files = sorted((path / "frames").glob("*.json"), key=lambda p: int(p.stem))
    frames = [
        (int(p.stem), FrameResult.model_validate_json(p.read_text(encoding="utf-8")))
        for p in frame_files
    ]
    return Fixture(path.name, path, meta, frames)


def _params() -> list[Any]:
    return [
        pytest.param(fixture, id=fixture.name)
        for fixture in (
            _load(path)
            for path in sorted(p for p in FIXTURES.iterdir() if (p / "meta.json").is_file())
        )
    ]


FIXTURE_PARAMS = _params()


def test_fixtures_found() -> None:
    assert len(FIXTURE_PARAMS) >= 12
    numbered = [p for p in FIXTURE_PARAMS if p.values[0].meta["uses_line_numbers"]]
    assert len(numbered) >= 4


def test_no_fixture_is_xfail() -> None:
    assert all(not p.marks for p in FIXTURE_PARAMS)


# --------------------------------------------------------------------------------------------
# Comparison helpers
# --------------------------------------------------------------------------------------------


def _events_by_frame(events: list[dict[str, Any]] | list[StitchEvent]) -> list[tuple[int, set]]:
    """Events grouped by frame in order; within one frame the order doesn't matter."""
    grouped: dict[int, set[str]] = {}
    for event in events:
        if isinstance(event, StitchEvent):
            seq, kind = event.frame_seq, event.kind.value
        else:
            seq, kind = event["frame_seq"], event["kind"]
        grouped.setdefault(seq, set()).add(kind)
    return list(grouped.items())


def _line_items(document: Document) -> list[tuple[int, DocLine]]:
    return [
        (number, item)
        for number, item in enumerate(render_items(document), start=1)
        if isinstance(item, DocLine)
    ]


def _flags(document: Document) -> list[dict[str, Any]]:
    return [
        {"line": number, "flags": sorted(flag.value for flag in line.flags)}
        for number, line in _line_items(document)
        if line.flags
    ]


def _conflicts(document: Document) -> list[dict[str, Any]]:
    return [
        {"line": number, "alternatives": line.alternatives}
        for number, line in _line_items(document)
        if DocLineFlag.CONFLICT in line.flags
    ]


def _check(fixture: Fixture, document: Document, events: list[StitchEvent]) -> None:
    meta = fixture.meta
    assert render(document) == fixture.text("expected.txt")
    assert _events_by_frame(events) == _events_by_frame(meta["expected_events"])
    assert _conflicts(document) == meta["expected_conflicts"]
    expected_flags = [
        {"line": f["line"], "flags": sorted(f["flags"])} for f in meta.get("expected_flags", [])
    ]
    assert _flags(document) == expected_flags


# --------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURE_PARAMS)
def test_incremental(fixture: Fixture) -> None:
    stitcher = Stitcher(Settings())
    events: list[StitchEvent] = []
    for seq, result in fixture.frames:
        events.extend(stitcher.add(seq, result))
        stitcher.render()  # the live view renders after every add; must not disturb state
    _check(fixture, stitcher.document(), events)
    assert stitcher.render() == fixture.text("expected.txt")


@pytest.mark.parametrize("fixture", FIXTURE_PARAMS)
def test_batch(fixture: Fixture) -> None:
    output = stitch(fixture.frames, Settings())
    _check(fixture, output.document, output.events)


@pytest.mark.parametrize("fixture", FIXTURE_PARAMS)
def test_incremental_equals_batch(fixture: Fixture) -> None:
    stitcher = Stitcher(Settings())
    for seq, result in fixture.frames:
        stitcher.add(seq, result)
    assert stitcher.document() == stitch(fixture.frames, Settings()).document


@pytest.mark.parametrize("fixture", [p for p in FIXTURE_PARAMS if "discard" in p.values[0].meta])
def test_discard_restitch(fixture: Fixture) -> None:
    """REQ-STI-10: discarding frames equals a document built without them."""
    discard = set(fixture.meta["discard"])
    remaining = [(seq, result) for seq, result in fixture.frames if seq not in discard]
    output = stitch(remaining, Settings())
    assert render(output.document) == fixture.text(fixture.meta["expected_after_discard"])
    assert _events_by_frame(output.events) == _events_by_frame(
        fixture.meta["expected_events_after_discard"]
    )
