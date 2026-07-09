"""Python API exposed to the pywebview frontend."""

from __future__ import annotations

import calendar
import logging
import threading
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.instance import read_command
from common import calculations, database, export, location
from common.balance import (
    classify_balance,
    format_minutes_as_decimal_hours,
    parse_decimal_hours_to_minutes,
)
from common.config import PROJECT_ROOT
from common.models import current_time_str, normalize_time_input, parse_date, today_str


class WorktimeApi:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else database.init_db()
        database.init_db(self.db_path)
        self._lock = threading.RLock()
        self.logger = logging.getLogger("worktime.app.api")
        self._window = None
        self._last_command_id: str | None = None

    def attach_window(self, window) -> None:
        self._window = window

    def dashboard(self) -> dict[str, Any]:
        with self._locked_conn() as conn:
            today = today_str()
            stats_range = calculations.get_statistics_date_range(conn, Date.today().year)
            if stats_range:
                calculations.recalculate_range(conn, *stats_range)
            summary = calculations.recalculate_day(conn, today)
            segments = database.get_segments_for_date(conn, today)
            settings = database.get_settings(conn)
            first_work = next((row["start_time"] for row in segments if row["type"] == "WORK"), "")
            ended_segments = [row["end_time"] for row in segments if row["end_time"]]
            last_end = ended_segments[-1] if ended_segments else ""
            open_segment = next((row for row in segments if row["end_time"] is None), None)
            flextime_minutes = calculations.get_flextime_balance(conn, today)
            flextime_status = classify_balance(flextime_minutes)
            return {
                "today": today,
                "range": f"{first_work[:5] if first_work else '--:--'} – {last_end[:5] if last_end else 'läuft'}",
                "target_minutes": summary.target_minutes,
                "work_minutes": summary.actual_minutes,
                "break_minutes": summary.break_minutes,
                "day_balance": summary.balance_minutes,
                "flextime": flextime_minutes,
                "flextime_hours": format_minutes_as_decimal_hours(flextime_minutes),
                "flextime_status": {
                    "key": flextime_status.key,
                    "class": flextime_status.css_class,
                    "label": flextime_status.label,
                },
                "remaining_vacation": calculations.get_remaining_vacation(conn, Date.today().year),
                "location": _display_location(summary.location),
                "location_stats": calculations.get_location_statistics(conn, today),
                "settings": _settings_for_ui(settings),
                "live_day": _live_day_info(summary.balance_minutes, summary.target_minutes, open_segment, bool(first_work)),
            }

    def calendar_month(self, year: int, month: int) -> dict[str, Any]:
        with self._locked_conn() as conn:
            last_day = calendar.monthrange(int(year), int(month))[1]
            start = f"{int(year):04d}-{int(month):02d}-01"
            end = f"{int(year):04d}-{int(month):02d}-{last_day:02d}"
            calculations.recalculate_range(conn, start, end)
            summaries = {row["date"]: dict(row) for row in database.get_day_summaries_between(conn, start, end)}
            day_types: dict[str, list[dict[str, Any]]] = {}
            for row in database.get_day_types_between(conn, start, end):
                day_types.setdefault(row["date"], []).append(dict(row))
            notes = database.get_notes_between(conn, start, end)
            days = []
            for day_num in range(1, last_day + 1):
                date_text = f"{int(year):04d}-{int(month):02d}-{day_num:02d}"
                days.append(
                    {
                        "date": date_text,
                        "weekday": parse_date(date_text).weekday(),
                        "summary": summaries.get(date_text),
                        "day_types": day_types.get(date_text, []),
                        "note": notes.get(date_text, ""),
                    }
                )
            return {"year": int(year), "month": int(month), "days": days}

    def day_detail(self, date: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            summary = calculations.recalculate_day(conn, date)
            return {
                "date": date,
                "summary": summary.__dict__,
                "segments": [dict(row) for row in database.get_segments_for_date(conn, date)],
                "day_types": [dict(row) for row in database.get_day_types_for_date(conn, date)],
                "day_type_ranges": _day_type_ranges_for_date(conn, date),
                "note": database.get_note_for_date(conn, date),
            }

    def save_segment(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._locked_conn() as conn:
            date = payload["date"]
            old_date = date
            segment_type = payload.get("type", "WORK")
            start_time = normalize_time_input(payload.get("start_time", current_time_str()))
            end_value = payload.get("end_time") or None
            end_time = normalize_time_input(end_value) if end_value else None
            location_value = payload.get("location") or None
            if segment_type != "WORK":
                location_value = None
            if payload.get("id"):
                existing = database.get_segment(conn, int(payload["id"]))
                if existing:
                    old_date = existing["date"]
                database.update_segment(
                    conn,
                    int(payload["id"]),
                    date=date,
                    type=segment_type,
                    start_time=start_time,
                    end_time=end_time,
                    location=location_value,
                    source=payload.get("source", "MANUAL"),
                )
            else:
                database.add_segment(
                    conn,
                    date,
                    segment_type,
                    start_time,
                    end_time=end_time,
                    location=location_value,
                    source=payload.get("source", "MANUAL"),
                )
            calculations.recalculate_day(conn, old_date)
            if old_date != date:
                calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def delete_segment(self, segment_id: int) -> dict[str, Any]:
        with self._locked_conn() as conn:
            row = database.get_segment(conn, int(segment_id))
            if not row:
                return {"ok": False, "error": "Segment nicht gefunden"}
            date = row["date"]
            database.delete_segment(conn, int(segment_id))
            calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def save_day_type(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._locked_conn() as conn:
            date = payload["date"]
            database.upsert_day_type(
                conn,
                date,
                payload["type"],
                bool(payload.get("half_day")),
                payload.get("note") or None,
            )
            calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def delete_day_type(self, day_type_id: int) -> dict[str, Any]:
        with self._locked_conn() as conn:
            row = conn.execute("SELECT * FROM day_types WHERE id = ?", (int(day_type_id),)).fetchone()
            if not row:
                return {"ok": False, "error": "Abwesenheit nicht gefunden"}
            date = row["date"]
            database.delete_day_type(conn, int(day_type_id))
            calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def delete_day_type_range(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_ids = payload.get("ids", [])
        ids = [int(value) for value in raw_ids if str(value).strip()]
        if not ids:
            return {"ok": False, "error": "Keine Abwesenheit ausgewählt"}
        placeholders = ",".join("?" for _ in ids)
        with self._locked_conn() as conn:
            rows = conn.execute(
                f"SELECT id, date FROM day_types WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            if not rows:
                return {"ok": False, "error": "Abwesenheit nicht gefunden"}
            dates = sorted({row["date"] for row in rows})
            for row in rows:
                database.delete_day_type(conn, int(row["id"]))
            for date in dates:
                calculations.recalculate_day(conn, date)
            return {"ok": True, "deleted": len(rows)}

    def save_note(self, date: str, text: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            database.replace_note(conn, date, text)
            calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def entries(self, start_date: str, end_date: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            calculations.recalculate_range(conn, start_date, end_date)
            summaries = database.get_day_summaries_between(conn, start_date, end_date)
            notes = database.get_notes_between(conn, start_date, end_date)
            rows: list[dict[str, Any]] = []
            for summary in summaries:
                segments = database.get_segments_for_date(conn, summary["date"])
                work_segments = [row for row in segments if row["type"] == "WORK"]
                start = work_segments[0]["start_time"] if work_segments else ""
                end_values = [row["end_time"] for row in work_segments if row["end_time"]]
                rows.append(
                    {
                        "date": summary["date"],
                        "start": start,
                        "end": end_values[-1] if end_values else "",
                        "break_minutes": summary["break_minutes"],
                        "actual_minutes": summary["actual_minutes"],
                        "balance_minutes": summary["balance_minutes"],
                        "type": summary["day_category"],
                        "location": summary["location"] or "",
                        "note": notes.get(summary["date"], ""),
                    }
                )
            return {"rows": rows}

    def statistics(self, year: int) -> dict[str, Any]:
        with self._locked_conn() as conn:
            stats_range = calculations.get_statistics_date_range(conn, int(year))
            if stats_range:
                calculations.recalculate_range(conn, *stats_range)
            return calculations.get_year_statistics(conn, int(year))

    def settings(self) -> dict[str, str]:
        with self._locked_conn() as conn:
            return _settings_for_ui(database.get_settings(conn))

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        autostart_enabled: bool | None = None
        with self._locked_conn() as conn:
            normalized = _normalize_settings_input(values)
            if "autostart_enabled" in normalized:
                autostart_enabled = normalized["autostart_enabled"] == "1"
            database.set_settings(conn, normalized)
            year = Date.today().year
            if "vacation_days_per_year" in normalized or "vacation_carry_over" in normalized:
                database.update_vacation_account(
                    conn,
                    year,
                    float(str(normalized.get("vacation_days_per_year", database.get_setting(conn, "vacation_days_per_year") or 0)).replace(",", ".")),
                    float(str(normalized.get("vacation_carry_over", database.get_setting(conn, "vacation_carry_over") or 0)).replace(",", ".")),
                )
            start, end = _known_recalculation_range(conn, database.get_settings(conn))
            calculations.recalculate_range(conn, start, end)
            result = {"ok": True, "settings": _settings_for_ui(database.get_settings(conn))}
        if autostart_enabled is not None:
            _apply_autostart_setting(autostart_enabled)
        return result

    def reset_application(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode", "")).strip()
        if mode not in {"settings", "data", "all"}:
            raise ValueError("Unbekannte Zurücksetzen-Option.")
        with self._locked_conn() as conn:
            database.reset_local_state(
                conn,
                reset_settings=mode in {"settings", "all"},
                reset_tracking_data=mode in {"data", "all"},
                require_initial_setup=mode == "all",
            )
            settings = database.get_settings(conn)
            start, end = _known_recalculation_range(conn, settings)
            calculations.recalculate_range(conn, start, end)
            return {"ok": True, "mode": mode, "settings": _settings_for_ui(database.get_settings(conn))}

    def add_day_type_range(self, payload: dict[str, Any]) -> dict[str, Any]:
        start = parse_date(payload["start_date"])
        end = parse_date(payload["end_date"])
        if end < start:
            raise ValueError("Enddatum darf nicht vor dem Startdatum liegen.")
        with self._locked_conn() as conn:
            current = start
            while current <= end:
                date_text = current.isoformat()
                database.upsert_day_type(
                    conn,
                    date_text,
                    payload["type"],
                    bool(payload.get("half_day")),
                    payload.get("note") or None,
                )
                calculations.recalculate_day(conn, date_text)
                current = Date.fromordinal(current.toordinal() + 1)
            return {"ok": True}

    def absences(self, year: int | None = None) -> dict[str, Any]:
        selected_year = int(year or Date.today().year)
        start = f"{selected_year:04d}-01-01"
        end = f"{selected_year:04d}-12-31"
        with self._locked_conn() as conn:
            calculations.recalculate_range(conn, start, min(end, today_str()) if selected_year == Date.today().year else end)
            return {"year": selected_year, "rows": _day_type_ranges(conn, start, end)}

    def create_backup(self) -> dict[str, Any]:
        with self._locked_conn() as conn:
            path = database.create_manual_backup(conn, self.db_path)
            return {"ok": True, "path": str(path), "name": path.name}

    def export_period(self, start_date: str, end_date: str, export_format: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            path = export.export_period(conn, start_date, end_date, export_format)
            return {"ok": True, "path": str(path), "name": path.name}

    def close_month(self, year_month: str, closed: bool) -> dict[str, Any]:
        with self._locked_conn() as conn:
            calculations.close_month(conn, year_month, bool(closed))
            return {"ok": True, "month": dict(database.get_month_closing(conn, year_month))}

    def detect_location_now(self) -> dict[str, Any]:
        with self._locked_conn() as conn:
            settings = database.get_settings(conn)
            try:
                detected = location.detect_location(
                    settings.get("homeoffice_check_targets", ""),
                    settings.get("homeoffice_check_timeout_ms", "1500"),
                )
            except ValueError as exc:
                self.logger.warning("Standort-Check-Konfiguration ungueltig: %s", exc)
                detected = "HOME"
            return {"location": detected, "label": _display_location(detected)}

    def consume_app_command(self) -> dict[str, Any] | None:
        command = read_command(self._last_command_id)
        if not command:
            return None
        self._last_command_id = command["id"]
        self._focus_window()
        return {"view": command["view"]}

    def _focus_window(self) -> None:
        if self._window is None:
            return
        try:
            for method_name in ("restore", "show"):
                method = getattr(self._window, method_name, None)
                if method:
                    method()
            evaluate_js = getattr(self._window, "evaluate_js", None)
            if evaluate_js:
                evaluate_js("window.focus();")
        except Exception:
            self.logger.debug("App-Fenster konnte nicht explizit fokussiert werden", exc_info=True)

    def _locked_conn(self):
        class LockedConnection:
            def __init__(self, outer: "WorktimeApi") -> None:
                self.outer = outer
                self.conn = None

            def __enter__(self):
                self.outer._lock.acquire()
                self.conn = database.connect(self.outer.db_path)
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                if self.conn:
                    self.conn.close()
                self.outer._lock.release()

        return LockedConnection(self)


def _display_location(value: str | None) -> str:
    return {"OFFICE": "Büro", "HOME": "Homeoffice", "MIXED": "Gemischt", "UNKNOWN": "Unbekannt"}.get(value or "", "Unbekannt")


def _live_day_info(balance_minutes: int, target_minutes: int, open_segment, has_work_today: bool) -> dict[str, Any]:
    note = "Der laufende Tag ist noch nicht im Gleitzeitkonto enthalten."
    zero_time = None
    if open_segment and open_segment["type"] == "WORK":
        if balance_minutes < 0:
            zero_time = (datetime.now() + timedelta(minutes=abs(balance_minutes))).strftime("%H:%M")
            detail = f"Wenn du weiter arbeitest, erreichst du heute gegen {zero_time} Uhr ungefähr ±0."
        else:
            detail = "Du bist heute live bereits im Plus."
    elif open_segment and open_segment["type"] == "BREAK":
        detail = "Pause läuft gerade; die Live-Berechnung zählt erst nach Arbeitsfortsetzung weiter."
    elif open_segment and open_segment["type"] == "ABSENCE":
        detail = "Abwesenheit läuft gerade; die Live-Berechnung zählt erst nach Arbeitsfortsetzung weiter."
    elif has_work_today:
        detail = "Heute ist aktuell kein Segment offen. Der Tagesstand wird beim nächsten Ereignis aktualisiert."
    else:
        detail = f"Ohne Arbeitssegment würde der heutige Tag mit {calculations.minutes_to_hhmm(-target_minutes)} abschließen."
    return {
        "balance_minutes": balance_minutes,
        "zero_time": zero_time,
        "note": note,
        "detail": detail,
        "has_open_segment": bool(open_segment),
        "open_type": open_segment["type"] if open_segment else None,
    }


def _settings_for_ui(settings: dict[str, str]) -> dict[str, str]:
    result = dict(settings)
    try:
        minutes = int(result.get("initial_flextime_minutes", "0") or "0")
    except ValueError:
        minutes = 0
    result["initial_flextime_hours"] = format_minutes_as_decimal_hours(minutes, signed=False).replace(" h", "")
    result["workday_weekdays"] = ",".join(str(day) for day in calculations.get_workday_indices(settings))
    try:
        break_minutes = max(0, int(result.get("daily_break_minutes", "0") or "0"))
    except ValueError:
        break_minutes = 0
    result["daily_break_minutes"] = str(break_minutes)
    result["office_baseline_days"] = _setting_float_for_ui(result.get("office_baseline_days", "0"))
    result["homeoffice_baseline_days"] = _setting_float_for_ui(result.get("homeoffice_baseline_days", "0"))
    result["office_start_buffer_minutes"] = _setting_int_for_ui(result.get("office_start_buffer_minutes", "0"))
    result["home_start_buffer_minutes"] = _setting_int_for_ui(result.get("home_start_buffer_minutes", "0"))
    return result


def _normalize_settings_input(values: dict[str, Any]) -> dict[str, str]:
    normalized = {key: "" if value is None else str(value).strip() for key, value in values.items()}
    if normalized.get("tracking_start_date"):
        parse_date(normalized["tracking_start_date"])
    if "daily_break_minutes" in normalized:
        try:
            break_minutes = int(normalized["daily_break_minutes"] or "0")
        except ValueError as exc:
            raise ValueError("Pausenzeit muss eine ganze Zahl in Minuten sein.") from exc
        if break_minutes < 0:
            raise ValueError("Pausenzeit darf nicht negativ sein.")
        normalized["daily_break_minutes"] = str(break_minutes)
    if "workday_weekdays" in normalized:
        weekdays = _normalize_workday_weekdays(normalized["workday_weekdays"])
        normalized["workday_weekdays"] = ",".join(str(day) for day in weekdays)
        normalized["workdays_per_week"] = str(len(weekdays))
    for key, label in (
        ("office_start_buffer_minutes", "Startpuffer Büro"),
        ("home_start_buffer_minutes", "Startpuffer Homeoffice"),
    ):
        if key in normalized:
            normalized[key] = _normalize_nonnegative_int(normalized[key], label)
    for key, label in (
        ("office_baseline_days", "Manuelle Büro-Tage"),
        ("homeoffice_baseline_days", "Manuelle Homeoffice-Tage"),
    ):
        if key in normalized:
            normalized[key] = _normalize_nonnegative_decimal(normalized[key], label)
    if "initial_flextime_hours" in normalized:
        minutes = parse_decimal_hours_to_minutes(
            normalized.pop("initial_flextime_hours"),
            "Anfangssaldo Gleitzeit in Stunden",
        )
        normalized["initial_flextime_minutes"] = str(minutes)
    if "initial_setup_required" in normalized:
        normalized["initial_setup_required"] = "1" if normalized["initial_setup_required"] in {"1", "true", "True", "ja"} else "0"
    for key in (
        "autostart_enabled",
        "automatic_work_start_enabled",
        "automatic_work_end_enabled",
        "automatic_recovery_enabled",
        "auto_resume_after_break_enabled",
        "auto_resume_after_absence_enabled",
    ):
        if key in normalized:
            normalized[key] = _normalize_bool_setting(normalized[key])
    return normalized


def _apply_autostart_setting(enabled: bool) -> None:
    from tracker import autostart_windows

    autostart_windows.configure_startup_shortcut(enabled, PROJECT_ROOT / "main.pyw")


def _normalize_bool_setting(value: str) -> str:
    return "1" if str(value).strip().lower() in {"1", "true", "ja", "yes", "on"} else "0"


def _normalize_workday_weekdays(raw_value: str) -> list[int]:
    weekdays: set[int] = set()
    for item in raw_value.split(","):
        if not item.strip():
            continue
        try:
            weekday = int(item.strip())
        except ValueError as exc:
            raise ValueError("Arbeitstage konnten nicht gelesen werden.") from exc
        if not 0 <= weekday <= 6:
            raise ValueError("Arbeitstage müssen zwischen Montag und Sonntag liegen.")
        weekdays.add(weekday)
    if not weekdays:
        raise ValueError("Mindestens ein Arbeitstag muss ausgewählt sein.")
    return sorted(weekdays)


def _normalize_nonnegative_decimal(raw_value: str, label: str) -> str:
    if not raw_value:
        return "0"
    try:
        value = float(raw_value.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{label} muss eine Zahl sein.") from exc
    if value < 0:
        raise ValueError(f"{label} darf nicht negativ sein.")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _normalize_nonnegative_int(raw_value: str, label: str) -> str:
    if not raw_value:
        return "0"
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} muss eine ganze Zahl sein.") from exc
    if value < 0:
        raise ValueError(f"{label} darf nicht negativ sein.")
    return str(value)


def _setting_float_for_ui(raw_value: str) -> str:
    try:
        value = float(str(raw_value or "0").replace(",", "."))
    except ValueError:
        value = 0.0
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _setting_int_for_ui(raw_value: str) -> str:
    try:
        value = int(str(raw_value or "0"))
    except ValueError:
        value = 0
    return str(max(0, value))


def _known_recalculation_range(conn, settings: dict[str, str]) -> tuple[str, str]:
    dates: list[str] = [f"{Date.today().year}-01-01", today_str()]
    if settings.get("tracking_start_date"):
        dates.append(settings["tracking_start_date"])
    for table in ("segments", "day_types", "day_summary"):
        row = conn.execute(f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table}").fetchone()
        if row["min_date"]:
            dates.append(row["min_date"])
        if row["max_date"]:
            dates.append(row["max_date"])
    return min(dates), max(dates)


def _day_type_ranges_for_date(conn, date: str) -> list[dict[str, Any]]:
    rows = database.get_day_types_for_date(conn, date)
    return [_expand_day_type_range(conn, dict(row)) for row in rows]


def _day_type_ranges(conn, start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM day_types
            WHERE date BETWEEN ? AND ?
            ORDER BY type, date, half_day, COALESCE(note, '')
            """,
            (start_date, end_date),
        ).fetchall()
    ]
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_date: Date | None = None
    for row in rows:
        row_date = parse_date(row["date"])
        row_key = _day_type_key(row)
        if current and current["_key"] == row_key and last_date and row_date == last_date + timedelta(days=1):
            current["end_date"] = row["date"]
            current["ids"].append(row["id"])
        else:
            if current:
                groups.append(_finalize_day_type_range(conn, current))
            current = {
                "_key": row_key,
                "type": row["type"],
                "start_date": row["date"],
                "end_date": row["date"],
                "half_day": bool(row["half_day"]),
                "note": row["note"] or "",
                "ids": [row["id"]],
            }
        last_date = row_date
    if current:
        groups.append(_finalize_day_type_range(conn, current))
    return sorted(groups, key=lambda group: (group["start_date"], group["type"]))


def _expand_day_type_range(conn, row: dict[str, Any]) -> dict[str, Any]:
    key = _day_type_key(row)
    start = parse_date(row["date"])
    end = parse_date(row["date"])
    while _matching_day_type_exists(conn, start - timedelta(days=1), key):
        start -= timedelta(days=1)
    while _matching_day_type_exists(conn, end + timedelta(days=1), key):
        end += timedelta(days=1)
    group = {
        "_key": key,
        "type": row["type"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "half_day": bool(row["half_day"]),
        "note": row["note"] or "",
        "ids": [row["id"]],
    }
    return _finalize_day_type_range(conn, group)


def _matching_day_type_exists(conn, date_value: Date, key: tuple[str, bool, str]) -> bool:
    row = conn.execute(
        """
        SELECT type, half_day, note FROM day_types
        WHERE date = ? AND type = ?
        LIMIT 1
        """,
        (date_value.isoformat(), key[0]),
    ).fetchone()
    return bool(row and _day_type_key(row) == key)


def _day_type_key(row) -> tuple[str, bool, str]:
    return row["type"], bool(row["half_day"]), row["note"] or ""


def _finalize_day_type_range(conn, group: dict[str, Any]) -> dict[str, Any]:
    group = {key: value for key, value in group.items() if key != "_key"}
    group["days"] = (parse_date(group["end_date"]) - parse_date(group["start_date"])).days + 1
    group["counted_days"] = _count_day_type_days(conn, group["type"], group["start_date"], group["end_date"])
    return group


def _count_day_type_days(conn, day_type: str, start_date: str, end_date: str) -> float:
    start = parse_date(start_date)
    end = parse_date(end_date)
    total = 0.0
    for year in range(start.year, end.year + 1):
        year_start = max(start, Date(year, 1, 1)).isoformat()
        year_end = min(end, Date(year, 12, 31)).isoformat()
        total += calculations.get_day_type_days(conn, year, day_type, year_start, year_end)
    return total
