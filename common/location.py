"""Network-based location detection.

The check is intentionally limited to configured server targets. It does not
scan the network, open a server port, or contact the internet unless such a host
was explicitly configured by the user.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


@dataclass(frozen=True)
class LocationTarget:
    host: str
    port: int | None = None


def parse_targets(raw_targets: str | Iterable[str] | None) -> list[LocationTarget]:
    """Parse comma-separated server targets.

    Supported forms:
    - ``server-name`` checks reachability with the system ping command.
    - ``server-name:443`` checks a TCP connection to the given port.
    - ``https://server-name`` checks TCP port 443.
    """

    if not raw_targets:
        return []
    values = raw_targets.split(",") if isinstance(raw_targets, str) else list(raw_targets)
    targets: list[LocationTarget] = []
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        targets.append(_parse_target(value))
    return targets


def detect_location(raw_targets: str | Iterable[str] | None, timeout_ms: int | str = 1500) -> str:
    """Return OFFICE or HOME based on configured network targets."""

    targets = parse_targets(raw_targets)
    if not targets:
        return "HOME"
    timeout = _timeout_seconds(timeout_ms)

    for target in targets:
        if target.port is None:
            if _ping_target(target.host, timeout):
                return "OFFICE"
        elif _tcp_target_reachable(target, timeout):
            return "OFFICE"
    return "HOME"


def _parse_target(value: str) -> LocationTarget:
    parsed = urlparse(value if "://" in value else f"//{value}", scheme="")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError(f"Standort-Ziel ohne Host: {value}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Ungueltiger Port in Standort-Ziel: {value}") from exc
    if port is not None:
        return LocationTarget(host=host, port=_validate_port(port, value))
    if parsed.scheme in {"http", "https"}:
        return LocationTarget(host=host, port=443 if parsed.scheme == "https" else 80)
    if ":" in value and not _looks_like_ipv6_host(value):
        raise ValueError(f"Ungueltiger Port in Standort-Ziel: {value}")
    return LocationTarget(host=host)


def _validate_port(port: int, value: str) -> int:
    if not (1 <= port <= 65535):
        raise ValueError(f"Port ausserhalb des erlaubten Bereichs: {value}")
    return port


def _looks_like_ipv6_host(value: str) -> bool:
    return value.count(":") > 1 and not value.rsplit(":", 1)[-1].isdigit()


def _timeout_seconds(timeout_ms: int | str) -> float:
    try:
        return max(0.05, int(timeout_ms) / 1000)
    except (TypeError, ValueError):
        return 1.5


def _tcp_target_reachable(target: LocationTarget, timeout: float) -> bool:
    try:
        with socket.create_connection((target.host, int(target.port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ping_target(host: str, timeout: float) -> bool:
    timeout_ms = max(50, int(timeout * 1000))
    if sys.platform.startswith("win"):
        command = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        command = ["ping", "-c", "1", host]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
