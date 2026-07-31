from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from datetime import timedelta

from app.api import WorktimeApi, _live_day_info
from common import database


def test_api_saves_popup_settings(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings(
        {
            "work_start_popup_mode": "on",
            "work_end_popup_mode": "always",
            "work_popup_timing": "custom",
            "work_popup_custom_time": "07:15",
            "daily_info_popup_mode": "work_end",
            "daily_info_popup_time": "16:45",
        }
    )

    assert result["settings"]["work_start_popup_mode"] == "on"
    assert result["settings"]["work_end_popup_mode"] == "always"
    assert result["settings"]["work_popup_custom_time"] == "07:15"


def test_api_saves_end_buffer_settings(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings(
        {
            "office_end_buffer_minutes": "3",
            "home_end_buffer_minutes": "2",
        }
    )

    assert result["settings"]["office_end_buffer_minutes"] == "3"
    assert result["settings"]["home_end_buffer_minutes"] == "2"


def test_api_saves_dashboard_absence_countdown_mode(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings({"dashboard_absence_countdown_mode": "calendar_days"})

    assert result["settings"]["dashboard_absence_countdown_mode"] == "calendar_days"


def test_api_saves_auto_refresh_interval(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings({"auto_refresh_interval_seconds": "300"})

    assert result["settings"]["auto_refresh_interval_seconds"] == "300"


def test_api_saves_office_quota_and_preload_settings(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings(
        {
            "office_quota_target_percent": "60,5",
            "office_quota_period_mode": "rolling_365",
            "preload_app_on_tracker_start": "0",
        }
    )

    assert result["settings"]["office_quota_target_percent"] == "60.5"
    assert result["settings"]["office_quota_period_mode"] == "rolling_365"
    assert result["settings"]["preload_app_on_tracker_start"] == "0"


def test_live_day_zero_time_includes_remaining_minimum_break():
    result = _live_day_info(
        balance_minutes=-468,
        target_minutes=468,
        break_minutes=5,
        settings={"daily_break_minutes": "45"},
        open_segment={"type": "WORK"},
        has_work_today=True,
        now=datetime(2026, 7, 6, 8, 5),
    )

    assert result["zero_time"] == "16:33"


def test_dashboard_reports_next_absence_countdown_with_workdays(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)
    api.save_settings(
        {
            "workday_weekdays": "0,1,2,3,4",
            "dashboard_absence_countdown_mode": "workdays",
            "bundesland": "",
        }
    )
    today = Date.today()
    absence_date = today + timedelta(days=7)
    with database.connect(db_path) as conn:
        database.upsert_day_type(conn, absence_date.isoformat(), "URLAUB", note="Sommerurlaub")

    result = api.dashboard()
    expected_workdays = sum(
        1
        for offset in range((absence_date - today).days)
        if (today + timedelta(days=offset)).weekday() in {0, 1, 2, 3, 4}
    )

    assert result["next_absence"]["date"] == absence_date.isoformat()
    assert result["next_absence"]["type"] == "URLAUB"
    assert result["next_absence"]["calendar_days"] == 7
    assert result["next_absence"]["workdays"] == expected_workdays
    assert result["next_absence"]["display_days"] == expected_workdays
    assert result["next_absence"]["note"] == "Sommerurlaub"


def test_dashboard_reports_next_absence_countdown_with_calendar_days(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)
    api.save_settings(
        {
            "workday_weekdays": "0,1,2,3,4",
            "dashboard_absence_countdown_mode": "calendar_days",
            "bundesland": "",
        }
    )
    today = Date.today()
    absence_date = today + timedelta(days=10)
    with database.connect(db_path) as conn:
        database.upsert_day_type(conn, absence_date.isoformat(), "URLAUB", note="Herbsturlaub")

    result = api.dashboard()

    assert result["next_absence"]["date"] == absence_date.isoformat()
    assert result["next_absence"]["calendar_days"] == 10
    assert result["next_absence"]["display_days"] == 10
    assert result["next_absence"]["display_label"] == "Kalendertage"
