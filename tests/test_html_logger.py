from __future__ import annotations

from common.html_logger import HtmlLogHandler, read_entries


def test_html_logger_writes_inline_searchable_html(tmp_path):
    handler = HtmlLogHandler(log_dir=tmp_path)
    path = handler.append_entry(
        {
            "timestamp": "2026-07-08 09:30:00",
            "level": "INFO",
            "module": "test",
            "message": "Arbeitsbeginn erfasst",
        }
    )
    handler.append_entry(
        {
            "timestamp": "2026-07-08 09:31:00",
            "level": "ERROR",
            "module": "test",
            "message": "Beispiel-Fehler",
        }
    )

    content = path.read_text(encoding="utf-8")
    entries = read_entries(path)

    assert len(entries) == 2
    assert "const LOG_ENTRIES = [" in content
    assert "addEventListener(\"input\", render)" in content
    assert "data-key=\"timestamp\"" in content
    assert "Beispiel-Fehler" in content

