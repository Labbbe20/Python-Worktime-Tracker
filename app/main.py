"""Entry point for the local pywebview desktop app."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from app.api import WorktimeApi
from app.instance import (
    another_instance_is_running,
    clear_current_process,
    normalize_view,
    register_current_process,
    request_app_view,
    read_command,
)
from common import database
from common.config import PROJECT_ROOT
from common.html_logger import HtmlLogHandler


APP_ICON_ICO = PROJECT_ROOT / "app" / "static" / "icons" / "app.ico"
APP_ICON_PNG = PROJECT_ROOT / "app" / "static" / "icons" / "app.png"


class AppCommandWatcher:
    """Focus the preloaded app from Python even when hidden WebView timers pause."""

    def __init__(self, api: WorktimeApi, interval_seconds: float = 0.12) -> None:
        self.api = api
        self.interval_seconds = interval_seconds
        self._last_command_id: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="worktime-app-command-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        logger = logging.getLogger("worktime.app")
        while not self._stop_event.wait(self.interval_seconds):
            try:
                command = read_command(self._last_command_id)
                if not command:
                    continue
                self._last_command_id = command["id"]
                if command.get("action") == "quit":
                    self.api.close_window()
                else:
                    self.api.focus_window(command["view"])
            except Exception:
                logger.debug("App-Kommando konnte nicht direkt verarbeitet werden", exc_info=True)


def configure_logging() -> None:
    logger = logging.getLogger("worktime")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, HtmlLogHandler) for handler in logger.handlers):
        handler = HtmlLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def main() -> None:
    configure_logging()
    db_path = database.init_db()
    _set_windows_app_id()
    initial_view = _initial_view_from_args()
    if another_instance_is_running():
        request_app_view(initial_view)
        logging.getLogger("worktime.app").info("App laeuft bereits; Vordergrund-Anfrage fuer %s gesendet", initial_view)
        return
    register_current_process()
    try:
        import webview
    except Exception as exc:
        clear_current_process()
        raise RuntimeError("Die Desktop-App benoetigt pywebview. Bitte requirements.txt installieren.") from exc

    html_path = PROJECT_ROOT / "app" / "templates" / "index.html"
    url = f"{html_path.resolve().as_uri()}#{initial_view}"
    api = WorktimeApi(db_path)
    window = webview.create_window(
        "ArbeitszeitTracker",
        url,
        js_api=api,
        width=1440,
        height=860,
        min_size=(1080, 680),
        hidden="--hidden" in sys.argv,
        background_color="#f8fafc",
    )
    api.attach_window(window)
    if _keep_alive_on_close():
        window.events.closing += api.hide_window_on_close
    command_watcher = AppCommandWatcher(api)
    command_watcher.start()
    logging.getLogger("worktime.app").info("App-Fenster gestartet: %s", window.title)
    try:
        webview.start(debug=False, icon=_app_icon_path())
    finally:
        command_watcher.stop()
        clear_current_process()


def _initial_view_from_args() -> str:
    if "--view" in sys.argv:
        index = sys.argv.index("--view")
        if index + 1 < len(sys.argv):
            return normalize_view(sys.argv[index + 1])
    return normalize_view("dashboard")


def _keep_alive_on_close() -> bool:
    return "--keep-alive-on-close" in sys.argv or "--hidden" in sys.argv


def _app_icon_path() -> str | None:
    preferred = APP_ICON_ICO if sys.platform.startswith("win") else APP_ICON_PNG
    for path in (preferred, APP_ICON_ICO, APP_ICON_PNG):
        if path.exists():
            return str(path)
    return None


def _set_windows_app_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArbeitszeitTracker.Local")
    except Exception:
        logging.getLogger("worktime.app").debug("Windows AppUserModelID konnte nicht gesetzt werden", exc_info=True)


if __name__ == "__main__":
    main()
