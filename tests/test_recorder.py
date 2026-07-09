from __future__ import annotations

from datetime import datetime

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


def test_previous_open_segment_uses_heartbeat_recovery(tmp_path, monkeypatch):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)
    monkeypatch.setattr("tracker.recorder.today_str", lambda: "2026-07-09")

    with database.connect(db_path) as conn:
        segment_id = database.add_segment(conn, "2026-07-08", "WORK", "08:00:00", location="OFFICE")
        database.set_setting(conn, "last_tracker_heartbeat", "2026-07-08T17:21:00")

    events = recorder.recover_previous_open_segments(lambda segment: None)

    assert events[0].kind == "AUTO_RECOVERY"
    assert events[0].date == "2026-07-08"
    assert events[0].time == "17:21:00"
    with database.connect(db_path) as conn:
        segment = database.get_segment(conn, segment_id)
        assert segment["end_time"] == "17:21:00"


def test_previous_open_segment_asks_without_matching_heartbeat(tmp_path, monkeypatch):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)
    monkeypatch.setattr("tracker.recorder.today_str", lambda: "2026-07-09")
    asked = []

    with database.connect(db_path) as conn:
        segment_id = database.add_segment(conn, "2026-07-08", "WORK", "08:00:00", location="HOME")
        database.set_setting(conn, "last_tracker_heartbeat", "2026-07-07T17:21:00")

    events = recorder.recover_previous_open_segments(lambda segment: asked.append(segment["id"]) or "16:30")

    assert asked == [segment_id]
    assert events[0].kind == "RECOVERY"
    with database.connect(db_path) as conn:
        segment = database.get_segment(conn, segment_id)
        assert segment["end_time"] == "16:30:00"


def test_record_heartbeat_stores_local_iso_timestamp(tmp_path):
    db_path = tmp_path / "database.db"
    database.init_db(db_path)
    recorder = WorktimeRecorder(db_path)

    recorder.record_heartbeat(datetime(2026, 7, 8, 17, 22, 3, 123456))

    with database.connect(db_path) as conn:
        assert database.get_setting(conn, "last_tracker_heartbeat") == "2026-07-08T17:22:03"
