"""Configurable desktop popup dialogs for tracker start and end checks."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from typing import Any

from common import database
from common.config import PROJECT_ROOT
from common.models import today_str
from tracker.notify import NotificationCenter
from tracker.recorder import RecorderEvent, WorktimeRecorder


SKIP_WORK_START = "__SKIP_WORK_START__"
WEEKDAY_LABELS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
TIME_DIALOG_SUBPROCESS = r"""
import json
import sys

payload = json.loads(sys.argv[1])
result = {"value": None}

try:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(payload["title"])
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")
    ttk.Label(frame, text=payload["message"], justify="left", wraplength=460).grid(
        row=0,
        column=0,
        columnspan=2,
        sticky="w",
    )
    value = tk.StringVar(value=payload["initial_time"])
    entry = ttk.Entry(frame, textvariable=value, width=10)
    entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 10))

    def save():
        result["value"] = value.get().strip()
        root.destroy()

    def secondary():
        result["value"] = payload["secondary_value"]
        root.destroy()

    ttk.Button(frame, text=payload["primary_label"], command=save).grid(row=2, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(frame, text=payload["secondary_label"], command=secondary).grid(row=2, column=1, sticky="ew")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    entry.focus_set()
    root.update_idletasks()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    width = min(max(root.winfo_reqwidth() + 24, 380), max(320, screen_width - 96))
    height = min(max(root.winfo_reqheight() + 24, 180), max(180, screen_height - 120))
    x = max(24, int((screen_width - width) / 2))
    y = max(48, int((screen_height - height) / 3))
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.mainloop()
    print(json.dumps(result, ensure_ascii=False), flush=True)
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
    sys.exit(1)
"""
INFO_POPUP_SUBPROCESS = r"""
import json
import sys

payload = json.loads(sys.argv[1])
try:
    from tracker import popups
    popups._show_info_dialog_from_payload(payload)
except Exception:
    sys.exit(1)
"""


def handle_startup_popups(recorder: WorktimeRecorder, notifier: NotificationCenter) -> list[RecorderEvent]:
    settings = _settings(recorder)
    events: list[RecorderEvent] = []

    if settings.get("work_popup_timing") == "startup":
        events.extend(_handle_work_end_startup_popup(recorder, settings))
        start_event = _handle_work_start_popup(recorder, settings)
    else:
        events.extend(
            recorder.recover_previous_open_segments(
                _ask_recovery_end_time,
                allow_prompt=False,
                prefer_prompt=False,
            )
        )
        start_event = recorder.auto_start_day()

    if start_event:
        events.append(start_event)

    for event in events:
        notifier.show("ArbeitszeitTracker", f"{event.message}: {event.date} {event.time[:5]} Uhr")
    return events


def end_day_with_optional_popup(recorder: WorktimeRecorder) -> RecorderEvent | None:
    settings = _settings(recorder)
    if settings.get("work_popup_timing") != "work_end" or settings.get("work_end_popup_mode") == "off":
        return recorder.end_day()

    status = recorder.get_status()
    if status.get("running"):
        value = _ask_work_end_time(
            {
                "date": status.get("date") or today_str(),
                "start_time": status.get("start_time") or "",
                "end_time": datetime.now().strftime("%H:%M"),
            },
            title="Feierabend prüfen",
            intro="Bitte prüfe die Arbeitsende-Zeit.",
        )
        return recorder.end_day(at_time=value) if value else recorder.end_day()

    if settings.get("work_end_popup_mode") == "always":
        candidate = recorder.get_last_work_end_candidate()
        if candidate:
            value = _ask_work_end_time(
                candidate,
                title="Letztes Arbeitsende prüfen",
                intro="Es ist kein Segment offen. Du kannst das letzte Arbeitsende trotzdem korrigieren.",
            )
            if value:
                return recorder.update_work_end_time(int(candidate["id"]), value)
    return recorder.end_day()


def show_info_popup_on_work_end(recorder: WorktimeRecorder, notifier: NotificationCenter) -> None:
    settings = _settings(recorder)
    if settings.get("daily_info_popup_mode") != "work_end":
        return
    text = recorder.day_information_text()
    if _show_info_popup("Tagesstand", text, dark=settings.get("darkmode") == "1"):
        return
    notifier.show("ArbeitszeitTracker", text.splitlines()[0] if text else "Tagesstand aktualisiert")


class PopupScheduler:
    def __init__(
        self,
        recorder: WorktimeRecorder,
        notifier: NotificationCenter,
        interval_seconds: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.recorder = recorder
        self.notifier = notifier
        self.interval_seconds = interval_seconds
        self.logger = logger or logging.getLogger("worktime.tracker.popups")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shown: set[tuple[str, str]] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="TrackerPopupScheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._tick()
            except Exception:
                self.logger.debug("Popup-Zeitplan konnte nicht ausgeführt werden", exc_info=True)

    def _tick(self) -> None:
        settings = _settings(self.recorder)
        now = datetime.now()
        today_key = now.date().isoformat()
        current_time = now.strftime("%H:%M")

        if (
            settings.get("work_popup_timing") == "custom"
            and settings.get("work_popup_custom_time") == current_time
            and ("work", today_key) not in self._shown
        ):
            self._shown.add(("work", today_key))
            events = _handle_work_end_startup_popup(self.recorder, settings)
            start_event = _handle_work_start_popup(self.recorder, settings)
            if start_event:
                events.append(start_event)
            for event in events:
                self.notifier.show("ArbeitszeitTracker", f"{event.message}: {event.date} {event.time[:5]} Uhr")

        if (
            settings.get("daily_info_popup_mode") == "custom"
            and settings.get("daily_info_popup_time") == current_time
            and ("info", today_key) not in self._shown
        ):
            self._shown.add(("info", today_key))
            text = self.recorder.day_information_text()
            if not _show_info_popup("Tagesstand", text, dark=settings.get("darkmode") == "1"):
                self.notifier.show("ArbeitszeitTracker", text.splitlines()[0] if text else "Tagesstand")


def _handle_work_start_popup(recorder: WorktimeRecorder, settings: dict[str, str]) -> RecorderEvent | None:
    plan = recorder.preview_auto_start_day()
    if not plan:
        return None
    if settings.get("work_start_popup_mode") != "on":
        return recorder.auto_start_day(at_time=plan.time)
    value = _ask_work_start_time(plan)
    if value == SKIP_WORK_START:
        return None
    return recorder.auto_start_day(at_time=value or plan.time)


def _handle_work_end_startup_popup(recorder: WorktimeRecorder, settings: dict[str, str]) -> list[RecorderEvent]:
    mode = settings.get("work_end_popup_mode", "open_only")
    if mode == "off":
        return recorder.recover_previous_open_segments(_ask_recovery_end_time, allow_prompt=False)

    events = recorder.recover_previous_open_segments(
        _ask_recovery_end_time,
        allow_prompt=True,
        prefer_prompt=True,
    )
    if mode == "always" and not events:
        candidate = recorder.get_last_work_end_candidate()
        if candidate:
            value = _ask_work_end_time(
                candidate,
                title="Letztes Arbeitsende prüfen",
                intro="Bitte prüfe, ob das letzte erfasste Arbeitsende stimmt.",
            )
            if value:
                event = recorder.update_work_end_time(int(candidate["id"]), value)
                if event:
                    events.append(event)
    return events


def _settings(recorder: WorktimeRecorder) -> dict[str, str]:
    with database.connect(recorder.db_path) as conn:
        return database.get_settings(conn)


def _ask_work_start_time(plan: RecorderEvent) -> str | None:
    message = (
        f"Arbeitsbeginn für {plan.date}\n"
        f"Geplante Zeit: {plan.time[:5]} Uhr\n"
        f"Standort: {plan.location or 'HOME'}"
    )
    return _time_dialog(
        title="Arbeitsbeginn prüfen",
        message=message,
        initial_time=plan.time[:5],
        primary_label="Arbeitsbeginn speichern",
        secondary_label="Arbeitsbeginn nicht speichern",
        secondary_value=SKIP_WORK_START,
    )


def _ask_recovery_end_time(segment: dict[str, Any]) -> str | None:
    return _ask_work_end_time(
        segment,
        title="Offenes Segment",
        intro="Dieses Segment wurde nicht geschlossen. Wann hast du aufgehört zu arbeiten?",
    )


def _ask_work_end_time(segment: dict[str, Any], *, title: str, intro: str) -> str | None:
    date_text = str(segment.get("date") or today_str())
    weekday = _weekday_label(date_text)
    initial = str(segment.get("end_time") or "17:00")[:5]
    start = str(segment.get("start_time") or "")[:5]
    message = f"{intro}\n\n{weekday}, {date_text}"
    if start:
        message += f"\nBeginn: {start} Uhr"
    return _time_dialog(
        title=title,
        message=message,
        initial_time=initial,
        primary_label="Arbeitsende speichern",
        secondary_label="Schließen",
        secondary_value=None,
    )


def _time_dialog(
    *,
    title: str,
    message: str,
    initial_time: str,
    primary_label: str,
    secondary_label: str,
    secondary_value: str | None,
) -> str | None:
    if _called_from_background_thread():
        return _time_dialog_subprocess(
            title=title,
            message=message,
            initial_time=initial_time,
            primary_label=primary_label,
            secondary_label=secondary_label,
            secondary_value=secondary_value,
        )

    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Popup-Dialog nicht verfügbar")
        return None

    result: dict[str, str | None] = {"value": None}
    root = None
    try:
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frame, text=message, justify="left", wraplength=460).grid(row=0, column=0, columnspan=2, sticky="w")
        value = tk.StringVar(value=initial_time)
        entry = ttk.Entry(frame, textvariable=value, width=10)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 10))

        def save() -> None:
            result["value"] = value.get().strip()
            root.destroy()

        def secondary() -> None:
            result["value"] = secondary_value
            root.destroy()

        ttk.Button(frame, text=primary_label, command=save).grid(row=2, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(frame, text=secondary_label, command=secondary).grid(row=2, column=1, sticky="ew")
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        entry.focus_set()
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width = min(max(root.winfo_reqwidth() + 24, 380), max(320, screen_width - 96))
        height = min(max(root.winfo_reqheight() + 24, 180), max(180, screen_height - 120))
        x = max(24, int((screen_width - width) / 2))
        y = max(48, int((screen_height - height) / 3))
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.mainloop()
        return result["value"]
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Popup-Dialog konnte nicht angezeigt werden", exc_info=True)
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _show_info_popup(title: str, message: str, *, dark: bool = False) -> bool:
    if _called_from_background_thread():
        return _info_popup_subprocess(title, message, dark=dark)

    try:
        import tkinter as tk
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Info-Popup nicht verfügbar")
        return False
    try:
        _show_info_dashboard_tk(tk, title, message, dark)
        return True
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Info-Popup konnte nicht angezeigt werden", exc_info=True)
        return False


def _show_info_dialog_from_payload(payload: dict[str, Any]) -> None:
    import tkinter as tk

    _show_info_dashboard_tk(
        tk,
        str(payload.get("title") or "Tagesstand"),
        str(payload.get("message") or ""),
        bool(payload.get("dark")),
    )


def _show_info_dashboard_tk(tk, title: str, message: str, dark: bool) -> None:
    palette = _info_popup_palette(dark)
    data = _parse_day_info_message(message)
    root = tk.Tk()
    root.title(title)
    root.resizable(True, True)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    root.configure(bg=palette["bg"])

    canvas = tk.Canvas(root, bg=palette["bg"], borderwidth=0, highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew")

    content = tk.Frame(canvas, bg=palette["bg"], padx=20, pady=18)
    content.columnconfigure(0, weight=1)
    content_window = canvas.create_window((0, 0), window=content, anchor="nw")
    metric_cards = []
    metric_layout: dict[str, int | None] = {"columns": None}

    def layout_metric_cards(available_width: int) -> None:
        columns = 3 if available_width >= 660 else 2 if available_width >= 460 else 1
        if metric_layout["columns"] == columns:
            return
        metric_layout["columns"] = columns
        for column in range(3):
            metrics_frame.columnconfigure(column, weight=0, uniform="")
        for column in range(columns):
            metrics_frame.columnconfigure(column, weight=1, uniform="metrics")
        for card in metric_cards:
            card.grid_forget()
        for index, card in enumerate(metric_cards):
            card.grid(
                row=index // columns,
                column=index % columns,
                sticky="nsew",
                padx=(0 if index % columns == 0 else 8, 0),
                pady=(0, 8),
            )

    def sync_scroll_region(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def sync_content_width(event) -> None:
        canvas.itemconfigure(content_window, width=max(1, event.width))
        layout_metric_cards(max(1, event.width - 40))

    def on_mousewheel(event) -> None:
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    content.bind("<Configure>", sync_scroll_region)
    canvas.bind("<Configure>", sync_content_width)
    canvas.bind_all("<MouseWheel>", on_mousewheel)

    header = tk.Frame(content, bg=palette["surface"], highlightbackground=palette["border"], highlightthickness=1)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
    header.columnconfigure(0, weight=1)
    tk.Label(
        header,
        text=title,
        bg=palette["surface"],
        fg=palette["text"],
        font=("", 20, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
    tk.Label(
        header,
        text=data["date"] or "Aktueller Tag",
        bg=palette["surface"],
        fg=palette["muted"],
        font=("", 12),
        anchor="w",
    ).grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 14))

    metrics = data["metrics"] or [{"label": "Info", "value": message or "Keine Tagesdaten", "tone": "neutral"}]
    metrics_frame = tk.Frame(content, bg=palette["bg"])
    metrics_frame.grid(row=1, column=0, sticky="ew")
    for metric in metrics:
        metric_cards.append(_build_info_metric_card(tk, metrics_frame, metric, palette))
    layout_metric_cards(740)

    segment_panel = tk.Frame(content, bg=palette["surface"], highlightbackground=palette["border"], highlightthickness=1)
    segment_panel.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    segment_panel.columnconfigure(0, weight=1)
    tk.Label(
        segment_panel,
        text="Segmente",
        bg=palette["surface"],
        fg=palette["text"],
        font=("", 15, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
    if data["segments"]:
        for index, segment in enumerate(data["segments"], start=1):
            _build_info_segment_row(tk, segment_panel, segment, palette).grid(row=index, column=0, sticky="ew", padx=14, pady=(0, 8))
    else:
        tk.Label(
            segment_panel,
            text="Keine Segmente für den Tag erfasst.",
            bg=palette["surface"],
            fg=palette["muted"],
            font=("", 12),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))

    if data["notes"]:
        notes_panel = tk.Frame(content, bg=palette["surface"], highlightbackground=palette["border"], highlightthickness=1)
        notes_panel.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        notes_panel.columnconfigure(0, weight=1)
        tk.Label(
            notes_panel,
            text="\n".join(data["notes"]),
            bg=palette["surface"],
            fg=palette["muted"],
            font=("", 12),
            justify="left",
            anchor="w",
            wraplength=680,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=12)

    footer = tk.Frame(root, bg=palette["surface"], highlightbackground=palette["border"], highlightthickness=1)
    footer.grid(row=1, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    close_button = _build_info_close_button(tk, footer, root, palette)
    close_button.grid(row=0, column=1, padx=18, pady=12)
    root.bind("<Escape>", lambda event: _close_tk_window(root))
    root.bind("<Command-w>", lambda event: _close_tk_window(root))
    root.protocol("WM_DELETE_WINDOW", lambda: _close_tk_window(root))

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    width = min(780, max(420, screen_width - 96))
    height = min(640, max(400, screen_height - 120))
    x = max(24, int((screen_width - width) / 2))
    y = max(48, int((screen_height - height) / 3))
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(min(520, width), min(360, height))
    topmost_release_enabled = {"value": False}

    def release_topmost(_event=None) -> None:
        if not topmost_release_enabled["value"]:
            return
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass

    def enable_topmost_release() -> None:
        topmost_release_enabled["value"] = True

    root.bind("<FocusOut>", release_topmost)
    root.after(0, lambda: _bring_tk_window_to_front(root))
    root.after(250, lambda: _bring_tk_window_to_front(root))
    root.after(900, lambda: _bring_tk_window_to_front(root))
    root.after(1200, enable_topmost_release)
    try:
        root.mainloop()
    finally:
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


def _build_info_metric_card(tk, parent, metric: dict[str, str], palette: dict[str, str]):
    accent = palette.get(metric.get("tone", "neutral"), palette["primary"])
    card = tk.Frame(parent, bg=palette["surface"], highlightbackground=palette["border"], highlightthickness=1)
    card.columnconfigure(0, minsize=4)
    card.columnconfigure(1, weight=1)
    tk.Frame(card, bg=accent, width=4).grid(row=0, column=0, rowspan=2, sticky="ns")
    tk.Label(
        card,
        text=metric["label"],
        bg=palette["surface"],
        fg=palette["muted"],
        font=("", 11, "bold"),
        anchor="w",
    ).grid(row=0, column=1, sticky="ew", padx=(12, 12), pady=(12, 2))
    tk.Label(
        card,
        text=metric["value"],
        bg=palette["surface"],
        fg=accent,
        font=("", 20, "bold"),
        anchor="w",
    ).grid(row=1, column=1, sticky="ew", padx=(12, 12), pady=(0, 12))
    return card


def _build_info_segment_row(tk, parent, segment: dict[str, str], palette: dict[str, str]):
    row = tk.Frame(parent, bg=palette["surface_2"])
    row.columnconfigure(1, weight=1)
    accent = palette.get(segment.get("tone", "neutral"), palette["primary"])
    tk.Label(
        row,
        text=segment["label"],
        bg=accent,
        fg="#ffffff",
        font=("", 10, "bold"),
        padx=10,
        pady=5,
    ).grid(row=0, column=0, sticky="w", padx=10, pady=8)
    tk.Label(
        row,
        text=segment["time"],
        bg=palette["surface_2"],
        fg=palette["text"],
        font=("", 13, "bold"),
        anchor="w",
    ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=8)
    return row


def _build_info_close_button(tk, parent, root, palette: dict[str, str]):
    button = tk.Frame(
        parent,
        bg=palette["primary"],
        cursor="hand2",
    )
    label = tk.Label(
        button,
        text="Schließen",
        bg=palette["primary"],
        fg="#ffffff",
        padx=26,
        pady=11,
        font=("", 12, "bold"),
        cursor="hand2",
    )
    label.pack(fill="both", expand=True)

    def close(_event=None) -> None:
        _close_tk_window(root)

    def activate(_event=None) -> None:
        button.configure(bg=palette["primary_active"])
        label.configure(bg=palette["primary_active"], fg="#ffffff")

    def deactivate(_event=None) -> None:
        button.configure(bg=palette["primary"])
        label.configure(bg=palette["primary"], fg="#ffffff")

    for widget in (button, label):
        widget.bind("<ButtonPress-1>", activate)
        widget.bind("<ButtonRelease-1>", close)
        widget.bind("<Enter>", activate)
        widget.bind("<Leave>", deactivate)
        widget.bind("<Return>", close)
        widget.bind("<space>", close)
    return button


def _close_tk_window(root) -> None:
    try:
        root.quit()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


def _bring_tk_window_to_front(root) -> None:
    try:
        root.deiconify()
    except Exception:
        pass
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.lift()
    except Exception:
        pass
    try:
        root.focus_force()
    except Exception:
        pass
    _activate_current_macos_process()


def _activate_current_macos_process() -> None:
    if sys.platform != "darwin":
        return
    script = f'tell application "System Events" to set frontmost of first process whose unix id is {os.getpid()} to true'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=1,
        )
    except Exception:
        pass


def _parse_day_info_message(message: str) -> dict[str, Any]:
    data: dict[str, Any] = {"date": "", "metrics": [], "segments": [], "notes": []}
    in_segments = False
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Tagesstand"):
            data["date"] = line.replace("Tagesstand", "", 1).strip()
            continue
        if line == "Segmente:":
            in_segments = True
            continue
        if in_segments:
            segment = _parse_info_segment_line(line)
            if segment:
                data["segments"].append(segment)
            else:
                data["notes"].append(line)
            continue
        if ":" in line:
            label, value = line.split(":", 1)
            label = label.strip()
            value = value.strip()
            data["metrics"].append({"label": label, "value": value, "tone": _info_metric_tone(label, value)})
        else:
            data["notes"].append(line)
    return data


def _parse_info_segment_line(line: str) -> dict[str, str] | None:
    cleaned = line[2:].strip() if line.startswith("- ") else line
    if ":" not in cleaned:
        return None
    segment_type, time_text = cleaned.split(":", 1)
    label, tone = {
        "WORK": ("Arbeit", "primary"),
        "BREAK": ("Pause", "warning"),
        "ABSENCE": ("Abwesenheit", "accent"),
    }.get(segment_type.strip(), (segment_type.strip(), "neutral"))
    return {"label": label, "time": time_text.strip(), "tone": tone}


def _info_metric_tone(label: str, value: str) -> str:
    lowered = label.casefold()
    if "saldo" in lowered:
        return "danger" if value.strip().startswith("-") else "success"
    if "arbeitszeit" in lowered:
        return "primary"
    if "pause" in lowered:
        return "warning"
    if "abwesenheit" in lowered:
        return "accent"
    return "neutral"


def _info_popup_palette(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "bg": "#0f172a",
            "surface": "#182033",
            "surface_2": "#111827",
            "text": "#f8fafc",
            "muted": "#cbd5e1",
            "border": "#334155",
            "primary": "#2563eb",
            "primary_active": "#1d4ed8",
            "success": "#22c55e",
            "warning": "#f59e0b",
            "danger": "#ef4444",
            "accent": "#8b5cf6",
            "neutral": "#94a3b8",
        }
    return {
        "bg": "#f8fafc",
        "surface": "#ffffff",
        "surface_2": "#f1f5f9",
        "text": "#0f172a",
        "muted": "#475569",
        "border": "#d8e0ea",
        "primary": "#2563eb",
        "primary_active": "#1d4ed8",
        "success": "#15803d",
        "warning": "#b45309",
        "danger": "#b91c1c",
        "accent": "#7c3aed",
        "neutral": "#64748b",
    }


def _time_dialog_subprocess(
    *,
    title: str,
    message: str,
    initial_time: str,
    primary_label: str,
    secondary_label: str,
    secondary_value: str | None,
) -> str | None:
    executable = _popup_python_executable()
    if not executable:
        logging.getLogger("worktime.tracker.popups").warning("Geplantes Popup ist in dieser App-Umgebung nicht verfügbar")
        return None
    payload = {
        "title": title,
        "message": message,
        "initial_time": initial_time,
        "primary_label": primary_label,
        "secondary_label": secondary_label,
        "secondary_value": secondary_value,
    }
    try:
        completed = subprocess.run(
            [executable, "-c", TIME_DIALOG_SUBPROCESS, json.dumps(payload, ensure_ascii=False)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Geplantes Popup konnte nicht gestartet werden", exc_info=True)
        return None
    if completed.returncode != 0:
        logging.getLogger("worktime.tracker.popups").warning("Geplantes Popup wurde ohne Ergebnis beendet")
        return None
    try:
        response = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        logging.getLogger("worktime.tracker.popups").warning("Geplantes Popup lieferte kein lesbares Ergebnis")
        return None
    return response.get("value")


def _info_popup_subprocess(title: str, message: str, *, dark: bool = False) -> bool:
    executable = _popup_python_executable()
    if not executable:
        return False
    payload = {"title": title, "message": message, "dark": dark}
    try:
        subprocess.Popen(
            [executable, "-c", INFO_POPUP_SUBPROCESS, json.dumps(payload, ensure_ascii=False)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_popup_subprocess_env(),
        )
        return True
    except Exception:
        logging.getLogger("worktime.tracker.popups").warning("Geplantes Info-Popup konnte nicht gestartet werden", exc_info=True)
        return False


def _called_from_background_thread() -> bool:
    return threading.current_thread() is not threading.main_thread()


def _popup_python_executable() -> str | None:
    if getattr(sys, "frozen", False):
        return None
    return sys.executable or None


def _popup_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT) if not existing else f"{PROJECT_ROOT}{os.pathsep}{existing}"
    return env


def _weekday_label(date_text: str) -> str:
    try:
        weekday = datetime.strptime(date_text, "%Y-%m-%d").date().weekday()
    except ValueError:
        return "Unbekannter Wochentag"
    return WEEKDAY_LABELS[weekday]
