"""Entry point for the background tracker."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common import database
from common.html_logger import HtmlLogHandler
from tracker import autostart_windows, shutdown_windows
from tracker.notify import NotificationCenter
from tracker.recorder import WorktimeRecorder
from tracker.tray import run_tray


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("worktime")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, HtmlLogHandler) for handler in logger.handlers):
        handler = HtmlLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    if _console_logging_available() and not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console)
    return logging.getLogger("worktime.tracker")


def _console_logging_available() -> bool:
    executable_name = Path(sys.executable).name.lower()
    return sys.stderr is not None and executable_name != "pythonw.exe"


def main() -> None:
    logger = configure_logging()
    database.init_db()
    notifier = NotificationCenter(logger=logging.getLogger("worktime.tracker.notify"))
    notifier.start()
    recorder = WorktimeRecorder(logger=logging.getLogger("worktime.tracker.recorder"))

    shutdown_notice = recorder.consume_shutdown_notice()
    if shutdown_notice:
        notifier.show("ArbeitszeitTracker", shutdown_notice)

    for event in recorder.recover_previous_open_segments(_ask_recovery_end_time):
        notifier.show("ArbeitszeitTracker", f"{event.message}: {event.date} {event.time[:5]} Uhr")

    start_event = recorder.auto_start_day()
    if start_event:
        notifier.show("ArbeitszeitTracker", f"{start_event.message} - {start_event.time[:5]} Uhr")

    shortcut = autostart_windows.ensure_startup_shortcut(Path(__file__).resolve())
    if shortcut:
        logger.info("Windows-Autostart-Verknuepfung vorhanden: %s", shortcut)

    shutdown_listener = shutdown_windows.ShutdownListener(
        lambda: _shutdown_end_day(recorder, notifier),
        logger=logging.getLogger("worktime.tracker.shutdown"),
    )
    shutdown_listener.start()

    try:
        run_tray(recorder, notifier, logger=logging.getLogger("worktime.tracker.tray"))
    finally:
        notifier.stop()


def _ask_recovery_end_time(segment: dict) -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        logging.getLogger("worktime.tracker").warning("Crash-Recovery-Dialog nicht verfuegbar")
        return None

    root = tk.Tk()
    root.withdraw()
    try:
        message = (
            f"Offenes Segment vom {segment['date']} seit {segment['start_time'][:5]} Uhr gefunden.\n"
            "Wann soll Feierabend nachgetragen werden? (HH:MM)"
        )
        value = simpledialog.askstring("Crash-Recovery", message, initialvalue="17:00", parent=root)
        return value
    finally:
        root.destroy()


def _shutdown_end_day(recorder: WorktimeRecorder, notifier: NotificationCenter) -> None:
    event = recorder.end_day(source="AUTO_SHUTDOWN", reason="Feierabend automatisch beim Herunterfahren")
    if event:
        notifier.show("ArbeitszeitTracker", f"{event.message} - {event.time[:5]} Uhr")


if __name__ == "__main__":
    main()
