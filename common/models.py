"""Typed data objects and time helpers used by tracker and app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, time
from typing import Any, Mapping


DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"

SEGMENT_TYPES = {"WORK", "BREAK", "ABSENCE"}
LOCATIONS = {"OFFICE", "HOME", "UNKNOWN", "MIXED"}
SOURCES = {"AUTO", "MANUAL"}
DAY_TYPES = {"URLAUB", "KRANK", "FEIERTAG", "GLEITZEITTAG", "DIENSTREISE"}


@dataclass(frozen=True)
class Segment:
    id: int | None
    date: str
    type: str
    start_time: str
    end_time: str | None
    location: str | None
    source: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Segment":
        return cls(
            id=row["id"],
            date=row["date"],
            type=row["type"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            location=row["location"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class DayType:
    id: int | None
    date: str
    type: str
    half_day: bool
    note: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DayType":
        return cls(
            id=row["id"],
            date=row["date"],
            type=row["type"],
            half_day=bool(row["half_day"]),
            note=row["note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class DaySummary:
    date: str
    target_minutes: int
    actual_minutes: int
    break_minutes: int
    balance_minutes: int
    day_category: str
    location: str | None
    updated_at: str


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

