const state = {
  view: initialView(),
  calendarDate: new Date(),
  selectedDate: isoToday(),
  calendarDetailDate: null,
  entryEditDate: null,
  dashboardDetailKey: null,
  settingsDetailKey: "work",
  entries: [],
  entriesSort: { key: "date", direction: -1 },
  statsChart: null,
};

const content = document.getElementById("content");
const toast = document.getElementById("toast");
let commandPollTimer = null;
let autoRefreshTimer = null;

document.querySelectorAll(".nav button").forEach(button => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.getElementById("refresh-view")?.addEventListener("click", () => render());

window.addEventListener("pywebviewready", () => {
  syncNav();
  loadInitialTheme().finally(async () => {
    await render();
    startCommandPolling();
    startAutoRefresh();
    await checkInitialSetup();
  });
});

setTimeout(() => {
  if (!window.pywebview) {
    content.innerHTML = `<section class="panel"><h1>pywebview nicht verbunden</h1><p class="muted">Starte die App mit <code>python3 app/main.py</code>, damit die lokale Python-API verfügbar ist.</p></section>`;
  }
}, 1200);

async function loadInitialTheme() {
  try {
    const settings = await api("settings");
    document.body.classList.toggle("dark", settings.darkmode === "1");
  } catch (error) {
    console.warn(error);
  }
}

async function checkInitialSetup() {
  try {
    const settings = await api("settings");
    if (settings.initial_setup_required !== "1") return;
    state.view = "settings";
    state.settingsDetailKey = "setup";
    window.location.hash = "settings";
    syncNav();
    await renderSettings();
    notify("Bitte einmal die Startwerte einrichten.");
  } catch (error) {
    console.warn(error);
  }
}

function setView(view) {
  state.view = view;
  window.location.hash = view;
  syncNav();
  render();
}

function syncNav() {
  document.querySelectorAll(".nav button").forEach(button => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
}

async function render() {
  content.focus();
  try {
    if (state.view === "dashboard") return renderDashboard();
    if (state.view === "calendar") return renderCalendar();
    if (state.view === "entries") return renderEntries();
    if (state.view === "statistics") return renderStatistics();
    if (state.view === "vacation") return renderVacation();
    if (state.view === "settings") return renderSettings();
  } catch (error) {
    showError(error);
  }
}

async function renderDashboard() {
  const data = await api("dashboard");
  const metrics = [
    {
      key: "work",
      label: "Arbeitszeit",
      value: fmtMinutes(data.work_minutes),
      detailHtml: [
        detailRow("Soll heute", fmtMinutes(data.target_minutes)),
        detailRow("Live-Saldo heute", signedMinutes(data.live_day.balance_minutes), data.live_day.balance_minutes >= 0 ? "positive" : "negative"),
        detailRow("Tagesfenster", data.range),
      ].join(""),
    },
    {
      key: "break",
      label: "Pause",
      value: fmtMinutes(data.break_minutes),
      detailHtml: [
        detailRow("Pausenzeit heute", fmtMinutes(data.break_minutes)),
        detailRow("Offenes Segment", data.live_day.open_type ? `${segmentTypeLabel(data.live_day.open_type)} läuft` : "Keins"),
        detailText(data.live_day.has_open_segment ? "Der laufende Abschnitt wird live angezeigt." : "Aktuell läuft kein Abschnitt."),
      ].join(""),
    },
    {
      key: "live",
      label: "Heute live",
      value: signedMinutes(data.live_day.balance_minutes),
      signedValue: data.live_day.balance_minutes,
      detailHtml: [
        detailText(data.live_day.detail),
        detailText(data.live_day.note, "muted"),
      ].join(""),
    },
    {
      key: "balance",
      label: "Gleitzeitkonto",
      value: data.flextime_hours,
      signedValue: data.flextime,
      extraClass: `balance-card ${escapeHtml(data.flextime_status?.class || "")}`,
      detailHtml: [
        detailRow("Kontostand", data.flextime_hours, data.flextime >= 0 ? "positive" : "negative"),
        detailRow("Grenzbereich", data.flextime_status?.label || "0 bis 45 Stunden"),
        detailText("Angefangene Tage zählen erst nach dem Schließen des offenen Segments ins Konto.", "muted"),
      ].join(""),
    },
    {
      key: "vacation",
      label: "Resturlaub",
      value: `${numberDe(data.remaining_vacation)} Tage`,
      detailHtml: [
        detailRow("Verbleibend", `${numberDe(data.remaining_vacation)} Tage`),
        detailRow("Jahr", String(new Date(data.today).getFullYear())),
        detailText("Urlaubstage werden aus den eingetragenen Abwesenheiten berechnet.", "muted"),
      ].join(""),
    },
    {
      key: "office",
      label: "Officequote",
      value: `${numberDe(data.location_stats.office_percent)} %`,
      signedValue: data.location_stats.office_requirement_met ? 1 : -1,
      detailHtml: [
        detailRow("Büro gesamt", `${numberDe(data.location_stats.office_days)} Tage`),
        detailRow("Homeoffice gesamt", `${numberDe(data.location_stats.homeoffice_days)} Tage`),
        detailRow("Getrackt Büro", `${numberDe(data.location_stats.tracked_office_days)} Tage`),
        detailRow("Getrackt Homeoffice", `${numberDe(data.location_stats.tracked_homeoffice_days)} Tage`),
        detailRow("Manuell Büro", `${numberDe(data.location_stats.manual_office_days)} Tage`),
        detailRow("Manuell Homeoffice", `${numberDe(data.location_stats.manual_homeoffice_days)} Tage`),
        detailText(data.location_stats.office_requirement_met ? "Mindestens 50 % Büro ist aktuell erfüllt." : "Achtung: unter 50 % Büroanteil.", data.location_stats.office_requirement_met ? "positive" : "negative"),
      ].join(""),
    },
    {
      key: "date",
      label: "Datum",
      value: data.today,
      detailHtml: [
        detailRow("Heute", data.today),
        detailRow("Standort", data.location),
        detailRow("Startdatum", data.settings.tracking_start_date || "automatisch ab erstem Eintrag"),
      ].join(""),
    },
  ];
  const activeMetric = metrics.find(metric => metric.key === state.dashboardDetailKey) || null;
  content.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Dashboard</h1>
        <p>Heute: ${escapeHtml(data.range)} · Standort: ${escapeHtml(data.location)}</p>
      </div>
      <button class="secondary" id="refresh-location">Standort prüfen</button>
    </div>
    <section class="dashboard-grid grid cols-3">
      ${metrics.map(metric => dashboardMetric(metric)).join("")}
    </section>
    ${activeMetric ? dashboardDetailPanel(activeMetric) : ""}
  `;
  document.getElementById("refresh-location").addEventListener("click", async () => {
    const result = await api("detect_location_now");
    notify(`Aktueller Standort: ${result.label}`);
  });
  document.querySelectorAll(".metric-trigger").forEach(button => {
    button.addEventListener("click", () => {
      state.dashboardDetailKey = state.dashboardDetailKey === button.dataset.metric ? null : button.dataset.metric;
      renderDashboard();
    });
  });
  document.getElementById("close-dashboard-detail")?.addEventListener("click", () => {
    state.dashboardDetailKey = null;
    renderDashboard();
  });
}

async function renderCalendar() {
  const year = state.calendarDate.getFullYear();
  const month = state.calendarDate.getMonth() + 1;
  const data = await api("calendar_month", year, month);
  const monthLabel = new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" }).format(state.calendarDate);
  const leading = new Date(year, month - 1, 1).getDay() || 7;
  content.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Kalender</h1>
        <p>${monthLabel}</p>
      </div>
      <div class="row-actions">
        <button class="secondary" id="prev-month">Zurück</button>
        <button class="secondary" id="today-month">Heute</button>
        <button class="secondary" id="next-month">Weiter</button>
      </div>
    </div>
    <section class="calendar-grid" aria-label="Monatskalender">
      <div class="weekday calendar-weekday">KW</div>
      ${["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].map(day => `<div class="weekday">${day}</div>`).join("")}
      ${renderCalendarWeeks(data.days, year, month, leading)}
    </section>
    <section id="day-detail-shell" class="detail-shell"></section>
  `;
  document.getElementById("prev-month").addEventListener("click", () => {
    state.calendarDetailDate = null;
    state.calendarDate = new Date(year, month - 2, 1);
    renderCalendar();
  });
  document.getElementById("today-month").addEventListener("click", () => {
    state.calendarDate = new Date();
    state.selectedDate = isoToday();
    state.calendarDetailDate = null;
    renderCalendar();
  });
  document.getElementById("next-month").addEventListener("click", () => {
    state.calendarDetailDate = null;
    state.calendarDate = new Date(year, month, 1);
    renderCalendar();
  });
  document.querySelectorAll(".day-tile").forEach(button => {
    button.addEventListener("click", async () => {
      state.selectedDate = button.dataset.date;
      state.calendarDetailDate = state.calendarDetailDate === button.dataset.date ? null : button.dataset.date;
      updateCalendarSelection();
      if (state.calendarDetailDate) await loadDayDetail(state.calendarDetailDate);
      else closeDayDetail();
    });
  });
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  if (state.calendarDetailDate?.startsWith(monthPrefix)) {
    await loadDayDetail(state.calendarDetailDate);
  } else {
    state.calendarDetailDate = null;
  }
}

function renderCalendarWeeks(days, year, month, leading) {
  const weeks = [];
  let dayIndex = 0;
  let weekIndex = 0;
  while (dayIndex < days.length) {
    const weekStart = new Date(year, month - 1, 1 - (leading - 1) + weekIndex * 7);
    let row = `<div class="calendar-week">KW ${isoWeekNumber(weekStart)}</div>`;
    for (let weekday = 0; weekday < 7; weekday += 1) {
      if (weekIndex === 0 && weekday < leading - 1) {
        row += `<div class="calendar-empty"></div>`;
      } else if (dayIndex < days.length) {
        row += renderDayTile(days[dayIndex]);
        dayIndex += 1;
      } else {
        row += `<div class="calendar-empty"></div>`;
      }
    }
    weeks.push(row);
    weekIndex += 1;
  }
  return weeks.join("");
}

function renderDayTile(day) {
  const summary = day.summary || {};
  const klass = `${dayClass(summary)} ${futureDayClass(day, summary)} ${balanceDayClass(day)}`.trim();
  const label = dayLabel(summary);
  const balance = !isFutureDate(day.date) && summary.balance_minutes ? signedMinutes(summary.balance_minutes) : "";
  const note = dayDisplayNote(day);
  return `
    <button class="day-tile ${klass} ${state.calendarDetailDate === day.date ? "active" : ""}" data-date="${day.date}" aria-expanded="${state.calendarDetailDate === day.date ? "true" : "false"}">
      <strong>${Number(day.date.slice(-2))}. ${label}</strong>
      <small>${summary.actual_minutes ? fmtMinutes(summary.actual_minutes) : ""} ${balance}</small>
      <small>${escapeHtml(note)}</small>
    </button>
  `;
}

function balanceDayClass(day) {
  if (isFutureDate(day.date)) return "";
  const balance = Number(day.summary?.balance_minutes || 0);
  if (balance > 0) return "balance-plus";
  if (balance < 0) return "balance-minus";
  return "";
}

function futureDayClass(day, summary) {
  if (!isFutureDate(day.date)) return "";
  return summary.day_category === "WORKDAY" && !summary.location ? "future" : "";
}

function dayDisplayNote(day) {
  const values = [
    day.note,
    ...(day.day_types || []).map(type => type.note),
  ].filter(Boolean);
  return [...new Set(values)].join(" · ");
}

async function loadDayDetail(date) {
  const detail = await api("day_detail", date);
  if (state.calendarDetailDate !== date) return;
  const target = document.getElementById("day-detail-shell");
  if (!target) return;
  target.innerHTML = `
    <section id="day-detail" class="detail-panel stack calendar-detail-panel" aria-live="polite">
      <div class="page-head compact">
        <div>
          <h2>${date}</h2>
          <p>${escapeHtml(categoryLabel(detail.summary.day_category))} · Ist ${fmtMinutes(detail.summary.actual_minutes)} · Pause ${fmtMinutes(detail.summary.break_minutes)} · Saldo ${signedMinutes(detail.summary.balance_minutes)}</p>
        </div>
        <button class="secondary" id="close-day-detail" type="button">Schließen</button>
      </div>
      ${renderDayEditorContent(detail, date)}
    </section>
  `;
  document.getElementById("close-day-detail").addEventListener("click", closeDayDetail);
  bindDayForms(date, () => renderCalendar());
}

function closeDayDetail() {
  state.calendarDetailDate = null;
  const target = document.getElementById("day-detail-shell");
  if (target) target.innerHTML = "";
  updateCalendarSelection();
}

function updateCalendarSelection() {
  document.querySelectorAll(".day-tile").forEach(tile => {
    const active = tile.dataset.date === state.calendarDetailDate;
    tile.classList.toggle("active", active);
    tile.setAttribute("aria-expanded", active ? "true" : "false");
  });
}

function renderDayEditorContent(detail, date) {
  return `
    <div class="stack">
      ${renderDayNotePreview(detail.note)}
      <div>
        <h3>Segmente</h3>
        ${renderSegmentTable(detail.segments, date)}
      </div>
      <form id="new-segment" class="form-row">
        <label>Typ
          <select name="type">
            <option value="WORK">Arbeit</option>
            <option value="BREAK">Pause</option>
            <option value="ABSENCE">Abwesenheit</option>
          </select>
        </label>
        <label>Beginn <input name="start_time" type="time" required></label>
        <label>Ende <input name="end_time" type="time"></label>
        <label>Standort
          <select name="location">
            <option value="UNKNOWN">Unbekannt</option>
            <option value="OFFICE">Büro</option>
            <option value="HOME">Homeoffice</option>
          </select>
        </label>
        <button>Segment hinzufügen</button>
      </form>
      <form id="note-form">
        <label>Notiz
          <textarea name="note">${escapeHtml(detail.note || "")}</textarea>
        </label>
        <button style="margin-top:8px">Notiz speichern</button>
      </form>
    </div>
  `;
}

function renderDayNotePreview(note) {
  if (!note) return "";
  return `
    <div class="day-note-preview">
      <span>Tagesnotiz</span>
      <strong>${escapeHtml(note)}</strong>
    </div>
  `;
}

function renderSegmentTable(segments, date) {
  if (!segments.length) return `<p class="muted">Noch keine Segmente für diesen Tag.</p>`;
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Typ</th><th>Beginn</th><th>Ende</th><th>Standort</th><th>Aktionen</th></tr></thead>
        <tbody>
          ${segments.map(segment => `
            <tr data-id="${segment.id}">
              <td>
                <select name="type">
                  ${["WORK", "BREAK", "ABSENCE"].map(value => `<option value="${value}" ${segment.type === value ? "selected" : ""}>${segmentTypeLabel(value)}</option>`).join("")}
                </select>
              </td>
              <td><input name="start_time" type="time" value="${escapeHtml((segment.start_time || "").slice(0, 5))}"></td>
              <td><input name="end_time" type="time" value="${escapeHtml((segment.end_time || "").slice(0, 5))}"></td>
              <td>
                <select name="location">
                  ${["UNKNOWN", "OFFICE", "HOME"].map(value => `<option value="${value}" ${segment.location === value ? "selected" : ""}>${locationLabel(value)}</option>`).join("")}
                </select>
              </td>
              <td class="row-actions">
                <button class="save-segment" data-date="${date}">Speichern</button>
                <button class="danger delete-segment">Löschen</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function bindDayForms(date, refresh = () => loadDayDetail(date)) {
  document.querySelectorAll(".save-segment").forEach(button => {
    button.addEventListener("click", async event => {
      event.preventDefault();
      const row = button.closest("tr");
      await api("save_segment", collectSegment(row, date));
      notify("Segment gespeichert");
      await refresh();
    });
  });
  document.querySelectorAll(".delete-segment").forEach(button => {
    button.addEventListener("click", async event => {
      event.preventDefault();
      if (!confirm("Segment wirklich löschen?")) return;
      const row = button.closest("tr");
      await api("delete_segment", Number(row.dataset.id));
      notify("Segment gelöscht");
      await refresh();
    });
  });
  document.getElementById("new-segment").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await api("save_segment", {
      date,
      type: form.type.value,
      start_time: form.start_time.value,
      end_time: form.end_time.value,
      location: form.location.value,
      source: "MANUAL",
    });
    notify("Segment hinzugefügt");
    await refresh();
  });
  document.getElementById("note-form").addEventListener("submit", async event => {
    event.preventDefault();
    await api("save_note", date, event.currentTarget.note.value);
    notify("Notiz gespeichert");
    await refresh();
  });
}

function collectSegment(row, date) {
  return {
    id: Number(row.dataset.id),
    date,
    type: row.querySelector('[name="type"]').value,
    start_time: row.querySelector('[name="start_time"]').value,
    end_time: row.querySelector('[name="end_time"]').value,
    location: row.querySelector('[name="location"]').value,
    source: "MANUAL",
  };
}

async function renderEntries() {
  const year = new Date().getFullYear();
  content.innerHTML = `
    <div class="page-head">
      <div><h1>Einträge</h1><p>Sortieren, filtern und durchsuchen.</p></div>
    </div>
    <section class="toolbar">
      <label>Von <input id="entry-start" type="date" value="${year}-01-01"></label>
      <label>Bis <input id="entry-end" type="date" value="${isoToday()}"></label>
      <label>Suche <input id="entry-search" type="search" placeholder="Notiz, Typ, Standort"></label>
      <button id="load-entries">Aktualisieren</button>
    </section>
    <section id="entries-table"></section>
  `;
  document.getElementById("load-entries").addEventListener("click", loadEntries);
  document.getElementById("entry-search").addEventListener("input", drawEntries);
  await loadEntries();
}

async function loadEntries() {
  const start = document.getElementById("entry-start").value;
  const end = document.getElementById("entry-end").value;
  const result = await api("entries", start, end);
  state.entries = result.rows;
  drawEntries();
}

function drawEntries() {
  const target = document.getElementById("entries-table");
  const query = document.getElementById("entry-search").value.toLowerCase();
  const rows = state.entries
    .filter(row => Object.values(row).join(" ").toLowerCase().includes(query))
    .sort((a, b) => String(a[state.entriesSort.key]).localeCompare(String(b[state.entriesSort.key])) * state.entriesSort.direction);
  if (state.entryEditDate && !rows.some(row => row.date === state.entryEditDate)) {
    state.entryEditDate = null;
  }
  target.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr>${[
          ["date", "Datum"], ["start", "Beginn"], ["end", "Ende"], ["break_minutes", "Pause"],
          ["actual_minutes", "Stunden"], ["balance_minutes", "Saldo"], ["type", "Typ"], ["location", "Standort"], ["note", "Notiz"]
        ].map(([key, label]) => `<th data-key="${key}">${label}</th>`).join("")}<th>Aktionen</th></tr></thead>
        <tbody>${rows.map(row => {
          const editing = state.entryEditDate === row.date;
          return `
          <tr class="entry-row ${editing ? "is-editing" : ""}">
            <td>${row.date}</td>
            <td>${timeShort(row.start)}</td>
            <td>${timeShort(row.end)}</td>
            <td>${fmtMinutes(row.break_minutes)}</td>
            <td>${fmtMinutes(row.actual_minutes)}</td>
            <td class="${row.balance_minutes >= 0 ? "positive" : "negative"}">${signedMinutes(row.balance_minutes)}</td>
            <td>${categoryLabel(row.type)}</td>
            <td>${locationLabel(row.location)}</td>
            <td>${escapeHtml(row.note)}</td>
            <td><button class="secondary edit-entry" data-date="${row.date}" aria-expanded="${editing ? "true" : "false"}">${editing ? "Schließen" : "Bearbeiten"}</button></td>
          </tr>
          ${editing ? `<tr class="entry-editor-row"><td colspan="10"><section id="entry-edit-panel" class="detail-panel stack entry-inline-panel"><div class="loading">Lade Eintrag ${escapeHtml(row.date)} …</div></section></td></tr>` : ""}
        `;
        }).join("")}</tbody>
      </table>
    </div>
  `;
  target.querySelectorAll("th[data-key]").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.entriesSort.key === key) state.entriesSort.direction *= -1;
      else state.entriesSort = { key, direction: 1 };
      drawEntries();
    });
  });
  target.querySelectorAll(".edit-entry").forEach(button => {
    button.addEventListener("click", () => {
      state.entryEditDate = state.entryEditDate === button.dataset.date ? null : button.dataset.date;
      drawEntries();
    });
  });
  if (state.entryEditDate) renderEntryEditor(state.entryEditDate);
}

async function renderEntryEditor(date) {
  const panel = document.getElementById("entry-edit-panel");
  if (!panel) return;
  panel.className = "detail-panel stack entry-inline-panel";
  panel.innerHTML = `<div class="loading">Lade Eintrag ${escapeHtml(date)} …</div>`;
  const detail = await api("day_detail", date);
  if (state.entryEditDate !== date) return;
  panel.innerHTML = `
    <div class="page-head compact">
      <div>
        <h2>${date} bearbeiten</h2>
        <p>${escapeHtml(categoryLabel(detail.summary.day_category))} · Ist ${fmtMinutes(detail.summary.actual_minutes)} · Pause ${fmtMinutes(detail.summary.break_minutes)} · Saldo ${signedMinutes(detail.summary.balance_minutes)}</p>
      </div>
      <button class="secondary" id="close-entry-editor">Schließen</button>
    </div>
    ${renderDayEditorContent(detail, date)}
  `;
  document.getElementById("close-entry-editor").addEventListener("click", () => {
    state.entryEditDate = null;
    drawEntries();
  });
  bindDayForms(date, async () => {
    await loadEntries();
  });
}

async function renderStatistics() {
  const year = new Date().getFullYear();
  content.innerHTML = `
    <div class="page-head">
      <div><h1>Statistiken</h1><p>Monats- und Jahreswerte.</p></div>
      <label>Jahr <input id="stats-year" type="number" value="${year}" min="2000" max="2100"></label>
    </div>
    <section id="stats-body" class="stack"></section>
  `;
  document.getElementById("stats-year").addEventListener("change", loadStatistics);
  await loadStatistics();
}

async function loadStatistics() {
  const year = Number(document.getElementById("stats-year").value);
  const data = await api("statistics", year);
  const body = document.getElementById("stats-body");
  body.innerHTML = `
    <div class="grid cols-4">
      ${metric("Soll", fmtMinutes(data.target_minutes))}
      ${metric("Ist", fmtMinutes(data.actual_minutes))}
      ${balanceMetric("Gleitzeit", data.flextime_hours, data.flextime_status, data.flextime_minutes)}
      ${metric("Resturlaub", `${numberDe(data.remaining_vacation)} Tage`)}
      ${metric("Urlaub", `${numberDe(data.vacation_used)} Tage`)}
      ${metric("Krank", `${numberDe(data.sick_used)} Tage`)}
      ${metric("Büro", `${data.office_days} Tage`)}
      ${metric("Homeoffice", `${data.homeoffice_days} Tage`)}
    </div>
    <div class="panel">
      <h2>Monatssalden</h2>
      <canvas id="stats-chart" height="280" aria-label="Balkendiagramm der Monatssalden"></canvas>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Monat</th><th>Soll</th><th>Ist</th><th>Saldo</th><th>Kumuliert</th><th>Urlaub</th><th>Krank</th><th>Homeoffice</th></tr></thead>
        <tbody>${data.months.map(month => `
          <tr>
            <td>${month.year_month}</td>
            <td>${fmtMinutes(month.target_minutes)}</td>
            <td>${fmtMinutes(month.actual_minutes)}</td>
            <td>${signedMinutes(month.balance_minutes)}</td>
            <td>${balanceBadge(month.carry_over_hours, month.carry_over_status)}</td>
            <td>${numberDe(month.vacation_days_used)}</td>
            <td>${numberDe(month.sick_days_used)}</td>
            <td>${month.homeoffice_days}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
  `;
  drawStatsChart(data.months);
}

function drawStatsChart(months) {
  const canvas = document.getElementById("stats-chart");
  if (state.statsChart) state.statsChart.destroy();
  state.statsChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: months.map(month => month.year_month.slice(5)),
      datasets: [{ label: "Saldo in Minuten", data: months.map(month => month.balance_minutes) }],
    },
  });
}

async function renderVacation() {
  const year = new Date().getFullYear();
  content.innerHTML = `
    <div class="page-head">
      <div><h1>Urlaub und Abwesenheiten</h1><p>Urlaub, Gleitzeit, Krankheit, Dienstreisen und Feiertags-Ausnahmen eintragen.</p></div>
    </div>
    <section class="panel">
      <form id="absence-form" class="grid cols-2">
        <label>Von <input name="start_date" type="date" value="${isoToday()}" required></label>
        <label>Bis <input name="end_date" type="date" value="${isoToday()}" required></label>
        <label>Typ
          <select name="type">
            <option value="URLAUB">Urlaub</option>
            <option value="KRANK">Krank</option>
            <option value="FEIERTAG">Feiertag (Ausnahme)</option>
            <option value="GLEITZEITTAG">Gleitzeittag</option>
            <option value="DIENSTREISE">Dienstreise</option>
          </select>
        </label>
        <label>Umfang
          <select name="half_day"><option value="0">Ganzer Tag</option><option value="1">Halber Tag</option></select>
        </label>
        <label style="grid-column:1 / -1">Notiz <input name="note" type="text"></label>
        <button>Speichern</button>
      </form>
    </section>
    <section class="panel stack" style="margin-top:16px">
      <div class="page-head compact">
        <div><h2>Geplante Abwesenheiten</h2><p>Zusammenhängende Einträge mit angerechneten Arbeitstagen.</p></div>
        <label>Jahr <input id="absence-year" type="number" value="${year}" min="2000" max="2100"></label>
      </div>
      <div id="absence-list" class="loading">Lade Abwesenheiten …</div>
    </section>
  `;
  document.getElementById("absence-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await api("add_day_type_range", {
      start_date: form.start_date.value,
      end_date: form.end_date.value,
      type: form.type.value,
      half_day: form.half_day.value === "1",
      note: form.note.value,
    });
    notify("Abwesenheit gespeichert");
    form.reset();
    form.start_date.value = isoToday();
    form.end_date.value = isoToday();
    await loadAbsences();
  });
  document.getElementById("absence-year").addEventListener("change", loadAbsences);
  await loadAbsences();
}

async function loadAbsences() {
  const year = Number(document.getElementById("absence-year").value);
  const data = await api("absences", year);
  const target = document.getElementById("absence-list");
  if (!data.rows.length) {
    target.className = "empty";
    target.textContent = "Noch keine Urlaube oder Abwesenheiten für dieses Jahr eingetragen.";
    return;
  }
  target.className = "table-wrap";
  target.innerHTML = `
    <table>
      <thead><tr><th>Zeitraum</th><th>Typ</th><th>Kalendertage</th><th>Angerechnet</th><th>Notiz</th><th>Aktionen</th></tr></thead>
      <tbody>${data.rows.map(row => `
        <tr>
          <td>${escapeHtml(periodLabel(row.start_date, row.end_date))}</td>
          <td>${categoryLabel(row.type)}${row.half_day ? " (halb)" : ""}</td>
          <td>${numberDe(row.days)}</td>
          <td>${escapeHtml(absenceCountLabel(row))}</td>
          <td>${escapeHtml(row.note || "")}</td>
          <td><button class="secondary delete-absence" data-ids="${escapeHtml((row.ids || []).join(","))}">Entfernen</button></td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
  target.querySelectorAll(".delete-absence").forEach(button => {
    button.addEventListener("click", async () => {
      if (!confirm("Abwesenheit wirklich entfernen?")) return;
      const ids = button.dataset.ids.split(",").filter(Boolean).map(Number);
      const result = await api("delete_day_type_range", { ids });
      if (result.ok === false) {
        notify(result.error || "Abwesenheit konnte nicht entfernt werden", "error");
        return;
      }
      notify("Abwesenheit entfernt");
      await loadAbsences();
    });
  });
}

async function renderSettings() {
  const settings = await api("settings");
  const sections = settingsSections(settings);
  if (state.settingsDetailKey && !sections.some(section => section.key === state.settingsDetailKey)) {
    state.settingsDetailKey = sections[0]?.key || "work";
  }
  const active = state.settingsDetailKey ? sections.find(section => section.key === state.settingsDetailKey) : null;
  content.innerHTML = `
    <div class="page-head">
      <div><h1>Einstellungen</h1><p>Arbeitsmodell, Urlaub und Abwesenheiten, Standortcheck, Backup und Export.</p></div>
    </div>
    <section class="settings-card-grid">
      ${sections.map(section => settingsSectionCard(section)).join("")}
    </section>
    ${active ? renderSettingsPanel(active, settings) : ""}
  `;
  document.querySelectorAll(".settings-card").forEach(button => {
    button.addEventListener("click", () => {
      state.settingsDetailKey = state.settingsDetailKey === button.dataset.section ? "" : button.dataset.section;
      renderSettings();
    });
  });
  bindSettingsForm(active);
  document.getElementById("backup-now")?.addEventListener("click", async () => {
    const result = await api("create_backup");
    notify(`Backup erstellt: ${result.name}`);
  });
  document.getElementById("export-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("export_period", form.start_date.value, form.end_date.value, form.format.value);
    notify(`Export erstellt: ${result.name}`);
  });
  document.getElementById("open-reset-dialog")?.addEventListener("click", openResetDialog);
}

function settingsSections(settings) {
  const sections = [];
  if (settings.initial_setup_required === "1") {
    sections.push({
      key: "setup",
      title: "Ersteinrichtung",
      summary: "Startwerte nach Reset eintragen",
      body: renderSetupSettings(settings),
      setup: true,
    });
  }
  return sections.concat([
    {
      key: "work",
      title: "Arbeitsmodell",
      summary: "Sollzeit, Pause und Arbeitstage",
      body: renderWorkSettings(settings),
    },
    {
      key: "start",
      title: "Startwerte",
      summary: "Gleitzeit, Urlaub und Nachträge",
      body: renderSetupSettings(settings),
      setup: false,
    },
    {
      key: "location",
      title: "Standort & Puffer",
      summary: "Büro/Homeoffice-Erkennung und Startversatz",
      body: renderLocationSettings(settings),
    },
    {
      key: "appearance",
      title: "Darstellung",
      summary: "Oberfläche und Lesbarkeit",
      body: renderAppearanceSettings(settings),
    },
    {
      key: "files",
      title: "Backup & Export",
      summary: "Lokale Sicherungen und Ausgaben",
      body: renderFileSettings(),
      plain: true,
    },
    {
      key: "reset",
      title: "Zurücksetzen",
      summary: "Daten oder Einstellungen gezielt leeren",
      body: renderResetSettings(),
      danger: true,
      plain: true,
    },
  ]);
}

function settingsSectionCard(section) {
  const active = state.settingsDetailKey === section.key;
  return `
    <button class="settings-card ${active ? "active" : ""} ${section.danger ? "danger-zone" : ""}" type="button" data-section="${escapeHtml(section.key)}" aria-expanded="${active ? "true" : "false"}">
      <span>${escapeHtml(section.title)}</span>
      <small>${escapeHtml(section.summary)}</small>
    </button>
  `;
}

function renderSettingsPanel(section, settings) {
  return `
    <section class="detail-panel settings-detail-panel" aria-live="polite">
      <div class="page-head compact">
        <div>
          <h2>${escapeHtml(section.title)}</h2>
          <p>${escapeHtml(section.summary)}</p>
        </div>
        <button class="secondary" id="close-settings-detail" type="button">Schließen</button>
      </div>
      ${section.plain ? section.body : `
        <form id="settings-form" class="grid cols-2" data-section="${escapeHtml(section.key)}">
          ${section.body}
          ${section.setup ? `<input type="hidden" name="initial_setup_required" value="0">` : ""}
          <button>${section.setup ? "Einrichtung speichern" : "Speichern"}</button>
        </form>
      `}
    </section>
  `;
}

function renderWorkSettings(settings) {
  return `
    <label>Wochenarbeitszeit (Stunden) <input name="weekly_target_hours" type="text" value="${escapeHtml(settings.weekly_target_hours)}"></label>
    <label>Automatische Pausenzeit (Minuten)
      <input name="daily_break_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.daily_break_minutes || "0")}">
      <small class="help-text">Wird von der Arbeitszeit abgezogen, wenn keine oder zu kurze Pausensegmente erfasst sind. 0 deaktiviert die automatische Pause.</small>
    </label>
    <fieldset class="weekday-picker">
      <legend>Arbeitstage</legend>
      ${weekdayCheckboxes(settings.workday_weekdays)}
    </fieldset>
    <label>Individuelle Tages-Sollzeiten (JSON Minuten) <input name="weekday_target_minutes" type="text" value='${escapeHtml(settings.weekday_target_minutes)}'></label>
    <label>Bundesland <input name="bundesland" type="text" value="${escapeHtml(settings.bundesland)}" placeholder="z. B. BW, BY, NRW"></label>
  `;
}

function renderSetupSettings(settings) {
  return `
    <label>Urlaubsanspruch/Jahr <input name="vacation_days_per_year" type="text" value="${escapeHtml(settings.vacation_days_per_year)}"></label>
    <label>Urlaubsübertrag Vorjahr <input name="vacation_carry_over" type="text" value="${escapeHtml(settings.vacation_carry_over)}"></label>
    <label>Startdatum der Zeiterfassung
      <input name="tracking_start_date" type="date" value="${escapeHtml(settings.tracking_start_date || "")}">
      <small class="help-text">Tage vor dem Startdatum werden für den Gleitzeitsaldo ignoriert.</small>
    </label>
    <label>Anfangssaldo Gleitzeit in Stunden
      <input name="initial_flextime_hours" type="text" inputmode="decimal" value="${escapeHtml(settings.initial_flextime_hours || "0,00")}" placeholder="50,89 oder -3.75">
      <small class="help-text">Komma und Punkt sind erlaubt, z. B. 50,89, -3,75 oder 12.5.</small>
    </label>
    <label>Manuelle Büro-Tage
      <input name="office_baseline_days" type="text" inputmode="decimal" value="${escapeHtml(settings.office_baseline_days || "0")}" placeholder="z. B. 42">
      <small class="help-text">Tage vor der Nutzung, die in die Officequote einfließen sollen.</small>
    </label>
    <label>Manuelle Homeoffice-Tage
      <input name="homeoffice_baseline_days" type="text" inputmode="decimal" value="${escapeHtml(settings.homeoffice_baseline_days || "0")}" placeholder="z. B. 38">
      <small class="help-text">Tage vor der Nutzung, die in die Homeofficequote einfließen sollen.</small>
    </label>
  `;
}

function renderLocationSettings(settings) {
  return `
    <label>Standort-Ziele host:port
      <input name="homeoffice_check_targets" type="text" value="${escapeHtml(settings.homeoffice_check_targets)}" placeholder="intranet.local:443,10.0.0.10:445">
      <small class="help-text">Erreichbar bedeutet Büro, nicht erreichbar bedeutet Homeoffice.</small>
    </label>
    <label>Timeout Standortcheck (ms) <input name="homeoffice_check_timeout_ms" type="number" min="50" value="${escapeHtml(settings.homeoffice_check_timeout_ms)}"></label>
    <label>Startpuffer Büro (Minuten)
      <input name="office_start_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.office_start_buffer_minutes || "0")}">
      <small class="help-text">Wird nur beim automatischen Arbeitsbeginn abgezogen.</small>
    </label>
    <label>Startpuffer Homeoffice (Minuten)
      <input name="home_start_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.home_start_buffer_minutes || "0")}">
      <small class="help-text">Für den Start im privaten WLAN vor VPN-Verbindung.</small>
    </label>
  `;
}

function renderAppearanceSettings(settings) {
  return `
    <label>Darkmode
      <select name="darkmode">
        <option value="0" ${settings.darkmode === "0" ? "selected" : ""}>Aus</option>
        <option value="1" ${settings.darkmode === "1" ? "selected" : ""}>An</option>
      </select>
    </label>
  `;
}

function renderFileSettings() {
  return `
    <div class="stack">
      <div class="row-actions">
        <button id="backup-now">Backup jetzt erstellen</button>
      </div>
      <form id="export-form" class="form-row">
        <label>Von <input name="start_date" type="date" value="${new Date().getFullYear()}-01-01"></label>
        <label>Bis <input name="end_date" type="date" value="${isoToday()}"></label>
        <label>Format
          <select name="format"><option value="xlsx">Excel</option><option value="csv">CSV</option><option value="pdf">PDF</option></select>
        </label>
        <button>Export erstellen</button>
      </form>
    </div>
  `;
}

function renderResetSettings() {
  return `
    <div class="stack">
      <p class="muted">Wähle im Dialog genau aus, ob nur Einstellungen, nur Trackingdaten oder alles zurückgesetzt werden soll.</p>
      <button id="open-reset-dialog" class="danger" type="button">Zurücksetzen öffnen</button>
    </div>
  `;
}

function bindSettingsForm(section) {
  document.getElementById("close-settings-detail")?.addEventListener("click", () => {
    state.settingsDetailKey = "";
    renderSettings();
  });
  const form = document.getElementById("settings-form");
  if (!form) return;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const values = collectSettingsValues(form);
    try {
      const result = await api("save_settings", values);
      document.body.classList.toggle("dark", result.settings.darkmode === "1");
      notify(section?.setup ? "Einrichtung gespeichert" : "Einstellungen gespeichert");
      await renderSettings();
    } catch (error) {
      notify(error.message || String(error), "error");
    }
  });
}

function collectSettingsValues(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const weekdayInputs = [...form.querySelectorAll('input[name="workday_weekday"]')];
  if (weekdayInputs.length) {
    values.workday_weekdays = weekdayInputs.filter(input => input.checked).map(input => input.value).join(",");
    delete values.workday_weekday;
  }
  return values;
}

function openResetDialog() {
  document.querySelector(".modal-backdrop")?.remove();
  content.insertAdjacentHTML("beforeend", `
    <div class="modal-backdrop" role="presentation">
      <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="reset-title">
        <div class="page-head compact">
          <div>
            <h2 id="reset-title">Zurücksetzen</h2>
            <p>Was soll gelöscht oder zurückgesetzt werden?</p>
          </div>
          <button class="secondary" id="close-reset-dialog" type="button">Schließen</button>
        </div>
        <form id="reset-form" class="stack">
          <label class="choice-row">
            <input type="radio" name="mode" value="settings" checked>
            <span><strong>Nur Einstellungen</strong><small>Daten bleiben erhalten, Einstellungen gehen auf Standardwerte.</small></span>
          </label>
          <label class="choice-row">
            <input type="radio" name="mode" value="data">
            <span><strong>Nur Trackingdaten</strong><small>Segmente, Sondertage, Notizen, Salden und Urlaubskonten werden geleert.</small></span>
          </label>
          <label class="choice-row danger-zone">
            <input type="radio" name="mode" value="all">
            <span><strong>Alles zurücksetzen</strong><small>Daten und Einstellungen werden geleert, danach startet die Ersteinrichtung.</small></span>
          </label>
          <div class="row-actions">
            <button class="danger" type="submit">Ausführen</button>
            <button class="secondary" id="cancel-reset-dialog" type="button">Abbrechen</button>
          </div>
        </form>
      </section>
    </div>
  `);
  const close = () => document.querySelector(".modal-backdrop")?.remove();
  document.getElementById("close-reset-dialog").addEventListener("click", close);
  document.getElementById("cancel-reset-dialog").addEventListener("click", close);
  document.querySelector(".modal-backdrop").addEventListener("click", event => {
    if (event.target.classList.contains("modal-backdrop")) close();
  });
  document.getElementById("reset-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const mode = form.mode.value;
    const label = mode === "all" ? "alles zurücksetzen" : mode === "data" ? "Trackingdaten zurücksetzen" : "Einstellungen zurücksetzen";
    if (!confirm(`Wirklich ${label}?`)) return;
    try {
      const result = await api("reset_application", { mode });
      document.body.classList.toggle("dark", result.settings.darkmode === "1");
      close();
      state.settingsDetailKey = mode === "all" ? "setup" : "work";
      state.entryEditDate = null;
      state.calendarDetailDate = null;
      state.dashboardDetailKey = null;
      notify("Zurücksetzen abgeschlossen");
      await renderSettings();
    } catch (error) {
      notify(error.message || String(error), "error");
    }
  });
}

function metric(label, value, signedValue = null) {
  const klass = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
  return `<article class="metric"><span>${label}</span><strong class="${klass}">${value}</strong></article>`;
}

function weekdayCheckboxes(rawWeekdays) {
  const selected = new Set(String(rawWeekdays || "0,1,2,3,4").split(",").filter(Boolean));
  return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].map((label, index) => `
    <label class="checkbox-row">
      <input name="workday_weekday" type="checkbox" value="${index}" ${selected.has(String(index)) ? "checked" : ""}>
      <span>${label}</span>
    </label>
  `).join("");
}

function balanceMetric(label, value, status, signedValue = null) {
  const valueClass = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
  return `<article class="metric balance-card ${escapeHtml(status?.class || "")}">
    <span>${label}</span>
    <strong class="${valueClass}">${escapeHtml(value)}</strong>
    <small class="balance-card-status">${escapeHtml(status?.label || "0 bis 45 Stunden")}</small>
  </article>`;
}

function dashboardMetric(metricConfig) {
  const klass = metricConfig.signedValue === undefined ? "" : metricConfig.signedValue >= 0 ? "positive" : "negative";
  const active = state.dashboardDetailKey === metricConfig.key;
  return `<article class="metric dashboard-card ${escapeHtml(metricConfig.extraClass || "")} ${active ? "active" : ""}">
    <button class="metric-trigger" type="button" data-metric="${escapeHtml(metricConfig.key)}" aria-expanded="${active ? "true" : "false"}">
      <span>${escapeHtml(metricConfig.label)}</span>
      <strong class="${klass}">${escapeHtml(metricConfig.value)}</strong>
      <em aria-hidden="true"></em>
    </button>
  </article>`;
}

function dashboardDetailPanel(metricConfig) {
  return `<section class="dashboard-detail-panel" aria-live="polite">
    <div class="page-head compact">
      <div>
        <h2>${escapeHtml(metricConfig.label)}</h2>
        <p>Details zur ausgewählten Dashboard-Kachel.</p>
      </div>
      <button class="secondary" id="close-dashboard-detail" type="button">Schließen</button>
    </div>
    <div class="dashboard-detail-grid">${metricConfig.detailHtml}</div>
  </section>`;
}

function detailRow(label, value, valueClass = "") {
  return `<div class="detail-row"><span>${escapeHtml(label)}</span><strong class="${valueClass}">${escapeHtml(value)}</strong></div>`;
}

function detailText(value, klass = "") {
  return `<p class="${klass}">${escapeHtml(value)}</p>`;
}

function balanceBadge(value, status) {
  const klass = escapeHtml(status?.class || "balance-ok");
  const label = escapeHtml(status?.label || "0 bis 45 Stunden");
  return `<span class="balance-badge ${klass}" title="${label}">${escapeHtml(value)} · ${label}</span>`;
}

async function api(name, ...args) {
  if (!window.pywebview?.api?.[name]) throw new Error(`API nicht verfügbar: ${name}`);
  return window.pywebview.api[name](...args);
}

function startCommandPolling() {
  if (commandPollTimer) return;
  checkAppCommand();
  commandPollTimer = setInterval(checkAppCommand, 800);
}

function startAutoRefresh() {
  if (autoRefreshTimer) return;
  window.addEventListener("focus", () => {
    if (shouldAutoRefresh()) render();
  });
  autoRefreshTimer = setInterval(() => {
    if (shouldAutoRefresh()) render();
  }, 60000);
}

function shouldAutoRefresh() {
  const active = document.activeElement;
  if (active?.closest?.("form")) return false;
  if (state.view === "settings") return false;
  return true;
}

async function checkAppCommand() {
  if (!window.pywebview?.api?.consume_app_command) return;
  try {
    const command = await api("consume_app_command");
    if (command?.view) setView(command.view);
  } catch (error) {
    console.warn(error);
  }
}

function notify(message, type = "info") {
  toast.textContent = message;
  toast.classList.toggle("error", type === "error");
  toast.classList.add("show");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove("show"), 3600);
}

function showError(error) {
  console.error(error);
  content.innerHTML = `<section class="panel"><h1>Fehler</h1><p>${escapeHtml(error.message || String(error))}</p><button onclick="render()">Erneut versuchen</button></section>`;
}

function fmtMinutes(minutes) {
  const value = Math.max(0, Math.round(Number(minutes) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function signedMinutes(minutes) {
  const value = Math.round(Number(minutes) || 0);
  return `${value >= 0 ? "+" : "-"}${fmtMinutes(Math.abs(value))}`;
}

function isoWeekNumber(date) {
  const utcDate = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = utcDate.getUTCDay() || 7;
  utcDate.setUTCDate(utcDate.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(utcDate.getUTCFullYear(), 0, 1));
  return Math.ceil((((utcDate - yearStart) / 86400000) + 1) / 7);
}

function numberDe(value) {
  return new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function periodLabel(startDate, endDate) {
  return startDate === endDate ? startDate : `${startDate} bis ${endDate}`;
}

function absenceCountLabel(row) {
  const value = numberDe(row.counted_days);
  if (row.type === "URLAUB") return `${value} Urlaubstage`;
  if (row.type === "KRANK") return `${value} Arbeitstage`;
  return `${value} Tage`;
}

function isFutureDate(dateText) {
  return dateText > isoToday();
}

function timeShort(value) {
  return value ? String(value).slice(0, 5) : "";
}

function isoToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function initialView() {
  const allowed = new Set(["dashboard", "calendar", "entries", "statistics", "vacation", "settings"]);
  const view = window.location.hash.replace("#", "");
  return allowed.has(view) ? view : "dashboard";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function categoryLabel(value) {
  return {
    WORKDAY: "Arbeitstag",
    WEEKEND: "Wochenende",
    HOLIDAY: "Feiertag",
    VACATION: "Urlaub",
    SICK: "Krank",
    TRAVEL: "Dienstreise",
    FLEXTIME: "Gleitzeittag",
    NOT_TRACKED: "Vor Startdatum",
    URLAUB: "Urlaub",
    KRANK: "Krank",
    FEIERTAG: "Feiertag",
    DIENSTREISE: "Dienstreise",
    GLEITZEITTAG: "Gleitzeittag",
  }[value] || value || "";
}

function segmentTypeLabel(value) {
  return { WORK: "Arbeit", BREAK: "Pause", ABSENCE: "Abwesenheit" }[value] || value;
}

function locationLabel(value) {
  return { OFFICE: "Büro", HOME: "Homeoffice", MIXED: "Gemischt", UNKNOWN: "Unbekannt", "": "" }[value] || value || "";
}

function dayLabel(summary) {
  if (!summary) return "";
  if (summary.day_category === "WORKDAY") return summary.location === "HOME" ? "Homeoffice" : summary.location === "OFFICE" ? "Büro" : "Arbeit";
  return categoryLabel(summary.day_category);
}

function dayClass(summary) {
  if (!summary) return "";
  if (summary.day_category === "WORKDAY") {
    if (summary.location === "HOME") return "home";
    if (summary.location === "OFFICE") return "office";
    return "";
  }
  return {
    WEEKEND: "weekend",
    HOLIDAY: "holiday",
    VACATION: "vacation",
    SICK: "sick",
    TRAVEL: "travel",
    FLEXTIME: "flex",
    NOT_TRACKED: "weekend",
  }[summary.day_category] || "";
}
