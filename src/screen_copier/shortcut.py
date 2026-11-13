"""Desktop shortcut creation for ``screen-copier --create-shortcut`` (REQ-APP-02, architecture §15).

The shortcut is written by PowerShell's ``WScript.Shell`` COM object (pywin32 is banned), into
``[Environment]::GetFolderPath('Desktop')`` so OneDrive-redirected Desktops work. The target is
the resolved ``screen-copier`` executable and the icon is the bundled ``assets/icon.ico``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

SHORTCUT_NAME = "Screen Copier.lnk"
EXECUTABLE_NAME = "screen-copier"
DESCRIPTION = "Copy code from another screen with a webcam"


def icon_path() -> Path:
    """Filesystem path of the bundled application icon."""
    return Path(str(files("screen_copier") / "assets" / "icon.ico"))


def find_executable() -> Path | None:
    """Resolve the installed ``screen-copier`` launcher.

    ``shutil.which`` first; if the tool's bin directory is not on ``PATH``, fall back to the
    launcher this process was started from.
    """
    found = shutil.which(EXECUTABLE_NAME)
    if found:
        return Path(found).resolve()
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.stem == EXECUTABLE_NAME and argv0.is_file():
        return argv0.resolve()
    return None


def _ps_quote(value: str) -> str:
    """Quote ``value`` as a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def build_script(target: Path, icon: Path) -> str:
    """PowerShell script that creates the shortcut and writes its path to stdout."""
    return "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$desktop = [Environment]::GetFolderPath('Desktop')",
            f"$path = Join-Path $desktop {_ps_quote(SHORTCUT_NAME)}",
            "$shell = New-Object -ComObject WScript.Shell",
            "$lnk = $shell.CreateShortcut($path)",
            f"$lnk.TargetPath = {_ps_quote(str(target))}",
            f"$lnk.WorkingDirectory = {_ps_quote(str(target.parent))}",
            f"$lnk.IconLocation = {_ps_quote(f'{icon},0')}",
            f"$lnk.Description = {_ps_quote(DESCRIPTION)}",
            "$lnk.Save()",
            "Write-Output $path",
        ]
    )


def _attach_console() -> None:
    """Make ``print`` visible when started as a GUI-subsystem launcher from a terminal.

    ``[project.gui-scripts]`` launchers have no console, so ``sys.stdout`` is ``None``; attach
    to the parent terminal's console when there is one. No-op elsewhere.
    """
    if sys.platform != "win32" or (sys.stdout is not None and sys.stderr is not None):
        return
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
    except OSError:
        pass


def _fail(message: str) -> int:
    print(f"Could not create the desktop shortcut: {message}", file=sys.stderr)
    return 1


def create_shortcut() -> int:
    """Create the "Screen Copier" desktop shortcut. Returns 0 on success, 1 on failure."""
    _attach_console()
    target = find_executable()
    if target is None:
        return _fail(
            f"the '{EXECUTABLE_NAME}' executable was not found on PATH. "
            "Install it with 'uv tool install .' and run 'uv tool update-shell'."
        )
    icon = icon_path()
    script = build_script(target, icon)
    command = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return _fail(str(exc))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return _fail(detail or f"PowerShell exited with code {result.returncode}")
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    location = lines[-1] if lines else os.path.join("Desktop", SHORTCUT_NAME)
    print(f"Created shortcut: {location}")
    return 0
