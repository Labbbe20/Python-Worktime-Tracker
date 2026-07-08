"""HTML logging handler with inline data and client-side search/sort."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import LOG_DIR, ensure_data_dirs


ARRAY_START = "const LOG_ENTRIES = ["
ARRAY_END = "];\n//__LOG_ENTRIES_END__"


class HtmlLogHandler(logging.Handler):
    """Python logging handler that writes one self-contained HTML file per month."""

    def __init__(self, log_dir: str | Path | None = None) -> None:
        super().__init__()
        ensure_data_dirs()
        self.log_dir = Path(log_dir) if log_dir else LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": datetime.fromtimestamp(record.created).replace(microsecond=0).isoformat(sep=" "),
                "level": record.levelname,
                "module": record.name,
                "message": self.format(record),
            }
            self.append_entry(entry)
        except Exception:
            self.handleError(record)

    def append_entry(self, entry: dict[str, Any]) -> Path:
        with self._lock:
            path = self.current_log_path()
            entries = read_entries(path)
            entries.append(entry)
            write_html_log(path, entries)
            return path

    def current_log_path(self) -> Path:
        return self.log_dir / f"log_{datetime.now().strftime('%Y-%m')}.html"


def setup_html_logging(name: str = "worktime", log_dir: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, HtmlLogHandler) for handler in logger.handlers):
        handler = HtmlLogHandler(log_dir=log_dir)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def read_entries(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    content = file_path.read_text(encoding="utf-8")
    try:
        start = content.index(ARRAY_START) + len(ARRAY_START)
        end = content.index(ARRAY_END, start)
    except ValueError:
        return []
    body = content[start:end].strip()
    if not body:
        return []
    try:
        return json.loads(f"[{body}]")
    except json.JSONDecodeError:
        return []


def write_html_log(path: str | Path, entries: list[dict[str, Any]]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(_safe_json(entry) for entry in entries)
    html = _render_html(body)
    fd, tmp_name = tempfile.mkstemp(prefix=file_path.name, suffix=".tmp", dir=file_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(html)
        os.replace(tmp_name, file_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _safe_json(entry: dict[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False).replace("</", "<\\/")


def _render_html(entries_body: str) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ArbeitszeitTracker Diagnose-Log</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f8fafc;
      --surface: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #d8e0ea;
      --info: #2563eb;
      --warning: #b45309;
      --error: #b91c1c;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111827;
        --surface: #182033;
        --text: #f8fafc;
        --muted: #cbd5e1;
        --border: #334155;
      }}
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    header {{
      padding: 24px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
    }}
    .toolbar {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }}
    input {{
      min-height: 40px;
      min-width: min(360px, 100%);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0 12px;
      background: var(--bg);
      color: var(--text);
      font: inherit;
    }}
    main {{
      padding: 24px;
      overflow-x: auto;
    }}
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
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .level-ERROR, .level-CRITICAL {{
      color: var(--error);
      font-weight: 700;
    }}
    .level-WARNING {{
      color: var(--warning);
      font-weight: 700;
    }}
    .level-INFO {{
      color: var(--info);
      font-weight: 600;
    }}
    .count {{
      color: var(--muted);
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Diagnose-Log</h1>
    <div class="toolbar">
      <label for="search">Suche</label>
      <input id="search" type="search" placeholder="Freitext über alle Spalten">
      <span class="count" id="count"></span>
    </div>
  </header>
  <main>
    <table aria-describedby="count">
      <thead>
        <tr>
          <th data-key="timestamp">Zeitstempel</th>
          <th data-key="level">Level</th>
          <th data-key="module">Modul</th>
          <th data-key="message">Nachricht</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
{ARRAY_START}
{entries_body}
{ARRAY_END}

let sortKey = "timestamp";
let sortDirection = -1;
const search = document.getElementById("search");
const rows = document.getElementById("rows");
const count = document.getElementById("count");

function render() {{
  const query = search.value.trim().toLowerCase();
  const filtered = LOG_ENTRIES
    .filter(entry => Object.values(entry).join(" ").toLowerCase().includes(query))
    .sort((a, b) => String(a[sortKey]).localeCompare(String(b[sortKey])) * sortDirection);
  rows.innerHTML = filtered.map(entry => `
    <tr>
      <td>${{escapeHtml(entry.timestamp)}}</td>
      <td class="level-${{escapeHtml(entry.level)}}">${{escapeHtml(entry.level)}}</td>
      <td>${{escapeHtml(entry.module)}}</td>
      <td>${{escapeHtml(entry.message)}}</td>
    </tr>
  `).join("");
  count.textContent = `${{filtered.length}} von ${{LOG_ENTRIES.length}} Einträgen`;
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
render();
  </script>
</body>
</html>
"""

