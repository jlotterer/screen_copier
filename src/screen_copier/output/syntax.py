"""Syntax checks for Python, JSON, YAML, TOML and XML (architecture §13.2).

Pure logic: must not import PySide6. Gap marker lines are blanked before parsing so the line
numbers reported match the editor's lines.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from screen_copier.output.language import normalize_language
from screen_copier.stitching.stitcher import GAP_MARKER

_GAP_PREFIX, _GAP_SUFFIX = GAP_MARKER.split("{description}")
_TOML_POSITION = re.compile(r"\s*\(at line (\d+), column (\d+)\)")


@dataclass(frozen=True)
class SyntaxIssue:
    """One syntax error. ``line`` is 1-based; ``column`` is 1-based or ``None`` if unknown."""

    line: int
    column: int | None
    message: str


def is_gap_marker(line: str) -> bool:
    """True if ``line`` is a gap marker line produced by the stitcher."""
    stripped = line.strip()
    return stripped.startswith(_GAP_PREFIX) and stripped.endswith(_GAP_SUFFIX)


def blank_gap_markers(text: str) -> str:
    """Replace every gap marker line with an empty line, keeping the line count."""
    lines = text.split("\n")
    return "\n".join("" if is_gap_marker(line.rstrip("\r")) else line for line in lines)


def _check_python(text: str) -> list[SyntaxIssue]:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return [SyntaxIssue(exc.lineno or 1, exc.offset, exc.msg)]
    return []


def _check_json(text: str) -> list[SyntaxIssue]:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return [SyntaxIssue(exc.lineno, exc.colno, exc.msg)]
    return []


def _check_yaml(text: str) -> list[SyntaxIssue]:
    try:
        yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        message = exc.problem or exc.context or str(exc)
        if mark is None:
            return [SyntaxIssue(1, None, message)]
        return [SyntaxIssue(mark.line + 1, mark.column + 1, message)]
    except yaml.YAMLError as exc:
        return [SyntaxIssue(1, None, str(exc))]
    return []


def _check_toml(text: str) -> list[SyntaxIssue]:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # Python 3.12/3.13 only put the position in the message: "... (at line L, column C)".
        message = str(exc)
        match = _TOML_POSITION.search(message)
        if match is None:
            return [SyntaxIssue(1, None, message)]
        line, column = int(match.group(1)), int(match.group(2))
        return [SyntaxIssue(line, column, message[: match.start()].rstrip())]
    return []


def _check_xml(text: str) -> list[SyntaxIssue]:
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        line, column = exc.position
        return [SyntaxIssue(line, column + 1, str(exc))]
    return []


_CHECKERS: dict[str, Callable[[str], list[SyntaxIssue]]] = {
    "python": _check_python,
    "json": _check_json,
    "yaml": _check_yaml,
    "toml": _check_toml,
    "xml": _check_xml,
}


def check(text: str, language: str) -> list[SyntaxIssue] | None:
    """Check ``text`` as ``language``. ``[]`` means valid; ``None`` means no checker exists."""
    checker = _CHECKERS.get(normalize_language(language))
    if checker is None:
        return None
    return checker(blank_gap_markers(text))
