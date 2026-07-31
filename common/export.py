"""Manual report exports and imports for local worktime data."""

from __future__ import annotations

import csv
import json
import re
from datetime import date as Date
from datetime import datetime, time
from pathlib import Path
from typing import Any

from . import database
from .calculations import daterange, minutes_to_hhmm, recalculate_range
from .config import DATA_DIR, ensure_data_dirs
from .models import DAY_TYPES, SEGMENT_TYPES, minutes_between, normalize_time_input, parse_date


EXPORT_DIR = DATA_DIR / "exports"
IMPORT_LOG_DIR = DATA_DIR / "import_logs"
EXPORT_HEADERS = [
    "Datensatz",
    "Datum",
    "Wochentag",
    "Kategorie",
    "Beginn",
    "Ende",
    "Dauer_Minuten",
    "Dauer",
    "Standort",
    "Quelle",
    "Halber_Tag",
    "Notiz",
    "Soll_Minuten",
    "Ist_Minuten",
    "Pause_Minuten",
    "Saldo_Minuten",
]
SUMMARY_HEADERS = [
    "Datum",
    "Wochentag",
    "Tagesart",
    "Beginn",
    "Ende",
    "Arbeitszeit",
    "Pause",
    "Soll",
    "Saldo",
    "Standort",
    "Notiz",
]
SEGMENT_SHEET_HEADERS = ["Datum", "Wochentag", "Typ", "Beginn", "Ende", "Dauer", "Standort", "Quelle"]
ABSENCE_SHEET_HEADERS = ["Datum", "Wochentag", "Typ", "Halber_Tag", "Notiz"]
NOTE_SHEET_HEADERS = ["Datum", "Wochentag", "Notiz"]
PDF_HEADERS = [
    "Datensatz",
    "Datum",
    "Kategorie",
    "Beginn",
    "Ende",
    "Dauer",
    "Standort",
    "Halber_Tag",
    "Soll_Minuten",
    "Ist_Minuten",
    "Pause_Minuten",
    "Saldo_Minuten",
    "Notiz",
]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
CATEGORY_LABELS = {
    "WORKDAY": "Arbeitstag",
    "WEEKEND": "Wochenende",
    "HOLIDAY": "Feiertag",
    "VACATION": "Urlaub",
    "SICK": "Krank",
    "TRAVEL": "Dienstreise",
    "FLEXTIME": "Gleitzeittag",
    "NOT_TRACKED": "Vor Startdatum",
    "WORK": "Arbeit",
    "BREAK": "Pause",
    "ABSENCE": "Abwesenheit",
    "URLAUB": "Urlaub",
    "KRANK": "Krank",
    "FEIERTAG": "Feiertag",
    "DIENSTREISE": "Dienstreise",
    "GLEITZEITTAG": "Gleitzeittag",
}
LOCATION_LABELS = {"OFFICE": "Büro", "HOME": "Homeoffice", "MIXED": "Gemischt", "UNKNOWN": "Unbekannt"}
EDITABLE_RECORD_TYPES = {"SEGMENT", "ABWESENHEIT", "NOTIZ"}
OVERVIEW_RECORD_TYPE = "UEBERSICHT"
DAY_TYPE_SUMMARY_CODES = {"VACATION", "SICK", "HOLIDAY", "TRAVEL", "FLEXTIME"}


def export_period(
    conn,
    start_date: str,
    end_date: str,
    export_format: str,
    output_dir: str | Path | None = None,
) -> Path:
    """Export a date range as csv, xlsx, or pdf."""

    ensure_data_dirs()
    recalculate_range(conn, start_date, end_date)
    extra_day_type_dates = _day_type_dates_outside_range(conn, start_date, end_date)
    for date_text in extra_day_type_dates:
        recalculate_range(conn, date_text, date_text)
    target_dir = Path(output_dir) if output_dir else EXPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    fmt = export_format.lower().strip(".")
    rows = _build_rows(conn, start_date, end_date, extra_day_type_dates)
    exported_dates = _export_dates(start_date, end_date, extra_day_type_dates)
    file_start_date = exported_dates[0].isoformat()
    file_end_date = exported_dates[-1].isoformat()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"arbeitszeit_{file_start_date}_bis_{file_end_date}_{timestamp}.{fmt}"
    if fmt == "csv":
        _export_csv(path, rows)
    elif fmt in {"xlsx", "excel"}:
        path = path.with_suffix(".xlsx")
        _export_xlsx(path, _build_summary_rows(conn, start_date, end_date, extra_day_type_dates), rows)
    elif fmt == "pdf":
        _export_pdf(path, rows, start_date, end_date)
    else:
        raise ValueError("Exportformat muss csv, xlsx oder pdf sein.")
    return path


def _day_type_dates_outside_range(conn, start_date: str, end_date: str) -> set[str]:
    return {
        row["date"]
        for row in conn.execute(
            "SELECT DISTINCT date FROM day_types WHERE date < ? OR date > ?",
            (start_date, end_date),
        ).fetchall()
    }


def _build_rows(conn, start_date: str, end_date: str, extra_dates: set[str] | None = None) -> list[dict[str, Any]]:
    export_dates = _export_dates(start_date, end_date, extra_dates)
    summaries = {row["date"]: dict(row) for row in database.get_day_summaries_between(conn, start_date, end_date)}
    for date_text in extra_dates or set():
        summary = database.get_day_summary(conn, date_text)
        if summary:
            summaries[date_text] = dict(summary)
    segment_rows: dict[str, list[dict[str, Any]]] = {}
    for row in database.get_segments_between(conn, start_date, end_date):
        segment_rows.setdefault(row["date"], []).append(dict(row))
    for date_text in extra_dates or set():
        for row in database.get_segments_for_date(conn, date_text):
            segment_rows.setdefault(date_text, []).append(dict(row))
    day_type_rows: dict[str, list[dict[str, Any]]] = {}
    for row in database.get_day_types_between(conn, start_date, end_date):
        day_type_rows.setdefault(row["date"], []).append(dict(row))
    for date_text in extra_dates or set():
        for row in database.get_day_types_for_date(conn, date_text):
            day_type_rows.setdefault(date_text, []).append(dict(row))
    notes = {
        row["date"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM notes WHERE date BETWEEN ? AND ? ORDER BY date, id",
            (start_date, end_date),
        ).fetchall()
    }
    for date_text in extra_dates or set():
        note = database.get_note_for_date(conn, date_text)
        if note:
            notes[date_text] = {"date": date_text, "text": note}
    rows: list[dict[str, Any]] = []
    for current in export_dates:
        date_text = current.isoformat()
        for segment in segment_rows.get(date_text, []):
            rows.append(_segment_export_row(segment, current))
        for day_type in day_type_rows.get(date_text, []):
            rows.append(_day_type_export_row(day_type, current))
        if date_text in notes:
            rows.append(_note_export_row(notes[date_text], current))
        has_explicit_data = bool(segment_rows.get(date_text) or day_type_rows.get(date_text) or date_text in notes)
        if date_text in summaries and _include_summary_in_export(date_text, has_explicit_data):
            rows.append(_summary_export_row(summaries[date_text], current, notes.get(date_text, {}).get("text", "")))
    return rows


def _export_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=_headers(), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _build_summary_rows(conn, start_date: str, end_date: str, extra_dates: set[str] | None = None) -> list[dict[str, Any]]:
    summaries = [dict(row) for row in database.get_day_summaries_between(conn, start_date, end_date)]
    for date_text in extra_dates or set():
        summary = database.get_day_summary(conn, date_text)
        if summary:
            summaries.append(dict(summary))
    summaries.sort(key=lambda row: row["date"])
    segments_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in database.get_segments_between(conn, start_date, end_date):
        segments_by_date.setdefault(row["date"], []).append(dict(row))
    for date_text in extra_dates or set():
        for row in database.get_segments_for_date(conn, date_text):
            segments_by_date.setdefault(date_text, []).append(dict(row))
    day_type_dates = {row["date"] for row in database.get_day_types_between(conn, start_date, end_date)}
    day_type_dates.update(extra_dates or set())
    notes = database.get_notes_between(conn, start_date, end_date)
    for date_text in extra_dates or set():
        note = database.get_note_for_date(conn, date_text)
        if note:
            notes[date_text] = note
    rows: list[dict[str, Any]] = []
    for summary in summaries:
        has_explicit_data = bool(segments_by_date.get(summary["date"]) or summary["date"] in day_type_dates or summary["date"] in notes)
        if not _include_summary_in_export(summary["date"], has_explicit_data):
            continue
        start, end = _first_last_work_times(segments_by_date.get(summary["date"], []))
        rows.append(
            {
                "Datum": summary["date"],
                "Wochentag": WEEKDAYS[parse_date(summary["date"]).weekday()],
                "Tagesart": _display_category(summary["day_category"]),
                "Beginn": _time_for_export(start),
                "Ende": _time_for_export(end),
                "Arbeitszeit": minutes_to_hhmm(summary["actual_minutes"]),
                "Pause": minutes_to_hhmm(summary["break_minutes"]),
                "Soll": minutes_to_hhmm(summary["target_minutes"]),
                "Saldo": minutes_to_hhmm(summary["balance_minutes"]),
                "Standort": _display_location(summary["location"]),
                "Notiz": notes.get(summary["date"], ""),
            }
        )
    return rows


def _export_dates(start_date: str, end_date: str, extra_dates: set[str] | None = None) -> list[Date]:
    dates = {day.isoformat() for day in daterange(parse_date(start_date), parse_date(end_date))}
    dates.update(extra_dates or set())
    return [parse_date(date_text) for date_text in sorted(dates)]


def _include_summary_in_export(date_text: str, has_explicit_data: bool) -> bool:
    """Keep future exports focused on real local data.

    Recalculating a future range creates technical day_summary rows for normal
    future workdays. Those rows are useful internally but make exports look like
    many future minus days. Explicit future data, such as planned vacation, is
    still exported with its calculated summary.
    """

    return has_explicit_data or parse_date(date_text) <= Date.today()


def _export_xlsx(path: Path, summary_rows: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Excel-Export benoetigt openpyxl. Bitte requirements.txt installieren.") from exc

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Übersicht"
    _write_xlsx_sheet(summary_sheet, SUMMARY_HEADERS, summary_rows, get_column_letter)

    segment_sheet = workbook.create_sheet("Segmente")
    _write_xlsx_sheet(segment_sheet, SEGMENT_SHEET_HEADERS, _segment_sheet_rows(rows), get_column_letter)

    absence_sheet = workbook.create_sheet("Abwesenheiten")
    _write_xlsx_sheet(absence_sheet, ABSENCE_SHEET_HEADERS, _absence_sheet_rows(rows), get_column_letter)

    note_sheet = workbook.create_sheet("Notizen")
    _write_xlsx_sheet(note_sheet, NOTE_SHEET_HEADERS, _note_sheet_rows(rows), get_column_letter)

    raw_sheet = workbook.create_sheet("Importdaten")
    _write_xlsx_sheet(raw_sheet, _headers(), _editable_export_rows(rows), get_column_letter)
    for sheet in (summary_sheet, segment_sheet, absence_sheet, note_sheet, raw_sheet):
        _style_xlsx_sheet(sheet, Font, PatternFill, Alignment, Border, Side)
    workbook.save(path)


def _export_pdf(path: Path, rows: list[dict[str, Any]], start_date: str, end_date: str) -> None:
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise RuntimeError("PDF-Export benoetigt fpdf2. Bitte requirements.txt installieren.") from exc

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"Arbeitszeit {start_date} bis {end_date}", ln=True)
    pdf.set_font("Helvetica", "B", 6)
    widths = [19, 22, 28, 16, 16, 17, 20, 16, 17, 17, 17, 18, 54]
    for header, width in zip(PDF_HEADERS, widths):
        pdf.cell(width, 7, header, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 6)
    for row in rows:
        for header, width in zip(PDF_HEADERS, widths):
            value = str(row.get(header, "")).encode("latin-1", "replace").decode("latin-1")
            limit = 48 if header == "Notiz" else 24
            pdf.cell(width, 5, value[:limit], border=1)
        pdf.ln()
    pdf.output(str(path))


def _write_xlsx_sheet(sheet, headers: list[str], rows: list[dict[str, Any]], get_column_letter) -> None:
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
    for index, header in enumerate(headers, start=1):
        width = max(len(header), *(len(str(row.get(header, ""))) for row in rows)) + 2 if rows else len(header) + 2
        sheet.column_dimensions[get_column_letter(index)].width = min(max(width, 10), 42)


def _editable_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("Datensatz") in EDITABLE_RECORD_TYPES]


def _segment_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("Datensatz") != "SEGMENT":
            continue
        result.append(
            {
                "Datum": row.get("Datum", ""),
                "Wochentag": row.get("Wochentag", ""),
                "Typ": _display_category(row.get("Kategorie", "")),
                "Beginn": row.get("Beginn", ""),
                "Ende": row.get("Ende", ""),
                "Dauer": row.get("Dauer", ""),
                "Standort": _display_location(row.get("Standort", "")),
                "Quelle": row.get("Quelle", ""),
            }
        )
    return result


def _absence_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("Datensatz") != "ABWESENHEIT":
            continue
        result.append(
            {
                "Datum": row.get("Datum", ""),
                "Wochentag": row.get("Wochentag", ""),
                "Typ": _display_category(row.get("Kategorie", "")),
                "Halber_Tag": row.get("Halber_Tag", ""),
                "Notiz": row.get("Notiz", ""),
            }
        )
    return result


def _note_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("Datensatz") != "NOTIZ":
            continue
        result.append(
            {
                "Datum": row.get("Datum", ""),
                "Wochentag": row.get("Wochentag", ""),
                "Notiz": row.get("Notiz", ""),
            }
        )
    return result


def _style_xlsx_sheet(sheet, Font, PatternFill, Alignment, Border, Side) -> None:
    header_fill = PatternFill("solid", fgColor="1E40AF")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="DBEAFE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
    sheet.sheet_view.showGridLines = False


def _headers() -> list[str]:
    return EXPORT_HEADERS


def import_file(conn, file_path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    """Import a detailed CSV/XLSX export back into the local database."""

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Importdatei nicht gefunden: {path}")
    rows = _read_import_rows(path)
    return import_rows(conn, rows, source_name=source_name or path.name)


def preview_sdata_file(conn, file_path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    """Parse SAP SDATA events and compare the derived segments with local data."""

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"SAP-Datei nicht gefunden: {path}")
    events, skipped = _read_sdata_events(path)
    days = _build_sdata_preview_days(conn, events)
    return {
        "ok": True,
        "source_name": source_name or path.name,
        "events_read": len(events),
        "rows_ignored": skipped,
        "days": days,
    }


def import_sdata_preview(
    conn,
    days: list[dict[str, Any]],
    selected_dates: list[str],
    *,
    source_name: str = "SAP SDATA Import",
) -> dict[str, Any]:
    """Import selected preview days by reusing the regular detailed importer."""

    selected = set(selected_dates)
    rows: list[dict[str, Any]] = []
    for day in days:
        date_text = parse_date(str(day.get("date", ""))).isoformat()
        if date_text not in selected:
            continue
        for segment in day.get("imported_segments", []):
            rows.append(_sdata_segment_import_row(date_text, segment))
    if not rows:
        raise ValueError("Keine SAP-SDATA-Tage für den Import ausgewählt.")
    return import_rows(conn, rows, source_name=source_name)


def import_rows(conn, rows: list[dict[str, Any]], *, source_name: str = "Manueller Import") -> dict[str, Any]:
    affected_dates: set[str] = set()
    prepared_rows: list[tuple[int, str, dict[str, Any]]] = []
    overview_rows: list[tuple[int, dict[str, Any]]] = []
    explicit_segment_dates: set[str] = set()
    explicit_day_type_dates: set[str] = set()
    explicit_note_dates: set[str] = set()
    log_entries: list[dict[str, Any]] = []
    counts = {
        "rows_read": len(rows),
        "segments": 0,
        "day_types": 0,
        "notes": 0,
        "overview_rows": 0,
        "overview_segments": 0,
        "overview_day_types": 0,
        "overview_notes": 0,
        "summaries_ignored": 0,
        "rows_ignored": 0,
        "warnings": 0,
        "errors": 0,
    }

    try:
        for index, raw_row in enumerate(rows, start=2):
            row = _normalize_import_row(raw_row)
            record_type = _record_type_code(_row_value(row, "Datensatz", "Satztyp"))
            if not record_type:
                counts["rows_ignored"] += 1
                continue
            if record_type == OVERVIEW_RECORD_TYPE:
                if not _clean_cell(_row_value(row, "Datum")):
                    counts["rows_ignored"] += 1
                    continue
                counts["overview_rows"] += 1
                overview_rows.append((index, row))
                if _overview_row_has_import_data(row):
                    affected_dates.add(_date_from_row(row, index))
                continue
            prepared_rows.append((index, record_type, row))
            if record_type in EDITABLE_RECORD_TYPES:
                date_text = _date_from_row(row, index)
                affected_dates.add(date_text)
                if record_type == "SEGMENT":
                    explicit_segment_dates.add(date_text)
                elif record_type == "ABWESENHEIT":
                    explicit_day_type_dates.add(date_text)
                elif record_type == "NOTIZ":
                    explicit_note_dates.add(date_text)

        with database.transaction(conn):
            before_snapshots = {date_text: _snapshot_date(conn, date_text) for date_text in sorted(affected_dates)}
            _clear_import_dates(conn, affected_dates)

            for index, record_type, row in prepared_rows:
                if record_type == "SEGMENT":
                    affected_dates.update(_import_segment_row(conn, row, index, affected_dates))
                    counts["segments"] += 1
                    _add_import_log(log_entries, "INFO", _date_from_row(row, index), "Segmente", "Segment übernommen", "", _describe_segment_row(row), f"Zeile {index}")
                elif record_type == "ABWESENHEIT":
                    date_text = _import_day_type_row(conn, row, index)
                    affected_dates.add(date_text)
                    counts["day_types"] += 1
                    _add_import_log(log_entries, "INFO", date_text, "Abwesenheiten", "Abwesenheit übernommen", "", _describe_day_type_row(row), f"Zeile {index}")
                elif record_type == "NOTIZ":
                    date_text = _import_note_row(conn, row, index)
                    affected_dates.add(date_text)
                    counts["notes"] += 1
                    _add_import_log(log_entries, "INFO", date_text, "Notizen", "Notiz übernommen", "", _clean_cell(_row_value(row, "Notiz")), f"Zeile {index}")
                elif record_type in {"TAGES_SUMME", "TAGES_SUMMARY", "SUMMARY"}:
                    counts["summaries_ignored"] += 1
                else:
                    counts["rows_ignored"] += 1

            for index, row in overview_rows:
                if not _overview_row_has_import_data(row):
                    continue
                date_text = _date_from_row(row, index)
                _import_overview_row(
                    conn,
                    row,
                    index,
                    explicit_segment_dates,
                    explicit_day_type_dates,
                    explicit_note_dates,
                    counts,
                    log_entries,
                )
                affected_dates.add(date_text)

            if affected_dates:
                recalculate_range(conn, min(affected_dates), max(affected_dates))

            for index, row in overview_rows:
                if _overview_row_has_import_data(row):
                    _compare_overview_summary(conn, row, index, counts, log_entries)

            after_snapshots = {date_text: _snapshot_date(conn, date_text) for date_text in sorted(affected_dates)}
            for date_text in sorted(affected_dates):
                before = before_snapshots.get(date_text, "keine Daten")
                after = after_snapshots.get(date_text, "keine Daten")
                _add_import_log(log_entries, "INFO", date_text, "Datenbank", "Tag ersetzt", before, after, "Nur dieser Tag wurde überschrieben.")

        log_path = _write_import_log(source_name, counts, log_entries)
        return {
            **counts,
            "start_date": min(affected_dates) if affected_dates else "",
            "end_date": max(affected_dates) if affected_dates else "",
            "log_path": str(log_path),
            "log_name": log_path.name,
        }
    except Exception as exc:
        counts["errors"] += 1
        _add_import_log(log_entries, "ERROR", "", "Import", "Import abgebrochen", "", str(exc), "Die Datenbanktransaktion wurde zurückgerollt.")
        log_path = _write_import_log(source_name, counts, log_entries)
        if isinstance(exc, ValueError):
            raise ValueError(f"{exc} Import-Protokoll: {log_path}") from exc
        raise


def _read_sdata_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    suffix = path.suffix.lower()
    values: list[tuple[int, Any]] = []
    if suffix == ".csv":
        for index, row in enumerate(_read_csv_rows(path), start=2):
            values.append((index, _sdata_value_from_row(row)))
    elif suffix in {".xlsx", ".xlsm"}:
        values = _read_xlsx_sdata_values(path)
    else:
        raise ValueError("SAP-SDATA-Import unterstützt CSV oder Excel (.xlsx).")

    events: list[dict[str, Any]] = []
    skipped = 0
    for row_number, value in values:
        event = _parse_sdata_value(value, row_number)
        if event:
            events.append(event)
        else:
            skipped += 1
    events.sort(key=lambda item: (item["date"], item["time"], item["event_type"], item["row_number"]))
    return events, skipped


def _read_xlsx_sdata_values(path: Path) -> list[tuple[int, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("SAP-SDATA-Excel-Import benoetigt openpyxl. Bitte requirements.txt installieren.") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        values: list[tuple[int, Any]] = []
        for sheet in workbook.worksheets:
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                for value in row:
                    if value not in (None, ""):
                        values.append((row_index, value))
        return values
    finally:
        workbook.close()


def _sdata_value_from_row(row: dict[str, Any]) -> Any:
    for key, value in row.items():
        if str(key).strip().casefold() == "sdata":
            return value
    for value in row.values():
        if value not in (None, ""):
            return value
    return ""


def _parse_sdata_value(value: Any, row_number: int) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", str(value or ""))
    if len(compact) < 45:
        return None
    event_type = compact[10:13]
    if event_type not in {"P10", "P20"}:
        return None
    try:
        log_date = datetime.strptime(compact[17:25], "%Y%m%d").date().isoformat()
        log_time = datetime.strptime(compact[25:31], "%H%M%S").time().strftime("%H:%M:%S")
        phys_date = datetime.strptime(compact[31:39], "%Y%m%d").date().isoformat()
        phys_time = datetime.strptime(compact[39:45], "%H%M%S").time().strftime("%H:%M:%S")
    except ValueError:
        return None
    return {
        "source_system": compact[:10],
        "event_type": event_type,
        "terminal_id": compact[13:17],
        "date": log_date,
        "time": log_time,
        "phys_date": phys_date,
        "phys_time": phys_time,
        "personnel_number": compact[45:],
        "row_number": row_number,
        "raw": str(value or ""),
    }


def _build_sdata_preview_days(conn, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events_by_date: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_date.setdefault(event["date"], []).append(event)

    days: list[dict[str, Any]] = []
    for date_text in sorted(events_by_date):
        calculations_before = _snapshot_date(conn, date_text)
        imported_segments, warnings = _segments_from_sdata_events(events_by_date[date_text])
        current_segments = [_preview_segment(dict(row)) for row in database.get_segments_for_date(conn, date_text)]
        status = "same" if _segment_signatures(current_segments) == _segment_signatures(imported_segments) else "changed"
        if warnings:
            status = "warning"
        if not imported_segments:
            status = "error"
        days.append(
            {
                "date": date_text,
                "status": status,
                "status_label": _sdata_status_label(status),
                "selected": status in {"changed", "warning"},
                "current_snapshot": calculations_before,
                "current_segments": current_segments,
                "imported_segments": imported_segments,
                "events": events_by_date[date_text],
                "warnings": warnings,
            }
        )
    return days


def _segments_from_sdata_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    work_segments: list[dict[str, Any]] = []
    warnings: list[str] = []
    open_start: dict[str, Any] | None = None
    for event in sorted(events, key=lambda item: (item["time"], item["event_type"], item["row_number"])):
        if event["event_type"] == "P10":
            if open_start:
                warnings.append(
                    f"Start {open_start['time'][:5]} ohne Ende wurde durch Start {event['time'][:5]} ersetzt."
                )
            open_start = event
            continue
        if not open_start:
            warnings.append(f"Ende {event['time'][:5]} ohne vorherigen Start wurde ignoriert.")
            continue
        if event["time"] <= open_start["time"]:
            warnings.append(f"Ende {event['time'][:5]} liegt nicht nach Start {open_start['time'][:5]} und wurde ignoriert.")
            open_start = None
            continue
        work_segments.append(
            {
                "type": "WORK",
                "start_time": open_start["time"],
                "end_time": event["time"],
                "location": "OFFICE",
                "source": "MANUAL",
            }
        )
        open_start = None
    if open_start:
        warnings.append(f"Start {open_start['time'][:5]} ohne Ende wurde nicht importiert.")

    segments: list[dict[str, Any]] = []
    previous_end = ""
    for segment in work_segments:
        if previous_end and segment["start_time"] > previous_end:
            segments.append(
                {
                    "type": "BREAK",
                    "start_time": previous_end,
                    "end_time": segment["start_time"],
                    "location": "",
                    "source": "MANUAL",
                }
            )
        segments.append(segment)
        previous_end = segment["end_time"]
    return segments, warnings


def _preview_segment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": row.get("type", ""),
        "start_time": row.get("start_time", ""),
        "end_time": row.get("end_time") or "",
        "location": row.get("location") or "",
        "source": row.get("source") or "",
    }


def _segment_signatures(segments: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    return [
        (
            str(segment.get("type") or ""),
            str(segment.get("start_time") or "")[:5],
            str(segment.get("end_time") or "")[:5],
            str(segment.get("location") or ""),
        )
        for segment in segments
    ]


def _sdata_status_label(status: str) -> str:
    return {
        "same": "Gleich",
        "changed": "Unterschied",
        "warning": "Warnung",
        "error": "Fehler",
    }.get(status, status)


def _sdata_segment_import_row(date_text: str, segment: dict[str, Any]) -> dict[str, Any]:
    segment_type = str(segment.get("type") or "")
    if segment_type not in SEGMENT_TYPES:
        raise ValueError(f"{date_text}: SAP-Segmenttyp ist ungültig.")
    start = normalize_time_input(str(segment.get("start_time") or ""))
    end = normalize_time_input(str(segment.get("end_time") or ""))
    location = str(segment.get("location") or "")
    if segment_type != "WORK":
        location = ""
    elif location not in {"OFFICE", "HOME", "UNKNOWN"}:
        location = "OFFICE"
    return {
        "Datensatz": "SEGMENT",
        "Datum": date_text,
        "Kategorie": segment_type,
        "Beginn": start,
        "Ende": end,
        "Standort": location,
        "Quelle": "MANUAL",
    }


def _clear_import_dates(conn, dates: set[str]) -> None:
    for date_text in sorted(dates):
        conn.execute("DELETE FROM segments WHERE date = ?", (date_text,))
        conn.execute("DELETE FROM day_types WHERE date = ?", (date_text,))
        conn.execute("DELETE FROM notes WHERE date = ?", (date_text,))


def _import_overview_row(
    conn,
    row: dict[str, Any],
    row_number: int,
    explicit_segment_dates: set[str],
    explicit_day_type_dates: set[str],
    explicit_note_dates: set[str],
    counts: dict[str, int],
    log_entries: list[dict[str, Any]],
) -> None:
    date_text = _date_from_row(row, row_number)
    start_time = _optional_time_from_row(row, "Beginn", row_number)
    end_time = _optional_time_from_row(row, "Ende", row_number)
    if bool(start_time) != bool(end_time):
        _warn_import(
            counts,
            log_entries,
            date_text,
            "Übersicht",
            "Unvollständiges Tagesfenster",
            "",
            f"Zeile {row_number}: Beginn und Ende müssen beide gefüllt sein, damit daraus ein Segment wird.",
        )
    elif start_time and end_time:
        if date_text in explicit_segment_dates:
            _compare_overview_segments(conn, row, row_number, counts, log_entries)
        else:
            location = _location_code(_row_value(row, "Standort")) or "UNKNOWN"
            if location not in {"OFFICE", "HOME", "UNKNOWN"}:
                location = "UNKNOWN"
            database.add_segment(conn, date_text, "WORK", start_time, end_time, location=location, source="MANUAL")
            counts["overview_segments"] += 1
            _add_import_log(
                log_entries,
                "INFO",
                date_text,
                "Übersicht",
                "Arbeitssegment aus Übersicht erzeugt",
                "",
                f"Arbeit {start_time[:5]}-{end_time[:5]} {_display_location(location)}",
                f"Zeile {row_number}",
            )

    day_type = _overview_day_type(row)
    if day_type:
        if date_text in explicit_day_type_dates:
            _compare_overview_day_type(conn, row, row_number, counts, log_entries)
        else:
            database.upsert_day_type(conn, date_text, day_type, False, _clean_cell(_row_value(row, "Notiz")) or None)
            counts["overview_day_types"] += 1
            _add_import_log(
                log_entries,
                "INFO",
                date_text,
                "Übersicht",
                "Abwesenheit aus Übersicht erzeugt",
                "",
                _display_category(day_type),
                f"Zeile {row_number}",
            )

    note = _clean_cell(_row_value(row, "Notiz"))
    if note and date_text not in explicit_note_dates:
        database.replace_note(conn, date_text, note)
        counts["overview_notes"] += 1
        _add_import_log(log_entries, "INFO", date_text, "Übersicht", "Notiz aus Übersicht übernommen", "", note, f"Zeile {row_number}")


def _compare_overview_segments(
    conn,
    row: dict[str, Any],
    row_number: int,
    counts: dict[str, int],
    log_entries: list[dict[str, Any]],
) -> None:
    date_text = _date_from_row(row, row_number)
    expected_start = _optional_time_from_row(row, "Beginn", row_number)
    expected_end = _optional_time_from_row(row, "Ende", row_number)
    if not expected_start or not expected_end:
        return
    segments = [dict(row) for row in database.get_segments_for_date(conn, date_text) if row["type"] == "WORK"]
    if not segments:
        _warn_import(
            counts,
            log_entries,
            date_text,
            "Übersicht",
            "Übersicht passt nicht zu Segmenten",
            f"Übersicht: Arbeit {expected_start[:5]}-{expected_end[:5]}",
            "Detailblatt enthält kein Arbeitssegment.",
        )
        return
    actual_start, actual_end = _first_last_work_times(segments)
    if actual_start[:5] != expected_start[:5] or actual_end[:5] != expected_end[:5]:
        _warn_import(
            counts,
            log_entries,
            date_text,
            "Übersicht",
            "Übersicht passt nicht zu Segmenten",
            f"Übersicht: Arbeit {expected_start[:5]}-{expected_end[:5]}",
            f"Segmente: Arbeit {actual_start[:5] or '-'}-{actual_end[:5] or '-'}",
            f"Zeile {row_number}; detaillierte Segmente haben Vorrang.",
        )


def _compare_overview_day_type(
    conn,
    row: dict[str, Any],
    row_number: int,
    counts: dict[str, int],
    log_entries: list[dict[str, Any]],
) -> None:
    date_text = _date_from_row(row, row_number)
    expected = _overview_day_type(row)
    if not expected:
        return
    actual = {day_type["type"] for day_type in database.get_day_types_for_date(conn, date_text)}
    if expected not in actual:
        _warn_import(
            counts,
            log_entries,
            date_text,
            "Übersicht",
            "Übersicht passt nicht zu Abwesenheiten",
            _display_category(expected),
            ", ".join(_display_category(value) for value in sorted(actual)) or "keine Abwesenheit",
            f"Zeile {row_number}; Abwesenheiten-Blatt hat Vorrang.",
        )


def _compare_overview_summary(
    conn,
    row: dict[str, Any],
    row_number: int,
    counts: dict[str, int],
    log_entries: list[dict[str, Any]],
) -> None:
    date_text = _date_from_row(row, row_number)
    summary = database.get_day_summary(conn, date_text)
    if not summary:
        return
    for label, field in (
        ("Arbeitszeit", "actual_minutes"),
        ("Pause", "break_minutes"),
        ("Soll", "target_minutes"),
        ("Saldo", "balance_minutes"),
    ):
        expected = _duration_minutes(_row_value(row, label))
        if expected is None:
            continue
        actual = int(summary[field])
        if expected != actual:
            _warn_import(
                counts,
                log_entries,
                date_text,
                "Übersicht",
                f"Berechneter Tageswert weicht ab: {label}",
                minutes_to_hhmm(expected),
                minutes_to_hhmm(actual),
                f"Zeile {row_number}; die Datenbankberechnung bleibt maßgeblich.",
            )


def _overview_row_has_import_data(row: dict[str, Any]) -> bool:
    return bool(
        _clean_cell(_row_value(row, "Beginn"))
        or _clean_cell(_row_value(row, "Ende"))
        or _clean_cell(_row_value(row, "Notiz"))
        or _overview_day_type(row)
    )


def _overview_day_type(row: dict[str, Any]) -> str:
    category = _category_code(_row_value(row, "Tagesart", "Kategorie", "Typ"), DAY_TYPES | DAY_TYPE_SUMMARY_CODES)
    if category in DAY_TYPES:
        return category
    return {
        "VACATION": "URLAUB",
        "SICK": "KRANK",
        "HOLIDAY": "FEIERTAG",
        "TRAVEL": "DIENSTREISE",
        "FLEXTIME": "GLEITZEITTAG",
    }.get(category, "")


def _optional_time_from_row(row: dict[str, Any], name: str, row_number: int) -> str | None:
    value = _row_value(row, name)
    if value in (None, ""):
        return None
    return _time_from_row(row, name, row_number, required=True)


def _duration_minutes(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(round(value * 24 * 60)) if abs(value) < 1 else int(round(value))
    text = _clean_cell(value).replace(" h", "").replace("Std.", "").strip()
    if not text:
        return None
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    if ":" in text:
        hours, minutes = text.split(":", 1)
        return sign * (int(hours) * 60 + int(minutes[:2]))
    normalized = text.replace(",", ".")
    try:
        return int(round(float(normalized) * 60))
    except ValueError as exc:
        raise ValueError(f"Dauerwert ist ungueltig: {value}") from exc


def _snapshot_date(conn, date_text: str) -> str:
    segments = [_format_segment(dict(row)) for row in database.get_segments_for_date(conn, date_text)]
    day_types = [_format_day_type(dict(row)) for row in database.get_day_types_for_date(conn, date_text)]
    note = database.get_note_for_date(conn, date_text)
    summary = database.get_day_summary(conn, date_text)
    parts: list[str] = []
    parts.append("Segmente: " + ("; ".join(segments) if segments else "keine"))
    parts.append("Abwesenheiten: " + ("; ".join(day_types) if day_types else "keine"))
    parts.append("Notiz: " + (note if note else "keine"))
    if summary:
        parts.append(
            "Berechnung: "
            f"Soll {minutes_to_hhmm(summary['target_minutes'])}, "
            f"Ist {minutes_to_hhmm(summary['actual_minutes'])}, "
            f"Pause {minutes_to_hhmm(summary['break_minutes'])}, "
            f"Saldo {minutes_to_hhmm(summary['balance_minutes'])}"
        )
    else:
        parts.append("Berechnung: keine")
    return " | ".join(parts)


def _format_segment(row: dict[str, Any]) -> str:
    end = (row.get("end_time") or "offen")[:5]
    location = f" {_display_location(row.get('location'))}" if row.get("location") else ""
    return f"#{row.get('id')} {_display_category(row.get('type'))} {str(row.get('start_time') or '')[:5]}-{end}{location}"


def _format_day_type(row: dict[str, Any]) -> str:
    half = " halb" if row.get("half_day") else ""
    note = f" ({row.get('note')})" if row.get("note") else ""
    return f"#{row.get('id')} {_display_category(row.get('type'))}{half}{note}"


def _describe_segment_row(row: dict[str, Any]) -> str:
    segment_type = _display_category(_category_code(_row_value(row, "Kategorie", "Typ"), SEGMENT_TYPES))
    start = _clean_cell(_row_value(row, "Beginn"))
    end = _clean_cell(_row_value(row, "Ende")) or "offen"
    location = _display_location(_location_code(_row_value(row, "Standort")))
    return f"{segment_type} {start}-{end} {location}".strip()


def _describe_day_type_row(row: dict[str, Any]) -> str:
    day_type = _display_category(_category_code(_row_value(row, "Kategorie", "Typ"), DAY_TYPES))
    half = "halb" if _bool_from_cell(_row_value(row, "Halber_Tag")) else "ganztägig"
    note = _clean_cell(_row_value(row, "Notiz"))
    return f"{day_type} {half}{f' ({note})' if note else ''}"


def _warn_import(
    counts: dict[str, int],
    log_entries: list[dict[str, Any]],
    date_text: str,
    source: str,
    action: str,
    before: str,
    after: str,
    details: str = "",
) -> None:
    counts["warnings"] += 1
    _add_import_log(log_entries, "WARNING", date_text, source, action, before, after, details)


def _add_import_log(
    entries: list[dict[str, Any]],
    status: str,
    date_text: str,
    source: str,
    action: str,
    before: str,
    after: str,
    details: str,
) -> None:
    entries.append(
        {
            "status": status,
            "date": date_text,
            "source": source,
            "action": action,
            "before": before,
            "after": after,
            "details": details,
        }
    )


def _write_import_log(source_name: str, counts: dict[str, int], entries: list[dict[str, Any]]) -> Path:
    ensure_data_dirs()
    IMPORT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = IMPORT_LOG_DIR / f"import_{stamp}.html"
    path.write_text(_render_import_log_html(source_name, counts, entries), encoding="utf-8")
    return path


def _group_import_log_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_by_date: dict[str, dict[str, Any]] = {}
    for entry in entries:
        normalized = {
            "status": _clean_cell(entry.get("status")) or "INFO",
            "date": _clean_cell(entry.get("date")) or "Ohne Datum",
            "source": _clean_cell(entry.get("source")),
            "action": _clean_cell(entry.get("action")),
            "before": _clean_cell(entry.get("before")),
            "after": _clean_cell(entry.get("after")),
            "details": _clean_cell(entry.get("details")),
        }
        date_text = normalized["date"]
        if date_text not in grouped_by_date:
            grouped_by_date[date_text] = {
                "id": f"day-{len(grouped_by_date)}",
                "date": date_text,
                "severity": 0,
                "before": "",
                "after": "",
                "entries": [],
            }
        group = grouped_by_date[date_text]
        group["entries"].append(normalized)
        group["severity"] = max(group["severity"], _import_log_severity(normalized["status"]))
        if normalized["source"] == "Datenbank" and normalized["action"] == "Tag ersetzt":
            group["before"] = normalized["before"] or "keine Daten"
            group["after"] = normalized["after"] or "keine Daten"

    result: list[dict[str, Any]] = []
    for date_text in sorted(grouped_by_date, key=lambda value: (value == "Ohne Datum", value)):
        group = grouped_by_date[date_text]
        action_entries = [
            entry
            for entry in group["entries"]
            if not (entry["source"] == "Datenbank" and entry["action"] == "Tag ersetzt")
        ]
        problem_entries = [entry for entry in group["entries"] if entry["status"] in {"WARNING", "ERROR"}]
        status, status_label, status_class = _import_log_status(group["severity"])
        group.update(
            {
                "status": status,
                "status_label": status_label,
                "status_class": status_class,
                "summary": _summarize_import_log_actions(action_entries),
                "problem": _summarize_import_log_problem(problem_entries),
                "entry_count": len(action_entries) or len(group["entries"]),
                "before": group["before"] or "keine Daten",
                "after": group["after"] or "keine Daten",
            }
        )
        result.append(group)
    return result


def _import_log_severity(status: str) -> int:
    return {"ERROR": 2, "WARNING": 1}.get(status, 0)


def _import_log_status(severity: int) -> tuple[str, str, str]:
    if severity >= 2:
        return "ERROR", "Fehler", "error"
    if severity == 1:
        return "WARNING", "Warnung", "warning"
    return "INFO", "Erfolgreich", "success"


def _summarize_import_log_actions(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "Tag geprüft"
    actions: list[str] = []
    for entry in entries:
        action = entry["action"] or "Aktion"
        if action not in actions:
            actions.append(action)
    if len(actions) <= 3:
        return "; ".join(actions)
    return "; ".join(actions[:3]) + f"; +{len(actions) - 3} weitere"


def _summarize_import_log_problem(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    priority = sorted(entries, key=lambda entry: _import_log_severity(entry["status"]), reverse=True)
    first = priority[0]
    parts = [first["action"], first["details"] or first["after"]]
    return " - ".join(part for part in parts if part)


def _render_import_log_html(source_name: str, counts: dict[str, int], entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(_group_import_log_entries(entries), ensure_ascii=False).replace("</", "<\\/")
    counts_payload = json.dumps(counts, ensure_ascii=False)
    source_payload = json.dumps(source_name, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ArbeitszeitTracker Import-Protokoll</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f8fafc;
      --surface: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #d8e0ea;
      --info: #2563eb;
      --success: #15803d;
      --success-bg: #dcfce7;
      --warning: #b45309;
      --warning-bg: #fef3c7;
      --error: #b91c1c;
      --error-bg: #fee2e2;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111827;
        --surface: #182033;
        --text: #f8fafc;
        --muted: #cbd5e1;
        --border: #334155;
        --success-bg: #123524;
        --warning-bg: #3d2c10;
        --error-bg: #3b1515;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    header {{
      display: grid;
      gap: 16px;
      padding: 24px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    h1 {{ margin: 0; font-size: 24px; }}
    .meta, .toolbar, .cards {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    .meta {{ color: var(--muted); }}
    .card {{
      min-width: 120px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: color-mix(in srgb, var(--surface), var(--bg) 35%);
    }}
    .card strong {{ display: block; font-size: 20px; }}
    input, select, button {{
      min-height: 40px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0 12px;
      background: var(--bg);
      color: var(--text);
      font: inherit;
    }}
    button {{
      cursor: pointer;
      background: var(--surface);
      white-space: nowrap;
      transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
    }}
    button:hover {{ transform: translateY(-1px); border-color: var(--info); }}
    input {{ min-width: min(420px, 100%); }}
    main {{ padding: 24px; overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      cursor: pointer;
      user-select: none;
      background: color-mix(in srgb, var(--surface), var(--bg) 45%);
      white-space: nowrap;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    td {{ overflow-wrap: anywhere; }}
    .day-row.success {{ background: color-mix(in srgb, var(--success-bg), transparent 45%); }}
    .day-row.warning {{ background: color-mix(in srgb, var(--warning-bg), transparent 35%); }}
    .day-row.error {{ background: color-mix(in srgb, var(--error-bg), transparent 35%); }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 96px;
      min-height: 30px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 13px;
      font-weight: 800;
    }}
    .badge.success {{ color: var(--success); background: var(--success-bg); }}
    .badge.warning {{ color: var(--warning); background: var(--warning-bg); }}
    .badge.error {{ color: var(--error); background: var(--error-bg); }}
    .problem {{ margin: 4px 0 0; color: var(--warning); font-weight: 700; }}
    .detail-row.is-hidden {{ display: none; }}
    .detail-cell {{
      padding: 0;
      background: color-mix(in srgb, var(--surface), var(--bg) 20%);
    }}
    .detail-panel {{
      display: grid;
      gap: 16px;
      padding: 16px;
      border-top: 1px solid var(--border);
    }}
    .detail-head {{
      display: grid;
      gap: 8px;
    }}
    .detail-head h2 {{ margin: 0; font-size: 18px; }}
    .detail-head p {{ margin: 0; color: var(--muted); }}
    .compare-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .compare-box {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: var(--surface);
    }}
    .compare-box h3 {{ margin: 0 0 8px; font-size: 14px; }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      color: var(--muted);
      font: inherit;
    }}
    .events th {{ cursor: default; }}
    .event-status-ERROR td:first-child {{ color: var(--error); font-weight: 800; }}
    .event-status-WARNING td:first-child {{ color: var(--warning); font-weight: 800; }}
    .event-status-INFO td:first-child {{ color: var(--success); font-weight: 800; }}
    .count {{ color: var(--muted); font-size: 14px; }}
    @media (max-width: 780px) {{
      header, main {{ padding: 16px; }}
      .compare-grid {{ grid-template-columns: 1fr; }}
      .day-table thead {{ display: none; }}
      .day-row, .day-row td {{
        display: grid;
        width: 100%;
      }}
      .day-row {{
        grid-template-columns: 1fr;
        border-bottom: 1px solid var(--border);
      }}
      .day-row td {{ border-bottom: 0; padding: 8px 12px; }}
      .day-row td::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
      }}
      .detail-row td {{ display: block; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Import-Protokoll</h1>
      <div class="meta"><span id="source"></span><span id="created"></span><span id="count"></span></div>
    </div>
    <section class="cards" id="cards"></section>
    <div class="toolbar">
      <label for="search">Suche</label>
      <input id="search" type="search" placeholder="Freitext über alle Spalten">
      <label for="status">Status</label>
      <select id="status">
        <option value="">Alle</option>
        <option value="WARNING">Warnungen</option>
        <option value="ERROR">Fehler</option>
        <option value="INFO">Erfolgreich</option>
      </select>
    </div>
  </header>
  <main>
    <table class="day-table" aria-describedby="count">
      <thead>
        <tr>
          <th data-key="status">Status</th>
          <th data-key="date">Datum</th>
          <th data-key="summary">Zusammenfassung</th>
          <th data-key="entry_count">Aktionen</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
const IMPORT_GROUPS = {payload};
const IMPORT_COUNTS = {counts_payload};
const IMPORT_SOURCE = {source_payload};
const CREATED_AT = {json.dumps(datetime.now().replace(microsecond=0).isoformat(sep=" "), ensure_ascii=False)};

let sortKey = "date";
let sortDirection = 1;
const expanded = new Set();
const search = document.getElementById("search");
const status = document.getElementById("status");
const rows = document.getElementById("rows");
const count = document.getElementById("count");

document.getElementById("source").textContent = `Quelle: ${{IMPORT_SOURCE}}`;
document.getElementById("created").textContent = `Erstellt: ${{CREATED_AT}}`;
document.getElementById("cards").innerHTML = [
  ["Tage", IMPORT_GROUPS.length],
  ["Zeilen", IMPORT_COUNTS.rows_read],
  ["Segmente", IMPORT_COUNTS.segments + IMPORT_COUNTS.overview_segments],
  ["Abwesenheiten", IMPORT_COUNTS.day_types + IMPORT_COUNTS.overview_day_types],
  ["Notizen", IMPORT_COUNTS.notes + IMPORT_COUNTS.overview_notes],
  ["Warnungen", IMPORT_COUNTS.warnings],
  ["Fehler", IMPORT_COUNTS.errors],
].map(([label, value]) => `<article class="card"><span>${{escapeHtml(label)}}</span><strong>${{escapeHtml(value)}}</strong></article>`).join("");

function render() {{
  const query = search.value.trim().toLowerCase();
  const selectedStatus = status.value;
  const filtered = IMPORT_GROUPS
    .filter(group => !selectedStatus || group.status === selectedStatus)
    .filter(group => groupMatches(group, query))
    .sort((a, b) => String(a[sortKey] ?? "").localeCompare(String(b[sortKey] ?? ""), "de") * sortDirection);
  rows.innerHTML = filtered.map(group => renderGroup(group)).join("");
  rows.querySelectorAll("button[data-id]").forEach(button => {{
    button.addEventListener("click", () => {{
      const id = button.dataset.id;
      if (expanded.has(id)) expanded.delete(id);
      else expanded.add(id);
      render();
    }});
  }});
  count.textContent = `${{filtered.length}} von ${{IMPORT_GROUPS.length}} Tagen`;
}}

function renderGroup(group) {{
  const isOpen = expanded.has(group.id);
  return `
    <tr class="day-row ${{escapeHtml(group.status_class)}}">
      <td data-label="Status"><span class="badge ${{escapeHtml(group.status_class)}}">${{escapeHtml(group.status_label)}}</span></td>
      <td data-label="Datum"><strong>${{escapeHtml(group.date)}}</strong></td>
      <td data-label="Zusammenfassung">
        <div>${{escapeHtml(group.summary)}}</div>
        ${{group.problem ? `<p class="problem">${{escapeHtml(group.problem)}}</p>` : ""}}
      </td>
      <td data-label="Aktionen">${{escapeHtml(group.entry_count)}}</td>
      <td data-label="Details"><button type="button" data-id="${{escapeHtml(group.id)}}" aria-expanded="${{isOpen}}">${{isOpen ? "Schließen" : "Details"}}</button></td>
    </tr>
    <tr class="detail-row ${{isOpen ? "" : "is-hidden"}}">
      <td class="detail-cell" colspan="5">${{renderDetails(group)}}</td>
    </tr>
  `;
}}

function renderDetails(group) {{
  return `
    <section class="detail-panel">
      <div class="detail-head">
        <h2>${{escapeHtml(group.date)}}</h2>
        <p>${{escapeHtml(group.summary)}}</p>
        ${{group.problem ? `<p class="problem">${{escapeHtml(group.problem)}}</p>` : ""}}
      </div>
      <div class="compare-grid">
        <section class="compare-box">
          <h3>Vorher</h3>
          <pre>${{escapeHtml(group.before)}}</pre>
        </section>
        <section class="compare-box">
          <h3>Nachher</h3>
          <pre>${{escapeHtml(group.after)}}</pre>
        </section>
      </div>
      <table class="events">
        <thead>
          <tr>
            <th>Status</th>
            <th>Quelle</th>
            <th>Aktion</th>
            <th>Vorher / Erwartet</th>
            <th>Nachher / Gefunden</th>
            <th>Hinweis</th>
          </tr>
        </thead>
        <tbody>${{group.entries.map(renderEvent).join("")}}</tbody>
      </table>
    </section>
  `;
}}

function renderEvent(entry) {{
  return `
    <tr class="event-status-${{escapeHtml(entry.status)}}">
      <td>${{escapeHtml(entry.status)}}</td>
      <td>${{escapeHtml(entry.source)}}</td>
      <td>${{escapeHtml(entry.action)}}</td>
      <td>${{escapeHtml(entry.before)}}</td>
      <td>${{escapeHtml(entry.after)}}</td>
      <td>${{escapeHtml(entry.details)}}</td>
    </tr>
  `;
}}

function groupMatches(group, query) {{
  if (!query) return true;
  return JSON.stringify(group).toLowerCase().includes(query);
}}

function escapeHtml(value) {{
  return String(value ?? "").replace(/[&<>"']/g, char => ({{
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }}[char]));
}}

document.querySelectorAll("th[data-key]").forEach(th => {{
  th.addEventListener("click", () => {{
    const key = th.dataset.key;
    if (sortKey === key) sortDirection *= -1;
    else {{
      sortKey = key;
      sortDirection = 1;
    }}
    render();
  }});
}});
search.addEventListener("input", render);
status.addEventListener("change", render);
render();
  </script>
</body>
</html>
"""


def _first_last_work_times(segments: list[dict[str, Any]]) -> tuple[str, str]:
    work_segments = [row for row in segments if row.get("type") == "WORK"]
    if not work_segments:
        return "", ""
    start = work_segments[0].get("start_time") or ""
    ended = [row.get("end_time") or "" for row in work_segments if row.get("end_time")]
    return start, ended[-1] if ended else ""


def _segment_export_row(segment: dict[str, Any], day: Date) -> dict[str, Any]:
    duration = ""
    if segment.get("end_time"):
        duration = minutes_between(segment["date"], segment["start_time"], segment["end_time"])
    return {
        **_empty_export_row("SEGMENT", segment["date"], day),
        "Kategorie": segment["type"],
        "Beginn": _time_for_export(segment["start_time"]),
        "Ende": _time_for_export(segment.get("end_time")),
        "Dauer_Minuten": duration,
        "Dauer": minutes_to_hhmm(int(duration)) if duration != "" else "",
        "Standort": segment.get("location") or "",
        "Quelle": segment.get("source") or "",
    }


def _day_type_export_row(day_type: dict[str, Any], day: Date) -> dict[str, Any]:
    return {
        **_empty_export_row("ABWESENHEIT", day_type["date"], day),
        "Kategorie": day_type["type"],
        "Halber_Tag": int(bool(day_type["half_day"])),
        "Notiz": day_type.get("note") or "",
    }


def _note_export_row(note: dict[str, Any], day: Date) -> dict[str, Any]:
    return {
        **_empty_export_row("NOTIZ", note["date"], day),
        "Kategorie": "NOTIZ",
        "Notiz": note.get("text") or "",
    }


def _summary_export_row(summary: dict[str, Any], day: Date, note: str = "") -> dict[str, Any]:
    return {
        **_empty_export_row("TAGES_SUMME", summary["date"], day),
        "Kategorie": summary["day_category"],
        "Standort": summary.get("location") or "",
        "Notiz": note,
        "Soll_Minuten": summary["target_minutes"],
        "Ist_Minuten": summary["actual_minutes"],
        "Pause_Minuten": summary["break_minutes"],
        "Saldo_Minuten": summary["balance_minutes"],
    }


def _empty_export_row(record_type: str, date_text: str, day: Date) -> dict[str, Any]:
    row = {header: "" for header in EXPORT_HEADERS}
    row["Datensatz"] = record_type
    row["Datum"] = date_text
    row["Wochentag"] = WEEKDAYS[day.weekday()]
    return row


def _read_import_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_rows(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx_rows(path)
    raise ValueError("Import unterstuetzt nur CSV oder Excel (.xlsx).")


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            reader = csv.DictReader(handle, dialect=dialect)
        except csv.Error:
            reader = csv.DictReader(handle, delimiter=";")
        return [dict(row) for row in reader]


def _read_xlsx_rows(path: Path) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel-Import benoetigt openpyxl. Bitte requirements.txt installieren.") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        structured_rows = _read_structured_xlsx_rows(workbook)
        if structured_rows:
            return structured_rows
        sheet = workbook["Importdaten"] if "Importdaten" in workbook.sheetnames else workbook.active
        return _read_xlsx_sheet_rows(sheet)
    finally:
        workbook.close()


def _read_xlsx_sheet_rows(sheet) -> list[dict[str, Any]]:
    iterator = sheet.iter_rows(values_only=True)
    try:
        headers = [str(value).strip() if value is not None else "" for value in next(iterator)]
    except StopIteration:
        return []
    return [dict(zip(headers, values)) for values in iterator if any(value not in (None, "") for value in values)]


def _read_structured_xlsx_rows(workbook) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "Übersicht" in workbook.sheetnames:
        for row in _read_xlsx_sheet_rows(workbook["Übersicht"]):
            rows.append(
                {
                    "Datensatz": OVERVIEW_RECORD_TYPE,
                    "Datum": row.get("Datum", ""),
                    "Wochentag": row.get("Wochentag", ""),
                    "Tagesart": row.get("Tagesart", ""),
                    "Beginn": row.get("Beginn", ""),
                    "Ende": row.get("Ende", ""),
                    "Arbeitszeit": row.get("Arbeitszeit", ""),
                    "Pause": row.get("Pause", ""),
                    "Soll": row.get("Soll", ""),
                    "Saldo": row.get("Saldo", ""),
                    "Standort": row.get("Standort", ""),
                    "Notiz": row.get("Notiz", ""),
                }
            )
    if "Segmente" in workbook.sheetnames:
        for row in _read_xlsx_sheet_rows(workbook["Segmente"]):
            rows.append(
                {
                    "Datensatz": "SEGMENT",
                    "Datum": row.get("Datum", ""),
                    "Wochentag": row.get("Wochentag", ""),
                    "ID": row.get("ID", ""),
                    "Kategorie": row.get("Typ", row.get("Kategorie", "")),
                    "Beginn": row.get("Beginn", ""),
                    "Ende": row.get("Ende", ""),
                    "Standort": row.get("Standort", ""),
                    "Quelle": row.get("Quelle", ""),
                }
            )
    if "Abwesenheiten" in workbook.sheetnames:
        for row in _read_xlsx_sheet_rows(workbook["Abwesenheiten"]):
            rows.append(
                {
                    "Datensatz": "ABWESENHEIT",
                    "Datum": row.get("Datum", ""),
                    "Wochentag": row.get("Wochentag", ""),
                    "ID": row.get("ID", ""),
                    "Kategorie": row.get("Typ", row.get("Kategorie", "")),
                    "Halber_Tag": row.get("Halber_Tag", ""),
                    "Notiz": row.get("Notiz", ""),
                }
            )
    if "Notizen" in workbook.sheetnames:
        for row in _read_xlsx_sheet_rows(workbook["Notizen"]):
            rows.append(
                {
                    "Datensatz": "NOTIZ",
                    "Datum": row.get("Datum", ""),
                    "Wochentag": row.get("Wochentag", ""),
                    "ID": row.get("ID", ""),
                    "Kategorie": "NOTIZ",
                    "Notiz": row.get("Notiz", ""),
                }
            )
    return rows


def _normalize_import_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip().lstrip("\ufeff"): value for key, value in row.items() if key is not None}


def _import_segment_row(conn, row: dict[str, Any], row_number: int, replace_dates: set[str]) -> set[str]:
    date_text = _date_from_row(row, row_number)
    segment_type = _category_code(_row_value(row, "Kategorie", "Typ"), SEGMENT_TYPES)
    if segment_type not in SEGMENT_TYPES:
        raise ValueError(f"Zeile {row_number}: Segment-Typ ist ungueltig.")
    start_time = _time_from_row(row, "Beginn", row_number)
    end_time = _time_from_row(row, "Ende", row_number, required=False)
    source = _clean_cell(_row_value(row, "Quelle")).upper() or "MANUAL"
    if source not in {"AUTO", "MANUAL"}:
        source = "MANUAL"
    location = _location_code(_row_value(row, "Standort")) or None
    if location not in {"OFFICE", "HOME", "UNKNOWN"}:
        location = None
    segment_id = _optional_int(_row_value(row, "ID"))
    affected = {date_text}
    existing = database.get_segment(conn, segment_id) if segment_id else None
    if existing and existing["date"] in replace_dates:
        affected.add(existing["date"])
        database.update_segment(
            conn,
            segment_id,
            date=date_text,
            type=segment_type,
            start_time=start_time,
            end_time=end_time,
            location=location,
            source=source,
        )
    else:
        database.add_segment(conn, date_text, segment_type, start_time, end_time=end_time, location=location, source=source)
    return affected


def _import_day_type_row(conn, row: dict[str, Any], row_number: int) -> str:
    date_text = _date_from_row(row, row_number)
    day_type = _category_code(_row_value(row, "Kategorie", "Typ"), DAY_TYPES)
    if day_type not in DAY_TYPES:
        raise ValueError(f"Zeile {row_number}: Abwesenheits-Typ ist ungueltig.")
    note = _clean_cell(_row_value(row, "Notiz")) or None
    database.upsert_day_type(conn, date_text, day_type, _bool_from_cell(_row_value(row, "Halber_Tag")), note)
    return date_text


def _import_note_row(conn, row: dict[str, Any], row_number: int) -> str:
    date_text = _date_from_row(row, row_number)
    database.replace_note(conn, date_text, _clean_cell(_row_value(row, "Notiz")))
    return date_text


def _date_from_row(row: dict[str, Any], row_number: int) -> str:
    value = _row_value(row, "Datum", "Date")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, Date):
        return value.isoformat()
    try:
        return parse_date(str(value).strip()[:10]).isoformat()
    except Exception as exc:
        raise ValueError(f"Zeile {row_number}: Datum ist ungueltig.") from exc


def _time_from_row(row: dict[str, Any], name: str, row_number: int, required: bool = True) -> str | None:
    value = _row_value(row, name)
    if value in (None, ""):
        if required:
            raise ValueError(f"Zeile {row_number}: {name} fehlt.")
        return None
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    try:
        return normalize_time_input(str(value).strip())
    except Exception as exc:
        raise ValueError(f"Zeile {row_number}: {name} ist ungueltig.") from exc


def _time_for_export(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:5]


def _display_category(value: Any) -> str:
    return CATEGORY_LABELS.get(str(value or ""), str(value or ""))


def _display_location(value: Any) -> str:
    return LOCATION_LABELS.get(str(value or ""), str(value or ""))


def _record_type_code(value: Any) -> str:
    text = _clean_cell(value).upper().replace("Ü", "UE")
    return {
        "OVERVIEW": OVERVIEW_RECORD_TYPE,
        "UEBERSICHT": OVERVIEW_RECORD_TYPE,
        "SUMMARY": "TAGES_SUMME",
        "TAGES_SUMMARY": "TAGES_SUMME",
    }.get(text, text)


def _category_code(value: Any, allowed: set[str] | None = None) -> str:
    text = _clean_cell(value)
    upper = text.upper()
    if upper in CATEGORY_LABELS and (allowed is None or upper in allowed):
        return upper
    lowered = text.casefold()
    for code, label in CATEGORY_LABELS.items():
        if lowered == label.casefold() and (allowed is None or code in allowed):
            return code
    return upper


def _location_code(value: Any) -> str:
    text = _clean_cell(value)
    upper = text.upper()
    if upper in LOCATION_LABELS:
        return upper
    lowered = text.casefold()
    for code, label in LOCATION_LABELS.items():
        if lowered == label.casefold():
            return code
    return upper


def _row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return ""


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_int(value: Any) -> int | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        text = _clean_cell(value)
        return int(text) if text else None
    except ValueError:
        return None


def _bool_from_cell(value: Any) -> bool:
    return _clean_cell(value).lower() in {"1", "true", "ja", "yes", "x"}
