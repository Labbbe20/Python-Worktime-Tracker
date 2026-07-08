"""Network-based location detection.

The check is intentionally limited to configured host:port targets. It does not
scan the network, open a server port, or contact the internet unless such a host
was explicitly configured by the user.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class LocationTarget:
    host: str
    port: int


def parse_targets(raw_targets: str | Iterable[str] | None) -> list[LocationTarget]:
    """Parse comma-separated host:port targets."""

    if not raw_targets:
        return []
    values = raw_targets.split(",") if isinstance(raw_targets, str) else list(raw_targets)
    targets: list[LocationTarget] = []
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        if ":" not in value:
            raise ValueError(f"Standort-Ziel muss host:port sein: {value}")
        host, port_text = value.rsplit(":", 1)
        host = host.strip()
        if not host:
            raise ValueError(f"Standort-Ziel ohne Host: {value}")
        try:
            port = int(port_text.strip())
        except ValueError as exc:
            raise ValueError(f"Ungueltiger Port in Standort-Ziel: {value}") from exc
        if not (1 <= port <= 65535):
            raise ValueError(f"Port ausserhalb des erlaubten Bereichs: {value}")
        targets.append(LocationTarget(host=host, port=port))
    return targets


def detect_location(raw_targets: str | Iterable[str] | None, timeout_ms: int | str = 1500) -> str:
    """Return OFFICE or HOME based on configured socket targets."""

    targets = parse_targets(raw_targets)
    if not targets:
        return "HOME"
    try:
        timeout_seconds = max(0.05, int(timeout_ms) / 1000)
    except (TypeError, ValueError):
        timeout_seconds = 1.5

    for target in targets:
        try:
            with socket.create_connection((target.host, target.port), timeout=timeout_seconds):
                return "OFFICE"
        except OSError:
            continue
    return "HOME"
