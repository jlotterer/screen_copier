"""Shared fixtures for preprocessing tests: loads the synthetic image generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_GENERATOR = Path(__file__).resolve().parents[1] / "fixtures" / "images" / "make_synthetic.py"


def _load_generator() -> ModuleType:
    name = "screen_copier_test_make_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def synth() -> ModuleType:
    """The ``make_synthetic`` module (tests/fixtures/images/make_synthetic.py)."""
    return _load_generator()
