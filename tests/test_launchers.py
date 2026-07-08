from __future__ import annotations

from pathlib import Path

from tracker import autostart_windows, tray


def test_autostart_prefers_pyw_launcher():
    script = Path(__file__).resolve().parents[1] / "tracker" / "main.py"

    assert autostart_windows._preferred_windowed_script(script).name == "main.pyw"


def test_tray_uses_pyw_app_launcher_on_windows(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")

    assert tray._app_script_path().name == "main.pyw"


def test_tray_prefers_pythonw_on_windows(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.touch()
    pythonw.touch()
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(tray.sys, "executable", str(python))

    assert tray._windowed_python_executable() == str(pythonw)
