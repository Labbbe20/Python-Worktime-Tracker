"""Windows-only shutdown detection.

This module listens for WM_QUERYENDSESSION / WM_ENDSESSION. It intentionally
does not register WTS session notifications, so WTS_SESSION_LOCK and
WTS_SESSION_UNLOCK cannot influence tracking.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable


def is_windows() -> bool:
    return sys.platform.startswith("win")


class ShutdownListener:
    def __init__(self, on_shutdown: Callable[[], None], logger: logging.Logger | None = None) -> None:
        self.on_shutdown = on_shutdown
        self.logger = logger or logging.getLogger("worktime.tracker.shutdown")
        self._thread: threading.Thread | None = None
        self._called = threading.Event()

    def start(self) -> None:
        if not is_windows():
            self.logger.info("Shutdown-Erkennung ist Windows-only; Stub aktiv.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="WindowsShutdownListener", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore
        except Exception as exc:
            self.logger.error("pywin32 fuer Shutdown-Erkennung nicht verfuegbar: %s", exc)
            return

        class_name = "ArbeitszeitTrackerShutdownWindow"

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_QUERYENDSESSION:
                self._handle_shutdown("WM_QUERYENDSESSION")
                return True
            if msg == win32con.WM_ENDSESSION and bool(wparam):
                self._handle_shutdown("WM_ENDSESSION")
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        wnd_class = win32gui.WNDCLASS()
        wnd_class.lpfnWndProc = wnd_proc
        wnd_class.lpszClassName = class_name
        class_atom = win32gui.RegisterClass(wnd_class)
        hwnd = win32gui.CreateWindow(class_atom, class_name, 0, 0, 0, 0, 0, 0, 0, 0, None)
        self.logger.info("Windows-Shutdown-Erkennung gestartet")
        try:
            win32gui.PumpMessages()
        finally:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:
                self.logger.debug("Shutdown-Fenster war bereits entfernt", exc_info=True)

    def _handle_shutdown(self, reason: str) -> None:
        if self._called.is_set():
            return
        self._called.set()
        self.logger.info("Echtes Sitzungsende erkannt: %s", reason)
        try:
            self.on_shutdown()
        except Exception:
            self.logger.exception("Fehler beim automatischen Feierabend waehrend Shutdown")

