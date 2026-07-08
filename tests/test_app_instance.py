from __future__ import annotations

from app import instance


def test_app_view_command_is_normalized_and_read_once(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "PID_FILE", tmp_path / "app.pid")
    monkeypatch.setattr(instance, "COMMAND_FILE", tmp_path / "app_command.json")

    instance.request_app_view("vacation")
    command = instance.read_command()

    assert command is not None
    assert command["view"] == "vacation"
    assert instance.read_command(command["id"]) is None
    assert instance.normalize_view("nicht-echt") == "dashboard"
