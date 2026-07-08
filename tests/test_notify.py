from __future__ import annotations

import threading

from tracker.notify import NotificationCenter


def test_notification_center_does_not_create_worker_gui_thread():
    before = {thread.name for thread in threading.enumerate()}
    center = NotificationCenter()
    center.start()
    center.show("ArbeitszeitTracker", "Test")
    after = {thread.name for thread in threading.enumerate()}

    assert "NotificationCenter" not in after - before

