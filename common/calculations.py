"""Central calculation engine for target time, balances, vacation, and summaries."""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from . import database
from .balance import (
    classify_balance,
    format_minutes_as_decimal_hours,
    get_initial_flextime_minutes,
    get_tracking_start_date,
    is_before_tracking_start,
)
from .config import DEFAULT_SETTINGS, normalize_state_code
from .models import minutes_between, parse_date


DAY_TYPE_PRIORITY = ["URLAUB", "KRANK", "FEIERTAG", "GLEITZEITTAG", "DIENSTREISE"]
DAY_CATEGORY_BY_TYPE = {
    "URLAUB": "VACATION",
    "KRANK": "SICK",
    "FEIERTAG": "HOLIDAY",
    "GLEITZEITTAG": "FLEXTIME",
    "DIENSTREISE": "TRAVEL",
}


@dataclass(frozen=True)
class DayComputation:
    date: str
    target_minutes: int
    actual_minutes: int
    break_minutes: int
    balance_minutes: int
    day_category: str
    location: str | None
    holiday_name: str | None = None


def minutes_to_hhmm(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    absolute = abs(int(minutes))
    return f"{sign}{absolute // 60}:{absolute % 60:02d}"


def daterange(start: Date, end: Date) -> Iterable[Date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def get_target_minutes_for_date(date_value: str | Date, settings: Mapping[str, str]) -> int:
    day = parse_date(date_value) if isinstance(date_value, str) else date_value
    custom_targets = _parse_weekday_targets(settings.get("weekday_target_minutes", "{}"))
    if str(day.weekday()) in custom_targets:
        return max(0, int(round(custom_targets[str(day.weekday())])))

    workdays = get_workday_indices(settings)
    if day.weekday() not in workdays:
        return 0
    weekly_hours = _safe_float(settings.get("weekly_target_hours"), float(DEFAULT_SETTINGS["weekly_target_hours"]))
    return int(round((weekly_hours * 60) / len(workdays)))


def get_workday_indices(settings: Mapping[str, str]) -> list[int]:
    raw_weekdays = str(settings.get("workday_weekdays", "") or "").strip()
    if raw_weekdays:
        values: set[int] = set()
        for item in raw_weekdays.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                weekday = int(item)
            except ValueError:
                continue
            if 0 <= weekday <= 6:
                values.add(weekday)
        if values:
            return sorted(values)

    workday_count = _safe_int(settings.get("workdays_per_week"), int(DEFAULT_SETTINGS["workdays_per_week"]))
    workday_count = min(7, max(1, workday_count))
    return list(range(workday_count))


def is_public_holiday(date_value: str | Date, settings: Mapping[str, str]) -> tuple[bool, str | None]:
    day = parse_date(date_value) if isinstance(date_value, str) else date_value
    state = normalize_state_code(settings.get("bundesland", ""))
    if not state:
        return False, None
    try:
        import holidays  # type: ignore
    except ImportError:
        return False, None

    try:
        holiday_map = holidays.country_holidays("DE", subdiv=state, years=[day.year])
    except Exception:
        try:
            holiday_map = holidays.Germany(state=state, years=[day.year])
        except Exception:
            return False, None
    if day in holiday_map:
        return True, str(holiday_map[day])
    return False, None


def compute_day(
    date_value: str,
    segments: Iterable[Mapping[str, Any]],
    day_types: Iterable[Mapping[str, Any]],
    settings: Mapping[str, str],
    now: datetime | None = None,
) -> DayComputation:
    day = parse_date(date_value)
    now = now or datetime.now()
    day_type_rows = list(day_types)
    segment_rows = list(segments)

    if is_before_tracking_start(day, settings):
        return DayComputation(
            date=date_value,
            target_minutes=0,
            actual_minutes=0,
            break_minutes=0,
            balance_minutes=0,
            day_category="NOT_TRACKED",
            location=None,
        )

    base_target = get_target_minutes_for_date(day, settings)
    holiday, holiday_name = is_public_holiday(day, settings)
    explicit_type = _primary_day_type(day_type_rows)

    gross_work_minutes = _sum_segment_minutes(date_value, segment_rows, "WORK", now)
    explicit_break_minutes = _sum_segment_minutes(date_value, segment_rows, "BREAK", now)
    auto_break_minutes = _auto_break_minutes(gross_work_minutes, explicit_break_minutes, settings)
    work_minutes = max(0, gross_work_minutes - auto_break_minutes)
    break_minutes = explicit_break_minutes + auto_break_minutes
    has_real_work = gross_work_minutes > 0
    location = _aggregate_location(_row_get(row, "location") for row in segment_rows if _row_get(row, "type") == "WORK")

    if explicit_type == "FEIERTAG" or holiday:
        target_minutes = 0
    else:
        target_minutes = base_target

    neutral_credit = 0
    if explicit_type and explicit_type != "FEIERTAG":
        primary_row = next(row for row in day_type_rows if _row_get(row, "type") == explicit_type)
        if not has_real_work:
            # Documented assumption: a standalone full or half special day is neutral.
            neutral_credit = target_minutes
        elif bool(_row_get(primary_row, "half_day")):
            # Half-day absence covers half of the target; real work covers the rest.
            neutral_credit = target_minutes // 2

    if explicit_type:
        day_category = DAY_CATEGORY_BY_TYPE[explicit_type]
    elif holiday:
        day_category = "HOLIDAY"
    elif target_minutes == 0 and not has_real_work:
        day_category = "WEEKEND"
    else:
        day_category = "WORKDAY"

    actual_minutes = work_minutes + neutral_credit
    balance_minutes = actual_minutes - target_minutes
    if target_minutes == 0 and not has_real_work and not explicit_type:
        balance_minutes = 0

    return DayComputation(
        date=date_value,
        target_minutes=target_minutes,
        actual_minutes=actual_minutes,
        break_minutes=break_minutes,
        balance_minutes=balance_minutes,
        day_category=day_category,
        location=location if day_category == "WORKDAY" or has_real_work else None,
        holiday_name=holiday_name,
    )


def recalculate_day(conn, date_value: str, now: datetime | None = None) -> DayComputation:
    settings = _settings_with_effective_tracking_start(conn)
    segments = database.get_segments_for_date(conn, date_value)
    day_types = database.get_day_types_for_date(conn, date_value)
    result = compute_day(date_value, segments, day_types, settings, now=now)
    database.upsert_day_summary(
        conn,
        result.date,
        result.target_minutes,
        result.actual_minutes,
        result.break_minutes,
        result.balance_minutes,
        result.day_category,
        result.location,
    )
    recalculate_month(conn, date_value[:7])
    return result


def recalculate_range(conn, start_date: str, end_date: str, now: datetime | None = None) -> None:
    settings = _settings_with_effective_tracking_start(conn)
    for day in daterange(parse_date(start_date), parse_date(end_date)):
        date_text = day.isoformat()
        result = compute_day(
            date_text,
            database.get_segments_for_date(conn, date_text),
            database.get_day_types_for_date(conn, date_text),
            settings,
            now=now,
        )
        database.upsert_day_summary(
            conn,
            result.date,
            result.target_minutes,
            result.actual_minutes,
            result.break_minutes,
            result.balance_minutes,
            result.day_category,
            result.location,
        )
    current = parse_date(start_date).replace(day=1)
    final = parse_date(end_date).replace(day=1)
    while current <= final:
        recalculate_month(conn, current.strftime("%Y-%m"))
        year = current.year + (current.month // 12)
        month = 1 if current.month == 12 else current.month + 1
        current = Date(year, month, 1)


def recalculate_month(conn, year_month: str) -> None:
    year, month = [int(part) for part in year_month.split("-")]
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year_month}-01"
    end = f"{year_month}-{last_day:02d}"
    settings = _settings_with_effective_tracking_start(conn)
    tracking_start = get_tracking_start_date(settings)
    summaries = [
        row
        for row in database.get_day_summaries_between(conn, start, end)
        if not tracking_start or row["date"] >= tracking_start.isoformat()
    ]

    target = sum(int(row["target_minutes"]) for row in summaries)
    actual = sum(int(row["actual_minutes"]) for row in summaries)
    open_dates = _open_segment_dates(conn, start, end)
    balance = sum(int(row["balance_minutes"]) for row in summaries if row["date"] not in open_dates)
    carry_over = get_flextime_balance(conn, end)
    period_start = max(start, tracking_start.isoformat()) if tracking_start else start
    vacation_days = get_day_type_days(conn, year, "URLAUB", period_start, end)
    sick_days = get_day_type_days(conn, year, "KRANK", period_start, end)
    homeoffice_days = sum(1 for row in summaries if row["location"] == "HOME")
    database.upsert_month_closing(
        conn,
        year_month,
        target,
        actual,
        balance,
        carry_over,
        vacation_days,
        sick_days,
        homeoffice_days,
    )


def close_month(conn, year_month: str, closed: bool = True) -> None:
    recalculate_month(conn, year_month)
    database.set_month_closed(conn, year_month, closed)


def get_flextime_balance(conn, through_date: str | None = None) -> int:
    settings = database.get_settings(conn)
    initial = get_initial_flextime_minutes(settings)
    tracking_start = get_effective_tracking_start_date(conn, settings)
    clauses: list[str] = []
    params: list[Any] = []
    if tracking_start:
        clauses.append("date >= ?")
        params.append(tracking_start.isoformat())
    if through_date:
        clauses.append("date <= ?")
        params.append(through_date)
    clauses.append(
        "NOT EXISTS (SELECT 1 FROM segments s WHERE s.date = day_summary.date AND s.end_time IS NULL)"
    )
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(f"SELECT COALESCE(SUM(balance_minutes), 0) AS total FROM day_summary{where}", params).fetchone()
    return initial + int(row["total"] or 0)


def get_flextime_status(conn, through_date: str | None = None) -> dict[str, str]:
    minutes = get_flextime_balance(conn, through_date)
    status = classify_balance(minutes)
    return {
        "key": status.key,
        "class": status.css_class,
        "label": status.label,
        "hours": format_minutes_as_decimal_hours(minutes),
    }


def get_day_type_days(conn, year: int, day_type: str, start_date: str | None = None, end_date: str | None = None) -> float:
    params: list[Any] = [str(year), day_type]
    sql = "SELECT date, half_day FROM day_types WHERE substr(date, 1, 4) = ? AND type = ?"
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    rows = conn.execute(sql, params).fetchall()
    settings = _settings_with_effective_tracking_start(conn)
    total = 0.0
    for row in rows:
        if day_type in {"URLAUB", "KRANK"} and not _counts_as_absence_day(conn, row["date"], settings):
            continue
        total += 0.5 if row["half_day"] else 1.0
    return total


def get_remaining_vacation(conn, year: int) -> float:
    account = database.get_vacation_account(conn, year)
    used = get_day_type_days(conn, year, "URLAUB")
    return float(account["entitlement_days"]) + float(account["carry_over_from_previous"]) - used


def get_year_statistics(conn, year: int) -> dict[str, Any]:
    stats_range = get_statistics_date_range(conn, year)
    if stats_range:
        start, end = stats_range
        summaries = database.get_day_summaries_between(conn, start, end)
        open_dates = _open_segment_dates(conn, start, end)
        month_rows = conn.execute(
            """
            SELECT * FROM month_closing
            WHERE year_month BETWEEN ? AND ?
            ORDER BY year_month
            """,
            (start[:7], end[:7]),
        ).fetchall()
        flextime_through = end
        vacation_used = get_day_type_days(conn, year, "URLAUB", start, end)
        sick_used = get_day_type_days(conn, year, "KRANK", start, end)
    else:
        summaries = []
        open_dates = set()
        month_rows = []
        today = Date.today()
        flextime_through = today.isoformat() if year >= today.year else f"{year}-12-31"
        vacation_used = 0.0
        sick_used = 0.0
    flextime_minutes = get_flextime_balance(conn, flextime_through)
    flextime_status = classify_balance(flextime_minutes)
    return {
        "year": year,
        "target_minutes": sum(int(row["target_minutes"]) for row in summaries),
        "actual_minutes": sum(int(row["actual_minutes"]) for row in summaries),
        "break_minutes": sum(int(row["break_minutes"]) for row in summaries),
        "balance_minutes": sum(int(row["balance_minutes"]) for row in summaries if row["date"] not in open_dates),
        "flextime_minutes": flextime_minutes,
        "flextime_hours": format_minutes_as_decimal_hours(flextime_minutes),
        "flextime_status": {
            "key": flextime_status.key,
            "class": flextime_status.css_class,
            "label": flextime_status.label,
        },
        "vacation_used": vacation_used,
        "sick_used": sick_used,
        "remaining_vacation": get_remaining_vacation(conn, year),
        "homeoffice_days": sum(1 for row in summaries if row["location"] == "HOME"),
        "office_days": sum(1 for row in summaries if row["location"] == "OFFICE"),
        "mixed_days": sum(1 for row in summaries if row["location"] == "MIXED"),
        "months": [_month_row_with_status(row) for row in month_rows],
    }


def get_location_statistics(conn, through_date: str | None = None) -> dict[str, Any]:
    settings = database.get_settings(conn)
    end = through_date or Date.today().isoformat()
    start = get_effective_tracking_start_date(conn, settings).isoformat()
    summaries = database.get_day_summaries_between(conn, start, end) if start <= end else []
    tracked_office = sum(1 for row in summaries if row["location"] == "OFFICE")
    tracked_home = sum(1 for row in summaries if row["location"] == "HOME")
    tracked_mixed = sum(1 for row in summaries if row["location"] == "MIXED")
    manual_office = max(0.0, _safe_float(settings.get("office_baseline_days"), 0.0))
    manual_home = max(0.0, _safe_float(settings.get("homeoffice_baseline_days"), 0.0))
    weighted_office = manual_office + tracked_office + (tracked_mixed * 0.5)
    weighted_home = manual_home + tracked_home + (tracked_mixed * 0.5)
    total_days = weighted_office + weighted_home
    office_percent = (weighted_office / total_days * 100) if total_days else 0.0
    home_percent = (weighted_home / total_days * 100) if total_days else 0.0
    return {
        "start_date": start,
        "end_date": end,
        "tracked_office_days": tracked_office,
        "tracked_homeoffice_days": tracked_home,
        "tracked_mixed_days": tracked_mixed,
        "manual_office_days": manual_office,
        "manual_homeoffice_days": manual_home,
        "office_days": weighted_office,
        "homeoffice_days": weighted_home,
        "total_days": total_days,
        "office_percent": round(office_percent, 1),
        "homeoffice_percent": round(home_percent, 1),
        "office_requirement_met": total_days == 0 or office_percent >= 50,
    }


def get_effective_tracking_start_date(conn, settings: Mapping[str, str] | None = None) -> Date:
    """Return explicit start date, first real record date, or today.

    Generated summary rows are intentionally ignored here. This prevents old
    recalculations from turning unused months into a large negative balance.
    """

    settings = settings or database.get_settings(conn)
    explicit = get_tracking_start_date(settings)
    if explicit:
        return explicit
    rows = conn.execute(
        """
        SELECT MIN(date) AS first_date FROM (
            SELECT date FROM segments
            UNION ALL SELECT date FROM day_types
            UNION ALL SELECT date FROM notes
        )
        """
    ).fetchone()
    if rows and rows["first_date"]:
        return min(parse_date(rows["first_date"]), Date.today())
    return Date.today()


def get_statistics_date_range(conn, year: int, settings: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    today = Date.today()
    if year > today.year:
        return None
    start = Date(year, 1, 1)
    end = Date(year, 12, 31)
    if year == today.year:
        end = min(end, today)
    effective_start = get_effective_tracking_start_date(conn, settings)
    start = max(start, effective_start)
    if start > end:
        return None
    return start.isoformat(), end.isoformat()


def _settings_with_effective_tracking_start(conn) -> dict[str, str]:
    settings = database.get_settings(conn)
    if get_tracking_start_date(settings):
        return settings
    effective_start = get_effective_tracking_start_date(conn, settings)
    settings = dict(settings)
    settings["tracking_start_date"] = effective_start.isoformat()
    return settings


def _month_row_with_status(row) -> dict[str, Any]:
    data = dict(row)
    status = classify_balance(int(data["carry_over_minutes"]))
    data["carry_over_status"] = {
        "key": status.key,
        "class": status.css_class,
        "label": status.label,
    }
    data["carry_over_hours"] = format_minutes_as_decimal_hours(int(data["carry_over_minutes"]))
    return data


def _open_segment_dates(conn, start_date: str, end_date: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM segments WHERE end_time IS NULL AND date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchall()
    return {row["date"] for row in rows}


def _auto_break_minutes(gross_work_minutes: int, explicit_break_minutes: int, settings: Mapping[str, str]) -> int:
    if gross_work_minutes <= 0:
        return 0
    configured_break = _safe_int(settings.get("daily_break_minutes"), int(DEFAULT_SETTINGS["daily_break_minutes"]))
    configured_break = max(0, configured_break)
    return max(0, min(gross_work_minutes, configured_break - explicit_break_minutes))


def _counts_as_absence_day(conn, date_value: str, settings: Mapping[str, str]) -> bool:
    if get_target_minutes_for_date(date_value, settings) <= 0:
        return False
    holiday, _ = is_public_holiday(date_value, settings)
    if holiday:
        return False
    explicit_holiday = conn.execute(
        "SELECT 1 FROM day_types WHERE date = ? AND type = 'FEIERTAG' LIMIT 1",
        (date_value,),
    ).fetchone()
    return explicit_holiday is None


def _sum_segment_minutes(date_value: str, segments: Iterable[Mapping[str, Any]], segment_type: str, now: datetime) -> int:
    total = 0
    for row in segments:
        if _row_get(row, "type") != segment_type:
            continue
        end_time = _row_get(row, "end_time")
        if not end_time:
            if parse_date(date_value) == now.date():
                end_time = now.strftime("%H:%M:%S")
            else:
                continue
        total += minutes_between(date_value, _row_get(row, "start_time"), end_time)
    return total


def _aggregate_location(values: Iterable[str | None]) -> str | None:
    clean = {value for value in values if value}
    if not clean:
        return None
    if len(clean) == 1:
        return next(iter(clean))
    return "MIXED"


def _primary_day_type(rows: list[Mapping[str, Any]]) -> str | None:
    types = {_row_get(row, "type") for row in rows}
    for day_type in DAY_TYPE_PRIORITY:
        if day_type in types:
            return day_type
    return None


def _parse_weekday_targets(raw_value: str | None) -> dict[str, int]:
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in parsed.items():
        if str(key) in {"0", "1", "2", "3", "4", "5", "6"}:
            result[str(key)] = int(round(float(value)))
    return result


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _row_get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default
