from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from app.api import WorktimeApi, _live_day_info
from common import database


APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"
APP_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"


def test_settings_categories_are_split_and_sap_help_explains_source_tables():
    script = APP_JS.read_text(encoding="utf-8")

    for label in (
        "Urlaub & Feiertage",
        "Gleitzeit-Startwerte",
        "Tracking-Automatik",
        "App & Aktualisierung",
        "Standortcheck",
        "Officequote",
        "Zeitpuffer",
        "E-Mail",
        "Backup, Import & Export",
    ):
        assert label in script
    assert "Standort & Puffer" not in script
    assert "EDIDC" in script
    assert "EDID4" in script
    assert "HRCC1UPTEVEN" in script
    assert "*02634" not in script


def test_vacation_and_popup_settings_use_conditional_editors():
    script = APP_JS.read_text(encoding="utf-8")

    assert "Standard-Abwesenheiten jedes Jahr" in script
    assert "Regel hinzufügen" in script
    assert "standard_absence_rules" in script
    assert "standard-rules-body" in script
    assert "Erinnerung an ungenehmigte Abwesenheiten" in script
    assert 'data-show-when="absence_reminder_mode:startup|work_end|custom"' in script
    assert 'data-show-when="absence_reminder_mode:custom"' in script
    assert "Datumsformat: 24.12. oder 24.12.-31.12." in script
    assert "standard-field-label" in script
    assert "standard-hidden-date-picker" not in script
    assert "showPicker" not in script


def test_email_template_settings_and_copy_buttons_are_available(tmp_path, monkeypatch):
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")
    api = WorktimeApi(tmp_path / "database.db")

    api.save_settings(
        {
            "pa_email_template": (
                "Hallo\n"
                "{wochentag}, {datum}\n"
                "{segmente}\n"
                "Ist {arbeitszeit}, Pause {pause}, Saldo {saldo}"
            )
        }
    )
    api.save_segment(
        {
            "date": "2026-07-06",
            "type": "WORK",
            "start_time": "06:45",
            "end_time": "12:00",
            "location": "HOME",
            "source": "MANUAL",
        }
    )
    api.save_segment(
        {
            "date": "2026-07-06",
            "type": "BREAK",
            "start_time": "12:00",
            "end_time": "12:45",
            "source": "MANUAL",
        }
    )
    api.save_segment(
        {
            "date": "2026-07-06",
            "type": "WORK",
            "start_time": "12:45",
            "end_time": "17:00",
            "location": "HOME",
            "source": "MANUAL",
        }
    )

    draft = api.homeoffice_email("2026-07-06")

    assert "pa_email_template" in script
    assert "copy-day-email" in script
    assert "copy_to_clipboard" in script
    assert "homeoffice_email" in script
    assert "E-Mail kopieren" in script
    assert ".entry-email-button" in styles
    assert "Montag, 06.07.2026" in draft["body"]
    assert "Arbeit: 06:45 bis 12:00 Uhr" in draft["body"]
    assert "Pause: 12:00 bis 12:45 Uhr" in draft["body"]
    assert "Arbeit: 12:45 bis 17:00 Uhr" in draft["body"]
    assert "Ist 9:30, Pause 0:45, Saldo +1:30" in draft["body"]

    copied = []
    monkeypatch.setattr("app.api._copy_text_to_clipboard", lambda text: copied.append(text))
    assert api.copy_to_clipboard("Test") == {"ok": True}
    assert copied == ["Test"]


def test_day_edits_save_all_segments_and_note_together(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")

    first = api.save_segment(
        {
            "date": "2026-07-06",
            "type": "WORK",
            "start_time": "08:00",
            "end_time": "12:00",
            "location": "HOME",
            "source": "MANUAL",
        }
    )["segments"][0]
    second = api.save_segment(
        {
            "date": "2026-07-06",
            "type": "WORK",
            "start_time": "13:00",
            "end_time": "16:00",
            "location": "HOME",
            "source": "MANUAL",
        }
    )["segments"][1]

    detail = api.save_day_edits(
        "2026-07-06",
        {
            "segments": [
                {
                    "id": first["id"],
                    "type": "WORK",
                    "start_time": "06:45",
                    "end_time": "12:00",
                    "location": "OFFICE",
                    "source": "MANUAL",
                },
                {
                    "id": second["id"],
                    "type": "WORK",
                    "start_time": "12:45",
                    "end_time": "17:00",
                    "location": "OFFICE",
                    "source": "MANUAL",
                },
            ],
            "note": "Alles gemeinsam gespeichert",
        },
    )

    assert detail["segments"][0]["start_time"] == "06:45:00"
    assert detail["segments"][0]["location"] == "OFFICE"
    assert detail["segments"][1]["start_time"] == "12:45:00"
    assert detail["segments"][1]["end_time"] == "17:00:00"
    assert detail["note"] == "Alles gemeinsam gespeichert"


def test_calendar_view_uses_compact_responsive_layout():
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    assert "calendar-view" in script
    assert "VIEW_CLASSES" in script
    assert ".shell.calendar-view .content" in styles
    assert "clamp(58px, calc((100vh - 226px) / 6), 84px)" in styles
    assert "repeat(12, minmax(48px, 1fr))" in styles


def test_api_accepts_german_standard_absence_dates(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")

    result = api.save_settings(
        {
            "standard_absence_rules": '[{"date":"24.12.","type":"URLAUB","half_day":true,"note":"Heiligabend"}]'
        }
    )

    assert '"date":"12-24"' in result["settings"]["standard_absence_rules"]
    assert '"end_date":""' in result["settings"]["standard_absence_rules"]


def test_api_accepts_german_standard_absence_date_ranges(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")

    result = api.save_settings(
        {
            "standard_absence_rules": '[{"date":"24.12.","end_date":"31.12.","type":"URLAUB","half_day":false,"note":"Betriebsruhe"}]'
        }
    )

    assert '"date":"12-24"' in result["settings"]["standard_absence_rules"]
    assert '"end_date":"12-31"' in result["settings"]["standard_absence_rules"]


def test_settings_subnavigation_preserves_scroll_position_between_categories():
    script = APP_JS.read_text(encoding="utf-8")

    assert "settingsSubnavScrollTop" in script
    assert "rememberSettingsSubnavScroll()" in script
    assert "restoreSettingsSubnavScroll()" in script
    assert "subnav.scrollTop = state.settingsSubnavScrollTop || 0" in script
    assert "state.settingsSubnavScrollTop = 0" in script


def test_dashboard_office_quota_detail_uses_compact_period_cards():
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    assert "officeQuotaDetail(data.location_stats)" in script
    assert "officeQuotaSummaryCard" in script
    assert "officeQuotaPeriodCard" in script
    assert "officeQuotaPeriodHint" in script
    assert "officeQuotaStatus" in script
    assert "quota-warning" in script
    assert "--office-share" in script
    assert "--quota-target" in script
    assert "Büro im Zeitraum" in script
    assert "Homeoffice im Zeitraum" in script
    assert "comparison_periods" in script
    assert "Aktuelle Einstellung" in script
    assert "officeBaselinePeriodLabel" in script
    assert "Zeitraum der manuellen Tage" in script
    assert "office_baseline_period_mode" in script
    assert ".office-quota-overview" in styles
    assert ".office-quota-period-card" in styles
    assert ".office-quota-bar" in styles
    assert ".office-quota-bar::after" in styles
    assert ".office-quota-bar::before" in styles
    assert "left: var(--office-share" in styles
    assert "translate(-50%, -50%)" in styles
    assert "var(--quota-warning-start" in styles
    assert "var(--quota-target" in styles
    assert ".office-quota-period-card:hover .office-quota-bar::after" in styles


def test_dashboard_other_details_use_insight_cards_and_trends():
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    for helper in (
        "todayWorkDetail(data)",
        "todayBreakDetail(data)",
        "liveDayDetail(data)",
        "flextimeDetail(data)",
        "vacationDetail(data)",
        "dateDetail(data)",
        "dashboardInsightCard",
        "dashboardProgress",
        "flextimePeriodCard",
    ):
        assert helper in script
    assert "Rest netto" in script
    assert "Mindestpause" in script
    assert "30 Tage" in script
    assert "Nächster Urlaub" in script
    assert ".dashboard-insight-card" in styles
    assert ".dashboard-period-card" in styles
    assert ".dashboard-progress-track" in styles
    assert ".dashboard-insight-card:hover" in styles
    assert ".dashboard-period-card:hover" in styles
    assert ".dashboard-progress:hover" in styles


def test_segment_details_default_to_view_mode_and_only_edit_mode_blocks_refresh():
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    assert "calendarSegmentEditDate" in script
    assert "entrySegmentEditDate" in script
    assert "detail-mode-toggle" in script
    assert "detail-view-mode" in script
    assert "detail-edit-mode" in script
    assert "Abbrechen" in script
    assert "Ungespeicherte Änderungen verwerfen" in script
    assert "updateDetailModeButtonLabel" in script
    assert "dayDetailHeading(date)" in script
    assert "dayDetailHeading(dateText)" in script
    assert "weekday ? `${weekday} - ${dateText} - Details`" in script
    assert "close-day-detail" in script
    assert "close-entry-editor" not in script
    assert "entry-email-button" in script
    entry_editor = script.split("async function renderEntryEditor", 1)[1].split("function bindEmailCopyButtons", 1)[0]
    assert "copy-day-email" not in entry_editor
    assert "E-Mail kopieren" not in entry_editor
    assert "canLeaveDetailEditMode" in script
    assert "Bitte Änderungen zuerst speichern." in script
    assert "save_day_edits" in script
    assert "Änderungen speichern" in script
    assert "save-segment" not in script
    assert "Notiz speichern" not in script
    assert "segment-remove" in script
    assert "segment-edit-table" in script
    assert 'aria-label="Aktionen"' in script
    assert "button.segment-remove" in styles
    assert ".segment-edit-table col.segment-col-actions" in styles
    assert "width: 62px" in styles
    assert "if (!canLeaveDetailEditMode(document)) return false;" in script
    assert "if (!canLeaveDetailEditMode(document)) return;" in script
    assert 'nextMode === "view" && !canLeaveDetailEditMode' not in script
    assert "state.calendarSegmentEditDate || state.entrySegmentEditDate" in script
    assert "state.calendarDetailDate || state.entryEditDate" not in script
    assert "renderSegmentTable(detail.segments, date, { editable: false })" in script
    assert "renderSegmentTable(detail.segments, date, { editable: true })" in script
    assert ".view-mode-hint" not in styles


def test_statistics_uses_readable_month_progress_instead_of_canvas_chart():
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    assert "statsTrendPanel(data.months)" in script
    assert "statsMonthComparisonRow" in script
    assert "Monatsfortschritt" in script
    assert "100 % entspricht dem Soll des Monats" in script
    assert "--progress-width" in script
    assert "Büro" in script
    assert "Gleitzeit" in script
    assert "loadChartLibrary" not in script
    assert "drawStatsChart" not in script
    assert "stats-chart" not in script
    assert ".stats-trend-panel" in styles
    assert ".stats-month-comparison" in styles
    assert ".stats-progress-track" in styles
    assert ".stats-month-comparison.complete" in styles
    assert ".stats-month-comparison:hover" in styles


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


def test_auto_refresh_recovers_from_hidden_windows_and_stale_guard():
    script = APP_JS.read_text(encoding="utf-8")

    assert "manualRefreshCurrentView" in script
    assert "resetAutoRefreshGuard()" in script
    assert "autoRefreshStartedAt" in script
    assert "autoRefreshDueAt" in script
    assert "checkAutoRefreshDue()" in script
    assert "scheduleNextAutoRefresh()" in script
    assert "autoRefreshIntervalMs()" in script
    assert "isAutoRefreshStale()" in script
    assert "visibilitychange" in script
    assert "pageshow" in script
    assert "Auto-Refresh hing fest" in script
    assert "if (document.hidden) return false" not in script


def test_export_defaults_cover_all_real_local_data_and_future_absence(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)
    planned_absence = Date.today() + timedelta(days=35)
    with database.connect(db_path) as conn:
        database.add_segment(conn, "2026-01-05", "WORK", "08:00:00", "16:00:00", "OFFICE")
        database.upsert_day_type(conn, planned_absence.isoformat(), "URLAUB", note="Geplant")

    result = api.export_defaults()

    assert result["start_date"] == "2026-01-05"
    assert result["end_date"] == planned_absence.isoformat()


def test_api_saves_office_quota_and_preload_settings(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)

    result = api.save_settings(
        {
            "office_quota_target_percent": "60,5",
            "office_quota_period_mode": "rolling_365",
            "office_baseline_period_mode": "current_year",
            "office_quota_mixed_day_mode": "homeoffice",
            "preload_app_on_tracker_start": "0",
        }
    )

    assert result["settings"]["office_quota_target_percent"] == "60.5"
    assert result["settings"]["office_quota_period_mode"] == "rolling_365"
    assert result["settings"]["office_baseline_period_mode"] == "current_year"
    assert result["settings"]["office_quota_mixed_day_mode"] == "homeoffice"
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


def test_dashboard_returns_rich_stats_for_detail_panels(tmp_path):
    db_path = tmp_path / "database.db"
    api = WorktimeApi(db_path)
    today = Date.today()
    api.save_settings(
        {
            "tracking_start_date": (today - timedelta(days=40)).isoformat(),
            "daily_break_minutes": "45",
            "bundesland": "",
        }
    )
    with database.connect(db_path) as conn:
        database.update_vacation_account(conn, today.year, 30, 2)
        database.upsert_day_summary(
            conn,
            (today - timedelta(days=2)).isoformat(),
            468,
            500,
            45,
            32,
            "WORKDAY",
            "OFFICE",
        )
        database.upsert_day_summary(
            conn,
            (today - timedelta(days=1)).isoformat(),
            468,
            430,
            45,
            -38,
            "WORKDAY",
            "HOME",
        )
        database.upsert_day_type(conn, (today + timedelta(days=7)).isoformat(), "URLAUB", note="Testurlaub")

    result = api.dashboard()

    assert result["today_detail"]["minimum_break_minutes"] == 45
    assert result["today_detail"]["remaining_work_minutes"] >= 0
    assert result["live_day"]["remaining_work_minutes"] == result["today_detail"]["remaining_work_minutes"]
    assert {period["key"] for period in result["flextime_trends"]} == {"last_7", "last_30", "month", "year"}
    assert result["vacation_stats"]["entitlement_days"] == 30.0
    assert result["vacation_stats"]["carry_over_days"] == 2.0
    assert result["vacation_stats"]["next_vacation"]["note"] == "Testurlaub"
