"""Structural checks for the stitching fixtures (TASK-03).

Plain ``json`` only, on purpose: this must not depend on ``screen_copier.models`` (TASK-02 ran in
parallel). TASK-08 additionally validates the frames through the pydantic models and runs the
stitcher against ``expected.txt``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

STITCHING = Path(__file__).parent / "stitching"

FIXTURE_NAMES = (
    "seq_basic",
    "seq_partial_lines",
    "seq_no_overlap",
    "seq_bridge",
    "seq_ambiguous_braces",
    "seq_scroll_back",
    "seq_conflicts",
    "seq_linenums_gap",
    "seq_linenums_start_missing",
    "seq_soft_wrap",
    "seq_mixed_numbering",
    "seq_discard",
)

# Fixtures whose expected output is exactly the true file (the "no issues" sequences).
EXPECTED_EQUALS_SOURCE = {
    "seq_basic",
    "seq_partial_lines",
    "seq_bridge",
    "seq_scroll_back",
    "seq_mixed_numbering",
}

# architecture §7.2 REPORT_TOOL
FRAME_KEYS = {"has_line_numbers", "word_wrap_detected", "lines"}
FRAME_OPTIONAL_KEYS = {"language_guess"}
LINE_KEYS = {"text", "line_number", "partial", "cut_off_right", "uncertain"}
LINE_OPTIONAL_KEYS = {"uncertain_note"}

EVENT_KINDS = {"gap_detected", "gap_closed", "ambiguous_overlap", "cut_off", "word_wrap"}
FLAGS = {"uncertain", "conflict", "cut_off", "soft_wrap"}
META_KEYS = {
    "description",
    "uses_line_numbers",
    "expected_events",
    "expected_conflicts",
    "expected_flags",
}
DISCARD_KEYS = {"discard", "expected_after_discard", "expected_events_after_discard"}

GAP_LINE = re.compile(r"<<<SCREEN-COPIER GAP: (.+)>>>")
GAP_DESCRIPTION = re.compile(
    r"unknown lines between capture [1-9]\d* and [1-9]\d*|lines [1-9]\d*\u2013[1-9]\d* missing"
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    assert b"\r" not in raw, f"{path} must use LF line endings"
    assert raw.endswith(b"\n"), f"{path} must end with a newline"
    return raw.decode("utf-8").split("\n")[:-1]


def _frame_files(name: str) -> list[Path]:
    return sorted((STITCHING / name / "frames").glob("*.json"), key=lambda p: int(p.stem))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def test_every_fixture_folder_exists() -> None:
    for name in FIXTURE_NAMES:
        folder = STITCHING / name
        for required in ("source.txt", "expected.txt", "meta.json", "frames"):
            assert (folder / required).exists(), f"{name}/{required} is missing"
    assert (STITCHING / "README.md").is_file()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_frames_are_numbered_from_one(name: str) -> None:
    seqs = [int(path.stem) for path in _frame_files(name)]
    assert seqs, f"{name} has no frames"
    assert seqs == list(range(1, len(seqs) + 1))
    assert all(path.name == f"{int(path.stem)}.json" for path in _frame_files(name))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_frame_json_matches_report_tool_schema(name: str) -> None:
    for path in _frame_files(name):
        frame = _load_json(path)
        where = f"{name}/frames/{path.name}"
        assert isinstance(frame, dict), where
        assert frame.keys() >= FRAME_KEYS, f"{where}: missing {FRAME_KEYS - frame.keys()}"
        assert frame.keys() <= FRAME_KEYS | FRAME_OPTIONAL_KEYS, where
        assert isinstance(frame["has_line_numbers"], bool), where
        assert isinstance(frame["word_wrap_detected"], bool), where
        assert frame.get("language_guess") is None or isinstance(frame["language_guess"], str)
        assert isinstance(frame["lines"], list), where
        for index, line in enumerate(frame["lines"]):
            at = f"{where} line {index}"
            assert isinstance(line, dict), at
            assert line.keys() >= LINE_KEYS, f"{at}: missing {LINE_KEYS - line.keys()}"
            assert line.keys() <= LINE_KEYS | LINE_OPTIONAL_KEYS, at
            assert isinstance(line["text"], str), at
            assert "\n" not in line["text"] and "\r" not in line["text"], at
            assert "\t" not in line["text"], f"{at}: indentation must be spaces"
            number = line["line_number"]
            assert number is None or (_is_int(number) and number >= 1), at
            for key in ("partial", "cut_off_right", "uncertain"):
                assert isinstance(line[key], bool), f"{at}: {key}"
            note = line.get("uncertain_note")
            assert note is None or isinstance(note, str), at
            if not frame["has_line_numbers"]:
                assert number is None, f"{at}: line_number without has_line_numbers"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_meta_json_shape(name: str) -> None:
    meta = _load_json(STITCHING / name / "meta.json")
    frame_count = len(_frame_files(name))
    expected = _lines(STITCHING / name / "expected.txt")

    assert meta.keys() >= META_KEYS, f"missing {META_KEYS - meta.keys()}"
    assert meta.keys() <= META_KEYS | DISCARD_KEYS
    assert isinstance(meta["description"], str) and meta["description"]
    assert isinstance(meta["uses_line_numbers"], bool)

    for event in meta["expected_events"]:
        assert event.keys() == {"kind", "frame_seq"}
        assert event["kind"] in EVENT_KINDS
        assert _is_int(event["frame_seq"]) and 1 <= event["frame_seq"] <= frame_count

    flagged = {}
    for entry in meta["expected_flags"]:
        assert entry.keys() == {"line", "flags"}
        assert _is_int(entry["line"]) and 1 <= entry["line"] <= len(expected)
        assert entry["flags"] and set(entry["flags"]) <= FLAGS
        assert entry["line"] not in flagged, "each line is listed once"
        flagged[entry["line"]] = set(entry["flags"])

    for conflict in meta["expected_conflicts"]:
        assert conflict.keys() == {"line", "alternatives"}
        line = conflict["line"]
        alternatives = conflict["alternatives"]
        assert _is_int(line) and 1 <= line <= len(expected)
        assert len(alternatives) >= 2 and len(set(alternatives)) == len(alternatives)
        assert all(isinstance(alt, str) for alt in alternatives)
        assert expected[line - 1] in alternatives, "the rendered text is one of the alternatives"
        assert "conflict" in flagged.get(line, set()), "a conflict line carries the conflict flag"
    conflict_lines = {c["line"] for c in meta["expected_conflicts"]}
    assert {n for n, flags in flagged.items() if "conflict" in flags} == conflict_lines


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_gap_markers_are_well_formed(name: str) -> None:
    meta = _load_json(STITCHING / name / "meta.json")
    outputs = [STITCHING / name / "expected.txt"]
    if "expected_after_discard" in meta:
        outputs.append(STITCHING / name / meta["expected_after_discard"])
    for path in outputs:
        for line in _lines(path):
            assert "\t" not in line, f"{path.name}: tabs are never produced"
            if "SCREEN-COPIER GAP" in line:
                match = GAP_LINE.fullmatch(line)
                assert match, f"malformed gap marker: {line!r}"
                assert GAP_DESCRIPTION.fullmatch(match.group(1)), line


def test_discard_fixture() -> None:
    meta = _load_json(STITCHING / "seq_discard" / "meta.json")
    frame_count = len(_frame_files("seq_discard"))
    assert meta["discard"] == [2]
    assert all(_is_int(seq) and 1 <= seq <= frame_count for seq in meta["discard"])
    after = STITCHING / "seq_discard" / meta["expected_after_discard"]
    assert _lines(after) == _lines(STITCHING / "seq_discard" / "source.txt")
    assert meta["expected_events_after_discard"] == []
    for name in FIXTURE_NAMES:
        if name != "seq_discard":
            assert not DISCARD_KEYS & _load_json(STITCHING / name / "meta.json").keys()


def _is_clean(name: str) -> bool:
    """No gap marker, no conflict, no soft-wrapped line: the output must be the source."""
    meta = _load_json(STITCHING / name / "meta.json")
    has_gap = any(GAP_LINE.fullmatch(line) for line in _lines(STITCHING / name / "expected.txt"))
    soft_wrapped = any("soft_wrap" in entry["flags"] for entry in meta["expected_flags"])
    return not has_gap and not meta["expected_conflicts"] and not soft_wrapped


def test_clean_fixture_list_is_right() -> None:
    assert {name for name in FIXTURE_NAMES if _is_clean(name)} == EXPECTED_EQUALS_SOURCE


@pytest.mark.parametrize("name", sorted(EXPECTED_EQUALS_SOURCE))
def test_expected_equals_source_without_gaps_or_conflicts(name: str) -> None:
    expected = (STITCHING / name / "expected.txt").read_bytes()
    assert expected == (STITCHING / name / "source.txt").read_bytes()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_source_is_realistic_code(name: str) -> None:
    source = _lines(STITCHING / name / "source.txt")
    assert 40 <= len(source) <= 150
    assert all("\t" not in line for line in source)


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_frames", STITCHING / "make_frames.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_generator_covers_every_fixture() -> None:
    generator = _load_generator()
    assert tuple(fixture.name for fixture in generator.FIXTURES) == FIXTURE_NAMES


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_committed_files_match_generator(name: str) -> None:
    """The committed JSON and expected output are reproducible from make_frames.py."""
    generator = _load_generator()
    fixture = next(f for f in generator.FIXTURES if f.name == name)
    generated = generator.build(fixture)
    folder = STITCHING / name
    for rel, content in generated.items():
        assert (folder / rel).read_bytes() == content.encode("utf-8"), (
            f"{name}/{rel} is stale: run `uv run python tests/fixtures/stitching/make_frames.py`"
        )
    committed_frames = {f"frames/{path.name}" for path in _frame_files(name)}
    assert committed_frames == {rel for rel in generated if rel.startswith("frames/")}
