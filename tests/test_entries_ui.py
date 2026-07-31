from __future__ import annotations

from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"
HOW_TO_USE = Path(__file__).resolve().parents[1] / "HOW_TO_USE.md"


def test_entries_view_exposes_filter_controls():
    script = APP_JS.read_text(encoding="utf-8")

    for snippet in (
        "entry-filter-day-type",
        "entry-filter-location",
        "entry-filter-balance",
        "entry-filter-status",
        "entry-filter-note",
        "entry-filter-min-hours",
        "entry-filter-max-hours",
        "entryMatchesFilters",
        "entryMatchesStatus",
        "entryMatchesNote",
    ):
        assert snippet in script


def test_entries_filter_docs_are_kept_in_manual_test_plan():
    guide = HOW_TO_USE.read_text(encoding="utf-8")

    assert "In `Einträge` kannst du die Liste zusätzlich filtern" in guide
    assert "Tagesart, Standort, Saldo, laufende/abgeschlossene Tage, Notizen" in guide
    assert "Filter für Tagesart, Standort, Saldo, Status, Notizen sowie Arbeitszeit testen" in guide
