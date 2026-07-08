from __future__ import annotations

from datetime import date as Date
from datetime import datetime

import pytest

from app.api import WorktimeApi
from common.balance import classify_balance, parse_decimal_hours_to_minutes
from common import calculations, database


def make_conn(tmp_path):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    conn = database.connect(db_path)
    database.set_settings(
        conn,
        {
            "weekly_target_hours": "40",
            "workdays_per_week": "5",
            "bundesland": "",
            "vacation_days_per_year": "30",
            "vacation_carry_over": "0",
            "tracking_start_date": "",
            "initial_flextime_minutes": "0",
        },
    )
    database.update_vacation_account(conn, 2026, 30, 0)
    return conn


def test_workday_break_and_balance(tmp_path):
    conn = make_conn(tmp_path)
    database.add_segment(conn, "2026-07-06", "WORK", "08:00:00", "12:00:00", "OFFICE")
    database.add_segment(conn, "2026-07-06", "BREAK", "12:00:00", "12:30:00")
    database.add_segment(conn, "2026-07-06", "WORK", "12:30:00", "17:00:00", "OFFICE")

    result = calculations.recalculate_day(conn, "2026-07-06")

    assert result.target_minutes == 480
    assert result.actual_minutes == 510
    assert result.break_minutes == 30
    assert result.balance_minutes == 30
    assert result.location == "OFFICE"
    assert calculations.get_flextime_balance(conn, "2026-07-06") == 30


def test_neutral_vacation_day_without_segments(tmp_path):
    conn = make_conn(tmp_path)
    database.upsert_day_type(conn, "2026-07-07", "URLAUB")

    result = calculations.recalculate_day(conn, "2026-07-07")

    assert result.day_category == "VACATION"
    assert result.target_minutes == 480
    assert result.actual_minutes == 480
    assert result.balance_minutes == 0
    assert calculations.get_remaining_vacation(conn, 2026) == 29


def test_half_day_vacation_credits_half_target_when_worked(tmp_path):
    conn = make_conn(tmp_path)
    database.upsert_day_type(conn, "2026-07-08", "URLAUB", half_day=True)
    database.add_segment(conn, "2026-07-08", "WORK", "08:00:00", "12:00:00", "HOME")

    result = calculations.recalculate_day(conn, "2026-07-08")

    assert result.target_minutes == 480
    assert result.actual_minutes == 480
    assert result.balance_minutes == 0
    assert result.location == "HOME"
    assert calculations.get_remaining_vacation(conn, 2026) == 29.5


def test_vacation_range_counts_only_workdays(tmp_path):
    conn = make_conn(tmp_path)
    for day in calculations.daterange(Date(2026, 7, 6), Date(2026, 7, 19)):
        database.upsert_day_type(conn, day.isoformat(), "URLAUB", note="Sommerurlaub")

    assert calculations.get_day_type_days(conn, 2026, "URLAUB", "2026-07-06", "2026-07-19") == 10
    assert calculations.get_remaining_vacation(conn, 2026) == 20


def test_absence_api_groups_range_with_note_and_counted_days(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    api.save_settings({"weekly_target_hours": "40", "workdays_per_week": "5", "bundesland": ""})

    api.add_day_type_range(
        {
            "start_date": "2026-07-06",
            "end_date": "2026-07-19",
            "type": "URLAUB",
            "half_day": False,
            "note": "Sommerurlaub",
        }
    )

    row = next(item for item in api.absences(2026)["rows"] if item["type"] == "URLAUB")

    assert row["start_date"] == "2026-07-06"
    assert row["end_date"] == "2026-07-19"
    assert row["days"] == 14
    assert row["counted_days"] == 10
    assert row["note"] == "Sommerurlaub"


def test_absence_api_deletes_grouped_range(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    api.add_day_type_range(
        {
            "start_date": "2026-07-06",
            "end_date": "2026-07-08",
            "type": "GLEITZEITTAG",
            "half_day": False,
            "note": "Ausgleich",
        }
    )
    row = next(item for item in api.absences(2026)["rows"] if item["type"] == "GLEITZEITTAG")

    result = api.delete_day_type_range({"ids": row["ids"]})

    assert result["ok"] is True
    assert not [item for item in api.absences(2026)["rows"] if item["type"] == "GLEITZEITTAG"]


def test_day_note_is_visible_in_calendar_and_entries(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    api.save_segment(
        {
            "date": "2026-07-06",
            "type": "WORK",
            "start_time": "08:00",
            "end_time": "16:00",
            "location": "HOME",
            "source": "MANUAL",
        }
    )

    api.save_note("2026-07-06", "Kundentermin")

    calendar_day = next(day for day in api.calendar_month(2026, 7)["days"] if day["date"] == "2026-07-06")
    entry = next(row for row in api.entries("2026-07-01", "2026-07-31")["rows"] if row["date"] == "2026-07-06")

    assert api.day_detail("2026-07-06")["note"] == "Kundentermin"
    assert calendar_day["note"] == "Kundentermin"
    assert entry["note"] == "Kundentermin"


def test_location_has_no_effect_on_balance(tmp_path):
    conn = make_conn(tmp_path)
    database.add_segment(conn, "2026-07-09", "WORK", "08:00:00", "16:00:00", "HOME")

    result = calculations.recalculate_day(conn, "2026-07-09")

    assert result.actual_minutes == 480
    assert result.balance_minutes == 0
    assert result.location == "HOME"


def test_month_closing_is_updated(tmp_path):
    conn = make_conn(tmp_path)
    database.add_segment(conn, "2026-07-06", "WORK", "08:00:00", "17:00:00", "OFFICE")

    calculations.recalculate_day(conn, "2026-07-06")
    closing = database.get_month_closing(conn, "2026-07")

    assert closing["target_minutes"] == 480
    assert closing["actual_minutes"] == 540
    assert closing["balance_minutes"] == 60
    assert closing["carry_over_minutes"] == 60


def test_open_segment_is_not_counted_in_flextime_account(tmp_path):
    conn = make_conn(tmp_path)
    database.add_segment(conn, "2026-07-06", "WORK", "08:00:00", None, "OFFICE")

    result = calculations.recalculate_day(conn, "2026-07-06", now=datetime(2026, 7, 6, 10, 0, 0))
    closing = database.get_month_closing(conn, "2026-07")

    assert result.balance_minutes == -360
    assert calculations.get_flextime_balance(conn, "2026-07-06") == 0
    assert closing["balance_minutes"] == 0
    assert closing["carry_over_minutes"] == 0


def test_configured_break_time_is_subtracted_from_single_work_segment(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(conn, {"weekly_target_hours": "39", "daily_break_minutes": "45"})
    database.add_segment(conn, "2026-07-06", "WORK", "07:00:00", "15:33:00", "OFFICE")

    result = calculations.recalculate_day(conn, "2026-07-06")

    assert result.target_minutes == 468
    assert result.actual_minutes == 468
    assert result.break_minutes == 45
    assert result.balance_minutes == 0


def test_explicit_break_is_not_subtracted_twice(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(conn, {"weekly_target_hours": "39", "daily_break_minutes": "45"})
    database.add_segment(conn, "2026-07-06", "WORK", "07:00:00", "12:00:00", "OFFICE")
    database.add_segment(conn, "2026-07-06", "BREAK", "12:00:00", "12:45:00")
    database.add_segment(conn, "2026-07-06", "WORK", "12:45:00", "15:33:00", "OFFICE")

    result = calculations.recalculate_day(conn, "2026-07-06")

    assert result.target_minutes == 468
    assert result.actual_minutes == 468
    assert result.break_minutes == 45
    assert result.balance_minutes == 0


def test_explicit_break_longer_than_configured_is_counted(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(conn, {"weekly_target_hours": "39", "daily_break_minutes": "45"})
    database.add_segment(conn, "2026-07-06", "WORK", "07:00:00", "12:00:00", "OFFICE")
    database.add_segment(conn, "2026-07-06", "BREAK", "12:00:00", "13:30:00")
    database.add_segment(conn, "2026-07-06", "WORK", "13:30:00", "16:00:00", "OFFICE")

    result = calculations.recalculate_day(conn, "2026-07-06")

    assert result.target_minutes == 468
    assert result.actual_minutes == 450
    assert result.break_minutes == 90
    assert result.balance_minutes == -18


def test_selected_workdays_drive_daily_target(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(conn, {"weekly_target_hours": "21", "workday_weekdays": "0,2,4"})

    monday = calculations.compute_day("2026-07-06", [], [], database.get_settings(conn))
    tuesday = calculations.compute_day("2026-07-07", [], [], database.get_settings(conn))
    friday = calculations.compute_day("2026-07-10", [], [], database.get_settings(conn))

    assert monday.target_minutes == 420
    assert tuesday.target_minutes == 0
    assert friday.target_minutes == 420


def test_location_statistics_include_manual_baseline_and_mixed_days(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-07-01",
            "office_baseline_days": "2",
            "homeoffice_baseline_days": "1",
        },
    )
    database.add_segment(conn, "2026-07-01", "WORK", "08:00:00", "16:00:00", "OFFICE")
    database.add_segment(conn, "2026-07-02", "WORK", "08:00:00", "16:00:00", "HOME")
    database.add_segment(conn, "2026-07-03", "WORK", "08:00:00", "12:00:00", "OFFICE")
    database.add_segment(conn, "2026-07-03", "WORK", "13:00:00", "16:00:00", "HOME")

    calculations.recalculate_range(conn, "2026-07-01", "2026-07-03")
    stats = calculations.get_location_statistics(conn, "2026-07-03")

    assert stats["tracked_office_days"] == 1
    assert stats["tracked_homeoffice_days"] == 1
    assert stats["tracked_mixed_days"] == 1
    assert stats["office_days"] == 3.5
    assert stats["homeoffice_days"] == 2.5
    assert stats["office_percent"] == 58.3
    assert stats["office_requirement_met"] is True


def test_location_statistics_marks_under_50_percent_office(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-07-01",
            "office_baseline_days": "1",
            "homeoffice_baseline_days": "3",
        },
    )

    stats = calculations.get_location_statistics(conn, "2026-07-03")

    assert stats["office_percent"] == 25.0
    assert stats["office_requirement_met"] is False


def test_tracking_start_date_ignores_days_before_start(tmp_path):
    conn = make_conn(tmp_path)
    database.set_setting(conn, "tracking_start_date", "2026-07-01")

    calculations.recalculate_range(conn, "2026-01-01", "2026-06-30")
    january = database.get_month_closing(conn, "2026-01")
    june = database.get_month_closing(conn, "2026-06")
    summary = database.get_day_summary(conn, "2026-01-05")

    assert summary["day_category"] == "NOT_TRACKED"
    assert summary["target_minutes"] == 0
    assert summary["balance_minutes"] == 0
    assert january["balance_minutes"] == 0
    assert june["carry_over_minutes"] == 0


def test_blank_start_date_uses_first_real_record_for_statistics(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    today = Date.today()
    today_text = today.isoformat()
    stale_date = f"{today.year}-01-05"
    with database.connect(api.db_path) as conn:
        database.set_settings(
            conn,
            {
                "weekly_target_hours": "40",
                "workdays_per_week": "5",
                "bundesland": "",
                "tracking_start_date": "",
                "initial_flextime_minutes": "0",
            },
        )
        database.upsert_day_summary(conn, stale_date, 480, 0, 0, -480, "WORKDAY", None)
        database.upsert_month_closing(conn, stale_date[:7], 480, 0, -480, -480, 0.0, 0.0, 0)
        database.add_segment(conn, today_text, "WORK", "08:00:00", "16:00:00", "OFFICE")

    stats = api.statistics(today.year)

    assert stats["balance_minutes"] >= 0
    assert stats["target_minutes"] <= 480
    assert all(month["year_month"] >= today_text[:7] for month in stats["months"])


def test_positive_initial_balance_is_added_once(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-07-01",
            "initial_flextime_minutes": str(parse_decimal_hours_to_minutes("12,5")),
        },
    )
    database.add_segment(conn, "2026-07-01", "WORK", "08:00:00", "16:00:00", "OFFICE")

    calculations.recalculate_day(conn, "2026-07-01")

    assert calculations.get_flextime_balance(conn, "2026-07-01") == 750


def test_negative_initial_balance_is_applied(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-07-01",
            "initial_flextime_minutes": str(parse_decimal_hours_to_minutes("-3.75")),
        },
    )
    database.add_segment(conn, "2026-07-01", "WORK", "08:00:00", "16:00:00", "OFFICE")

    calculations.recalculate_day(conn, "2026-07-01")

    assert calculations.get_flextime_balance(conn, "2026-07-01") == -225


def test_decimal_hour_parser_accepts_comma_and_dot():
    assert parse_decimal_hours_to_minutes("50,89") == 3053
    assert parse_decimal_hours_to_minutes("50.89") == 3053
    assert parse_decimal_hours_to_minutes("12,5") == 750
    assert parse_decimal_hours_to_minutes("0") == 0


def test_decimal_hour_parser_rejects_invalid_values():
    with pytest.raises(ValueError, match="muss eine Zahl sein"):
        parse_decimal_hours_to_minutes("nicht-gut", "Anfangssaldo")


def test_balance_status_thresholds():
    assert classify_balance(-1).css_class == "balance-negative"
    assert classify_balance(0).css_class == "balance-ok"
    assert classify_balance(45 * 60).css_class == "balance-ok"
    assert classify_balance(45 * 60 + 1).css_class == "balance-warning"
    assert classify_balance(50 * 60).css_class == "balance-warning"
    assert classify_balance(50 * 60 + 1).css_class == "balance-danger"


def test_month_closing_does_not_add_initial_balance_multiple_times(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-07-01",
            "initial_flextime_minutes": str(parse_decimal_hours_to_minutes("10")),
        },
    )
    database.add_segment(conn, "2026-07-01", "WORK", "08:00:00", "17:00:00", "OFFICE")

    calculations.recalculate_day(conn, "2026-07-01")
    closing = database.get_month_closing(conn, "2026-07")

    assert closing["balance_minutes"] == 60
    assert closing["carry_over_minutes"] == 660


def test_settings_change_recalculates_existing_summaries(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    with database.connect(api.db_path) as conn:
        database.set_settings(
            conn,
            {
                "weekly_target_hours": "40",
                "workdays_per_week": "5",
                "bundesland": "",
                "tracking_start_date": "2026-01-01",
            },
        )
        calculations.recalculate_day(conn, "2026-01-05")
        assert database.get_day_summary(conn, "2026-01-05")["balance_minutes"] == -480

    api.save_settings({"tracking_start_date": "2026-02-01", "initial_flextime_hours": "0"})

    with database.connect(api.db_path) as conn:
        summary = database.get_day_summary(conn, "2026-01-05")
        assert summary["day_category"] == "NOT_TRACKED"
        assert summary["balance_minutes"] == 0


def test_reset_all_clears_data_and_requires_setup(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    with database.connect(api.db_path) as conn:
        database.add_segment(conn, "2026-07-06", "WORK", "08:00:00", "17:00:00", "OFFICE")
        database.upsert_day_type(conn, "2026-07-07", "URLAUB", note="Test")
        database.replace_note(conn, "2026-07-06", "Notiz")
        calculations.recalculate_range(conn, "2026-07-06", "2026-07-07")

    result = api.reset_application({"mode": "all"})

    assert result["settings"]["initial_setup_required"] == "1"
    with database.connect(api.db_path) as conn:
        assert database.get_segments_between(conn, "2026-01-01", "2026-12-31") == []
        assert database.get_day_types_between(conn, "2026-01-01", "2026-12-31") == []
        assert database.get_notes_between(conn, "2026-01-01", "2026-12-31") == {}
        assert database.get_setting(conn, "weekly_target_hours") == "40"


def test_year_change_with_tracking_start_and_initial_balance(tmp_path):
    conn = make_conn(tmp_path)
    database.set_settings(
        conn,
        {
            "tracking_start_date": "2026-12-31",
            "initial_flextime_minutes": str(parse_decimal_hours_to_minutes("1")),
        },
    )
    database.add_segment(conn, "2026-12-31", "WORK", "08:00:00", "17:00:00", "OFFICE")
    database.add_segment(conn, "2027-01-04", "WORK", "08:00:00", "16:00:00", "HOME")

    calculations.recalculate_range(conn, "2026-12-01", "2027-01-31")

    assert database.get_day_summary(conn, "2026-12-30")["balance_minutes"] == 0
    assert database.get_month_closing(conn, "2026-12")["carry_over_minutes"] == 120
    assert database.get_month_closing(conn, "2027-01")["carry_over_minutes"] <= 120
