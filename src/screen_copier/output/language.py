"""Language normalization, voting and file extensions (architecture §13.1).

Pure logic: must not import PySide6.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

UNKNOWN = "unknown"

EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "json": ".json",
    "yaml": ".yaml",
    "toml": ".toml",
    "xml": ".xml",
    "html": ".html",
    "javascript": ".js",
    "typescript": ".ts",
    "java": ".java",
    "csharp": ".cs",
    "cpp": ".cpp",
    "c": ".c",
    "go": ".go",
    "rust": ".rs",
    "sql": ".sql",
    "shell": ".sh",
    "powershell": ".ps1",
    "markdown": ".md",
    "text": ".txt",
    UNKNOWN: ".txt",
}
"""Every canonical language name and its file extension."""

ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "c#": "csharp",
    "cs": "csharp",
    "c++": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "yml": "yaml",
    "bash": "shell",
    "sh": "shell",
    "zsh": "shell",
    "ps1": "powershell",
    "pwsh": "powershell",
    "md": "markdown",
    "htm": "html",
    "golang": "go",
    "rs": "rust",
    "plaintext": "text",
    "plain": "text",
    "txt": "text",
}
"""Alternative spellings mapped to a canonical name in ``EXTENSIONS``."""


def normalize_language(guess: str | None) -> str:
    """Map a raw language guess to a canonical name; unmapped guesses become ``unknown``."""
    if guess is None:
        return UNKNOWN
    key = guess.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in EXTENSIONS else UNKNOWN


def vote_language(guesses_in_capture_order: Sequence[str | None]) -> str:
    """Return the most common normalized guess; ties go to the most recent; ``[]`` → ``unknown``.

    Guesses that normalize to ``unknown`` (``None``, empty, unmapped) do not vote, so a few
    unreadable frames never outvote a real language.
    """
    normalized = [normalize_language(g) for g in guesses_in_capture_order]
    normalized = [language for language in normalized if language != UNKNOWN]
    if not normalized:
        return UNKNOWN
    counts = Counter(normalized)
    best = max(counts.values())
    for language in reversed(normalized):
        if counts[language] == best:
            return language
    return UNKNOWN  # pragma: no cover - unreachable


def extension_for(language: str) -> str:
    """Return the file extension (with the dot) for a language; unknown languages get ``.txt``."""
    return EXTENSIONS[normalize_language(language)]
