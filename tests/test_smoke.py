"""Smoke tests for the project scaffold (TASK-01)."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_app_module_imports() -> None:
    module = importlib.import_module("screen_copier.app")
    assert callable(module.main)


@pytest.mark.parametrize(
    "subpackage",
    ["camera", "preprocess", "recognition", "stitching", "output", "session", "config", "ui"],
)
def test_subpackages_import(subpackage: str) -> None:
    importlib.import_module(f"screen_copier.{subpackage}")


def test_gui_script_entry_point_declared() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    gui_scripts = data["project"]["gui-scripts"]
    assert gui_scripts["screen-copier"] == "screen_copier.app:main"
    # REQ-APP-01: a gui-script (not console script) so no console window opens.
    assert "screen-copier" not in data["project"].get("scripts", {})


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    from screen_copier.app import main

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_main_window_can_be_created(qtbot) -> None:  # type: ignore[no-untyped-def]
    from screen_copier.app import create_main_window

    window = create_main_window()
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle() == "Screen Copier"
    assert window.isVisible()
