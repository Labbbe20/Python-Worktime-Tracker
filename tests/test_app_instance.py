from __future__ import annotations

import time

from app import main as app_main
from app import instance
from app.api import WorktimeApi


def test_app_view_command_is_normalized_and_read_once(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "PID_FILE", tmp_path / "app.pid")
    monkeypatch.setattr(instance, "COMMAND_FILE", tmp_path / "app_command.json")

    instance.request_app_view("vacation")
    command = instance.read_command()

    assert command is not None
    assert command["action"] == "show"
    assert command["view"] == "vacation"
    assert instance.read_command(command["id"]) is None
    assert instance.normalize_view("nicht-echt") == "dashboard"


def test_app_quit_command_is_distinct_from_show_command(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "PID_FILE", tmp_path / "app.pid")
    monkeypatch.setattr(instance, "COMMAND_FILE", tmp_path / "app_command.json")

    instance.request_app_quit()
    command = instance.read_command()

    assert command is not None
    assert command["action"] == "quit"
    assert command["view"] == "dashboard"


def test_app_command_watcher_focuses_window_without_waiting_for_javascript(monkeypatch):
    calls = []
    commands = [{"id": "1", "view": "dashboard"}, None]

    class FakeApi:
        def focus_window(self, view=None):
            calls.append(view)

    def fake_read_command(last_seen_id=None):
        return commands.pop(0) if commands else None

    monkeypatch.setattr(app_main, "read_command", fake_read_command)

    watcher = app_main.AppCommandWatcher(FakeApi(), interval_seconds=0.001)
    watcher.start()
    time.sleep(0.02)
    watcher.stop()

    assert calls == ["dashboard"]


def test_window_close_button_hides_instead_of_destroying_when_keepalive(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    calls = []

    class FakeWindow:
        def hide(self):
            calls.append("hide")

        def destroy(self):
            calls.append("destroy")

    api.attach_window(FakeWindow())

    assert api.hide_window_on_close() is False
    assert calls == ["hide"]

    api.close_window()
    assert calls == ["hide", "destroy"]
    assert api.hide_window_on_close() is True
