"""High-level recording actions used by tray menu, startup, and shutdown."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from common import calculations, database, location
from common.models import current_time_str, normalize_time_input, today_str


HEARTBEAT_SETTING = "last_tracker_heartbeat"


@dataclass(frozen=True)
class RecorderEvent:
    kind: str
    message: str
    time: str
    date: str
    segment_id: int | None = None
    location: str | None = None


class WorktimeRecorder:
    """Records work, breaks, absences, and end-of-day events."""

    def __init__(self, db_path: str | Path | None = None, logger: logging.Logger | None = None) -> None:
        self.db_path = Path(db_path) if db_path else database.init_db()
        database.init_db(self.db_path)
        self.logger = logger or logging.getLogger("worktime.tracker.recorder")
        self._lock = threading.RLock()

    def get_status(self) -> dict[str, str | int | None]:
        with self._connect() as conn:
            current = database.get_current_open_segment(conn)
            if not current:
                return {"running": 0, "type": None, "date": None, "start_time": None}
            return {
                "running": 1,
                "type": current["type"],
                "date": current["date"],
                "start_time": current["start_time"],
                "id": current["id"],
            }

    def auto_start_day(self) -> RecorderEvent | None:
        today = today_str()
        with self._lock, self._connect() as conn:
            open_today = conn.execute(
                "SELECT 1 FROM segments WHERE date = ? AND end_time IS NULL LIMIT 1",
                (today,),
            ).fetchone()
            if open_today:
                return None
        return self.start_work(source="AUTO", reason="Arbeitsbeginn automatisch erfasst")

    def start_work(self, source: str = "MANUAL", reason: str = "Arbeitsbeginn") -> RecorderEvent | None:
        now = current_time_str()
        today = today_str()
        with self._lock, self._connect() as conn:
            current = database.get_current_open_segment(conn)
            if current and current["type"] == "WORK" and current["date"] == today:
                return None
            if current:
                database.close_segment(conn, current["id"], now)
                calculations.recalculate_day(conn, current["date"])

            settings = database.get_settings(conn)
            detected = self._detect_location(conn, settings)
            start_time = _start_time_with_buffer(now, source, detected, settings)
            segment_id = database.add_segment(conn, today, "WORK", start_time, location=detected, source=source)
            calculations.recalculate_day(conn, today)
            self.logger.info("%s um %s, Standort %s", reason, start_time, detected)
            return RecorderEvent("WORK_START", reason, start_time, today, segment_id=segment_id, location=detected)

    def start_break(self) -> RecorderEvent | None:
        return self._switch_to("BREAK", "Pause gestartet")

    def end_break(self) -> RecorderEvent | None:
        return self._end_non_work_and_resume("BREAK", "Pause beendet")

    def start_absence(self) -> RecorderEvent | None:
        return self._switch_to("ABSENCE", "Abwesenheit gestartet")

    def end_absence(self) -> RecorderEvent | None:
        return self._end_non_work_and_resume("ABSENCE", "Abwesenheit beendet")

    def end_day(self, source: str = "MANUAL", reason: str = "Feierabend", at_time: str | None = None) -> RecorderEvent | None:
        now = normalize_time_input(at_time) if at_time else current_time_str()
        with self._lock, self._connect() as conn:
            current = database.get_current_open_segment(conn)
            if not current:
                return None
            database.close_segment(conn, current["id"], now)
            calculations.recalculate_day(conn, current["date"])
            if source == "AUTO_SHUTDOWN":
                database.set_setting(
                    conn,
                    "last_auto_shutdown_notice",
                    json.dumps({"date": current["date"], "time": now}, ensure_ascii=False),
                )
            self.logger.info("%s um %s fuer %s", reason, now, current["date"])
            return RecorderEvent("END_DAY", reason, now, current["date"], segment_id=current["id"])

    def recover_previous_open_segments(
        self,
        ask_end_time: Callable[[dict], str | None],
    ) -> list[RecorderEvent]:
        recovered: list[RecorderEvent] = []
        today = today_str()
        with self._lock, self._connect() as conn:
            open_segments = [row for row in database.get_open_segments(conn) if row["date"] < today]
            for row in open_segments:
                automatic_end_time = self._heartbeat_recovery_end_time(conn, row)
                if automatic_end_time:
                    database.close_segment(conn, row["id"], automatic_end_time)
                    calculations.recalculate_day(conn, row["date"])
                    event = RecorderEvent(
                        "AUTO_RECOVERY",
                        "Feierabend automatisch aus letztem Tracker-Signal nachgetragen",
                        automatic_end_time,
                        row["date"],
                        segment_id=row["id"],
                    )
                    recovered.append(event)
                    self.logger.info("Automatische Recovery fuer %s um %s", row["date"], automatic_end_time)
                    continue

                end_time = ask_end_time(dict(row))
                if not end_time:
                    self.logger.warning("Offenes Segment %s bleibt unveraendert", row["id"])
                    continue
                normalized = normalize_time_input(end_time)
                database.close_segment(conn, row["id"], normalized)
                calculations.recalculate_day(conn, row["date"])
                event = RecorderEvent(
                    "RECOVERY",
                    "Feierabend nach Absturz/Stromausfall nachgetragen",
                    normalized,
                    row["date"],
                    segment_id=row["id"],
                )
                recovered.append(event)
                self.logger.info("Crash-Recovery fuer %s um %s", row["date"], normalized)
        return recovered

    def record_heartbeat(self, at: datetime | None = None) -> None:
        stamp = (at or datetime.now()).replace(microsecond=0).isoformat()
        with self._lock, self._connect() as conn:
            database.set_setting(conn, HEARTBEAT_SETTING, stamp)

    def _heartbeat_recovery_end_time(self, conn, segment) -> str | None:
        raw = database.get_setting(conn, HEARTBEAT_SETTING, "")
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
        if stamp.date().isoformat() != segment["date"]:
            return None
        end_time = stamp.strftime("%H:%M:%S")
        if end_time <= str(segment["start_time"]):
            return None
        return end_time

    def consume_shutdown_notice(self) -> str | None:
        with self._lock, self._connect() as conn:
            raw = database.get_setting(conn, "last_auto_shutdown_notice", "")
            if not raw:
                return None
            database.set_setting(conn, "last_auto_shutdown_notice", "")
        try:
            payload = json.loads(raw)
            return f"{payload['date']} wurde um {payload['time']} automatisch Feierabend erfasst."
        except (json.JSONDecodeError, KeyError, TypeError):
            return "Beim letzten Herunterfahren wurde automatisch Feierabend erfasst."

    def _switch_to(self, segment_type: str, label: str) -> RecorderEvent | None:
        now = current_time_str()
        today = today_str()
        with self._lock, self._connect() as conn:
            current = database.get_current_open_segment(conn)
            if current and current["type"] == segment_type and current["date"] == today:
                return None
            if current:
                database.close_segment(conn, current["id"], now)
                calculations.recalculate_day(conn, current["date"])
            segment_id = database.add_segment(conn, today, segment_type, now, source="MANUAL")
            calculations.recalculate_day(conn, today)
            self.logger.info("%s um %s", label, now)
            return RecorderEvent(segment_type, label, now, today, segment_id=segment_id)

    def _end_non_work_and_resume(self, expected_type: str, label: str) -> RecorderEvent | None:
        now = current_time_str()
        today = today_str()
        with self._lock, self._connect() as conn:
            current = database.get_current_open_segment(conn)
            if current and current["type"] == expected_type:
                database.close_segment(conn, current["id"], now)
                calculations.recalculate_day(conn, current["date"])
            else:
                self.logger.warning("%s angefordert, aber kein passendes Segment offen", label)
        resumed = self.start_work(source="MANUAL", reason=label)
        return resumed or RecorderEvent(expected_type, label, now, today)

    def _detect_location(self, conn, settings: dict[str, str] | None = None) -> str:
        existing_location = self._existing_day_location(conn, today_str())
        if existing_location:
            self.logger.info("Standort-Check: %s aus erstem Arbeitssegment des Tages uebernommen", existing_location)
            return existing_location
        settings = settings or database.get_settings(conn)
        targets = settings.get("homeoffice_check_targets", "")
        timeout = settings.get("homeoffice_check_timeout_ms", "1500")
        try:
            detected = location.detect_location(targets, timeout)
        except ValueError as exc:
            self.logger.warning("Standort-Check-Konfiguration ungueltig: %s", exc)
            return "HOME"
        self.logger.info("Standort-Check: %s", detected)
        return detected

    @staticmethod
    def _existing_day_location(conn, date: str) -> str | None:
        row = conn.execute(
            """
            SELECT location FROM segments
            WHERE date = ? AND type = 'WORK' AND location IN ('OFFICE', 'HOME')
            ORDER BY start_time, id
            LIMIT 1
            """,
            (date,),
        ).fetchone()
        return row["location"] if row else None

    def _connect(self):
        return database.connect(self.db_path)


def _start_time_with_buffer(now: str, source: str, detected_location: str, settings: dict[str, str]) -> str:
    if source != "AUTO":
        return now
    key = "office_start_buffer_minutes" if detected_location == "OFFICE" else "home_start_buffer_minutes"
    try:
        buffer_minutes = max(0, int(settings.get(key, "0") or "0"))
    except ValueError:
        buffer_minutes = 0
    if buffer_minutes <= 0:
        return now
    hour, minute, second = [int(part) for part in now[:8].split(":")]
    total_seconds = max(0, hour * 3600 + minute * 60 + second - buffer_minutes * 60)
    adjusted_hour = total_seconds // 3600
    adjusted_minute = (total_seconds % 3600) // 60
    adjusted_second = total_seconds % 60
    return f"{adjusted_hour:02d}:{adjusted_minute:02d}:{adjusted_second:02d}"
