from __future__ import annotations

from pathlib import Path

from app.api import WorktimeApi
from app import instance


APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"
APP_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"
INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"


def test_calculator_view_is_registered_in_single_instance_commands():
    assert instance.normalize_view("calculator") == "calculator"


def test_calculator_defaults_come_from_work_settings(tmp_path):
    api = WorktimeApi(tmp_path / "database.db")
    api.save_settings(
        {
            "weekly_target_hours": "39",
            "workday_weekdays": "0,1,2,3,4",
            "daily_break_minutes": "45",
            "bundesland": "",
        }
    )

    defaults = api.calculator_defaults()

    assert defaults["daily_break_minutes"] == 45
    assert defaults["workday_weekdays"] == [0, 1, 2, 3, 4]
    assert defaults["target_minutes_by_weekday"]["0"] == 468
    assert defaults["target_minutes_by_weekday"]["4"] == 468
    assert defaults["target_minutes_by_weekday"]["5"] == 0


def test_calculator_navigation_and_route_exist():
    html = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")
    styles = APP_CSS.read_text(encoding="utf-8")

    assert 'data-view="calculator"' in html
    assert "Arbeitszeit Rechner" in html
    assert '"calculator", "settings"' in script
    assert 'state.view === "calculator"' in script
    assert "renderCalculator()" in script
    assert "calculator_defaults" in script
    assert "fillCalculatorWeekRows" in script
    assert "nextCalculatorWorkdayDate" in script
    assert "workdays.has(weekdayIndexFromDate(candidate))" in script
    assert 'state.calculatorRows = [createCalculatorRow(defaults.today || isoToday())]' in script
    assert "calculator-action-row" in script
    assert "calculator-plan-head" in script
    assert "target_balance" in script
    assert "Ende aus Gleitzeit" in script
    assert "desiredBalanceUnit" in script
    assert "parseBalanceTargetMinutes" in script
    assert "formatBalanceTargetInput" in script
    assert "Std:Min" in script
    assert "Min</option>" in script
    assert ".calculator-row-card" in styles
    assert ".calculator-action-row" in styles
    assert ".calculator-plan-head" in styles
    assert ".calculator-unit-field" in styles
    assert "minmax(168px, 1.25fr)" in styles
    assert "container-type: inline-size" in styles
    assert ".calculator-row-card > *" in styles
    assert ".calculator-row-card label" in styles
    assert "button.calculator-remove" in styles
    assert "button.calculator-remove:hover:not(:disabled)" in styles
    assert "color: var(--danger)" in styles
    assert "background: var(--danger)" in styles
    assert "color: #ffffff" in styles
    assert ".nav-calculator .nav-icon" in styles
    assert script.index('<section class="panel calculator-table-panel">') < script.index('<div class="calculator-action-row">')


def test_command_polling_reacts_quickly_for_preloaded_app():
    script = APP_JS.read_text(encoding="utf-8")

    assert "setInterval(checkAppCommand, 250)" in script
