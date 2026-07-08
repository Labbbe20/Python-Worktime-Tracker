from __future__ import annotations

import socket

import pytest

from common.location import detect_location, parse_targets


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


def test_invalid_target_is_rejected():
    with pytest.raises(ValueError):
        parse_targets("missing-port")
