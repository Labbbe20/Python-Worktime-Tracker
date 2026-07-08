"""Local single-instance coordination for the pywebview app.

This uses files in the local data directory instead of a network socket or
localhost server. A second app start writes a command for the already running
app and exits.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from common.config import DATA_DIR, ensure_data_dirs


ALLOWED_VIEWS = {"dashboard", "calendar", "entries", "statistics", "vacation", "settings"}
PID_FILE = DATA_DIR / "app.pid"
COMMAND_FILE = DATA_DIR / "app_command.json"


def normalize_view(view: str | None) -> str:
    return view if view in ALLOWED_VIEWS else "dashboard"


def request_app_view(view: str | None) -> Path:
    ensure_data_dirs()
    payload = {
        "id": uuid.uuid4().hex,
        "view": normalize_view(view),
        "created_at": time.time(),
    }
    tmp_path = COMMAND_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(COMMAND_FILE)
    return COMMAND_FILE


def another_instance_is_running() -> bool:
    pid = _read_pid()
    return bool(pid and pid != os.getpid() and _pid_is_alive(pid))


def register_current_process() -> None:
    ensure_data_dirs()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        COMMAND_FILE.unlink()
    except FileNotFoundError:
        pass


def clear_current_process() -> None:
    pid = _read_pid()
    if pid == os.getpid() and PID_FILE.exists():
        PID_FILE.unlink()


def read_command(last_seen_id: str | None = None) -> dict[str, Any] | None:
    if not COMMAND_FILE.exists():
        return None
    try:
        payload = json.loads(COMMAND_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("id") == last_seen_id:
        return None
    payload["view"] = normalize_view(payload.get("view"))
    return payload


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return True

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    exit_code = wintypes.DWORD()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
