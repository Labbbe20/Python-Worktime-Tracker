"""Project paths, defaults, and small configuration helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


PROJECT_ROOT = _resource_root()
RUNTIME_ROOT = _runtime_root()
DATA_DIR = Path(os.environ.get("WORKTIME_DATA_DIR", RUNTIME_ROOT / "data")).expanduser().resolve()
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
DEFAULT_DB_PATH = DATA_DIR / "database.db"


DEFAULT_SETTINGS: dict[str, str] = {
    "weekly_target_hours": "40",
    "workdays_per_week": "5",
    "daily_break_minutes": "0",
    "weekday_target_minutes": "{}",  # JSON mapping "0".."6" to minutes, optional.
    "bundesland": "",
    "vacation_days_per_year": "30",
    "vacation_carry_over": "0",
    "tracking_start_date": "",
    "initial_flextime_minutes": "0",
    "office_baseline_days": "0",
    "homeoffice_baseline_days": "0",
    "office_baseline_period_mode": "all",
    "office_baseline_custom_start": "",
    "office_baseline_custom_end": "",
    "office_quota_target_percent": "50",
    "office_quota_period_mode": "all",
    "office_quota_custom_start": "",
    "office_quota_custom_end": "",
    "office_quota_mixed_day_mode": "split",
    "homeoffice_check_targets": "",
    "homeoffice_check_timeout_ms": "1500",
    "office_start_buffer_minutes": "0",
    "home_start_buffer_minutes": "0",
    "office_end_buffer_minutes": "0",
    "home_end_buffer_minutes": "0",
    "autostart_enabled": "1",
    "automatic_work_start_enabled": "1",
    "automatic_work_end_enabled": "1",
    "automatic_recovery_enabled": "1",
    "auto_resume_after_break_enabled": "1",
    "auto_resume_after_absence_enabled": "1",
    "auto_refresh_interval_seconds": "60",
    "preload_app_on_tracker_start": "1",
    "work_start_popup_mode": "off",
    "work_end_popup_mode": "open_only",
    "work_popup_timing": "startup",
    "work_popup_custom_time": "07:00",
    "daily_info_popup_mode": "off",
    "daily_info_popup_time": "16:30",
    "absence_reminder_mode": "off",
    "absence_reminder_days": "14",
    "absence_reminder_time": "09:00",
    "pa_email_template": (
        "Hallo zusammen,\n\n"
        "hier meine Arbeitszeiten aus dem Homeoffice vom {datum}:\n\n"
        "{segmente}\n\n"
        "Vielen Dank fürs Eintragen.\n\n"
        "Viele Grüße"
    ),
    "dashboard_absence_countdown_mode": "workdays",
    "standard_absence_1224_mode": "off",
    "standard_absence_1231_mode": "off",
    "standard_absence_bridge_mode": "off",
    "standard_absence_rules": "[]",
    "initial_setup_required": "0",
    "darkmode": "0",
}


GERMAN_STATE_CODES: dict[str, str] = {
    "baden-wuerttemberg": "BW",
    "baden-württemberg": "BW",
    "bw": "BW",
    "bayern": "BY",
    "by": "BY",
    "berlin": "BE",
    "be": "BE",
    "brandenburg": "BB",
    "bb": "BB",
    "bremen": "HB",
    "hb": "HB",
    "hamburg": "HH",
    "hh": "HH",
    "hessen": "HE",
    "he": "HE",
    "mecklenburg-vorpommern": "MV",
    "mv": "MV",
    "niedersachsen": "NI",
    "ni": "NI",
    "nordrhein-westfalen": "NW",
    "nrw": "NW",
    "nw": "NW",
    "rheinland-pfalz": "RP",
    "rp": "RP",
    "saarland": "SL",
    "sl": "SL",
    "sachsen": "SN",
    "sn": "SN",
    "sachsen-anhalt": "ST",
    "st": "ST",
    "schleswig-holstein": "SH",
    "sh": "SH",
    "thueringen": "TH",
    "thüringen": "TH",
    "th": "TH",
}


def ensure_data_dirs() -> None:
    """Create local data folders. No backup or network activity happens here."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_database_path() -> Path:
    """Return the database path, optionally overridden for tests."""

    override = os.environ.get("WORKTIME_DB_PATH")
    return Path(override).expanduser().resolve() if override else DEFAULT_DB_PATH


def normalize_state_code(value: str | None) -> str:
    """Normalize a German federal state setting for the holidays package."""

    if not value:
        return ""
    stripped = value.strip()
    if not stripped:
        return ""
    if len(stripped) == 2 and stripped.isalpha():
        return stripped.upper()
    return GERMAN_STATE_CODES.get(stripped.lower(), "")
