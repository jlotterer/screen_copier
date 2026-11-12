import ast
from pathlib import Path

import pytest

from screen_copier.output.syntax import SyntaxIssue, blank_gap_markers, check
from screen_copier.stitching.stitcher import GAP_MARKER

OUTPUT_DIR = Path(__file__).parents[2] / "src" / "screen_copier" / "output"


def _python_with_missing_colon_at_12() -> str:
    lines = [f"x{i} = {i}" for i in range(1, 12)]  # lines 1..11
    lines.append("def f()")  # line 12: missing colon
    lines.append("    return 1")
    return "\n".join(lines) + "\n"


def test_python_missing_colon_line_12() -> None:
    issues = check(_python_with_missing_colon_at_12(), "python")
    assert issues is not None
    assert len(issues) == 1
    assert issues[0].line == 12
    assert issues[0].message


def test_python_gap_marker_keeps_line_numbers() -> None:
    lines = _python_with_missing_colon_at_12().split("\n")
    lines[2] = GAP_MARKER.format(description="unknown lines between capture 1 and 2")  # line 3
    text = "\n".join(lines)
    issues = check(text, "python")
    assert issues is not None
    assert [i.line for i in issues] == [12]


def test_gap_marker_alone_is_not_an_error() -> None:
    marker = GAP_MARKER.format(description="lines 4\u20139 missing")
    text = f"a = 1\n{marker}\nb = 2\n"
    assert check(text, "python") == []
    assert blank_gap_markers(text) == "a = 1\n\nb = 2\n"


VALID = {
    "python": "def f(x):\n    return x\n",
    "json": '{\n  "a": [1, 2, 3],\n  "b": null\n}\n',
    "yaml": "a: 1\nb:\n  - x\n  - y\n",
    "toml": '[tool]\nname = "x"\nvalues = [1, 2]\n',
    "xml": '<?xml version="1.0"?>\n<root a="1"><child>text</child></root>\n',
}


@pytest.mark.parametrize("language", sorted(VALID))
def test_valid_text_has_no_issues(language: str) -> None:
    assert check(VALID[language], language) == []


@pytest.mark.parametrize(
    ("language", "text", "line"),
    [
        ("json", '{\n  "a": 1,\n  "b" 2\n}\n', 3),
        ("yaml", "a: 1\nb: [1, 2\nc: 3\n", 3),
        ("toml", 'a = 1\nb = "x\n', 2),
        ("xml", "<root>\n  <a>\n</root>\n", 3),
    ],
)
def test_invalid_text_reports_line(language: str, text: str, line: int) -> None:
    issues = check(text, language)
    assert issues is not None and len(issues) == 1
    assert isinstance(issues[0], SyntaxIssue)
    assert issues[0].line == line
    assert issues[0].message


@pytest.mark.parametrize("language", ["json", "yaml", "toml", "xml"])
def test_gap_marker_blanked_for_all_checkers(language: str) -> None:
    marker = GAP_MARKER.format(description="unknown lines between capture 3 and 4")
    lines = VALID[language].split("\n")
    lines.insert(1, marker) if language != "xml" else lines.append(marker)
    assert check("\n".join(lines), language) == []


@pytest.mark.parametrize("language", ["rust", "javascript", "text", "unknown"])
def test_no_checker_returns_none(language: str) -> None:
    assert check("fn main() {}", language) is None


def test_output_modules_do_not_import_pyside6() -> None:
    files = sorted(OUTPUT_DIR.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith("PySide6"), f"{path.name} imports {name}"
