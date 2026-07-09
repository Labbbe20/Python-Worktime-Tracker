"""Entry point for the local pywebview desktop app."""

from __future__ import annotations

import logging
import sys
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
)
from common import database
from common.config import PROJECT_ROOT
from common.html_logger import HtmlLogHandler


APP_ICON_ICO = PROJECT_ROOT / "app" / "static" / "icons" / "app.ico"
APP_ICON_PNG = PROJECT_ROOT / "app" / "static" / "icons" / "app.png"


def configure_logging() -> None:
    logger = logging.getLogger("worktime")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, HtmlLogHandler) for handler in logger.handlers):
        handler = HtmlLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def main() -> None:
    configure_logging()
    database.init_db()
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
    api = WorktimeApi()
    window = webview.create_window(
        "ArbeitszeitTracker",
        url,
        js_api=api,
        width=1180,
        height=820,
        min_size=(920, 640),
        background_color="#f8fafc",
    )
    api.attach_window(window)
    logging.getLogger("worktime.app").info("App-Fenster gestartet: %s", window.title)
    try:
        webview.start(debug=False, icon=_app_icon_path())
    finally:
        clear_current_process()


def _initial_view_from_args() -> str:
    if "--view" in sys.argv:
        index = sys.argv.index("--view")
        if index + 1 < len(sys.argv):
            return normalize_view(sys.argv[index + 1])
    return normalize_view("dashboard")


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
