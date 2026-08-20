"""SQLite persistence for the worktime tracker.

The database is the single source of truth shared by the tracker and pywebview
app. WAL mode and a busy timeout are enabled on every connection so both
programs can be open at the same time.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from .config import BACKUP_DIR, DEFAULT_SETTINGS, ensure_data_dirs, get_database_path
from .models import DAY_TYPES, LOCATIONS, SEGMENT_TYPES, SOURCES, now_iso

APPROVAL_STATUSES = {"planned", "approved"}
DAY_TYPE_SOURCES = {"MANUAL", "AUTO_STANDARD"}
STANDARD_ABSENCE_MODES = {
    "vacation_half": ("URLAUB", True),
    "vacation_full": ("URLAUB", False),
    "flextime_half": ("GLEITZEITTAG", True),
    "flextime_full": ("GLEITZEITTAG", False),
    "holiday": ("FEIERTAG", False),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN ('WORK','BREAK','ABSENCE')),
    start_time  TEXT    NOT NULL,
    end_time    TEXT,
    location    TEXT    CHECK (location IN ('OFFICE','HOME','UNKNOWN')),
    source      TEXT    NOT NULL DEFAULT 'AUTO' CHECK (source IN ('AUTO','MANUAL')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_date ON segments(date);

CREATE TABLE IF NOT EXISTS day_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN
                 ('URLAUB','KRANK','FEIERTAG','GLEITZEITTAG','DIENSTREISE')),
    half_day    INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    approval_status TEXT NOT NULL DEFAULT 'approved' CHECK (approval_status IN ('planned','approved')),
    source      TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL','AUTO_STANDARD')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (date, type)
);
CREATE INDEX IF NOT EXISTS idx_daytypes_date ON day_types(date);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_summary (
    date            TEXT PRIMARY KEY,
    target_minutes  INTEGER NOT NULL,
    actual_minutes  INTEGER NOT NULL,
    break_minutes   INTEGER NOT NULL,
    balance_minutes INTEGER NOT NULL,
    day_category    TEXT    NOT NULL,
    location        TEXT,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS month_closing (
    year_month          TEXT PRIMARY KEY,
    target_minutes      INTEGER NOT NULL,
    actual_minutes      INTEGER NOT NULL,
    balance_minutes     INTEGER NOT NULL,
    carry_over_minutes  INTEGER NOT NULL,
    vacation_days_used  REAL    NOT NULL DEFAULT 0,
    sick_days_used      REAL    NOT NULL DEFAULT 0,
    homeoffice_days     INTEGER NOT NULL DEFAULT 0,
    closed              INTEGER NOT NULL DEFAULT 0,
    closed_at           TEXT
);

CREATE TABLE IF NOT EXISTS vacation_account (
    year                     INTEGER PRIMARY KEY,
    entitlement_days         REAL NOT NULL,
    carry_over_from_previous REAL NOT NULL DEFAULT 0
);
"""


class ManagedConnection(sqlite3.Connection):
    """sqlite3 connection that closes when used as a context manager."""

    def __exit__(self, exc_type, exc, tb):
        result = super().__exit__(exc_type, exc, tb)
        self.close()
        return result


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    ensure_data_dirs()
    path = Path(db_path) if db_path else get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else get_database_path()
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        ensure_vacation_account(conn, datetime.now().year)
        apply_standard_day_types(conn, get_settings(conn), datetime.now().year)
    return path


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def validate_choice(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"Invalid {label}: {value}")


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = DEFAULT_SETTINGS.copy()
    settings.update({row["key"]: row["value"] for row in rows})
    return settings


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, default)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def set_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    with transaction(conn):
        for key, value in values.items():
            set_setting(conn, key, "" if value is None else str(value))


def reset_local_state(
    conn: sqlite3.Connection,
    *,
    reset_settings: bool = False,
    reset_tracking_data: bool = False,
    require_initial_setup: bool = False,
) -> None:
    """Reset selected local state while keeping the SQLite schema intact."""

    with transaction(conn):
        if reset_tracking_data:
            _clear_tracking_tables(conn)
        if reset_settings:
            values = DEFAULT_SETTINGS.copy()
            if require_initial_setup:
                values["initial_setup_required"] = "1"
            _replace_settings(conn, values)
        elif require_initial_setup:
            set_setting(conn, "initial_setup_required", "1")
        ensure_vacation_account(conn, datetime.now().year)


def add_segment(
    conn: sqlite3.Connection,
    date: str,
    segment_type: str,
    start_time: str,
    end_time: str | None = None,
    location: str | None = None,
    source: str = "AUTO",
) -> int:
    validate_choice(segment_type, SEGMENT_TYPES, "segment_type")
    validate_choice(source, SOURCES, "source")
    if location is not None:
        validate_choice(location, {"OFFICE", "HOME", "UNKNOWN"}, "location")
    if segment_type != "WORK":
        location = None
    stamp = now_iso()
    cur = conn.execute(
        """
        INSERT INTO segments (date, type, start_time, end_time, location, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, segment_type, start_time, end_time, location, source, stamp, stamp),
    )
    return int(cur.lastrowid)


def update_segment(conn: sqlite3.Connection, segment_id: int, **fields: Any) -> None:
    allowed = {"date", "type", "start_time", "end_time", "location", "source"}
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unknown segment field: {key}")
        if key == "type":
            validate_choice(str(value), SEGMENT_TYPES, "segment_type")
        if key == "source":
            validate_choice(str(value), SOURCES, "source")
        if key == "location" and value is not None:
            validate_choice(str(value), {"OFFICE", "HOME", "UNKNOWN"}, "location")
        assignments.append(f"{key} = ?")
        values.append(value)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(now_iso())
    values.append(segment_id)
    conn.execute(f"UPDATE segments SET {', '.join(assignments)} WHERE id = ?", values)


def delete_segment(conn: sqlite3.Connection, segment_id: int) -> None:
    conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))


def close_segment(conn: sqlite3.Connection, segment_id: int, end_time: str) -> None:
    update_segment(conn, segment_id, end_time=end_time)


def get_segment(conn: sqlite3.Connection, segment_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()


def get_segments_for_date(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM segments WHERE date = ? ORDER BY start_time, id",
            (date,),
        ).fetchall()
    )


def get_segments_between(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM segments WHERE date BETWEEN ? AND ? ORDER BY date, start_time, id",
            (start_date, end_date),
        ).fetchall()
    )


def get_open_segments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM segments WHERE end_time IS NULL ORDER BY date, start_time").fetchall())


def get_current_open_segment(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM segments WHERE end_time IS NULL ORDER BY date DESC, start_time DESC, id DESC LIMIT 1"
    ).fetchone()


def upsert_day_type(
    conn: sqlite3.Connection,
    date: str,
    day_type: str,
    half_day: bool = False,
    note: str | None = None,
    approval_status: str = "approved",
    source: str = "MANUAL",
) -> int:
    validate_choice(day_type, DAY_TYPES, "day_type")
    validate_choice(approval_status, APPROVAL_STATUSES, "approval_status")
    validate_choice(source, DAY_TYPE_SOURCES, "day_type_source")
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO day_types (date, type, half_day, note, approval_status, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, type) DO UPDATE SET
            half_day = excluded.half_day,
            note = excluded.note,
            approval_status = excluded.approval_status,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (date, day_type, int(bool(half_day)), note, approval_status, source, stamp, stamp),
    )
    row = conn.execute("SELECT id FROM day_types WHERE date = ? AND type = ?", (date, day_type)).fetchone()
    return int(row["id"])


def delete_day_type(conn: sqlite3.Connection, day_type_id: int) -> None:
    conn.execute("DELETE FROM day_types WHERE id = ?", (day_type_id,))


def apply_standard_day_types(conn: sqlite3.Connection, settings: dict[str, str], year: int) -> None:
    """Materialize configured company-wide absence rules for one year."""

    year = int(year)
    stamp = now_iso()
    desired = {
        (date_text, day_type): (int(bool(half_day)), note)
        for date_text, day_type, half_day, note in _standard_day_type_rows(settings, year)
    }
    manual_dates = {
        row["date"]
        for row in conn.execute(
            """
            SELECT DISTINCT date FROM day_types
            WHERE source = 'MANUAL' AND date BETWEEN ? AND ?
            """,
            (f"{year:04d}-01-01", f"{year:04d}-12-31"),
        ).fetchall()
    }
    existing = conn.execute(
        """
        SELECT id, date, type, half_day, note FROM day_types
        WHERE source = 'AUTO_STANDARD' AND date BETWEEN ? AND ?
        """,
        (f"{year:04d}-01-01", f"{year:04d}-12-31"),
    ).fetchall()
    for row in existing:
        key = (row["date"], row["type"])
        if row["date"] in manual_dates:
            conn.execute("DELETE FROM day_types WHERE id = ?", (row["id"],))
            continue
        desired_value = desired.get(key)
        if desired_value is None:
            conn.execute("DELETE FROM day_types WHERE id = ?", (row["id"],))
            continue
        half_day, note = desired_value
        if int(row["half_day"]) != half_day or (row["note"] or "") != note:
            conn.execute(
                """
                UPDATE day_types
                SET half_day = ?, note = ?, approval_status = 'approved', updated_at = ?
                WHERE id = ?
                """,
                (half_day, note, stamp, row["id"]),
            )
    for (date_text, day_type), (half_day, note) in desired.items():
        if date_text in manual_dates:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO day_types (
                date, type, half_day, note, approval_status, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'approved', 'AUTO_STANDARD', ?, ?)
            """,
            (date_text, day_type, half_day, note, stamp, stamp),
        )


def get_day_types_for_date(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM day_types WHERE date = ? ORDER BY type", (date,)).fetchall())


def get_day_types_between(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM day_types WHERE date BETWEEN ? AND ? ORDER BY date, type",
            (start_date, end_date),
        ).fetchall()
    )


def _standard_day_type_rows(settings: dict[str, str], year: int) -> list[tuple[str, str, bool, str]]:
    configured_rules = _configured_standard_absence_rules(settings, year)
    if configured_rules:
        return configured_rules

    rows: list[tuple[str, str, bool, str]] = []
    rows.extend(_single_standard_day_type(settings.get("standard_absence_1224_mode"), year, 12, 24, "Heiligabend"))
    rows.extend(_single_standard_day_type(settings.get("standard_absence_1231_mode"), year, 12, 31, "Silvester"))
    bridge_mode = settings.get("standard_absence_bridge_mode", "off")
    workdays = _configured_workday_indices(settings)
    current = Date(year, 12, 27)
    while current <= Date(year, 12, 30):
        if current.weekday() in workdays:
            rows.extend(
                _single_standard_day_type(
                    bridge_mode,
                    year,
                    current.month,
                    current.day,
                    "Betriebsruhe Weihnachten/Neujahr",
                )
            )
        current += timedelta(days=1)
    return rows


def _configured_standard_absence_rules(settings: dict[str, str], year: int) -> list[tuple[str, str, bool, str]]:
    raw = str(settings.get("standard_absence_rules", "") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    rows: list[tuple[str, str, bool, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        month_day = _normalize_standard_month_day(item.get("date") or item.get("month_day"))
        end_month_day = _normalize_standard_month_day(item.get("end_date") or item.get("end_month_day"))
        day_type = str(item.get("type") or "").strip().upper()
        if not month_day or day_type not in DAY_TYPES:
            continue
        half_day = bool(item.get("half_day"))
        note = str(item.get("note") or "").strip() or "Standard-Abwesenheit"
        for date_value in _standard_rule_dates_for_year(month_day, end_month_day, year):
            rows.append((date_value, day_type, half_day, f"{note} (automatisch)"))
    return rows


def _standard_rule_dates_for_year(start_month_day: str, end_month_day: str | None, year: int) -> list[str]:
    start_date = _month_day_to_date(start_month_day, year)
    end_date = _month_day_to_date(end_month_day or start_month_day, year)
    if not start_date or not end_date or end_date < start_date:
        return []
    current = start_date
    dates: list[str] = []
    while current <= end_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def _month_day_to_date(month_day: str, year: int) -> Date | None:
    month_text, day_text = month_day.split("-")
    try:
        return Date(year, int(month_text), int(day_text))
    except ValueError:
        return None


def _normalize_standard_month_day(value: Any) -> str | None:
    original = str(value or "").strip()
    text = original
    if not text:
        return None
    text = text.replace(".", "-").replace("/", "-")
    parts = [part.zfill(2) for part in text.split("-") if part]
    try:
        if len(parts) == 3 and len(parts[0]) == 4:
            month, day = parts[1], parts[2]
        elif len(parts) == 3:
            day, month = parts[0], parts[1]
        elif len(parts) == 2:
            first, second = int(parts[0]), int(parts[1])
            if "." in original or "/" in original or first > 12:
                day, month = parts[0], parts[1]
            elif second > 12:
                month, day = parts[0], parts[1]
            else:
                month, day = parts[0], parts[1]
        else:
            return None
    except ValueError:
        return None
    try:
        Date(2024, int(month), int(day))
    except ValueError:
        return None
    return f"{month}-{day}"


def _single_standard_day_type(
    mode: str | None,
    year: int,
    month: int,
    day: int,
    label: str,
) -> list[tuple[str, str, bool, str]]:
    config = STANDARD_ABSENCE_MODES.get(str(mode or "off"))
    if not config:
        return []
    day_type, half_day = config
    return [(f"{year:04d}-{month:02d}-{day:02d}", day_type, half_day, f"{label} (automatisch)")]


def _configured_workday_indices(settings: dict[str, str]) -> set[int]:
    raw = str(settings.get("workday_weekdays", "") or "").strip()
    if raw:
        values = {int(item) for item in raw.split(",") if item.strip().isdigit() and 0 <= int(item) <= 6}
        if values:
            return values
    try:
        count = int(settings.get("workdays_per_week", DEFAULT_SETTINGS["workdays_per_week"]) or 5)
    except ValueError:
        count = 5
    return set(range(max(1, min(7, count))))


def replace_note(conn: sqlite3.Connection, date: str, text: str) -> None:
    stamp = now_iso()
    existing = conn.execute("SELECT id FROM notes WHERE date = ? ORDER BY id LIMIT 1", (date,)).fetchone()
    if text.strip():
        if existing:
            conn.execute(
                "UPDATE notes SET text = ?, updated_at = ? WHERE id = ?",
                (text.strip(), stamp, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO notes (date, text, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (date, text.strip(), stamp, stamp),
            )
    elif existing:
        conn.execute("DELETE FROM notes WHERE date = ?", (date,))


def get_note_for_date(conn: sqlite3.Connection, date: str) -> str:
    row = conn.execute("SELECT text FROM notes WHERE date = ? ORDER BY id LIMIT 1", (date,)).fetchone()
    return row["text"] if row else ""


def get_notes_between(conn: sqlite3.Connection, start_date: str, end_date: str) -> dict[str, str]:
    rows = conn.execute("SELECT date, text FROM notes WHERE date BETWEEN ? AND ?", (start_date, end_date)).fetchall()
    return {row["date"]: row["text"] for row in rows}


def upsert_day_summary(
    conn: sqlite3.Connection,
    date: str,
    target_minutes: int,
    actual_minutes: int,
    break_minutes: int,
    balance_minutes: int,
    day_category: str,
    location: str | None,
) -> None:
    if location is not None:
        validate_choice(location, LOCATIONS, "location")
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO day_summary (
            date, target_minutes, actual_minutes, break_minutes,
            balance_minutes, day_category, location, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            target_minutes = excluded.target_minutes,
            actual_minutes = excluded.actual_minutes,
            break_minutes = excluded.break_minutes,
            balance_minutes = excluded.balance_minutes,
            day_category = excluded.day_category,
            location = excluded.location,
            updated_at = excluded.updated_at
        """,
        (date, target_minutes, actual_minutes, break_minutes, balance_minutes, day_category, location, stamp),
    )


def get_day_summary(conn: sqlite3.Connection, date: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM day_summary WHERE date = ?", (date,)).fetchone()


def get_day_summaries_between(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM day_summary WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        ).fetchall()
    )


def upsert_month_closing(
    conn: sqlite3.Connection,
    year_month: str,
    target_minutes: int,
    actual_minutes: int,
    balance_minutes: int,
    carry_over_minutes: int,
    vacation_days_used: float,
    sick_days_used: float,
    homeoffice_days: int,
    closed: int | None = None,
) -> None:
    existing = conn.execute("SELECT closed, closed_at FROM month_closing WHERE year_month = ?", (year_month,)).fetchone()
    final_closed = int(existing["closed"]) if existing and closed is None else int(bool(closed))
    closed_at = existing["closed_at"] if existing else None
    if closed is not None and final_closed:
        closed_at = now_iso()
    if closed is not None and not final_closed:
        closed_at = None
    conn.execute(
        """
        INSERT INTO month_closing (
            year_month, target_minutes, actual_minutes, balance_minutes,
            carry_over_minutes, vacation_days_used, sick_days_used,
            homeoffice_days, closed, closed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(year_month) DO UPDATE SET
            target_minutes = excluded.target_minutes,
            actual_minutes = excluded.actual_minutes,
            balance_minutes = excluded.balance_minutes,
            carry_over_minutes = excluded.carry_over_minutes,
            vacation_days_used = excluded.vacation_days_used,
            sick_days_used = excluded.sick_days_used,
            homeoffice_days = excluded.homeoffice_days,
            closed = excluded.closed,
            closed_at = excluded.closed_at
        """,
        (
            year_month,
            target_minutes,
            actual_minutes,
            balance_minutes,
            carry_over_minutes,
            vacation_days_used,
            sick_days_used,
            homeoffice_days,
            final_closed,
            closed_at,
        ),
    )


def get_month_closing(conn: sqlite3.Connection, year_month: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM month_closing WHERE year_month = ?", (year_month,)).fetchone()


def ensure_vacation_account(conn: sqlite3.Connection, year: int) -> None:
    settings = get_settings(conn) if _table_exists(conn, "settings") else DEFAULT_SETTINGS
    entitlement = float(settings.get("vacation_days_per_year", DEFAULT_SETTINGS["vacation_days_per_year"]) or 0)
    carry_over = float(settings.get("vacation_carry_over", DEFAULT_SETTINGS["vacation_carry_over"]) or 0)
    conn.execute(
        """
        INSERT OR IGNORE INTO vacation_account (year, entitlement_days, carry_over_from_previous)
        VALUES (?, ?, ?)
        """,
        (year, entitlement, carry_over),
    )


def get_vacation_account(conn: sqlite3.Connection, year: int) -> sqlite3.Row:
    ensure_vacation_account(conn, year)
    return conn.execute("SELECT * FROM vacation_account WHERE year = ?", (year,)).fetchone()


def update_vacation_account(conn: sqlite3.Connection, year: int, entitlement: float, carry_over: float) -> None:
    conn.execute(
        """
        INSERT INTO vacation_account (year, entitlement_days, carry_over_from_previous)
        VALUES (?, ?, ?)
        ON CONFLICT(year) DO UPDATE SET
            entitlement_days = excluded.entitlement_days,
            carry_over_from_previous = excluded.carry_over_from_previous
        """,
        (year, entitlement, carry_over),
    )


def create_manual_backup(conn: sqlite3.Connection, db_path: str | Path | None = None) -> Path:
    """Create a manual local backup in data/backups.

    WAL is checkpointed first so the resulting file is a complete SQLite
    database. This function is never called automatically.
    """

    ensure_data_dirs()
    path = Path(db_path) if db_path else get_database_path()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = BACKUP_DIR / f"database_backup_{timestamp}.db"
    shutil.copy2(path, destination)
    return destination


def _replace_settings(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    conn.execute("DELETE FROM settings")
    for key, value in values.items():
        set_setting(conn, key, value)


def _clear_tracking_tables(conn: sqlite3.Connection) -> None:
    for table in ("segments", "day_types", "notes", "day_summary", "month_closing", "vacation_account"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute(
        """
        DELETE FROM sqlite_sequence
        WHERE name IN ('segments', 'day_types', 'notes')
        """
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _migrate_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "day_types"):
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(day_types)").fetchall()}
    if "approval_status" not in columns:
        conn.execute("ALTER TABLE day_types ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'approved'")
    if "source" not in columns:
        conn.execute("ALTER TABLE day_types ADD COLUMN source TEXT NOT NULL DEFAULT 'MANUAL'")
