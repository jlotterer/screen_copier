"""Application entry point: CLI parsing and the Qt main window."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from PySide6.QtWidgets import QApplication, QMainWindow

APP_NAME = "Screen Copier"
DISTRIBUTION_NAME = "screen-copier"


def get_version() -> str:
    """Return the installed package version."""
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
        return "0+unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=DISTRIBUTION_NAME, description=APP_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=get_version(),
        help="print the version and exit",
    )
    return parser


def create_main_window() -> QMainWindow:
    """Create the (currently empty) main window. Requires a QApplication."""
    window = QMainWindow()
    window.setWindowTitle(APP_NAME)
    return window


def main(argv: Sequence[str] | None = None) -> int:
    """Run the app. Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        build_parser().parse_args(args)
    except SystemExit as exc:  # --version / --help / bad args
        return exc.code if isinstance(exc.code, int) else 0

    app = QApplication.instance() or QApplication([sys.argv[0], *args])
    app.setApplicationName(APP_NAME)
    window = create_main_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
