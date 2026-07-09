"""Tray/menu-bar icon and menu actions."""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from pathlib import Path

from app.instance import another_instance_is_running, request_app_view
from common import database
from common.config import LOG_DIR, PROJECT_ROOT
from tracker.notify import NotificationCenter
from tracker.recorder import RecorderEvent, WorktimeRecorder


def run_tray(recorder: WorktimeRecorder, notifier: NotificationCenter, logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger("worktime.tracker.tray")
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as exc:
        raise RuntimeError("Tray-Icon benoetigt pystray und Pillow. Bitte requirements.txt installieren.") from exc

    def event_message(event: RecorderEvent | None) -> None:
        if not event:
            return
        suffix = f" - {event.location}" if event.location else ""
        notifier.show("ArbeitszeitTracker", f"{event.message} - {event.time[:5]} Uhr{suffix}")
        notifier.flush()

    def action_start(icon, item) -> None:
        event_message(recorder.start_work(source="MANUAL"))

    def action_break(icon, item) -> None:
        status = recorder.get_status()
        if status.get("type") == "BREAK":
            event_message(recorder.end_break())
        else:
            event_message(recorder.start_break())

    def action_absence(icon, item) -> None:
        status = recorder.get_status()
        if status.get("type") == "ABSENCE":
            event_message(recorder.end_absence())
        else:
            event_message(recorder.start_absence())

    def action_end_day(icon, item) -> None:
        event_message(recorder.end_day())

    def launch_app(view: str | None = None) -> None:
        if another_instance_is_running():
            request_app_view(view)
            return
        subprocess.Popen(_app_launch_command(view), cwd=str(_launch_cwd()))

    def action_open_app(icon, item) -> None:
        launch_app()

    def open_vacation(icon, item) -> None:
        launch_app("vacation")

    def open_settings(icon, item) -> None:
        launch_app("settings")

    def backup_now(icon, item) -> None:
        with database.connect(recorder.db_path) as conn:
            path = database.create_manual_backup(conn, recorder.db_path)
        logger.info("Manuelles Backup erstellt: %s", path)
        notifier.show("ArbeitszeitTracker", f"Backup erstellt: {path.name}")
        notifier.flush()

    def open_log(icon, item) -> None:
        logs = sorted(LOG_DIR.glob("log_*.html"), reverse=True)
        path = logs[0] if logs else LOG_DIR / "log_empty.html"
        if not path.exists():
            path.write_text("<!doctype html><meta charset='utf-8'><title>Diagnose-Log</title><p>Noch keine Log-Eintraege.</p>", encoding="utf-8")
        webbrowser.open(path.resolve().as_uri())

    def quit_app(icon, item) -> None:
        icon.stop()

    def break_label(item) -> str:
        return "☕ Pause beenden" if recorder.get_status().get("type") == "BREAK" else "☕ Pause starten"

    def absence_label(item) -> str:
        return "🚶 Abwesenheit beenden" if recorder.get_status().get("type") == "ABSENCE" else "🚶 Abwesenheit starten"

    def start_visible(item) -> bool:
        return not bool(recorder.get_status().get("running"))

    menu = pystray.Menu(
        pystray.MenuItem("▶ Arbeitsbeginn", action_start, visible=start_visible),
        pystray.MenuItem(break_label, action_break),
        pystray.MenuItem(absence_label, action_absence),
        pystray.MenuItem("🏠 Feierabend", action_end_day),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🌴 Urlaub und Abwesenheiten", open_vacation),
        pystray.MenuItem("📅 App öffnen", action_open_app, default=True),
        pystray.MenuItem("🗄 Backup jetzt erstellen", backup_now),
        pystray.MenuItem("📋 Diagnose-Log öffnen", open_log),
        pystray.MenuItem("⚙ Einstellungen", open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌ Beenden", quit_app),
    )
    icon = pystray.Icon("ArbeitszeitTracker", _load_icon(Image, ImageDraw), "ArbeitszeitTracker", menu)
    notifier.attach_tray_icon(icon)
    icon.run()


def _create_icon(image_module, draw_module):
    image = image_module.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = draw_module.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="#2563eb")
    draw.ellipse((18, 18, 46, 46), fill="#ffffff")
    draw.line((32, 32, 32, 21), fill="#2563eb", width=4)
    draw.line((32, 32, 42, 38), fill="#2563eb", width=4)
    return image


def _load_icon(image_module, draw_module):
    icon_path = PROJECT_ROOT / "app" / "static" / "icons" / "app.png"
    if icon_path.exists():
        try:
            with image_module.open(icon_path) as image:
                return image.convert("RGBA")
        except Exception:
            pass
    return _create_icon(image_module, draw_module)


def _app_launch_command(view: str | None = None) -> list[str]:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--app"]
    else:
        script = _app_script_path()
        command = [_windowed_python_executable(), str(script)]
        if script.name == "main.pyw" and script.parent == PROJECT_ROOT:
            command.append("--app")
    if view:
        command.extend(["--view", view])
    return command


def _launch_cwd() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def _app_script_path() -> Path:
    root_launcher = PROJECT_ROOT / "main.pyw"
    if root_launcher.exists():
        return root_launcher
    script = PROJECT_ROOT / "app" / "main.py"
    if sys.platform.startswith("win"):
        pyw_script = script.with_suffix(".pyw")
        if pyw_script.exists():
            return pyw_script
    return script


def _windowed_python_executable() -> str:
    if sys.platform.startswith("win"):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable
