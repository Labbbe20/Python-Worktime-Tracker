from __future__ import annotations

from common import database
from common.models import today_str
from tracker.recorder import WorktimeRecorder


def test_recorder_reuses_first_real_location_of_day(tmp_path):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)
    today = today_str()

    with database.connect(db_path) as conn:
        database.add_segment(conn, today, "WORK", "08:00:00", "10:00:00", "HOME")

        assert recorder._detect_location(conn) == "HOME"


def test_auto_start_uses_homeoffice_buffer(tmp_path, monkeypatch):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)
    today = today_str()

    monkeypatch.setattr("tracker.recorder.current_time_str", lambda: "07:00:00")
    with database.connect(db_path) as conn:
        database.set_settings(conn, {"home_start_buffer_minutes": "10"})

    event = recorder.auto_start_day()

    assert event is not None
    assert event.time == "06:50:00"
    with database.connect(db_path) as conn:
        segment = database.get_segments_for_date(conn, today)[0]
        assert segment["start_time"] == "06:50:00"
        assert segment["location"] == "HOME"


def test_manual_start_does_not_use_buffer(tmp_path, monkeypatch):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)

    monkeypatch.setattr("tracker.recorder.current_time_str", lambda: "07:00:00")
    with database.connect(db_path) as conn:
        database.set_settings(conn, {"home_start_buffer_minutes": "10"})

    event = recorder.start_work(source="MANUAL")

    assert event is not None
    assert event.time == "07:00:00"
