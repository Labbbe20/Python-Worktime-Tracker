"""Main-thread-safe notification queue for the tracker.

macOS requires GUI objects such as NSWindow/Tk windows to be created on the
main thread. This notification center therefore does not start a Tk worker
thread. It queues messages and lets the tray/main-thread code flush them through
pystray's native notification hook.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Notification:
    title: str
    message: str


class NotificationCenter:
    """Thread-safe notification queue without worker-thread GUI creation."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("worktime.tracker.notify")
        self._queue: queue.Queue[Notification] = queue.Queue()
        self._main_thread_id = threading.get_ident()
        self._tray_icon: Any | None = None

    def start(self) -> None:
        self._main_thread_id = threading.get_ident()

    def show(self, title: str, message: str) -> None:
        notification = Notification(title, message)
        if self._is_main_thread() and self._tray_icon is not None:
            self._show_now(notification)
        else:
            self._queue.put(notification)

    def stop(self) -> None:
        self._tray_icon = None

    def attach_tray_icon(self, icon: Any) -> None:
        self._tray_icon = icon
        self.flush()

    def flush(self) -> None:
        if not self._is_main_thread():
            return
        while True:
            try:
                notification = self._queue.get_nowait()
            except queue.Empty:
                break
            self._show_now(notification)

    def _show_now(self, notification: Notification) -> None:
        self.logger.info("%s: %s", notification.title, notification.message)
        if self._tray_icon is None:
            return
        try:
            self._tray_icon.notify(notification.message, notification.title)
        except Exception:
            self.logger.debug("Native Tray-Benachrichtigung nicht verfuegbar", exc_info=True)

    def _is_main_thread(self) -> bool:
        return threading.get_ident() == self._main_thread_id
