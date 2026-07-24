"""Typed data objects and time helpers used by tracker and app."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time


DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

SEGMENT_TYPES = {"WORK", "BREAK", "ABSENCE"}
LOCATIONS = {"OFFICE", "HOME", "UNKNOWN", "MIXED"}
SOURCES = {"AUTO", "MANUAL"}
DAY_TYPES = {"URLAUB", "KRANK", "FEIERTAG", "GLEITZEITTAG", "DIENSTREISE"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_str() -> str:
    return Date.today().strftime(DATE_FORMAT)


def current_time_str() -> str:
    return datetime.now().strftime(TIME_FORMAT)


def parse_date(value: str) -> Date:
    return datetime.strptime(value, DATE_FORMAT).date()


def parse_time(value: str) -> time:
    parts = value.split(":")
    if len(parts) == 2:
        value = f"{value}:00"
    return datetime.strptime(value, TIME_FORMAT).time()


def combine_datetime(date_value: str, time_value: str) -> datetime:
    return datetime.combine(parse_date(date_value), parse_time(time_value))


def minutes_between(date_value: str, start_time: str, end_time: str) -> int:
    """Return rounded-down whole minutes between two local clock values."""

    start = combine_datetime(date_value, start_time)
    end = combine_datetime(date_value, end_time)
    if end < start:
        return 0
    return int((end - start).total_seconds() // 60)


def normalize_time_input(value: str) -> str:
    """Normalize user-entered HH:MM or HH:MM:SS into HH:MM:SS."""

    parsed = parse_time(value.strip())
    return parsed.strftime(TIME_FORMAT)
