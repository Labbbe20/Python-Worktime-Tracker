"""Windows-only autostart setup via the user's Startup folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common.config import PROJECT_ROOT


APP_ICON_PATH = PROJECT_ROOT / "app" / "static" / "icons" / "app.ico"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def get_startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA ist nicht gesetzt; Startup-Ordner kann nicht gefunden werden.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def ensure_startup_shortcut(script_path: str | Path | None = None, name: str = "ArbeitszeitTracker.lnk") -> Path | None:
    """Create a per-user Startup-folder shortcut on Windows.

    No registry keys, scheduled tasks, or administrator privileges are used.
    """

    if not is_windows():
        return None
    startup = get_startup_folder()
    startup.mkdir(parents=True, exist_ok=True)
    shortcut_path = startup / name

    import win32com.client  # type: ignore

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    if getattr(sys, "frozen", False):
        shortcut.Targetpath = sys.executable
        shortcut.Arguments = ""
        shortcut.WorkingDirectory = str(Path(sys.executable).resolve().parent)
    else:
        target_script = _preferred_windowed_script(Path(script_path) if script_path else Path(__file__).resolve().parent / "main.py")
        shortcut.Targetpath = str(_windowed_python_executable())
        shortcut.Arguments = f'"{target_script}"'
        shortcut.WorkingDirectory = str(target_script.resolve().parents[1])
    shortcut.Description = "ArbeitszeitTracker Hintergrundprogramm"
    if getattr(sys, "frozen", False):
        shortcut.IconLocation = str(sys.executable)
    elif APP_ICON_PATH.exists():
        shortcut.IconLocation = str(APP_ICON_PATH)
    shortcut.save()
    return shortcut_path


def remove_startup_shortcut(name: str = "ArbeitszeitTracker.lnk") -> Path | None:
    """Remove the per-user Startup-folder shortcut on Windows if it exists."""

    if not is_windows():
        return None
    shortcut_path = get_startup_folder() / name
    try:
        shortcut_path.unlink()
    except FileNotFoundError:
        return None
    return shortcut_path


def configure_startup_shortcut(
    enabled: bool,
    script_path: str | Path | None = None,
    name: str = "ArbeitszeitTracker.lnk",
) -> Path | None:
    return ensure_startup_shortcut(script_path, name) if enabled else remove_startup_shortcut(name)


def _preferred_windowed_script(script_path: Path) -> Path:
    pyw_path = script_path.with_suffix(".pyw")
    return pyw_path if pyw_path.exists() else script_path


def _windowed_python_executable() -> Path:
    executable = Path(sys.executable)
    if is_windows():
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return executable
