from __future__ import annotations

from pathlib import Path

from common.config import PROJECT_ROOT
from tracker import autostart_windows, tray


def test_autostart_prefers_pyw_launcher():
    script = Path(__file__).resolve().parents[1] / "tracker" / "main.py"

    assert autostart_windows._preferred_windowed_script(script).name == "main.pyw"


def test_autostart_remove_shortcut(tmp_path, monkeypatch):
    startup = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    shortcut = startup / "ArbeitszeitTracker.lnk"
    shortcut.write_text("shortcut", encoding="utf-8")
    monkeypatch.setattr(autostart_windows.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    removed = autostart_windows.configure_startup_shortcut(False)

    assert removed == shortcut
    assert not shortcut.exists()


def test_tracker_startup_prefers_root_launcher():
    from tracker.main import _startup_launcher_path

    assert _startup_launcher_path() == PROJECT_ROOT / "main.pyw"


def test_tray_uses_pyw_app_launcher_on_windows(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")

    assert tray._app_script_path().name == "main.pyw"
    assert tray._app_script_path().parent == PROJECT_ROOT


def test_tray_prefers_pythonw_on_windows(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.touch()
    pythonw.touch()
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(tray.sys, "executable", str(python))

    assert tray._windowed_python_executable() == str(pythonw)


def test_tray_launches_same_executable_when_frozen(monkeypatch):
    monkeypatch.setattr(tray.sys, "executable", r"C:\Tools\ArbeitszeitTracker.exe")
    monkeypatch.setattr(tray.sys, "frozen", True, raising=False)

    assert tray._app_launch_command("settings") == [
        r"C:\Tools\ArbeitszeitTracker.exe",
        "--app",
        "--view",
        "settings",
    ]


def test_tray_uses_app_script_when_not_frozen(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.delattr(tray.sys, "frozen", raising=False)

    command = tray._app_launch_command("vacation")

    assert command[-4:] == [str(PROJECT_ROOT / "main.pyw"), "--app", "--view", "vacation"]
