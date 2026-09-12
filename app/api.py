"""Python API exposed to the pywebview frontend."""

from __future__ import annotations

import base64
import binascii
import calendar
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
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
        if db_path is None:
            self.db_path = database.init_db()
        else:
            self.db_path = Path(db_path)
            database.init_db(self.db_path)
        self._lock = threading.RLock()
        self.logger = logging.getLogger("worktime.app.api")
        self._window = None
        self._last_command_id: str | None = None
        self._allow_window_close = False

    def attach_window(self, window) -> None:
        self._window = window

    def dashboard(self) -> dict[str, Any]:
        with self._locked_conn() as conn:
            today = today_str()
            settings = database.get_settings(conn)
            summary = calculations.recalculate_day(conn, today)
            segments = database.get_segments_for_date(conn, today)
            first_work = next((row["start_time"] for row in segments if row["type"] == "WORK"), "")
            ended_segments = [row["end_time"] for row in segments if row["end_time"]]
            last_end = ended_segments[-1] if ended_segments else ""
            open_segment = next((row for row in segments if row["end_time"] is None), None)
            flextime_minutes = calculations.get_flextime_balance(conn, today)
            flextime_status = classify_balance(flextime_minutes)
            today_detail = _today_dashboard_detail(summary, segments, settings, open_segment)
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
                "vacation_stats": _dashboard_vacation_stats(conn, parse_date(today), settings),
                "next_absence": _next_absence_countdown(conn, today, settings),
                "location": _display_location(summary.location),
                "location_stats": calculations.get_location_statistics(conn, today),
                "flextime_trends": _dashboard_flextime_trends(conn, parse_date(today), settings),
                "today_detail": today_detail,
                "settings": _settings_for_ui(settings),
                "live_day": _live_day_info(
                    summary.balance_minutes,
                    summary.target_minutes,
                    summary.break_minutes,
                    settings,
                    open_segment,
                    bool(first_work),
                    remaining_work_minutes=today_detail["remaining_work_minutes"],
                ),
            }

    def calendar_month(self, year: int, month: int, live_only: bool = False) -> dict[str, Any]:
        with self._locked_conn() as conn:
            last_day = calendar.monthrange(int(year), int(month))[1]
            start = f"{int(year):04d}-{int(month):02d}-01"
            end = f"{int(year):04d}-{int(month):02d}-{last_day:02d}"
            if live_only:
                today = today_str()
                if start <= today <= end:
                    calculations.recalculate_day(conn, today)
            else:
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

    def homeoffice_email(self, date: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            summary = calculations.recalculate_day(conn, date)
            settings = database.get_settings(conn)
            segments = [dict(row) for row in database.get_segments_for_date(conn, date)]
            template = settings.get("pa_email_template", "") or ""
            return {
                "date": date,
                "body": _render_pa_email_template(template, date, summary.__dict__, segments),
            }

    def copy_to_clipboard(self, text: str) -> dict[str, Any]:
        _copy_text_to_clipboard(str(text or ""))
        return {"ok": True}

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

    def save_day_edits(self, date: str, payload: dict[str, Any]) -> dict[str, Any]:
        affected_dates = {date}
        with self._locked_conn() as conn:
            with database.transaction(conn):
                for segment in payload.get("segments", []):
                    segment_id = int(segment.get("id") or 0)
                    existing = database.get_segment(conn, segment_id)
                    if not existing:
                        raise ValueError("Segment nicht gefunden.")
                    old_date = existing["date"]
                    affected_dates.add(old_date)
                    segment_type = segment.get("type", "WORK")
                    start_time = normalize_time_input(segment.get("start_time", current_time_str()))
                    end_value = segment.get("end_time") or None
                    end_time = normalize_time_input(end_value) if end_value else None
                    location_value = segment.get("location") or None
                    if segment_type != "WORK":
                        location_value = None
                    database.update_segment(
                        conn,
                        segment_id,
                        date=date,
                        type=segment_type,
                        start_time=start_time,
                        end_time=end_time,
                        location=location_value,
                        source=segment.get("source", "MANUAL"),
                    )
                if "note" in payload:
                    database.replace_note(conn, date, payload.get("note") or "")
                for date_text in sorted(affected_dates):
                    calculations.recalculate_day(conn, date_text)
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
                payload.get("approval_status") or "approved",
                "MANUAL",
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

    def update_day_type_range(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_ids = payload.get("ids", [])
        ids = [int(value) for value in raw_ids if str(value).strip()]
        if not ids:
            return {"ok": False, "error": "Keine Abwesenheit ausgewählt"}
        start = parse_date(payload["start_date"])
        end = parse_date(payload["end_date"])
        if end < start:
            raise ValueError("Enddatum darf nicht vor dem Startdatum liegen.")
        placeholders = ",".join("?" for _ in ids)
        with self._locked_conn() as conn:
            rows = conn.execute(
                f"SELECT id, date FROM day_types WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            if not rows:
                return {"ok": False, "error": "Abwesenheit nicht gefunden"}
            affected_dates = {row["date"] for row in rows}
            for row in rows:
                database.delete_day_type(conn, int(row["id"]))
            current = start
            while current <= end:
                date_text = current.isoformat()
                database.upsert_day_type(
                    conn,
                    date_text,
                    payload["type"],
                    bool(payload.get("half_day")),
                    payload.get("note") or None,
                    payload.get("approval_status") or "planned",
                    "MANUAL",
                )
                affected_dates.add(date_text)
                current = Date.fromordinal(current.toordinal() + 1)
            for date_text in sorted(affected_dates):
                calculations.recalculate_day(conn, date_text)
            return {"ok": True, "updated": len(ids)}

    def save_note(self, date: str, text: str) -> dict[str, Any]:
        with self._locked_conn() as conn:
            database.replace_note(conn, date, text)
            calculations.recalculate_day(conn, date)
            return self.day_detail(date)

    def entries(self, start_date: str, end_date: str, live_only: bool = False) -> dict[str, Any]:
        with self._locked_conn() as conn:
            if live_only:
                today = today_str()
                if start_date <= today <= end_date:
                    calculations.recalculate_day(conn, today)
            else:
                calculations.recalculate_range(conn, start_date, end_date)
            summaries = database.get_day_summaries_between(conn, start_date, end_date)
            notes = database.get_notes_between(conn, start_date, end_date)
            segments_by_date: dict[str, list[Any]] = {}
            for segment in database.get_segments_between(conn, start_date, end_date):
                segments_by_date.setdefault(segment["date"], []).append(segment)
            rows: list[dict[str, Any]] = []
            for summary in summaries:
                segments = segments_by_date.get(summary["date"], [])
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

    def statistics(self, year: int, live_only: bool = False) -> dict[str, Any]:
        with self._locked_conn() as conn:
            stats_range = calculations.get_statistics_date_range(conn, int(year))
            if live_only:
                today = today_str()
                if int(year) == Date.today().year and stats_range and stats_range[0] <= today <= stats_range[1]:
                    calculations.recalculate_day(conn, today)
            elif stats_range:
                calculations.recalculate_range(conn, *stats_range)
            return calculations.get_year_statistics(conn, int(year))

    def calculator_defaults(self) -> dict[str, Any]:
        with self._locked_conn() as conn:
            today = Date.today()
            settings = database.get_settings(conn)
            workdays = calculations.get_workday_indices(settings)
            monday = today - timedelta(days=today.weekday())
            targets = {
                str(day): calculations.get_target_minutes_for_date(monday + timedelta(days=day), settings)
                for day in range(7)
            }
            segments = database.get_segments_for_date(conn, today.isoformat())
            first_work = next((row["start_time"] for row in segments if row["type"] == "WORK"), "")
            return {
                "today": today.isoformat(),
                "today_work_start_time": first_work[:5] if first_work else "",
                "workday_weekdays": workdays,
                "daily_break_minutes": _safe_nonnegative_int(settings.get("daily_break_minutes"), 0),
                "weekly_target_hours": _setting_float_for_ui(settings.get("weekly_target_hours", "40")),
                "target_minutes_by_weekday": targets,
                "flextime_minutes": calculations.get_flextime_balance(conn, today.isoformat()),
            }

    def settings(self) -> dict[str, str]:
        with self._locked_conn() as conn:
            return _settings_for_ui(database.get_settings(conn))

    def export_defaults(self) -> dict[str, str]:
        with self._locked_conn() as conn:
            return {
                "start_date": _default_export_start_date(conn),
                "end_date": _default_export_end_date(conn),
            }

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
            settings = database.get_settings(conn)
            if any(key.startswith("standard_absence_") for key in normalized):
                for standard_year in range(year - 1, year + 3):
                    database.apply_standard_day_types(conn, settings, standard_year)
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
                    payload.get("approval_status") or "planned",
                    "MANUAL",
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

    def import_uploaded_file(self, file_name: str, payload_base64: str) -> dict[str, Any]:
        suffix = Path(file_name or "").suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xlsm"}:
            raise ValueError("Import unterstuetzt nur CSV oder Excel (.xlsx).")
        try:
            payload = base64.b64decode(payload_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Importdatei konnte nicht gelesen werden.") from exc
        if not payload:
            raise ValueError("Importdatei ist leer.")

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="worktime-import-", suffix=suffix, delete=False) as handle:
                handle.write(payload)
                tmp_path = Path(handle.name)
            with self._locked_conn() as conn:
                result = export.import_file(conn, tmp_path, source_name=Path(file_name).name)
            return {"ok": True, "name": Path(file_name).name, **result}
        finally:
            _unlink_temp_file(tmp_path, self.logger)

    def preview_sap_sdata_file(self, file_name: str, payload_base64: str) -> dict[str, Any]:
        suffix = Path(file_name or "").suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xlsm"}:
            raise ValueError("SAP-SDATA-Import unterstützt CSV oder Excel (.xlsx).")
        try:
            payload = base64.b64decode(payload_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("SAP-SDATA-Datei konnte nicht gelesen werden.") from exc
        if not payload:
            raise ValueError("SAP-SDATA-Datei ist leer.")

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="worktime-sap-sdata-", suffix=suffix, delete=False) as handle:
                handle.write(payload)
                tmp_path = Path(handle.name)
            with self._locked_conn() as conn:
                preview = export.preview_sdata_file(conn, tmp_path, source_name=Path(file_name).name)
            return {"ok": True, "name": Path(file_name).name, **preview}
        finally:
            _unlink_temp_file(tmp_path, self.logger)

    def import_sap_sdata_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        days = payload.get("days")
        selected_dates = payload.get("selected_dates")
        if not isinstance(days, list) or not isinstance(selected_dates, list):
            raise ValueError("SAP-SDATA-Auswahl konnte nicht gelesen werden.")
        source_name = str(payload.get("source_name") or "SAP SDATA Import")
        with self._locked_conn() as conn:
            result = export.import_sdata_preview(
                conn,
                days,
                [str(value) for value in selected_dates],
                source_name=source_name,
            )
        return {"ok": True, "name": source_name, **result}

    def open_import_log(self, log_path: str) -> dict[str, Any]:
        path = Path(log_path).expanduser().resolve()
        log_root = export.IMPORT_LOG_DIR.resolve()
        if path.parent != log_root or path.suffix.lower() != ".html" or not path.exists():
            raise ValueError("Import-Protokoll konnte nicht geöffnet werden.")
        webbrowser.open(path.as_uri())
        return {"ok": True, "path": str(path)}

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
        if command.get("action") == "quit":
            self.close_window()
            return {"action": "quit"}
        self.focus_window(command["view"])
        return {"action": "show", "view": command["view"]}

    def focus_window(self, view: str | None = None) -> None:
        self._focus_window()
        if view:
            self._set_frontend_view(view)

    def hide_window_on_close(self) -> bool:
        if self._allow_window_close:
            return True
        self.hide_window()
        return False

    def hide_window(self) -> None:
        if self._window is None:
            return
        try:
            method = getattr(self._window, "hide", None)
            if method:
                method()
        except Exception:
            self.logger.debug("App-Fenster konnte nicht versteckt werden", exc_info=True)

    def close_window(self) -> None:
        if self._window is None:
            return
        self._allow_window_close = True
        try:
            method = getattr(self._window, "destroy", None)
            if method:
                method()
        except Exception:
            self.logger.debug("App-Fenster konnte nicht beendet werden", exc_info=True)

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

    def _set_frontend_view(self, view: str) -> None:
        if self._window is None:
            return
        try:
            evaluate_js = getattr(self._window, "evaluate_js", None)
            if evaluate_js:
                evaluate_js(f"if (window.__worktimeSetView) {{ window.__worktimeSetView({json.dumps(view)}); }}")
        except Exception:
            self.logger.debug("App-Ansicht konnte nicht per Direktkommando gesetzt werden", exc_info=True)

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


def _today_dashboard_detail(summary, segments, settings: dict[str, str], open_segment) -> dict[str, Any]:
    try:
        minimum_break = max(0, int(settings.get("daily_break_minutes", "0") or "0"))
    except ValueError:
        minimum_break = 0
    work_segments = [row for row in segments if row["type"] == "WORK"]
    break_segments = [row for row in segments if row["type"] == "BREAK"]
    absence_segments = [row for row in segments if row["type"] == "ABSENCE"]
    remaining_work = max(0, int(summary.target_minutes) - int(summary.actual_minutes))
    remaining_break = _remaining_minimum_break_minutes(summary.break_minutes, settings)
    return {
        "minimum_break_minutes": minimum_break,
        "remaining_break_minutes": remaining_break,
        "remaining_work_minutes": remaining_work,
        "work_segment_count": len(work_segments),
        "break_segment_count": len(break_segments),
        "absence_segment_count": len(absence_segments),
        "open_segment_label": _segment_type_label(open_segment["type"]) if open_segment else "Keins",
    }


def _dashboard_flextime_trends(conn, today: Date, settings: dict[str, str]) -> list[dict[str, Any]]:
    tracking_start = calculations.get_effective_tracking_start_date(conn, settings)
    raw_periods = (
        ("last_7", "Letzte 7 Tage", today - timedelta(days=6)),
        ("last_30", "Letzte 30 Tage", today - timedelta(days=29)),
        ("month", "Dieser Monat", Date(today.year, today.month, 1)),
        ("year", "Dieses Jahr", Date(today.year, 1, 1)),
    )
    trends: list[dict[str, Any]] = []
    for key, label, raw_start in raw_periods:
        start = max(raw_start, tracking_start)
        rows = database.get_day_summaries_between(conn, start.isoformat(), today.isoformat()) if start <= today else []
        workday_rows = [row for row in rows if row["day_category"] == "WORKDAY"]
        balance = sum(int(row["balance_minutes"]) for row in rows)
        actual = sum(int(row["actual_minutes"]) for row in rows)
        target = sum(int(row["target_minutes"]) for row in rows)
        day_count = len(workday_rows)
        trends.append(
            {
                "key": key,
                "label": label,
                "start_date": start.isoformat(),
                "end_date": today.isoformat(),
                "workday_count": day_count,
                "actual_minutes": actual,
                "target_minutes": target,
                "balance_minutes": balance,
                "average_balance_minutes": round(balance / day_count) if day_count else 0,
            }
        )
    return trends


def _dashboard_vacation_stats(conn, today: Date, settings: dict[str, str]) -> dict[str, Any]:
    year = today.year
    account = database.get_vacation_account(conn, year)
    year_start = Date(year, 1, 1)
    year_end = Date(year, 12, 31)
    yesterday = today - timedelta(days=1)
    used = calculations.get_day_type_days(conn, year, "URLAUB", year_start.isoformat(), yesterday.isoformat()) if yesterday >= year_start else 0.0
    planned = calculations.get_day_type_days(conn, year, "URLAUB", today.isoformat(), f"{year}-12-31")
    sick = calculations.get_day_type_days(conn, year, "KRANK", year_start.isoformat(), today.isoformat())
    flextime_days = calculations.get_day_type_days(conn, year, "GLEITZEITTAG", year_start.isoformat(), year_end.isoformat())
    next_vacation = _next_day_type_range(conn, today, "URLAUB")
    return {
        "year": year,
        "entitlement_days": float(account["entitlement_days"]),
        "carry_over_days": float(account["carry_over_from_previous"]),
        "used_days": used,
        "planned_days": planned,
        "remaining_days": calculations.get_remaining_vacation(conn, year),
        "sick_days": sick,
        "flextime_days": flextime_days,
        "next_vacation": next_vacation,
    }


def _next_day_type_range(conn, today: Date, day_type: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM day_types
        WHERE date >= ? AND type = ?
        ORDER BY date
        LIMIT 1
        """,
        (today.isoformat(), day_type),
    ).fetchone()
    return _expand_day_type_range(conn, dict(row)) if row else None


def _live_day_info(
    balance_minutes: int,
    target_minutes: int,
    break_minutes: int,
    settings: dict[str, str],
    open_segment,
    has_work_today: bool,
    now: datetime | None = None,
    remaining_work_minutes: int | None = None,
) -> dict[str, Any]:
    note = "Der laufende Tag ist noch nicht im Gleitzeitkonto enthalten."
    zero_time = None
    now = now or datetime.now()
    if open_segment and open_segment["type"] == "WORK":
        if balance_minutes < 0:
            remaining_break = _remaining_minimum_break_minutes(break_minutes, settings)
            remaining_clock_minutes = abs(balance_minutes) + remaining_break
            zero_time = (now + timedelta(minutes=remaining_clock_minutes)).strftime("%H:%M")
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
        "remaining_work_minutes": max(0, int(remaining_work_minutes if remaining_work_minutes is not None else target_minutes - max(0, balance_minutes))),
    }


def _remaining_minimum_break_minutes(current_break_minutes: int, settings: dict[str, str]) -> int:
    try:
        configured_break = int(settings.get("daily_break_minutes", "0") or "0")
    except ValueError:
        configured_break = 0
    return max(0, configured_break - max(0, int(current_break_minutes)))


def _segment_type_label(value: str | None) -> str:
    return {"WORK": "Arbeit", "BREAK": "Pause", "ABSENCE": "Abwesenheit"}.get(value or "", "Unbekannt")


def _next_absence_countdown(conn, today: str, settings: dict[str, str]) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM day_types
        WHERE date >= ?
        ORDER BY date, type
        LIMIT 1
        """,
        (today,),
    ).fetchone()
    if not row:
        return None

    relevant_date = parse_date(row["date"])
    today_date = parse_date(today)
    calendar_days = max(0, (relevant_date - today_date).days)
    workdays = _workdays_until_absence(today_date, relevant_date, settings)
    mode = settings.get("dashboard_absence_countdown_mode", "workdays")
    display_days = calendar_days if mode == "calendar_days" else workdays
    display_label = "Kalendertage" if mode == "calendar_days" else "Arbeitstage"
    range_info = _expand_day_type_range(conn, dict(row))
    return {
        "date": row["date"],
        "type": row["type"],
        "half_day": bool(row["half_day"]),
        "note": row["note"] or "",
        "start_date": range_info["start_date"],
        "end_date": range_info["end_date"],
        "counted_days": range_info["counted_days"],
        "calendar_days": calendar_days,
        "workdays": workdays,
        "display_days": display_days,
        "display_label": display_label,
        "display_mode": mode,
    }


def _workdays_until_absence(start: Date, absence_date: Date, settings: dict[str, str]) -> int:
    if absence_date <= start:
        return 0
    total = 0
    current = start
    while current < absence_date:
        holiday, _ = calculations.is_public_holiday(current, settings)
        if not holiday and calculations.get_target_minutes_for_date(current, settings) > 0:
            total += 1
        current += timedelta(days=1)
    return total


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
    result["office_baseline_period_mode"] = result.get("office_baseline_period_mode", "all") or "all"
    result["office_quota_target_percent"] = _setting_float_for_ui(result.get("office_quota_target_percent", "50"))
    result["office_start_buffer_minutes"] = _setting_int_for_ui(result.get("office_start_buffer_minutes", "0"))
    result["home_start_buffer_minutes"] = _setting_int_for_ui(result.get("home_start_buffer_minutes", "0"))
    result["office_end_buffer_minutes"] = _setting_int_for_ui(result.get("office_end_buffer_minutes", "0"))
    result["home_end_buffer_minutes"] = _setting_int_for_ui(result.get("home_end_buffer_minutes", "0"))
    result["auto_refresh_interval_seconds"] = _setting_int_for_ui(result.get("auto_refresh_interval_seconds", "60"))
    result["absence_reminder_days"] = _setting_int_for_ui(result.get("absence_reminder_days", "14"))
    return result


def _render_pa_email_template(
    template: str,
    date: str,
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
) -> str:
    work_segments = [segment for segment in segments if segment.get("type") == "WORK"]
    first_start = _time_for_email(work_segments[0].get("start_time")) if work_segments else ""
    ended_work = [segment for segment in work_segments if segment.get("end_time")]
    last_end = _time_for_email(ended_work[-1].get("end_time")) if ended_work else ""
    replacements = {
        "datum": _date_for_email(date),
        "datum_iso": date,
        "wochentag": _weekday_for_email(date),
        "segmente": _segments_for_email(segments),
        "beginn": first_start or "--:--",
        "ende": last_end or "läuft",
        "arbeitszeit": calculations.minutes_to_hhmm(int(summary.get("actual_minutes") or 0)),
        "pause": calculations.minutes_to_hhmm(int(summary.get("break_minutes") or 0)),
        "saldo": _signed_minutes_for_email(int(summary.get("balance_minutes") or 0)),
    }
    result = template or "{segmente}"
    for key, value in replacements.items():
        result = result.replace("{" + key + "}", value)
    return result


def _segments_for_email(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return "Keine Arbeitszeiten erfasst."
    lines = []
    for segment in segments:
        start = _time_for_email(segment.get("start_time"))
        end = _time_for_email(segment.get("end_time")) if segment.get("end_time") else ""
        time_text = f"{start} bis {end} Uhr" if end else f"seit {start} Uhr läuft"
        lines.append(f"- {_segment_label_for_email(segment.get('type'))}: {time_text}")
    return "\n".join(lines)


def _segment_label_for_email(segment_type: Any) -> str:
    return {
        "WORK": "Arbeit",
        "BREAK": "Pause",
        "ABSENCE": "Abwesenheit",
    }.get(str(segment_type or ""), "Segment")


def _time_for_email(value: Any) -> str:
    return str(value or "")[:5] or "--:--"


def _date_for_email(date: str) -> str:
    day = parse_date(date)
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def _weekday_for_email(date: str) -> str:
    return ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][parse_date(date).weekday()]


def _signed_minutes_for_email(minutes: int) -> str:
    prefix = "+" if minutes >= 0 else ""
    return f"{prefix}{calculations.minutes_to_hhmm(minutes)}"


def _copy_text_to_clipboard(text: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return
    if sys.platform == "win32":
        try:
            import win32clipboard  # type: ignore
            import win32con  # type: ignore
        except ImportError:
            _copy_text_with_tkinter(text)
            return
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return
    for command in ("wl-copy", "xclip", "xsel"):
        if shutil.which(command):
            args = {
                "wl-copy": ["wl-copy"],
                "xclip": ["xclip", "-selection", "clipboard"],
                "xsel": ["xsel", "--clipboard", "--input"],
            }[command]
            subprocess.run(args, input=text, text=True, check=True)
            return
    _copy_text_with_tkinter(text)


def _copy_text_with_tkinter(text: str) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
    finally:
        root.destroy()


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
    if "absence_reminder_days" in normalized:
        normalized["absence_reminder_days"] = _normalize_nonnegative_int(
            normalized["absence_reminder_days"],
            "Abwesenheits-Erinnerung",
        )
    if "standard_absence_rules" in normalized:
        normalized["standard_absence_rules"] = _normalize_standard_absence_rules(
            normalized["standard_absence_rules"]
        )
    if "workday_weekdays" in normalized:
        weekdays = _normalize_workday_weekdays(normalized["workday_weekdays"])
        normalized["workday_weekdays"] = ",".join(str(day) for day in weekdays)
        normalized["workdays_per_week"] = str(len(weekdays))
    for key, label in (
        ("office_start_buffer_minutes", "Startpuffer Büro"),
        ("home_start_buffer_minutes", "Startpuffer Homeoffice"),
        ("office_end_buffer_minutes", "Arbeitsende-Puffer Büro"),
        ("home_end_buffer_minutes", "Arbeitsende-Puffer Homeoffice"),
    ):
        if key in normalized:
            normalized[key] = _normalize_nonnegative_int(normalized[key], label)
    if "auto_refresh_interval_seconds" in normalized:
        normalized["auto_refresh_interval_seconds"] = _normalize_auto_refresh_interval(
            normalized["auto_refresh_interval_seconds"]
        )
    for key, label in (
        ("office_baseline_days", "Manuelle Büro-Tage"),
        ("homeoffice_baseline_days", "Manuelle Homeoffice-Tage"),
    ):
        if key in normalized:
            normalized[key] = _normalize_nonnegative_decimal(normalized[key], label)
    if "office_quota_target_percent" in normalized:
        normalized["office_quota_target_percent"] = _normalize_percent(
            normalized["office_quota_target_percent"],
            "Officequote-Schwelle",
        )
    for key in (
        "office_baseline_custom_start",
        "office_baseline_custom_end",
        "office_quota_custom_start",
        "office_quota_custom_end",
    ):
        if normalized.get(key):
            parse_date(normalized[key])
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
        "preload_app_on_tracker_start",
    ):
        if key in normalized:
            normalized[key] = _normalize_bool_setting(normalized[key])
    for key, allowed in (
        ("work_start_popup_mode", {"off", "on"}),
        ("work_end_popup_mode", {"off", "open_only", "always"}),
        ("work_popup_timing", {"startup", "work_end", "custom"}),
        ("daily_info_popup_mode", {"off", "work_end", "custom"}),
        ("absence_reminder_mode", {"off", "startup", "work_end", "custom"}),
        ("dashboard_absence_countdown_mode", {"workdays", "calendar_days"}),
        ("office_baseline_period_mode", {"all", "current_year", "rolling_365", "custom"}),
        ("office_quota_period_mode", {"all", "current_year", "rolling_365", "custom"}),
        ("office_quota_mixed_day_mode", {"split", "office", "homeoffice"}),
        ("standard_absence_1224_mode", {"off", "vacation_half", "vacation_full", "flextime_half", "flextime_full", "holiday"}),
        ("standard_absence_1231_mode", {"off", "vacation_half", "vacation_full", "flextime_half", "flextime_full", "holiday"}),
        ("standard_absence_bridge_mode", {"off", "vacation_half", "vacation_full", "flextime_half", "flextime_full", "holiday"}),
    ):
        if key in normalized:
            normalized[key] = _normalize_choice_setting(normalized[key], allowed, key)
    for key, label in (
        ("work_popup_custom_time", "Popup-Uhrzeit"),
        ("daily_info_popup_time", "Info-Popup-Uhrzeit"),
        ("absence_reminder_time", "Abwesenheits-Erinnerung"),
    ):
        if key in normalized and normalized[key]:
            normalized[key] = normalize_time_input(normalized[key])[:5]
    return normalized


def _apply_autostart_setting(enabled: bool) -> None:
    from tracker import autostart_windows

    autostart_windows.configure_startup_shortcut(enabled, PROJECT_ROOT / "main.pyw")


def _normalize_bool_setting(value: str) -> str:
    return "1" if str(value).strip().lower() in {"1", "true", "ja", "yes", "on"} else "0"


def _normalize_choice_setting(value: str, allowed: set[str], label: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned not in allowed:
        raise ValueError(f"Ungültige Einstellung für {label}.")
    return cleaned


def _normalize_standard_absence_rules(raw_value: str) -> str:
    if not raw_value:
        return "[]"
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Standard-Abwesenheiten konnten nicht gelesen werden.") from exc
    if not isinstance(data, list):
        raise ValueError("Standard-Abwesenheiten müssen eine Liste sein.")
    normalized: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        date_value = str(item.get("date") or "").strip()
        day_type = str(item.get("type") or "").strip().upper()
        if not date_value and not day_type and not str(item.get("note") or "").strip():
            continue
        date_value = _normalize_standard_rule_date(date_value)
        end_date_value = str(item.get("end_date") or "").strip()
        end_date_value = _normalize_standard_rule_date(end_date_value) if end_date_value else ""
        if end_date_value and _month_day_sort_key(end_date_value) < _month_day_sort_key(date_value):
            raise ValueError("Enddatum einer Standard-Abwesenheit darf nicht vor dem Startdatum liegen.")
        if day_type not in {"URLAUB", "GLEITZEITTAG", "FEIERTAG", "DIENSTREISE"}:
            raise ValueError("Typ einer Standard-Abwesenheit ist ungültig.")
        normalized.append(
            {
                "date": date_value,
                "end_date": end_date_value,
                "type": day_type,
                "half_day": bool(item.get("half_day")),
                "note": str(item.get("note") or "").strip(),
            }
        )
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _normalize_standard_rule_date(raw_value: str) -> str:
    original = str(raw_value or "").strip()
    text = original.replace(".", "-").replace("/", "-")
    parts = [part.zfill(2) for part in text.split("-") if part.strip()]
    if len(parts) == 3 and len(parts[0]) == 4:
        month, day = parts[1], parts[2]
    elif len(parts) == 3:
        day, month = parts[0], parts[1]
    elif len(parts) == 2:
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("Datum einer Standard-Abwesenheit muss als TT.MM. angegeben sein.") from exc
        if "." in original or "/" in original or first > 12:
            day, month = parts[0], parts[1]
        elif second > 12:
            month, day = parts[0], parts[1]
        else:
            month, day = parts[0], parts[1]
    else:
        raise ValueError("Datum einer Standard-Abwesenheit muss als TT.MM. angegeben sein.")
    try:
        Date(2024, int(month), int(day))
    except ValueError as exc:
        raise ValueError("Datum einer Standard-Abwesenheit ist ungültig.") from exc
    return f"{month}-{day}"


def _month_day_sort_key(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return int(month), int(day)


def _normalize_auto_refresh_interval(raw_value: str) -> str:
    if not raw_value:
        return "0"
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("Aktualisierungsintervall muss eine ganze Zahl in Sekunden sein.") from exc
    if value < 0:
        raise ValueError("Aktualisierungsintervall darf nicht negativ sein.")
    if 0 < value < 10:
        raise ValueError("Aktualisierungsintervall muss mindestens 10 Sekunden betragen oder 0 zum Deaktivieren.")
    if value > 3600:
        raise ValueError("Aktualisierungsintervall darf maximal 3600 Sekunden betragen.")
    return str(value)


def _normalize_percent(raw_value: str, label: str) -> str:
    try:
        value = float(str(raw_value or "0").replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{label} muss eine Zahl sein.") from exc
    if not 0 <= value <= 100:
        raise ValueError(f"{label} muss zwischen 0 und 100 liegen.")
    return f"{value:.2f}".rstrip("0").rstrip(".")


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


def _safe_nonnegative_int(raw_value: Any, default: int) -> int:
    try:
        value = int(str(raw_value or default))
    except (TypeError, ValueError):
        value = default
    return max(0, value)


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


def _default_export_start_date(conn) -> str:
    dates = _real_local_data_dates(conn, "MIN")
    return min(dates) if dates else f"{Date.today().year}-01-01"


def _default_export_end_date(conn) -> str:
    dates = _real_local_data_dates(conn, "MAX")
    return max([today_str(), *dates]) if dates else today_str()


def _real_local_data_dates(conn, aggregate: str) -> list[str]:
    if aggregate not in {"MIN", "MAX"}:
        raise ValueError("Ungueltige Datumsaggregation.")
    dates: list[str] = []
    for table in ("segments", "day_types", "notes"):
        row = conn.execute(f"SELECT {aggregate}(date) AS value FROM {table}").fetchone()
        if row["value"]:
            dates.append(row["value"])
    return dates


def _unlink_temp_file(path: Path | None, logger: logging.Logger) -> None:
    if not path:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Temporaere Importdatei konnte nicht geloescht werden: %s", exc)


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
            ORDER BY type, approval_status, source, date, half_day, COALESCE(note, '')
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
                "approval_status": row["approval_status"],
                "source": row["source"],
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
        "approval_status": row["approval_status"],
        "source": row["source"],
        "ids": [row["id"]],
    }
    return _finalize_day_type_range(conn, group)


def _matching_day_type_exists(conn, date_value: Date, key: tuple[str, bool, str, str, str]) -> bool:
    row = conn.execute(
        """
        SELECT type, half_day, note, approval_status, source FROM day_types
        WHERE date = ? AND type = ?
        LIMIT 1
        """,
        (date_value.isoformat(), key[0]),
    ).fetchone()
    return bool(row and _day_type_key(row) == key)


def _day_type_key(row) -> tuple[str, bool, str, str, str]:
    return (
        row["type"],
        bool(row["half_day"]),
        row["note"] or "",
        row["approval_status"],
        row["source"],
    )


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
