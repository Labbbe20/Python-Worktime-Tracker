from __future__ import annotations

import socket

import pytest

from common import location
from common.location import LocationTarget, detect_location, parse_targets


class DummySocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_no_targets_returns_home():
    assert detect_location("", 100) == "HOME"


def test_all_targets_unreachable_returns_home(monkeypatch):
    def fake_create_connection(address, timeout):
        raise OSError("not reachable")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    assert detect_location("host1:443,host2:445", 100) == "HOME"


def test_one_reachable_target_returns_office(monkeypatch):
    def fake_create_connection(address, timeout):
        if address == ("office", 443):
            return DummySocket()
        raise OSError("not reachable")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    assert detect_location("home:443,office:443", 100) == "OFFICE"


def test_host_without_port_uses_ping(monkeypatch):
    calls = []

    def fake_ping(host, timeout):
        calls.append((host, timeout))
        return host == "office-server"

    monkeypatch.setattr(location, "_ping_target", fake_ping)

    assert parse_targets("office-server") == [LocationTarget("office-server")]
    assert detect_location("home-router,office-server", 100) == "OFFICE"
    assert calls[0][0] == "home-router"
    assert calls[1][0] == "office-server"


def test_https_target_defaults_to_port_443(monkeypatch):
    def fake_create_connection(address, timeout):
        if address == ("intranet.example", 443):
            return DummySocket()
        raise OSError("not reachable")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    assert parse_targets("https://intranet.example")[0] == LocationTarget("intranet.example", 443)
    assert detect_location("https://intranet.example", 100) == "OFFICE"


def test_invalid_target_is_rejected():
    with pytest.raises(ValueError):
        parse_targets("server:not-a-port")
