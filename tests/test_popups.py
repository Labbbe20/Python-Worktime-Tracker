from __future__ import annotations

import subprocess
import threading

from common import database
from common.models import today_str
from tracker import popups
from tracker.recorder import WorktimeRecorder


def test_info_popup_uses_subprocess_from_background_thread(monkeypatch):
    calls = []

    class FakeProcess:
        pass

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(popups.subprocess, "Popen", fake_popen)
    result = {}

    thread = threading.Thread(target=lambda: result.update(ok=popups._show_info_popup("Tagesstand", "Alles gut")))
    thread.start()
    thread.join(timeout=2)

    assert result["ok"] is True
    assert calls
    assert calls[0][0][0][1] == "-c"


def test_time_dialog_uses_subprocess_from_background_thread(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout='{"value": "07:15"}\n')

    monkeypatch.setattr(popups.subprocess, "run", fake_run)
    result = {}

    thread = threading.Thread(
        target=lambda: result.update(
            value=popups._time_dialog(
                title="Arbeitsbeginn prüfen",
                message="Bitte prüfen",
                initial_time="07:00",
                primary_label="Speichern",
                secondary_label="Nicht speichern",
                secondary_value=None,
            )
        )
    )
    thread.start()
    thread.join(timeout=2)

    assert result["value"] == "07:15"
    assert calls
    assert calls[0][0][0][1] == "-c"


def test_day_info_message_is_split_into_dashboard_sections():
    data = popups._parse_day_info_message(
        "\n".join(
            [
                "Tagesstand 2026-07-24",
                "Arbeitszeit: 7:30",
                "Pause: 0:45",
                "Soll: 8:00",
                "Saldo heute: -0:30",
                "",
                "Segmente:",
                "- WORK: 08:00 bis 12:00",
                "- BREAK: 12:00 bis 12:45",
            ]
        )
    )

    assert data["date"] == "2026-07-24"
    assert data["metrics"][0] == {"label": "Arbeitszeit", "value": "7:30", "tone": "primary"}
    assert data["metrics"][3] == {"label": "Saldo heute", "value": "-0:30", "tone": "danger"}
    assert data["segments"][0] == {"label": "Arbeit", "time": "08:00 bis 12:00", "tone": "primary"}
    assert data["segments"][1] == {"label": "Pause", "time": "12:00 bis 12:45", "tone": "warning"}


def test_work_end_popup_always_can_update_last_closed_segment(tmp_path, monkeypatch):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)
    today = today_str()

    with database.connect(db_path) as conn:
        database.set_settings(conn, {"work_popup_timing": "work_end", "work_end_popup_mode": "always"})
        segment_id = database.add_segment(conn, today, "WORK", "08:00:00", "17:00:00", "HOME")

    monkeypatch.setattr(popups, "_ask_work_end_time", lambda segment, **kwargs: "17:15")

    event = popups.end_day_with_optional_popup(recorder)

    assert event is not None
    assert event.kind == "END_DAY_CORRECTION"
    assert event.segment_id == segment_id
    assert event.time == "17:15:00"
    with database.connect(db_path) as conn:
        assert database.get_segment(conn, segment_id)["end_time"] == "17:15:00"
