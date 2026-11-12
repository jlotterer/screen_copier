import pytest

from screen_copier.output.language import (
    EXTENSIONS,
    extension_for,
    normalize_language,
    vote_language,
)


def test_vote_majority() -> None:
    assert vote_language(["python", "python", "text"]) == "python"


def test_vote_empty_is_unknown() -> None:
    assert vote_language([]) == "unknown"


def test_vote_tie_goes_to_most_recent() -> None:
    assert vote_language(["json", "python"]) == "python"
    assert vote_language(["python", "json"]) == "json"


def test_vote_normalizes_before_counting() -> None:
    assert vote_language(["py", "python", "json"]) == "python"
    assert vote_language(["json", "py", "python", "json", "json"]) == "json"


def test_vote_ignores_unknown_guesses() -> None:
    assert vote_language([None, None, "cobol", "python"]) == "python"
    assert vote_language([None, "", "cobol"]) == "unknown"


@pytest.mark.parametrize(
    ("guess", "expected"),
    [
        ("py", "python"),
        ("js", "javascript"),
        ("ts", "typescript"),
        ("c#", "csharp"),
        ("cs", "csharp"),
        ("c++", "cpp"),
        ("yml", "yaml"),
        ("bash", "shell"),
        ("sh", "shell"),
        ("ps1", "powershell"),
        ("plaintext", "text"),
        ("Python", "python"),
        (" JSON ", "json"),
        ("cobol", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_normalize_language(guess: str | None, expected: str) -> None:
    assert normalize_language(guess) == expected


@pytest.mark.parametrize(
    ("language", "ext"),
    [
        ("python", ".py"),
        ("json", ".json"),
        ("yaml", ".yaml"),
        ("toml", ".toml"),
        ("xml", ".xml"),
        ("html", ".html"),
        ("javascript", ".js"),
        ("typescript", ".ts"),
        ("java", ".java"),
        ("csharp", ".cs"),
        ("cpp", ".cpp"),
        ("c", ".c"),
        ("go", ".go"),
        ("rust", ".rs"),
        ("sql", ".sql"),
        ("shell", ".sh"),
        ("powershell", ".ps1"),
        ("markdown", ".md"),
        ("text", ".txt"),
        ("unknown", ".txt"),
        ("cobol", ".txt"),
    ],
)
def test_extension_for(language: str, ext: str) -> None:
    assert extension_for(language) == ext


def test_every_language_has_an_extension() -> None:
    assert all(ext.startswith(".") for ext in EXTENSIONS.values())
