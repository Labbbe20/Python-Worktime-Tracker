const state = {
  view: initialView(),
  calendarDate: new Date(),
  selectedDate: isoToday(),
  calendarDetailDate: null,
  entryEditDate: null,
  dashboardDetailKey: null,
  settingsDetailKey: "",
  entries: [],
  entriesSort: { key: "date", direction: -1 },
  entryFilters: {
    dayType: "all",
    location: "all",
    balance: "all",
    status: "all",
    note: "all",
    minHours: "",
    maxHours: "",
  },
  statsChart: null,
  dashboardData: null,
  calculatorDefaults: null,
  calculatorRows: [],
  calculatorStartBalanceHours: "",
  calculatorLimitHours: "50",
  autoRefreshIntervalSeconds: 60,
  sapSdataPreview: null,
  settingsSubnavScrollTop: 0,
};

const content = document.getElementById("content");
const toast = document.getElementById("toast");
const shell = document.querySelector(".shell");
let commandPollTimer = null;
let autoRefreshTimer = null;
let autoRefreshInFlight = false;
let refreshFocusHandlerAttached = false;
let chartLibraryPromise = null;

const SETTINGS_HELP = {
  export: {
    title: "Export",
    paragraphs: [
      "Der Export erstellt eine Datei mit deinen lokalen Arbeitszeiten, Abwesenheiten und Notizen. Excel ist am besten geeignet, wenn du die Daten später wieder importieren möchtest.",
      "Der Zeitraum wird automatisch bis zum neuesten sinnvollen Eintrag erweitert, damit auch geplante Urlaube oder Abwesenheiten enthalten sind.",
    ],
  },
  import_excel: {
    title: "Excel-Import",
    paragraphs: [
      "Am einfachsten nimmst du eine vorher exportierte Excel-Datei als Vorlage. In der Übersicht reicht pro Tag Datum, Start, Ende und Standort; Details aus Segmente, Abwesenheiten und Notizen werden bevorzugt, wenn sie vorhanden sind.",
      "Beim Import werden nur die Tage überschrieben, die in der Datei wirklich vorkommen. Danach wird ein HTML-Protokoll mit Vorher/Nachher und Warnungen geschrieben.",
    ],
  },
  sap_sdata: {
    title: "SAP-SDATA-Import",
    paragraphs: [
      "Die SAP-Zeitereignisse liegen typischerweise in den Tabellen EDIDC und EDID4. Suche zuerst in EDIDC nach deinem Datumsbereich und dem Nachrichtentyp HRCC1UPTEVEN. Kopiere danach die gefundenen DOCNUM-Werte.",
      "Öffne danach EDID4, füge die DOCNUM-Werte ein und achte darauf, dass die Ergebnisanzahl hoch genug eingestellt ist. Filtere anschließend die Spalte SDATA auf deine eigene Personalnummer oder nutze ein dafür gespeichertes eigenes Layout.",
      "Exportiere die Treffer als Excel- oder CSV-Datei. Wichtig ist nur, dass die Spalte SDATA enthalten ist. P10 wird als Arbeitsbeginn gelesen, P20 als Arbeitsende; SAP-SDATA gilt hier immer als Bürotag.",
    ],
  },
  location_targets: {
    title: "Standort-Ziele",
    paragraphs: [
      "Trage interne Ziele ein, die nur im Büro oder per Firmennetz erreichbar sind, zum Beispiel intranet.firma.local, server:443 oder https://intranet.firma.local.",
      "Ist eines dieser Ziele erreichbar, wird der Tag als Büro erkannt. Ist keines erreichbar, wird Homeoffice verwendet. Wenn du unsicher bist, frage deine IT nach einem internen Servernamen.",
    ],
  },
};

document.querySelectorAll(".nav button[data-view]").forEach(button => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.getElementById("refresh-view")?.addEventListener("click", () => refreshCurrentView({ silent: state.view !== "settings" }));

window.addEventListener("pywebviewready", async () => {
  syncNav();
  startCommandPolling();
  try {
    await render();
    const settings = state.dashboardData?.settings || await loadInitialTheme();
    startAutoRefresh(settings);
    checkInitialSetup(settings);
  } catch (error) {
    showError(error);
  }
});

setTimeout(() => {
  if (!window.pywebview) {
    content.innerHTML = `<section class="panel"><h1>pywebview nicht verbunden</h1><p class="muted">Starte die App mit <code>python3 main.py --app</code>, damit die lokale Python-API verfügbar ist.</p></section>`;
  }
}, 1200);

async function loadInitialTheme() {
  try {
    const settings = await api("settings");
    document.body.classList.toggle("dark", settings.darkmode === "1");
    return settings;
  } catch (error) {
    console.warn(error);
    return null;
  }
}

async function checkInitialSetup(settings = null) {
  try {
    settings = settings || await api("settings");
    if (settings.initial_setup_required !== "1") return;
    if (state.view !== "settings") {
      notify("Startwerte sind noch offen. Du findest sie in den Einstellungen.");
      return;
    }
    state.settingsDetailKey = "start";
    window.location.hash = "settings";
    syncNav();
    await renderSettings();
    notify("Bitte einmal die Startwerte einrichten.");
  } catch (error) {
    console.warn(error);
  }
}

function setView(view) {
  if (view === "settings") {
    state.settingsDetailKey = "";
  }
  state.view = view;
  window.location.hash = view;
  syncNav();
  render();
}

window.__worktimeSetView = setView;

function syncNav() {
  shell?.classList.toggle("utility-view", state.view === "settings");
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
    if (state.view === "calculator") return renderCalculator();
    if (state.view === "settings") return renderSettings();
  } catch (error) {
    showError(error);
  }
}

async function renderDashboard() {
  const data = await api("dashboard");
  renderDashboardFrame(data);
}

function renderDashboardFrame(data) {
  state.dashboardData = data;
  document.body.classList.toggle("dark", data.settings?.darkmode === "1");
  const metrics = dashboardMetrics(data);
  const activeMetric = metrics.find(metric => metric.key === state.dashboardDetailKey) || null;
  content.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Dashboard</h1>
        <p id="dashboard-range-summary">Heute: ${escapeHtml(data.range)} · Standort: ${escapeHtml(data.location)}</p>
      </div>
      <button class="secondary" id="refresh-location">Standort prüfen</button>
    </div>
    <section class="dashboard-grid grid cols-3">
      ${metrics.map(metric => dashboardMetric(metric)).join("")}
    </section>
    ${activeMetric ? dashboardDetailPanel(activeMetric) : ""}
  `;
  bindDashboardControls();
}

function dashboardMetrics(data) {
  return [
    {
      key: "work",
      label: "Arbeitszeit",
      value: fmtMinutes(data.work_minutes),
      detailHtml: todayWorkDetail(data),
    },
    {
      key: "break",
      label: "Pause",
      value: fmtMinutes(data.break_minutes),
      detailHtml: todayBreakDetail(data),
    },
    {
      key: "live",
      label: "Heute live",
      value: signedMinutes(data.live_day.balance_minutes),
      signedValue: data.live_day.balance_minutes,
      detailHtml: liveDayDetail(data),
    },
    {
      key: "balance",
      label: "Gleitzeitkonto",
      value: data.flextime_hours,
      signedValue: data.flextime,
      extraClass: `balance-card ${escapeHtml(data.flextime_status?.class || "")}`,
      detailHtml: flextimeDetail(data),
    },
    {
      key: "vacation",
      label: "Resturlaub",
      value: `${numberDe(data.remaining_vacation)} Tage`,
      detailHtml: vacationDetail(data),
    },
    {
      key: "next_absence",
      label: "Nächste Abwesenheit",
      value: absenceCountdownValue(data.next_absence),
      extraClass: "countdown-card",
      detailHtml: absenceCountdownDetail(data.next_absence),
    },
    {
      key: "office",
      label: "Officequote",
      value: `${numberDe(data.location_stats.office_percent)} %`,
      signedValue: data.location_stats.office_requirement_met ? 1 : -1,
      detailHtml: officeQuotaDetail(data.location_stats),
    },
    {
      key: "date",
      label: "Datum",
      value: data.today,
      detailHtml: dateDetail(data),
    },
  ];
}

async function refreshDashboard({ silent = true } = {}) {
  const data = await api("dashboard");
  if (!silent || !document.querySelector("[data-dashboard-card]")) {
    renderDashboardFrame(data);
    return;
  }
  updateDashboardFrame(data);
}

function updateDashboardFrame(data) {
  state.dashboardData = data;
  const summary = document.getElementById("dashboard-range-summary");
  if (summary) summary.textContent = `Heute: ${data.range} · Standort: ${data.location}`;
  const metrics = dashboardMetrics(data);
  document.querySelectorAll("[data-dashboard-card]").forEach(card => {
    const metric = metrics.find(item => item.key === card.dataset.dashboardCard);
    if (!metric) return;
    const active = state.dashboardDetailKey === metric.key;
    card.className = dashboardCardClass(metric, active);
    const button = card.querySelector(".metric-trigger");
    button?.setAttribute("aria-expanded", active ? "true" : "false");
    const value = card.querySelector("[data-dashboard-value]");
    if (value) {
      value.textContent = metric.value;
      value.className = dashboardValueClass(metric);
    }
  });
  syncDashboardDetail(metrics);
}

function syncDashboardDetail(metrics) {
  const activeMetric = metrics.find(metric => metric.key === state.dashboardDetailKey) || null;
  const existing = document.querySelector(".dashboard-detail-panel");
  if (!activeMetric) {
    existing?.remove();
    return;
  }
  if (existing) {
    const title = existing.querySelector("h2");
    const grid = existing.querySelector(".dashboard-detail-grid");
    if (title) title.textContent = activeMetric.label;
    if (grid) grid.innerHTML = activeMetric.detailHtml;
  } else {
    document.querySelector(".dashboard-grid")?.insertAdjacentHTML("afterend", dashboardDetailPanel(activeMetric));
    bindDashboardDetailClose();
  }
}

function bindDashboardControls() {
  document.getElementById("refresh-location").addEventListener("click", async () => {
    const result = await api("detect_location_now");
    notify(`Aktueller Standort: ${result.label}`);
  });
  document.querySelectorAll(".metric-trigger").forEach(button => {
    button.addEventListener("click", () => {
      state.dashboardDetailKey = state.dashboardDetailKey === button.dataset.metric ? null : button.dataset.metric;
      renderDashboardFrame(state.dashboardData);
    });
  });
  bindDashboardDetailClose();
}

function bindDashboardDetailClose() {
  document.getElementById("close-dashboard-detail")?.addEventListener("click", () => {
    state.dashboardDetailKey = null;
    renderDashboardFrame(state.dashboardData);
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

async function refreshCalendar({ silent = true } = {}) {
  const year = state.calendarDate.getFullYear();
  const month = state.calendarDate.getMonth() + 1;
  const data = await api("calendar_month", year, month, true);
  const grid = document.querySelector(".calendar-grid");
  if (!silent || !grid) {
    await renderCalendar();
    return;
  }
  updateCalendarTiles(data.days);
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  if (state.calendarDetailDate?.startsWith(monthPrefix)) {
    await loadDayDetail(state.calendarDetailDate);
  } else {
    state.calendarDetailDate = null;
  }
}

function updateCalendarTiles(days) {
  days.forEach(day => {
    const tile = document.querySelector(`.day-tile[data-date="${day.date}"]`);
    if (!tile) return;
    const fresh = htmlElement(renderDayTile(day));
    if (!fresh) return;
    tile.className = fresh.className;
    tile.innerHTML = fresh.innerHTML;
    tile.setAttribute("aria-expanded", fresh.getAttribute("aria-expanded") || "false");
  });
  updateCalendarSelection();
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
      <strong><span class="day-number">${Number(day.date.slice(-2))}.</span><span class="day-label"> ${label}</span></strong>
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
        <button class="form-submit">Notiz speichern</button>
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
              <td data-label="Typ">
                <select name="type">
                  ${["WORK", "BREAK", "ABSENCE"].map(value => `<option value="${value}" ${segment.type === value ? "selected" : ""}>${segmentTypeLabel(value)}</option>`).join("")}
                </select>
              </td>
              <td data-label="Beginn"><input name="start_time" type="time" value="${escapeHtml((segment.start_time || "").slice(0, 5))}"></td>
              <td data-label="Ende"><input name="end_time" type="time" value="${escapeHtml((segment.end_time || "").slice(0, 5))}"></td>
              <td data-label="Standort">
                <select name="location">
                  ${["UNKNOWN", "OFFICE", "HOME"].map(value => `<option value="${value}" ${segment.location === value ? "selected" : ""}>${locationLabel(value)}</option>`).join("")}
                </select>
              </td>
              <td class="row-actions" data-label="Aktionen">
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
    <section class="toolbar entries-filterbar" aria-label="Einträge filtern">
      <label>Tag
        <select id="entry-filter-day-type">
          ${entryFilterOptions("dayType", [
            ["all", "Alle Tage"],
            ["workdays", "Arbeitstage"],
            ["absence_days", "Abwesenheitstage"],
            ["weekend", "Wochenende"],
            ["vacation", "Urlaub"],
            ["sick", "Krank"],
            ["holiday", "Feiertag"],
            ["flextime", "Gleitzeittag"],
            ["travel", "Dienstreise"],
            ["not_tracked", "Vor Startdatum"],
          ])}
        </select>
      </label>
      <label>Standort
        <select id="entry-filter-location">
          ${entryFilterOptions("location", [
            ["all", "Alle Standorte"],
            ["office", "Büro"],
            ["home", "Homeoffice"],
            ["mixed", "Gemischt"],
            ["unknown", "Unbekannt/leer"],
          ])}
        </select>
      </label>
      <label>Saldo
        <select id="entry-filter-balance">
          ${entryFilterOptions("balance", [
            ["all", "Alle Salden"],
            ["positive", "Plus"],
            ["negative", "Minus"],
            ["neutral", "Genau 0"],
          ])}
        </select>
      </label>
      <label>Status
        <select id="entry-filter-status">
          ${entryFilterOptions("status", [
            ["all", "Alle Status"],
            ["running", "Laufend"],
            ["complete", "Abgeschlossen"],
            ["without_time", "Ohne Zeiten"],
          ])}
        </select>
      </label>
      <label>Notiz
        <select id="entry-filter-note">
          ${entryFilterOptions("note", [
            ["all", "Alle Notizen"],
            ["with", "Mit Notiz"],
            ["without", "Ohne Notiz"],
          ])}
        </select>
      </label>
      <label>Arbeitszeit &gt; h
        <input id="entry-filter-min-hours" type="number" min="0" step="0.25" inputmode="decimal" value="${escapeHtml(state.entryFilters.minHours)}" placeholder="z. B. 8">
      </label>
      <label>Arbeitszeit &lt; h
        <input id="entry-filter-max-hours" type="number" min="0" step="0.25" inputmode="decimal" value="${escapeHtml(state.entryFilters.maxHours)}" placeholder="z. B. 6">
      </label>
      <button id="reset-entry-filters" class="secondary compact-button" type="button">Filter zurücksetzen</button>
    </section>
    <div id="entry-filter-summary" class="filter-summary" aria-live="polite"></div>
    <section id="entries-table"></section>
  `;
  document.getElementById("load-entries").addEventListener("click", loadEntries);
  document.getElementById("entry-search").addEventListener("input", drawEntries);
  bindEntryFilters();
  await loadEntries();
}

async function loadEntries() {
  const range = entryDateRange();
  if (!range) return;
  const { start, end } = range;
  const result = await api("entries", start, end);
  state.entries = result.rows;
  drawEntries();
}

async function refreshEntries({ silent = true } = {}) {
  const range = entryDateRange();
  const target = document.getElementById("entries-table");
  if (!silent || !range || !target) {
    await renderEntries();
    return;
  }
  const renderedDates = Array.from(target.querySelectorAll(".entry-row")).map(row => row.dataset.date);
  const result = await api("entries", range.start, range.end, true);
  state.entries = result.rows;
  const rows = visibleEntryRows();
  updateEntryFilterSummary(rows.length);
  if (!sameStringList(renderedDates, rows.map(row => row.date))) {
    drawEntries();
    return;
  }
  if (state.entryEditDate && !rows.some(row => row.date === state.entryEditDate)) {
    state.entryEditDate = null;
    drawEntries();
    return;
  }
  updateEntryRows(rows);
  if (state.entryEditDate) await renderEntryEditor(state.entryEditDate, { showLoading: false });
}

function entryDateRange() {
  const startInput = document.getElementById("entry-start");
  const endInput = document.getElementById("entry-end");
  if (!startInput || !endInput) return null;
  return { start: startInput.value, end: endInput.value };
}

function visibleEntryRows() {
  const query = normalizeSearchText(document.getElementById("entry-search")?.value || "");
  return state.entries
    .filter(row => !query || entrySearchText(row).includes(query))
    .filter(entryMatchesFilters)
    .sort((a, b) => String(a[state.entriesSort.key]).localeCompare(String(b[state.entriesSort.key])) * state.entriesSort.direction);
}

function bindEntryFilters() {
  const bindings = [
    ["entry-filter-day-type", "dayType", "change"],
    ["entry-filter-location", "location", "change"],
    ["entry-filter-balance", "balance", "change"],
    ["entry-filter-status", "status", "change"],
    ["entry-filter-note", "note", "change"],
    ["entry-filter-min-hours", "minHours", "input"],
    ["entry-filter-max-hours", "maxHours", "input"],
  ];
  bindings.forEach(([id, key, eventName]) => {
    document.getElementById(id)?.addEventListener(eventName, event => {
      state.entryFilters[key] = event.currentTarget.value;
      drawEntries();
    });
  });
  document.getElementById("reset-entry-filters")?.addEventListener("click", () => {
    state.entryFilters = {
      dayType: "all",
      location: "all",
      balance: "all",
      status: "all",
      note: "all",
      minHours: "",
      maxHours: "",
    };
    syncEntryFilterControls();
    drawEntries();
  });
}

function syncEntryFilterControls() {
  const values = {
    "entry-filter-day-type": state.entryFilters.dayType,
    "entry-filter-location": state.entryFilters.location,
    "entry-filter-balance": state.entryFilters.balance,
    "entry-filter-status": state.entryFilters.status,
    "entry-filter-note": state.entryFilters.note,
    "entry-filter-min-hours": state.entryFilters.minHours,
    "entry-filter-max-hours": state.entryFilters.maxHours,
  };
  Object.entries(values).forEach(([id, value]) => {
    const control = document.getElementById(id);
    if (control) control.value = value;
  });
}

function entryMatchesFilters(row) {
  const filters = state.entryFilters;
  if (!entryMatchesDayType(row, filters.dayType)) return false;
  if (!entryMatchesLocation(row, filters.location)) return false;
  if (!entryMatchesBalance(row, filters.balance)) return false;
  if (!entryMatchesStatus(row, filters.status)) return false;
  if (!entryMatchesNote(row, filters.note)) return false;
  const actualHours = Number(row.actual_minutes || 0) / 60;
  const minHours = parseFilterNumber(filters.minHours);
  const maxHours = parseFilterNumber(filters.maxHours);
  if (minHours !== null && actualHours <= minHours) return false;
  if (maxHours !== null && actualHours >= maxHours) return false;
  return true;
}

function entryMatchesDayType(row, filter) {
  const type = row.type || "";
  if (filter === "all") return true;
  if (filter === "workdays") return type === "WORKDAY";
  if (filter === "absence_days") return ["VACATION", "SICK", "HOLIDAY", "FLEXTIME", "TRAVEL"].includes(type);
  return {
    weekend: "WEEKEND",
    vacation: "VACATION",
    sick: "SICK",
    holiday: "HOLIDAY",
    flextime: "FLEXTIME",
    travel: "TRAVEL",
    not_tracked: "NOT_TRACKED",
  }[filter] === type;
}

function entryMatchesLocation(row, filter) {
  const location = row.location || "";
  if (filter === "all") return true;
  if (filter === "office") return location === "OFFICE";
  if (filter === "home") return location === "HOME";
  if (filter === "mixed") return location === "MIXED";
  if (filter === "unknown") return !location || location === "UNKNOWN";
  return true;
}

function entryMatchesBalance(row, filter) {
  const balance = Number(row.balance_minutes || 0);
  if (filter === "positive") return balance > 0;
  if (filter === "negative") return balance < 0;
  if (filter === "neutral") return balance === 0;
  return true;
}

function entryMatchesStatus(row, filter) {
  const hasStart = Boolean(row.start);
  const hasEnd = Boolean(row.end);
  if (filter === "running") return hasStart && !hasEnd;
  if (filter === "complete") return hasStart && hasEnd;
  if (filter === "without_time") return !hasStart && !hasEnd;
  return true;
}

function entryMatchesNote(row, filter) {
  const hasNote = Boolean(String(row.note || "").trim());
  if (filter === "with") return hasNote;
  if (filter === "without") return !hasNote;
  return true;
}

function parseFilterNumber(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const parsed = Number(raw.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

function entryFilterOptions(key, options) {
  const current = state.entryFilters[key];
  return options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${current === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function entrySearchText(row) {
  return normalizeSearchText([
    row.date,
    germanDate(row.date),
    weekdayShort(row.date),
    timeShort(row.start),
    timeShort(row.end),
    fmtMinutes(row.break_minutes),
    fmtMinutes(row.actual_minutes),
    signedMinutes(row.balance_minutes),
    row.type,
    categoryLabel(row.type),
    row.location,
    locationLabel(row.location),
    row.note,
    entryRangeLabel(row),
  ].filter(Boolean).join(" "));
}

function germanDate(dateText) {
  const parts = String(dateText || "").split("-");
  return parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : "";
}

function drawEntries() {
  const target = document.getElementById("entries-table");
  if (!target) return;
  const rows = visibleEntryRows();
  updateEntryFilterSummary(rows.length);
  if (state.entryEditDate && !rows.some(row => row.date === state.entryEditDate)) {
    state.entryEditDate = null;
  }
  if (!rows.length) {
    target.innerHTML = `<div class="empty">Keine Einträge für diese Filter gefunden.</div>`;
    return;
  }
  target.innerHTML = `
    <div class="table-wrap entry-table-wrap">
      <table class="entries-table">
        <thead><tr>${[
          ["date", "Datum"], ["start", "Beginn"], ["end", "Ende"], ["break_minutes", "Pause"],
          ["actual_minutes", "Stunden"], ["balance_minutes", "Saldo"], ["type", "Typ"], ["location", "Standort"], ["note", "Notiz"]
        ].map(([key, label]) => `<th data-key="${key}">${label}</th>`).join("")}<th>Aktionen</th></tr></thead>
        <tbody>${rows.map(row => {
          const editing = state.entryEditDate === row.date;
          return `
          <tr class="entry-row ${editing ? "is-editing" : ""}" data-date="${row.date}">
            <td data-label="Datum" class="entry-date-cell"><strong>${row.date}</strong><small>${weekdayShort(row.date)}<span class="entry-inline-range"> · ${entryRangeLabel(row)}</span></small></td>
            <td data-label="Beginn" class="entry-start-cell entry-time-cell" data-entry-field="start">${timeShort(row.start) || "—"}</td>
            <td data-label="Ende" class="entry-end-cell entry-time-cell" data-entry-field="end">${timeShort(row.end) || "—"}</td>
            <td data-label="Pause" class="entry-detail-cell" data-entry-field="break">${fmtMinutes(row.break_minutes)}</td>
            <td data-label="Stunden" class="entry-detail-cell" data-entry-field="actual">${fmtMinutes(row.actual_minutes)}</td>
            <td data-label="Saldo" class="entry-balance-cell ${row.balance_minutes >= 0 ? "positive" : "negative"}" data-entry-field="balance">${signedMinutes(row.balance_minutes)}</td>
            <td data-label="Typ" class="entry-detail-cell" data-entry-field="type">${categoryLabel(row.type)}</td>
            <td data-label="Standort" class="entry-detail-cell" data-entry-field="location">${locationLabel(row.location)}</td>
            <td data-label="Notiz" class="entry-detail-cell entry-note-cell" data-entry-field="note">${escapeHtml(row.note || "—")}</td>
            <td class="row-actions entry-action-cell" data-label="Aktionen">
              <button class="secondary edit-entry entry-toggle" data-date="${row.date}" aria-expanded="${editing ? "true" : "false"}" aria-label="${editing ? "Details schließen" : `Details zu ${row.date} öffnen`}">
                <span class="button-label">${editing ? "Schließen" : "Details"}</span>
                <span class="toggle-chevron" aria-hidden="true"></span>
              </button>
            </td>
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

function updateEntryFilterSummary(visibleCount) {
  const summary = document.getElementById("entry-filter-summary");
  if (!summary) return;
  const total = state.entries.length;
  const active = activeEntryFilterLabels();
  summary.innerHTML = `
    <span>${visibleCount} von ${total} Tagen sichtbar</span>
    ${active.length ? `<strong>${active.map(escapeHtml).join(" · ")}</strong>` : `<strong>Keine Zusatzfilter aktiv</strong>`}
  `;
}

function activeEntryFilterLabels() {
  const filters = state.entryFilters;
  const labels = [];
  if (filters.dayType !== "all") labels.push(`Tag: ${entryFilterLabel("dayType", filters.dayType)}`);
  if (filters.location !== "all") labels.push(`Standort: ${entryFilterLabel("location", filters.location)}`);
  if (filters.balance !== "all") labels.push(`Saldo: ${entryFilterLabel("balance", filters.balance)}`);
  if (filters.status !== "all") labels.push(`Status: ${entryFilterLabel("status", filters.status)}`);
  if (filters.note !== "all") labels.push(`Notiz: ${entryFilterLabel("note", filters.note)}`);
  if (filters.minHours) labels.push(`Mehr als ${filters.minHours} h`);
  if (filters.maxHours) labels.push(`Weniger als ${filters.maxHours} h`);
  return labels;
}

function entryFilterLabel(key, value) {
  const options = {
    dayType: {
      all: "Alle Tage",
      workdays: "Arbeitstage",
      absence_days: "Abwesenheitstage",
      weekend: "Wochenende",
      vacation: "Urlaub",
      sick: "Krank",
      holiday: "Feiertag",
      flextime: "Gleitzeittag",
      travel: "Dienstreise",
      not_tracked: "Vor Startdatum",
    },
    location: {
      all: "Alle Standorte",
      office: "Büro",
      home: "Homeoffice",
      mixed: "Gemischt",
      unknown: "Unbekannt/leer",
    },
    balance: {
      all: "Alle Salden",
      positive: "Plus",
      negative: "Minus",
      neutral: "Genau 0",
    },
    status: {
      all: "Alle Status",
      running: "Laufend",
      complete: "Abgeschlossen",
      without_time: "Ohne Zeiten",
    },
    note: {
      all: "Alle Notizen",
      with: "Mit Notiz",
      without: "Ohne Notiz",
    },
  };
  return options[key]?.[value] || value;
}

function updateEntryRows(rows) {
  rows.forEach(row => {
    const entryRow = document.querySelector(`#entries-table .entry-row[data-date="${row.date}"]`);
    if (!entryRow) return;
    const dateCell = entryRow.querySelector(".entry-date-cell small");
    if (dateCell) dateCell.innerHTML = `${weekdayShort(row.date)}<span class="entry-inline-range"> · ${entryRangeLabel(row)}</span>`;
    setEntryCell(entryRow, "start", timeShort(row.start) || "—");
    setEntryCell(entryRow, "end", timeShort(row.end) || "—");
    setEntryCell(entryRow, "break", fmtMinutes(row.break_minutes));
    setEntryCell(entryRow, "actual", fmtMinutes(row.actual_minutes));
    setEntryCell(entryRow, "type", categoryLabel(row.type));
    setEntryCell(entryRow, "location", locationLabel(row.location));
    setEntryCell(entryRow, "note", row.note || "—");
    const balance = entryRow.querySelector('[data-entry-field="balance"]');
    if (balance) {
      balance.textContent = signedMinutes(row.balance_minutes);
      balance.classList.toggle("positive", row.balance_minutes >= 0);
      balance.classList.toggle("negative", row.balance_minutes < 0);
    }
  });
}

function setEntryCell(row, field, value) {
  const cell = row.querySelector(`[data-entry-field="${field}"]`);
  if (cell) cell.textContent = value;
}

async function renderEntryEditor(date, { showLoading = true } = {}) {
  const panel = document.getElementById("entry-edit-panel");
  if (!panel) return;
  panel.className = "detail-panel stack entry-inline-panel";
  if (showLoading) panel.innerHTML = `<div class="loading">Lade Eintrag ${escapeHtml(date)} …</div>`;
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
  const yearInput = document.getElementById("stats-year");
  const body = document.getElementById("stats-body");
  if (!yearInput || !body) return;
  const year = Number(yearInput.value);
  const data = await api("statistics", year);
  renderStatisticsBody(data);
  await loadChartLibrary();
  drawStatsChart(data.months);
}

function renderStatisticsBody(data) {
  const body = document.getElementById("stats-body");
  if (!body) return;
  body.innerHTML = `
    <div class="grid cols-4 stats-metrics">
      ${metric("Soll", fmtMinutes(data.target_minutes), null, "target")}
      ${metric("Ist", fmtMinutes(data.actual_minutes), null, "actual")}
      ${balanceMetric("Gleitzeit", data.flextime_hours, data.flextime_status, data.flextime_minutes, "flextime")}
      ${metric("Resturlaub", `${numberDe(data.remaining_vacation)} Tage`, null, "remaining-vacation")}
      ${metric("Urlaub", `${numberDe(data.vacation_used)} Tage`, null, "vacation")}
      ${metric("Krank", `${numberDe(data.sick_used)} Tage`, null, "sick")}
      ${metric("Büro", `${data.office_days} Tage`, null, "office")}
      ${metric("Homeoffice", `${data.homeoffice_days} Tage`, null, "homeoffice")}
    </div>
    <div class="panel stats-chart-panel">
      <h2>Monatssalden</h2>
      <canvas id="stats-chart" height="280" aria-label="Balkendiagramm der Monatssalden"></canvas>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Monat</th><th>Soll</th><th>Ist</th><th>Saldo</th><th>Kumuliert</th><th>Urlaub</th><th>Krank</th><th>Homeoffice</th></tr></thead>
        <tbody>${data.months.map(month => `
          <tr data-stats-month="${escapeHtml(month.year_month)}">
            <td data-label="Monat">${month.year_month}</td>
            <td data-label="Soll" data-stats-field="target">${fmtMinutes(month.target_minutes)}</td>
            <td data-label="Ist" data-stats-field="actual">${fmtMinutes(month.actual_minutes)}</td>
            <td data-label="Saldo" data-stats-field="balance">${signedMinutes(month.balance_minutes)}</td>
            <td data-label="Kumuliert" data-stats-field="carry">${balanceBadge(month.carry_over_hours, month.carry_over_status)}</td>
            <td data-label="Urlaub" data-stats-field="vacation">${numberDe(month.vacation_days_used)}</td>
            <td data-label="Krank" data-stats-field="sick">${numberDe(month.sick_days_used)}</td>
            <td data-label="Homeoffice" data-stats-field="homeoffice">${month.homeoffice_days}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
  `;
}

async function refreshStatistics({ silent = true } = {}) {
  const yearInput = document.getElementById("stats-year");
  const body = document.getElementById("stats-body");
  if (!silent || !yearInput || !body || !body.querySelector("[data-stats-metric]")) {
    await renderStatistics();
    return;
  }
  const data = await api("statistics", Number(yearInput.value), true);
  const renderedMonths = Array.from(body.querySelectorAll("[data-stats-month]")).map(row => row.dataset.statsMonth);
  if (!sameStringList(renderedMonths, data.months.map(month => month.year_month))) {
    renderStatisticsBody(data);
  } else {
    updateStatisticsBody(data);
  }
  await loadChartLibrary();
  drawStatsChart(data.months);
}

function updateStatisticsBody(data) {
  updateStatsMetric("target", fmtMinutes(data.target_minutes));
  updateStatsMetric("actual", fmtMinutes(data.actual_minutes));
  updateStatsBalanceMetric("flextime", data.flextime_hours, data.flextime_status, data.flextime_minutes);
  updateStatsMetric("remaining-vacation", `${numberDe(data.remaining_vacation)} Tage`);
  updateStatsMetric("vacation", `${numberDe(data.vacation_used)} Tage`);
  updateStatsMetric("sick", `${numberDe(data.sick_used)} Tage`);
  updateStatsMetric("office", `${data.office_days} Tage`);
  updateStatsMetric("homeoffice", `${data.homeoffice_days} Tage`);
  data.months.forEach(updateStatsMonthRow);
}

function updateStatsMetric(key, value, signedValue = null) {
  const metricElement = document.querySelector(`[data-stats-metric="${key}"]`);
  const valueElement = metricElement?.querySelector("[data-stats-value]");
  if (!valueElement) return;
  valueElement.textContent = value;
  valueElement.className = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
}

function updateStatsBalanceMetric(key, value, status, signedValue = null) {
  const metricElement = document.querySelector(`[data-stats-metric="${key}"]`);
  if (!metricElement) return;
  metricElement.className = `metric balance-card ${status?.class || ""}`.trim();
  const valueElement = metricElement.querySelector("[data-stats-value]");
  const statusElement = metricElement.querySelector("[data-stats-status]");
  if (valueElement) {
    valueElement.textContent = value;
    valueElement.className = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
  }
  if (statusElement) statusElement.textContent = status?.label || "0 bis 45 Stunden";
}

function updateStatsMonthRow(month) {
  const row = document.querySelector(`[data-stats-month="${month.year_month}"]`);
  if (!row) return;
  setStatsCell(row, "target", fmtMinutes(month.target_minutes));
  setStatsCell(row, "actual", fmtMinutes(month.actual_minutes));
  setStatsCell(row, "balance", signedMinutes(month.balance_minutes));
  setStatsCell(row, "vacation", numberDe(month.vacation_days_used));
  setStatsCell(row, "sick", numberDe(month.sick_days_used));
  setStatsCell(row, "homeoffice", month.homeoffice_days);
  const carry = row.querySelector('[data-stats-field="carry"]');
  if (carry) carry.innerHTML = balanceBadge(month.carry_over_hours, month.carry_over_status);
}

function setStatsCell(row, field, value) {
  const cell = row.querySelector(`[data-stats-field="${field}"]`);
  if (cell) cell.textContent = value;
}

function loadChartLibrary() {
  if (window.Chart) return Promise.resolve();
  if (chartLibraryPromise) return chartLibraryPromise;
  chartLibraryPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "../static/vendor/chart.umd.js";
    script.onload = resolve;
    script.onerror = () => reject(new Error("Chart-Bibliothek konnte nicht geladen werden."));
    document.head.appendChild(script);
  });
  return chartLibraryPromise;
}

function drawStatsChart(months) {
  const canvas = document.getElementById("stats-chart");
  if (!canvas) return;
  const labels = months.map(month => month.year_month.slice(5));
  const values = months.map(month => month.balance_minutes);
  if (state.statsChart && state.statsChart.canvas !== canvas) {
    state.statsChart.destroy();
    state.statsChart = null;
  }
  if (state.statsChart) {
    state.statsChart.data.labels = labels;
    state.statsChart.data.datasets[0].data = values;
    state.statsChart.update("none");
    return;
  }
  state.statsChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "Saldo in Minuten", data: values }],
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
        <label class="field-wide">Notiz <input name="note" type="text"></label>
        <button>Speichern</button>
      </form>
    </section>
    <section class="panel stack section-gap">
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
  const yearInput = document.getElementById("absence-year");
  const target = document.getElementById("absence-list");
  if (!yearInput || !target) return;
  const year = Number(yearInput.value);
  const data = await api("absences", year);
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
          <td data-label="Zeitraum">${escapeHtml(periodLabel(row.start_date, row.end_date))}</td>
          <td data-label="Typ">${categoryLabel(row.type)}${row.half_day ? " (halb)" : ""}</td>
          <td data-label="Kalendertage">${numberDe(row.days)}</td>
          <td data-label="Angerechnet">${escapeHtml(absenceCountLabel(row))}</td>
          <td data-label="Notiz">${escapeHtml(row.note || "")}</td>
          <td class="row-actions" data-label="Aktionen"><button class="secondary delete-absence" data-ids="${escapeHtml((row.ids || []).join(","))}">Entfernen</button></td>
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

async function refreshVacation({ silent = true } = {}) {
  if (!silent || !document.getElementById("absence-list")) {
    await renderVacation();
    return;
  }
  await loadAbsences();
}

async function renderCalculator() {
  if (!state.calculatorDefaults) {
    state.calculatorDefaults = await api("calculator_defaults");
    initializeCalculatorRows();
  } else if (!state.calculatorRows.length) {
    initializeCalculatorRows();
  }
  renderCalculatorFrame();
}

function initializeCalculatorRows() {
  const defaults = state.calculatorDefaults || {};
  if (!state.calculatorStartBalanceHours) {
    state.calculatorStartBalanceHours = minutesToDecimalInput(defaults.flextime_minutes || 0);
  }
  state.calculatorRows = [createCalculatorRow(defaults.today || isoToday())];
}

function fillCalculatorWeekRows() {
  const defaults = state.calculatorDefaults || {};
  const workdays = calculatorWorkdays();
  const today = parseIsoDate(defaults.today) || new Date();
  const monday = addDays(today, -weekdayIndexFromDate(today));
  const rows = workdays.map(day => createCalculatorRow(isoFromDate(addDays(monday, day))));
  state.calculatorRows = rows.length ? rows : [createCalculatorRow(defaults.today || isoToday())];
}

function renderCalculatorFrame() {
  content.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Arbeitszeit Rechner</h1>
        <p>Plane einzelne Tage oder eine Woche, ohne gespeicherte Arbeitszeiten zu ändern.</p>
      </div>
    </div>
    <section class="calculator-shell">
      <section class="panel calculator-summary-panel">
        <div class="calculator-config-grid">
          <label>Startsaldo Gleitzeit (h)
            <input id="calculator-start-balance" type="text" inputmode="decimal" value="${escapeHtml(state.calculatorStartBalanceHours)}" placeholder="z. B. 48,5">
          </label>
          <label>Obergrenze (h)
            <input id="calculator-limit-hours" type="text" inputmode="decimal" value="${escapeHtml(state.calculatorLimitHours)}" placeholder="z. B. 50">
          </label>
          <button id="calculator-reset" class="secondary" type="button">Aus Einstellungen neu laden</button>
        </div>
        <div class="calculator-summary-grid">
          ${calculatorSummaryCard("plannedBalance", "Geplante Gleitzeit")}
          ${calculatorSummaryCard("plannedAccount", "Geplanter Kontostand")}
          ${calculatorSummaryCard("limitGap", "Puffer bis Grenze")}
          ${calculatorSummaryCard("worktime", "Geplante Arbeitszeit")}
        </div>
      </section>
      <section class="panel calculator-table-panel">
        <div class="calculator-plan-head">
          <div>
            <h2>Planung</h2>
            <p>Eine Zeile entspricht einem Arbeitstag. Komma und Punkt sind bei Stundenwerten erlaubt.</p>
          </div>
          <div class="calculator-action-row">
            <button id="calculator-fill-week" class="secondary" type="button">Diese Woche füllen</button>
            <button id="calculator-add-row" type="button">Tag hinzufügen</button>
          </div>
        </div>
        <div class="calculator-row-list">
          ${state.calculatorRows.map(calculatorRowHtml).join("")}
        </div>
      </section>
    </section>
  `;
  bindCalculatorControls();
  updateCalculatorOutputs();
}

function calculatorSummaryCard(key, label) {
  return `
    <div class="metric calculator-summary-card" data-calculator-summary="${escapeHtml(key)}">
      <span>${escapeHtml(label)}</span>
      <strong data-calculator-summary-value>--</strong>
      <small data-calculator-summary-help></small>
    </div>
  `;
}

function calculatorRowHtml(row, index) {
  const balanceMode = row.mode === "balance";
  const endMode = row.mode === "end";
  const targetBalanceMode = row.mode === "target_balance";
  const endLikeMode = endMode || targetBalanceMode;
  const canRemove = state.calculatorRows.length > 1;
  return `
    <article class="calculator-row-card" data-calculator-row="${escapeHtml(row.id)}">
      <div class="calculator-row-title">
        <strong>Tag ${index + 1}</strong>
        <small>${escapeHtml(weekdayLong(row.date))}</small>
      </div>
      <label>Datum
        <input class="calculator-control" data-field="date" type="date" value="${escapeHtml(row.date)}">
      </label>
      <label>Rechenart
        <select class="calculator-control" data-field="mode">
          <option value="balance" ${balanceMode ? "selected" : ""}>Gleitzeit berechnen</option>
          <option value="end" ${endMode ? "selected" : ""}>Ende für ±0</option>
          <option value="target_balance" ${targetBalanceMode ? "selected" : ""}>Ende aus Gleitzeit</option>
        </select>
      </label>
      <label>Start
        <input class="calculator-control" data-field="start" type="time" value="${escapeHtml(row.start)}">
      </label>
      ${balanceMode ? `
        <label>Ende
          <input class="calculator-control" data-field="end" type="time" value="${escapeHtml(row.end)}">
        </label>
      ` : endMode ? `
        <label>Ziel-Gleitzeit (h)
          <input type="text" value="0" readonly>
        </label>
      ` : `
        <label>Ziel-Gleitzeit
          <span class="calculator-unit-field">
            <input class="calculator-control" data-field="desiredBalanceHours" type="text" inputmode="decimal" value="${escapeHtml(row.desiredBalanceHours)}" placeholder="${escapeHtml(balanceTargetPlaceholder(row.desiredBalanceUnit))}">
            <select class="calculator-control calculator-unit-select" data-field="desiredBalanceUnit" aria-label="Einheit Ziel-Gleitzeit">
              <option value="decimal" ${row.desiredBalanceUnit !== "clock" && row.desiredBalanceUnit !== "minutes" ? "selected" : ""}>Dezimal</option>
              <option value="clock" ${row.desiredBalanceUnit === "clock" ? "selected" : ""}>Std:Min</option>
              <option value="minutes" ${row.desiredBalanceUnit === "minutes" ? "selected" : ""}>Min</option>
            </select>
          </span>
        </label>
      `}
      <label>Pause (h)
        <input class="calculator-control" data-field="pauseHours" type="text" inputmode="decimal" value="${escapeHtml(row.pauseHours)}">
      </label>
      <label>Soll (h)
        <input class="calculator-control" data-field="targetHours" type="text" inputmode="decimal" value="${escapeHtml(row.targetHours)}">
      </label>
      <div class="calculator-result" data-calculator-result="${escapeHtml(row.id)}">
        <span data-calculator-main-label>${endLikeMode ? "Ende" : "Gleitzeit"}</span>
        <strong data-calculator-main-value>--</strong>
        <small data-calculator-detail-value>--</small>
      </div>
      <button class="secondary calculator-remove" type="button" data-row-id="${escapeHtml(row.id)}" ${canRemove ? "" : "disabled"} title="Zeile entfernen" aria-label="Zeile entfernen">×</button>
    </article>
  `;
}

function bindCalculatorControls() {
  document.getElementById("calculator-start-balance")?.addEventListener("input", event => {
    state.calculatorStartBalanceHours = event.currentTarget.value;
    updateCalculatorOutputs();
  });
  document.getElementById("calculator-limit-hours")?.addEventListener("input", event => {
    state.calculatorLimitHours = event.currentTarget.value;
    updateCalculatorOutputs();
  });
  document.getElementById("calculator-fill-week")?.addEventListener("click", () => {
    fillCalculatorWeekRows();
    renderCalculatorFrame();
  });
  document.getElementById("calculator-reset")?.addEventListener("click", async () => {
    state.calculatorDefaults = await api("calculator_defaults");
    state.calculatorStartBalanceHours = minutesToDecimalInput(state.calculatorDefaults.flextime_minutes || 0);
    initializeCalculatorRows();
    renderCalculatorFrame();
  });
  document.getElementById("calculator-add-row")?.addEventListener("click", () => {
    const previousRow = state.calculatorRows[state.calculatorRows.length - 1];
    const previousDate = previousRow?.date || state.calculatorDefaults?.today || isoToday();
    state.calculatorRows.push(createCalculatorRow(nextCalculatorWorkdayDate(previousDate)));
    renderCalculatorFrame();
  });
  document.querySelectorAll(".calculator-row-card").forEach(card => {
    const row = calculatorRowById(card.dataset.calculatorRow);
    if (!row) return;
    card.querySelectorAll(".calculator-control").forEach(control => {
      const eventName = control.tagName === "SELECT" || control.type === "date" ? "change" : "input";
      control.addEventListener(eventName, event => {
        updateCalculatorRowValue(row, event.currentTarget.dataset.field, event.currentTarget.value);
      });
    });
  });
  document.querySelectorAll(".calculator-remove").forEach(button => {
    button.addEventListener("click", () => {
      if (state.calculatorRows.length <= 1) return;
      state.calculatorRows = state.calculatorRows.filter(row => row.id !== button.dataset.rowId);
      renderCalculatorFrame();
    });
  });
}

function updateCalculatorRowValue(row, field, value) {
  if (!row || !field) return;
  if (field === "desiredBalanceUnit") {
    const currentMinutes = parseBalanceTargetMinutes(row.desiredBalanceHours, row.desiredBalanceUnit, 0);
    row.desiredBalanceUnit = value;
    row.desiredBalanceHours = formatBalanceTargetInput(currentMinutes, value);
    renderCalculatorFrame();
    return;
  }
  if (field === "date") {
    const previousTarget = calculatorDefaultTargetMinutes(row.date);
    const currentTarget = parseDecimalHoursToMinutes(row.targetHours, previousTarget);
    row.date = value;
    if (currentTarget === previousTarget) {
      row.targetHours = minutesToDecimalInput(calculatorDefaultTargetMinutes(value));
    }
    renderCalculatorFrame();
    return;
  }
  row[field] = value;
  if (field === "mode") {
    renderCalculatorFrame();
    return;
  }
  updateCalculatorOutputs();
}

function updateCalculatorOutputs() {
  let plannedBalance = 0;
  let plannedWork = 0;
  let validRows = 0;
  state.calculatorRows.forEach(row => {
    const result = calculatorRowResult(row);
    const target = document.querySelector(`[data-calculator-result="${cssEscape(row.id)}"]`);
    if (!target) return;
    target.classList.toggle("positive", result.valid && result.balanceMinutes >= 0);
    target.classList.toggle("negative", result.valid && result.balanceMinutes < 0);
    const mainLabel = target.querySelector("[data-calculator-main-label]");
    const mainValue = target.querySelector("[data-calculator-main-value]");
    const detailValue = target.querySelector("[data-calculator-detail-value]");
    const detailText = result.valid
      ? `Erfasst ${fmtMinutes(result.capturedMinutes)} · Arbeit ${fmtMinutes(result.workMinutes)} · Pause ${fmtMinutes(result.pauseMinutes)}`
      : result.error;
    const endLikeMode = row.mode === "end" || row.mode === "target_balance";
    if (mainLabel) mainLabel.textContent = endLikeMode ? "Ende" : "Gleitzeit";
    if (mainValue) mainValue.textContent = result.valid ? (endLikeMode ? result.endLabel : signedMinutes(result.balanceMinutes)) : "--";
    if (detailValue) detailValue.textContent = detailText;
    target.title = detailText;
    if (result.valid) {
      plannedBalance += result.balanceMinutes;
      plannedWork += result.workMinutes;
      validRows += 1;
    }
  });

  const startBalance = parseDecimalHoursToMinutes(state.calculatorStartBalanceHours, state.calculatorDefaults?.flextime_minutes || 0);
  const limit = parseDecimalHoursToMinutes(state.calculatorLimitHours, 50 * 60);
  const plannedAccount = startBalance + plannedBalance;
  const limitGap = limit - plannedAccount;
  setCalculatorSummary("plannedBalance", signedMinutes(plannedBalance), `${validRows} geplante Tage`);
  setCalculatorSummary("plannedAccount", formatSignedDecimalHours(plannedAccount), `Startsaldo ${formatSignedDecimalHours(startBalance)}`);
  setCalculatorSummary("limitGap", signedMinutes(limitGap), `Grenze ${formatDecimalHours(limit)} h`, limitGap);
  setCalculatorSummary("worktime", fmtMinutes(plannedWork), "Summe der geplanten Arbeitszeit");
}

function setCalculatorSummary(key, value, help, signedValue = null) {
  const card = document.querySelector(`[data-calculator-summary="${key}"]`);
  if (!card) return;
  card.classList.toggle("positive", signedValue !== null && signedValue >= 0);
  card.classList.toggle("negative", signedValue !== null && signedValue < 0);
  const valueElement = card.querySelector("[data-calculator-summary-value]");
  const helpElement = card.querySelector("[data-calculator-summary-help]");
  if (valueElement) valueElement.textContent = value;
  if (helpElement) helpElement.textContent = help;
}

function calculatorRowResult(row) {
  const start = parseClockMinutes(row.start);
  if (start === null) return { valid: false, error: "Startzeit fehlt." };
  const pauseMinutes = parseDecimalHoursToMinutes(row.pauseHours, 0);
  const targetMinutes = parseDecimalHoursToMinutes(row.targetHours, 0);
  if (row.mode === "end" || row.mode === "target_balance") {
    const desiredBalance = row.mode === "target_balance" ? parseBalanceTargetMinutes(row.desiredBalanceHours, row.desiredBalanceUnit, 0) : 0;
    const workMinutes = Math.max(0, targetMinutes + desiredBalance);
    const capturedMinutes = workMinutes + pauseMinutes;
    return {
      valid: true,
      capturedMinutes,
      workMinutes,
      pauseMinutes,
      targetMinutes,
      balanceMinutes: workMinutes - targetMinutes,
      endLabel: formatClockWithDay(start + capturedMinutes),
    };
  }
  const end = parseClockMinutes(row.end);
  if (end === null) return { valid: false, error: "Endzeit fehlt." };
  let capturedMinutes = end - start;
  if (capturedMinutes < 0) capturedMinutes += 24 * 60;
  const workMinutes = Math.max(0, capturedMinutes - pauseMinutes);
  return {
    valid: true,
    capturedMinutes,
    workMinutes,
    pauseMinutes,
    targetMinutes,
    balanceMinutes: workMinutes - targetMinutes,
    endLabel: formatClockWithDay(start + capturedMinutes),
  };
}

function createCalculatorRow(date) {
  const targetMinutes = calculatorDefaultTargetMinutes(date);
  const pauseMinutes = Number(state.calculatorDefaults?.daily_break_minutes || 0);
  const defaultStart = calculatorDefaultStartTime(date);
  const defaultEnd = formatClockWithDay(parseClockMinutes(defaultStart) + targetMinutes + pauseMinutes).slice(0, 5);
  return {
    id: `calc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    date,
    mode: "balance",
    start: defaultStart,
    end: defaultEnd,
    pauseHours: minutesToDecimalInput(pauseMinutes),
    targetHours: minutesToDecimalInput(targetMinutes),
    desiredBalanceHours: "0",
    desiredBalanceUnit: "decimal",
  };
}

function calculatorDefaultStartTime(dateText) {
  const today = state.calculatorDefaults?.today || isoToday();
  const trackedStart = state.calculatorDefaults?.today_work_start_time || "";
  if (dateText === today && trackedStart) return trackedStart.slice(0, 5);
  return "07:30";
}

function nextCalculatorWorkdayDate(previousDateText) {
  const previousDate = parseIsoDate(previousDateText) || parseIsoDate(state.calculatorDefaults?.today) || new Date();
  const workdays = new Set(calculatorWorkdays());
  for (let offset = 1; offset <= 14; offset += 1) {
    const candidate = addDays(previousDate, offset);
    if (workdays.has(weekdayIndexFromDate(candidate))) return isoFromDate(candidate);
  }
  return isoFromDate(addDays(previousDate, 1));
}

function calculatorWorkdays() {
  const raw = state.calculatorDefaults?.workday_weekdays;
  const values = Array.isArray(raw) ? raw.map(Number).filter(value => Number.isInteger(value) && value >= 0 && value <= 6) : [];
  return values.length ? values : [0, 1, 2, 3, 4];
}

function calculatorDefaultTargetMinutes(dateText) {
  const weekday = weekdayIndexFromIso(dateText);
  const targets = state.calculatorDefaults?.target_minutes_by_weekday || {};
  const value = Number(targets[String(weekday)]);
  if (Number.isFinite(value)) return Math.max(0, Math.round(value));
  const weeklyHours = Number.parseFloat(String(state.calculatorDefaults?.weekly_target_hours || "40").replace(",", "."));
  const workdayCount = Math.max(1, calculatorWorkdays().length);
  return Math.round(((Number.isFinite(weeklyHours) ? weeklyHours : 40) * 60) / workdayCount);
}

function calculatorRowById(id) {
  return state.calculatorRows.find(row => row.id === id);
}

function parseDecimalHoursToMinutes(value, fallback = 0) {
  const raw = String(value ?? "").trim();
  if (!raw) return fallback;
  const parsed = Number(raw.replace(",", "."));
  return Number.isFinite(parsed) ? Math.round(parsed * 60) : fallback;
}

function parseBalanceTargetMinutes(value, unit = "decimal", fallback = 0) {
  const raw = String(value ?? "").trim();
  if (!raw) return fallback;
  if (unit === "minutes") {
    const parsed = Number(raw.replace(",", "."));
    return Number.isFinite(parsed) ? Math.round(parsed) : fallback;
  }
  if (unit === "clock") {
    const match = raw.match(/^([+-])?\s*(\d{1,3})(?::|h|\s)(\d{1,2})$/i);
    if (!match) return fallback;
    const sign = match[1] === "-" ? -1 : 1;
    const hours = Number(match[2]);
    const minutes = Number(match[3]);
    if (!Number.isFinite(hours) || !Number.isFinite(minutes) || minutes > 59) return fallback;
    return sign * ((hours * 60) + minutes);
  }
  return parseDecimalHoursToMinutes(raw, fallback);
}

function formatBalanceTargetInput(minutes, unit = "decimal") {
  const value = Math.round(Number(minutes) || 0);
  if (unit === "minutes") return String(value);
  if (unit === "clock") {
    const sign = value < 0 ? "-" : "";
    const absolute = Math.abs(value);
    return `${sign}${Math.floor(absolute / 60)}:${String(absolute % 60).padStart(2, "0")}`;
  }
  return minutesToDecimalInput(value);
}

function balanceTargetPlaceholder(unit = "decimal") {
  if (unit === "minutes") return "z. B. 15";
  if (unit === "clock") return "z. B. 0:15";
  return "z. B. 0,25";
}

function minutesToDecimalInput(minutes) {
  const value = (Number(minutes) || 0) / 60;
  return value.toFixed(2).replace(".", ",").replace(/,?0+$/, "");
}

function formatSignedDecimalHours(minutes) {
  const value = Number(minutes) || 0;
  return `${value >= 0 ? "+" : "-"}${formatDecimalHours(Math.abs(value))} h`;
}

function formatDecimalHours(minutes) {
  const value = (Number(minutes) || 0) / 60;
  return new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 }).format(value);
}

function parseClockMinutes(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

function formatClockWithDay(totalMinutes) {
  const rounded = Math.max(0, Math.round(Number(totalMinutes) || 0));
  const dayOffset = Math.floor(rounded / (24 * 60));
  const minutesOfDay = rounded % (24 * 60);
  const label = `${String(Math.floor(minutesOfDay / 60)).padStart(2, "0")}:${String(minutesOfDay % 60).padStart(2, "0")}`;
  if (!dayOffset) return label;
  return `${label} +${dayOffset} Tag${dayOffset > 1 ? "e" : ""}`;
}

function weekdayIndexFromIso(dateText) {
  const date = parseIsoDate(dateText);
  return date ? weekdayIndexFromDate(date) : 0;
}

function weekdayIndexFromDate(date) {
  return (date.getDay() + 6) % 7;
}

function parseIsoDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

function isoFromDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function weekdayLong(dateText) {
  const date = parseIsoDate(dateText);
  if (!date) return "Datum offen";
  return new Intl.DateTimeFormat("de-DE", { weekday: "long" }).format(date);
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

async function renderSettings() {
  rememberSettingsSubnavScroll();
  const settings = await api("settings");
  const sections = settingsSections(settings);
  if (state.settingsDetailKey && !sections.some(section => section.key === state.settingsDetailKey)) {
    state.settingsDetailKey = "";
  }
  const active = state.settingsDetailKey ? sections.find(section => section.key === state.settingsDetailKey) : null;
  content.innerHTML = `
    <div class="page-head">
      <div><h1>Einstellungen</h1><p>Arbeitsmodell, Gleitzeit, Automatik, Popups, Standort, Officequote und Dateiaustausch.</p></div>
      ${active ? `<button class="secondary" id="settings-overview" type="button">Übersicht</button>` : ""}
    </div>
    ${active ? `
      <section class="settings-workspace">
        ${renderSettingsSubnav(sections)}
        ${renderSettingsPanel(active, settings)}
      </section>
    ` : `
      <section class="settings-card-grid">
        ${sections.map(section => settingsSectionCard(section)).join("")}
      </section>
    `}
  `;
  document.querySelectorAll(".settings-card").forEach(button => {
    button.addEventListener("click", () => {
      state.settingsSubnavScrollTop = 0;
      state.settingsDetailKey = button.dataset.section;
      renderSettings();
    });
  });
  document.querySelectorAll(".settings-subnav button[data-section]").forEach(button => {
    button.addEventListener("click", () => {
      rememberSettingsSubnavScroll();
      state.settingsDetailKey = button.dataset.section;
      renderSettings();
    });
  });
  document.getElementById("settings-overview")?.addEventListener("click", () => {
    state.settingsDetailKey = "";
    renderSettings();
  });
  restoreSettingsSubnavScroll();
  bindSettingsForm(active);
  document.getElementById("backup-now")?.addEventListener("click", async () => {
    const result = await api("create_backup");
    notify(`Backup erstellt: ${result.name}`);
  });
  hydrateExportDefaults();
  document.getElementById("export-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("export_period", form.start_date.value, form.end_date.value, form.format.value);
    notify(`Export erstellt: ${result.name}`);
  });
  const importButton = document.getElementById("import-file");
  const importInput = document.getElementById("import-file-input");
  importButton?.addEventListener("click", () => {
    importInput?.click();
  });
  importInput?.addEventListener("change", async event => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    const button = importButton;
    const output = document.getElementById("import-result");
    if (button) {
      button.disabled = true;
      button.textContent = "Importiere ...";
    }
    try {
      const payload = await fileToBase64(file);
      const result = await api("import_uploaded_file", file.name, payload);
      const range = result.start_date ? `${result.start_date} bis ${result.end_date}` : "keine Änderungen";
      const warningText = Number(result.warnings || 0) ? ` · Warnungen ${result.warnings}` : "";
      const logButton = result.log_path ? `<button id="open-import-log" class="secondary" type="button" data-path="${escapeHtml(result.log_path)}">Protokoll öffnen</button>` : "";
      if (output) {
        output.innerHTML = `<div class="success-panel"><strong>Import abgeschlossen</strong><small>${escapeHtml(result.name)} · ${escapeHtml(range)} · Segmente ${result.segments + (result.overview_segments || 0)}, Abwesenheiten ${result.day_types + (result.overview_day_types || 0)}, Notizen ${result.notes + (result.overview_notes || 0)}${escapeHtml(warningText)} · Protokoll ${escapeHtml(result.log_name || "")}</small>${logButton}</div>`;
        document.getElementById("open-import-log")?.addEventListener("click", async event => {
          try {
            await api("open_import_log", event.currentTarget.dataset.path);
          } catch (error) {
            notify(error.message || String(error), "error");
          }
        });
      }
      notify(`Import abgeschlossen: ${result.name}`);
    } catch (error) {
      notify(error.message || String(error), "error");
      if (output) output.innerHTML = `<div class="error-panel">${escapeHtml(error.message || String(error))}</div>`;
    } finally {
      event.currentTarget.value = "";
      if (button) {
        button.disabled = false;
        button.textContent = "Daten importieren";
      }
    }
  });
  bindSapSdataImportControls();
  document.getElementById("open-reset-dialog")?.addEventListener("click", openResetDialog);
}

function rememberSettingsSubnavScroll() {
  const subnav = document.querySelector(".settings-subnav");
  if (subnav) state.settingsSubnavScrollTop = subnav.scrollTop;
}

function restoreSettingsSubnavScroll() {
  const subnav = document.querySelector(".settings-subnav");
  if (!subnav) return;
  subnav.scrollTop = state.settingsSubnavScrollTop || 0;
  subnav.addEventListener("scroll", () => {
    state.settingsSubnavScrollTop = subnav.scrollTop;
  }, { passive: true });
}

function settingsSections(settings) {
  const setupRequired = settings.initial_setup_required === "1";
  return [
    {
      key: "work",
      title: "Arbeitsmodell",
      summary: "Sollzeit, Pause und Arbeitstage",
      body: renderWorkSettings(settings),
    },
    {
      key: "vacation_rules",
      title: "Urlaub & Feiertage",
      summary: "Jahresanspruch, Übertrag und Bundesland",
      body: renderVacationSettings(settings),
    },
    {
      key: "start",
      title: setupRequired ? "Gleitzeit einrichten" : "Gleitzeit-Startwerte",
      summary: setupRequired ? "Trackingstart und Anfangssaldo setzen" : "Trackingstart und Anfangssaldo",
      body: renderBalanceStartSettings(settings),
      setup: setupRequired,
    },
    {
      key: "automation",
      title: "Tracking-Automatik",
      summary: "Arbeitsbeginn, Arbeitsende und Fortsetzen",
      body: renderTrackingAutomationSettings(settings),
    },
    {
      key: "runtime",
      title: "App & Aktualisierung",
      summary: "Autostart, Vorladen und Live-Aktualisierung",
      body: renderRuntimeSettings(settings),
    },
    {
      key: "popups",
      title: "Popups",
      summary: "Arbeitsbeginn, Arbeitsende und Tagesinfo",
      body: renderPopupSettings(settings),
    },
    {
      key: "location",
      title: "Standortcheck",
      summary: "Büro/Homeoffice-Erkennung",
      body: renderLocationSettings(settings),
    },
    {
      key: "office_quota",
      title: "Officequote",
      summary: "Mindestquote, Zeitraum und gemischte Tage",
      body: renderOfficeQuotaSettings(settings),
    },
    {
      key: "buffers",
      title: "Zeitpuffer",
      summary: "Start- und Feierabendversatz je Standort",
      body: renderBufferSettings(settings),
    },
    {
      key: "appearance",
      title: "Darstellung",
      summary: "Oberfläche und Lesbarkeit",
      body: renderAppearanceSettings(settings),
    },
    {
      key: "files",
      title: "Backup, Import & Export",
      summary: "Lokale Sicherungen und Dateiaustausch",
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
  ];
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

function renderSettingsSubnav(sections) {
  return `
    <nav class="settings-subnav" aria-label="Einstellungskategorien">
      <button class="secondary settings-back" id="close-settings-detail" type="button">Zur Übersicht</button>
      ${sections.map(section => {
        const active = state.settingsDetailKey === section.key;
        return `
          <button class="${active ? "active" : ""} ${section.danger ? "danger-zone" : ""}" type="button" data-section="${escapeHtml(section.key)}" aria-current="${active ? "page" : "false"}">
            <span>${escapeHtml(section.title)}</span>
            <small>${escapeHtml(section.summary)}</small>
          </button>
        `;
      }).join("")}
    </nav>
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
      </div>
      ${section.plain ? section.body : `
        <form id="settings-form" class="settings-form grid cols-2" data-section="${escapeHtml(section.key)}">
          ${section.body}
          ${section.setup ? `<input type="hidden" name="initial_setup_required" value="0">` : ""}
          <div class="form-actions"><button>${section.setup ? "Einrichtung speichern" : "Speichern"}</button></div>
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
  `;
}

function renderVacationSettings(settings) {
  return `
    <label>Urlaubsanspruch/Jahr <input name="vacation_days_per_year" type="text" value="${escapeHtml(settings.vacation_days_per_year)}"></label>
    <label>Urlaubsübertrag Vorjahr <input name="vacation_carry_over" type="text" value="${escapeHtml(settings.vacation_carry_over)}"></label>
    <label>Bundesland
      <input name="bundesland" type="text" value="${escapeHtml(settings.bundesland)}" placeholder="z. B. BW, BY, NRW">
      <small class="help-text">Wird für gesetzliche Feiertage in Kalender, Statistik und Arbeitszeitberechnung genutzt.</small>
    </label>
  `;
}

function renderBalanceStartSettings(settings) {
  return `
    <label>Startdatum der Zeiterfassung
      <input name="tracking_start_date" type="date" value="${escapeHtml(settings.tracking_start_date || "")}">
      <small class="help-text">Tage vor dem Startdatum werden für den Gleitzeitsaldo ignoriert.</small>
    </label>
    <label>Anfangssaldo Gleitzeit in Stunden
      <input name="initial_flextime_hours" type="text" inputmode="decimal" value="${escapeHtml(settings.initial_flextime_hours || "0,00")}" placeholder="50,89 oder -3.75">
      <small class="help-text">Komma und Punkt sind erlaubt, z. B. 50,89, -3,75 oder 12.5.</small>
    </label>
  `;
}

function renderLocationSettings(settings) {
  return `
    <div class="settings-field field-wide">
      <div class="settings-field-head">
        <strong>Standort-Ziele</strong>
        ${settingHelpButton("location_targets")}
      </div>
      <input name="homeoffice_check_targets" type="text" value="${escapeHtml(settings.homeoffice_check_targets)}" placeholder="server-firma, intranet.local:443, https://intranet.local">
      <small class="help-text">Servername ohne Port wird per Ping geprüft. host:port oder https://... wird per TCP geprüft. Erreichbar bedeutet Büro, nicht erreichbar bedeutet Homeoffice.</small>
    </div>
    <label>Timeout Standortcheck (ms) <input name="homeoffice_check_timeout_ms" type="number" min="50" value="${escapeHtml(settings.homeoffice_check_timeout_ms)}"></label>
  `;
}

function renderOfficeQuotaSettings(settings) {
  return `
    <label>Manuelle Büro-Tage
      <input name="office_baseline_days" type="text" inputmode="decimal" value="${escapeHtml(settings.office_baseline_days || "0")}" placeholder="z. B. 42">
      <small class="help-text">Tage vor der Nutzung, die in die Officequote einfließen sollen.</small>
    </label>
    <label>Manuelle Homeoffice-Tage
      <input name="homeoffice_baseline_days" type="text" inputmode="decimal" value="${escapeHtml(settings.homeoffice_baseline_days || "0")}" placeholder="z. B. 38">
      <small class="help-text">Tage vor der Nutzung, die in die Homeofficequote einfließen sollen.</small>
    </label>
    <label>Zeitraum der manuellen Tage
      <select name="office_baseline_period_mode">
        <option value="all" ${settings.office_baseline_period_mode !== "current_year" && settings.office_baseline_period_mode !== "rolling_365" && settings.office_baseline_period_mode !== "custom" ? "selected" : ""}>Alles seit Trackingstart</option>
        <option value="current_year" ${settings.office_baseline_period_mode === "current_year" ? "selected" : ""}>Aktuelles Kalenderjahr</option>
        <option value="rolling_365" ${settings.office_baseline_period_mode === "rolling_365" ? "selected" : ""}>Letzte 365 Tage</option>
        <option value="custom" ${settings.office_baseline_period_mode === "custom" ? "selected" : ""}>Eigener Zeitraum</option>
      </select>
      <small class="help-text">Die manuellen Tage zählen nur vollständig mit, wenn dieser Zeitraum komplett im ausgewerteten Officequote-Zeitraum liegt.</small>
    </label>
    <label class="conditional-field" data-show-when="office_baseline_period_mode:custom">
      Manuelle Tage von
      <input name="office_baseline_custom_start" type="date" value="${escapeHtml(settings.office_baseline_custom_start || "")}">
    </label>
    <label class="conditional-field" data-show-when="office_baseline_period_mode:custom">
      Manuelle Tage bis
      <input name="office_baseline_custom_end" type="date" value="${escapeHtml(settings.office_baseline_custom_end || "")}">
    </label>
    <label>Mindestquote Büro (%)
      <input name="office_quota_target_percent" type="number" min="0" max="100" step="0.1" value="${escapeHtml(settings.office_quota_target_percent || "50")}">
      <small class="help-text">Schwelle, ab der die Dashboard-Kachel als erfüllt markiert wird.</small>
    </label>
    <label>Gemischte Tage zählen als
      <select name="office_quota_mixed_day_mode">
        <option value="split" ${settings.office_quota_mixed_day_mode !== "office" && settings.office_quota_mixed_day_mode !== "homeoffice" ? "selected" : ""}>Anteilig Büro und Homeoffice</option>
        <option value="office" ${settings.office_quota_mixed_day_mode === "office" ? "selected" : ""}>Immer Bürotag</option>
        <option value="homeoffice" ${settings.office_quota_mixed_day_mode === "homeoffice" ? "selected" : ""}>Immer Homeoffice-Tag</option>
      </select>
      <small class="help-text">Gilt für Tage, an denen Arbeitssegmente im Büro und im Homeoffice vorkommen.</small>
    </label>
    <label>Zeitraum für Officequote
      <select name="office_quota_period_mode">
        <option value="all" ${settings.office_quota_period_mode !== "current_year" && settings.office_quota_period_mode !== "rolling_365" && settings.office_quota_period_mode !== "custom" ? "selected" : ""}>Alles seit Trackingstart</option>
        <option value="current_year" ${settings.office_quota_period_mode === "current_year" ? "selected" : ""}>Aktuelles Kalenderjahr</option>
        <option value="rolling_365" ${settings.office_quota_period_mode === "rolling_365" ? "selected" : ""}>Letzte 365 Tage</option>
        <option value="custom" ${settings.office_quota_period_mode === "custom" ? "selected" : ""}>Eigener Zeitraum</option>
      </select>
      <small class="help-text">Die Dashboard-Kachel nutzt diesen Zeitraum. Manuelle Startwerte ohne tagesgenaue Zuordnung werden bei Teilzeiträumen nicht geraten.</small>
    </label>
    <label class="conditional-field" data-show-when="office_quota_period_mode:custom">
      Officequote von
      <input name="office_quota_custom_start" type="date" value="${escapeHtml(settings.office_quota_custom_start || "")}">
    </label>
    <label class="conditional-field" data-show-when="office_quota_period_mode:custom">
      Officequote bis
      <input name="office_quota_custom_end" type="date" value="${escapeHtml(settings.office_quota_custom_end || "")}">
    </label>
  `;
}

function renderBufferSettings(settings) {
  return `
    <label>Startpuffer Büro (Minuten)
      <input name="office_start_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.office_start_buffer_minutes || "0")}">
      <small class="help-text">Wird nur beim automatischen Arbeitsbeginn abgezogen.</small>
    </label>
    <label>Startpuffer Homeoffice (Minuten)
      <input name="home_start_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.home_start_buffer_minutes || "0")}">
      <small class="help-text">Für den Start im privaten WLAN vor VPN-Verbindung.</small>
    </label>
    <label>Arbeitsende-Puffer Büro (Minuten)
      <input name="office_end_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.office_end_buffer_minutes || "0")}">
      <small class="help-text">Wird beim automatischen Feierabend durch Windows-Herunterfahren auf die erkannte Endzeit aufgeschlagen.</small>
    </label>
    <label>Arbeitsende-Puffer Homeoffice (Minuten)
      <input name="home_end_buffer_minutes" type="number" min="0" step="1" value="${escapeHtml(settings.home_end_buffer_minutes || "0")}">
      <small class="help-text">Für den Weg vom automatischen Shutdown bis zur echten Abmeldung.</small>
    </label>
  `;
}

function renderTrackingAutomationSettings(settings) {
  return `
    ${settingToggle(
      "automatic_work_start_enabled",
      "Arbeitsbeginn automatisch erfassen",
      settings.automatic_work_start_enabled,
      "Startet beim Tracker-Start automatisch ein Arbeitssegment. Bei Aus musst du Arbeitsbeginn im Tray-Menü manuell starten."
    )}
    ${settingToggle(
      "automatic_work_end_enabled",
      "Arbeitsende automatisch beim Herunterfahren erfassen",
      settings.automatic_work_end_enabled,
      "Schließt offene Segmente, wenn Windows ein echtes Herunterfahren oder Neustarten meldet."
    )}
    ${settingToggle(
      "automatic_recovery_enabled",
      "Offene Vortagssegmente automatisch schließen",
      settings.automatic_recovery_enabled,
      "Nutzt beim nächsten Start den letzten Tracker-Zeitstempel, falls Windows kein Shutdown-Ereignis geliefert hat."
    )}
    ${settingToggle(
      "auto_resume_after_break_enabled",
      "Nach Pause automatisch weiterarbeiten",
      settings.auto_resume_after_break_enabled,
      "Startet nach „Pause beenden“ automatisch wieder ein Arbeitssegment."
    )}
    ${settingToggle(
      "auto_resume_after_absence_enabled",
      "Nach Abwesenheit automatisch weiterarbeiten",
      settings.auto_resume_after_absence_enabled,
      "Startet nach „Abwesenheit beenden“ automatisch wieder ein Arbeitssegment."
    )}
  `;
}

function renderRuntimeSettings(settings) {
  return `
    ${settingToggle(
      "autostart_enabled",
      "Beim Hochfahren automatisch starten",
      settings.autostart_enabled,
      "Legt unter Windows eine Verknüpfung im Autostart-Ordner an oder entfernt sie."
    )}
    ${settingToggle(
      "preload_app_on_tracker_start",
      "App-Fenster im Hintergrund vorladen",
      settings.preload_app_on_tracker_start,
      "Startet die Oberfläche beim Tracker-Start versteckt, damit „App öffnen“ unter Windows deutlich schneller reagiert."
    )}
    <label>Ansicht automatisch aktualisieren
      <select name="auto_refresh_interval_seconds">
        ${autoRefreshIntervalOptions(settings.auto_refresh_interval_seconds)}
      </select>
      <small class="help-text">Aktualisiert die sichtbare Ansicht im Hintergrund. Eingaben und Formulare werden dabei nicht unterbrochen.</small>
    </label>
  `;
}

function renderPopupSettings(settings) {
  return `
    <label>Arbeitsbeginn-Popup
      <select name="work_start_popup_mode">
        <option value="off" ${settings.work_start_popup_mode !== "on" ? "selected" : ""}>Aus</option>
        <option value="on" ${settings.work_start_popup_mode === "on" ? "selected" : ""}>An</option>
      </select>
      <small class="help-text">Wenn aktiv, zeigt der Tracker die automatisch geplante Startzeit an. Schließen speichert automatisch diese Zeit; „nicht speichern“ legt keinen Arbeitsbeginn an.</small>
    </label>
    <label>Arbeitsende-Popup
      <select name="work_end_popup_mode">
        <option value="off" ${settings.work_end_popup_mode === "off" ? "selected" : ""}>Aus</option>
        <option value="open_only" ${settings.work_end_popup_mode !== "off" && settings.work_end_popup_mode !== "always" ? "selected" : ""}>Nur wenn ein Segment offen ist</option>
        <option value="always" ${settings.work_end_popup_mode === "always" ? "selected" : ""}>Immer prüfen</option>
      </select>
      <small class="help-text">„Immer“ zeigt auch das letzte gespeicherte Arbeitsende an, damit du es korrigieren kannst.</small>
    </label>
    <label>Wann Arbeits-Popups anzeigen?
      <select name="work_popup_timing">
        <option value="startup" ${settings.work_popup_timing !== "work_end" && settings.work_popup_timing !== "custom" ? "selected" : ""}>Beim Start des Trackers</option>
        <option value="work_end" ${settings.work_popup_timing === "work_end" ? "selected" : ""}>Beim Feierabend-Klick</option>
        <option value="custom" ${settings.work_popup_timing === "custom" ? "selected" : ""}>Zu fester Uhrzeit</option>
      </select>
    </label>
    <label class="conditional-field" data-show-when="work_popup_timing:custom">
      Uhrzeit für Arbeits-Popups
      <input name="work_popup_custom_time" type="time" value="${escapeHtml(settings.work_popup_custom_time || "07:00")}">
      <small class="help-text">Gilt nur, wenn „Zu fester Uhrzeit“ ausgewählt ist.</small>
    </label>
    <label>Tagesinfo-Popup
      <select name="daily_info_popup_mode">
        <option value="off" ${settings.daily_info_popup_mode !== "work_end" && settings.daily_info_popup_mode !== "custom" ? "selected" : ""}>Aus</option>
        <option value="work_end" ${settings.daily_info_popup_mode === "work_end" ? "selected" : ""}>Bei Arbeitsende</option>
        <option value="custom" ${settings.daily_info_popup_mode === "custom" ? "selected" : ""}>Zu fester Uhrzeit</option>
      </select>
      <small class="help-text">Zeigt Arbeitszeit, Pause, Soll, Tages-Saldo und Segmente des aktuellen Tages.</small>
    </label>
    <label class="conditional-field" data-show-when="daily_info_popup_mode:custom">
      Uhrzeit für Tagesinfo
      <input name="daily_info_popup_time" type="time" value="${escapeHtml(settings.daily_info_popup_time || "16:30")}">
    </label>
  `;
}

function settingToggle(name, label, value, helpText) {
  return `
    <label>${escapeHtml(label)}
      <select name="${escapeHtml(name)}">
        <option value="1" ${value !== "0" ? "selected" : ""}>Ja</option>
        <option value="0" ${value === "0" ? "selected" : ""}>Nein</option>
      </select>
      <small class="help-text">${escapeHtml(helpText)}</small>
    </label>
  `;
}

function autoRefreshIntervalOptions(currentValue) {
  const current = String(currentValue || "60");
  return [
    ["0", "Aus"],
    ["30", "Alle 30 Sekunden"],
    ["60", "Jede Minute"],
    ["120", "Alle 2 Minuten"],
    ["300", "Alle 5 Minuten"],
    ["600", "Alle 10 Minuten"],
  ].map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
}

function renderAppearanceSettings(settings) {
  return `
    <label>Darkmode
      <select name="darkmode">
        <option value="0" ${settings.darkmode === "0" ? "selected" : ""}>Aus</option>
        <option value="1" ${settings.darkmode === "1" ? "selected" : ""}>An</option>
      </select>
    </label>
    <label>Dashboard-Countdown
      <select name="dashboard_absence_countdown_mode">
        <option value="workdays" ${settings.dashboard_absence_countdown_mode !== "calendar_days" ? "selected" : ""}>Arbeitstage anzeigen</option>
        <option value="calendar_days" ${settings.dashboard_absence_countdown_mode === "calendar_days" ? "selected" : ""}>Kalendertage anzeigen</option>
      </select>
      <small class="help-text">Bestimmt die große Zahl der Kachel „Nächste Abwesenheit“. In den Details werden immer beide Werte angezeigt.</small>
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
        <div class="field-wide help-inline">
          <small id="export-range-hint" class="help-text">Der Zeitraum wird automatisch bis zum neuesten Eintrag, zur neuesten Abwesenheit oder Notiz gesetzt.</small>
          ${settingHelpButton("export")}
        </div>
      </form>
      <div class="import-panel">
        <div>
          <strong>Daten importieren</strong>
          <small>CSV oder Excel aus einem Arbeitszeit-Export. Summenzeilen werden ignoriert, echte Segmente, Abwesenheiten und Notizen werden übernommen.</small>
        </div>
        <div class="import-panel-actions">
          ${settingHelpButton("import_excel")}
          <button id="import-file" class="secondary" type="button">Daten importieren</button>
        </div>
        <input id="import-file-input" type="file" accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden>
      </div>
      <div id="import-result" aria-live="polite"></div>
      <div class="import-panel">
        <div>
          <strong>SAP-SDATA prüfen</strong>
          <small>CSV oder Excel mit SDATA-Zeilen. P10 wird als Arbeitsbeginn, P20 als Arbeitsende gelesen; Lücken zwischen Arbeitssegmenten werden als Pause vorgeschlagen.</small>
        </div>
        <div class="import-panel-actions">
          ${settingHelpButton("sap_sdata")}
          <button id="sap-sdata-file" class="secondary import-action-button" type="button">SAP-Datei auswählen</button>
        </div>
        <input id="sap-sdata-file-input" type="file" accept=".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden>
      </div>
      <div id="sap-sdata-result" class="sap-import-preview" aria-live="polite"></div>
    </div>
  `;
}

async function hydrateExportDefaults() {
  const form = document.getElementById("export-form");
  if (!form) return;
  const initialStart = form.start_date.value;
  const initialEnd = form.end_date.value;
  try {
    const defaults = await api("export_defaults");
    if (!document.body.contains(form)) return;
    if (defaults.start_date && form.start_date.value === initialStart) {
      form.start_date.value = defaults.start_date;
    }
    if (defaults.end_date && form.end_date.value === initialEnd) {
      form.end_date.value = defaults.end_date;
    }
    const hint = document.getElementById("export-range-hint");
    if (hint) {
      hint.textContent = `Standardzeitraum: ${form.start_date.value} bis ${form.end_date.value}. Geplante Abwesenheiten nach heute werden mit exportiert.`;
    }
  } catch (error) {
    console.warn(error);
  }
}

function bindSapSdataImportControls() {
  const button = document.getElementById("sap-sdata-file");
  const input = document.getElementById("sap-sdata-file-input");
  button?.addEventListener("click", () => input?.click());
  input?.addEventListener("change", async event => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    const output = document.getElementById("sap-sdata-result");
    if (button) {
      button.disabled = true;
      button.textContent = "Prüfe ...";
    }
    try {
      const payload = await fileToBase64(file);
      state.sapSdataPreview = await api("preview_sap_sdata_file", file.name, payload);
      renderSapSdataPreview();
      notify(`SAP-Vorschau erstellt: ${file.name}`);
    } catch (error) {
      state.sapSdataPreview = null;
      notify(error.message || String(error), "error");
      if (output) output.innerHTML = `<div class="error-panel">${escapeHtml(error.message || String(error))}</div>`;
    } finally {
      event.currentTarget.value = "";
      if (button) {
        button.disabled = false;
        button.textContent = "SAP-Datei auswählen";
      }
    }
  });
}

function renderSapSdataPreview() {
  const output = document.getElementById("sap-sdata-result");
  const preview = state.sapSdataPreview;
  if (!output || !preview) return;
  if (!preview.days?.length) {
    output.innerHTML = `<div class="empty">Keine verwertbaren SAP-SDATA-Ereignisse gefunden.</div>`;
    return;
  }
  output.innerHTML = `
    <section class="sap-preview-panel stack">
      <div class="page-head compact">
        <div>
          <h3>SAP-SDATA Vorschau</h3>
          <p>${escapeHtml(preview.source_name || preview.name || "SAP-Datei")} · Events ${Number(preview.events_read || 0)} · ignoriert ${Number(preview.rows_ignored || 0)}</p>
        </div>
        <div class="row-actions sap-preview-actions">
          <button id="sap-sdata-toggle-selection" class="secondary compact-button" type="button">Alle abwählen</button>
          <button id="sap-sdata-import-selected" type="button">Ausgewählte Tage importieren</button>
        </div>
      </div>
      <div class="table-wrap">
        <table class="sap-preview-table">
          <thead><tr><th>Import</th><th>Datum</th><th>Status</th><th>Aktuell</th><th>SAP-Vorschlag</th></tr></thead>
          <tbody>
            ${preview.days.map(day => renderSapSdataDay(day)).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
  output.querySelectorAll(".sap-day-toggle").forEach(button => {
    button.addEventListener("click", () => {
      const details = document.getElementById(`sap-day-details-${button.dataset.date}`);
      if (!details) return;
      const open = details.hidden;
      details.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      button.textContent = open ? "Details schließen" : "Details";
    });
  });
  output.querySelectorAll(".sap-day-select").forEach(input => {
    input.addEventListener("change", updateSapSelectionToggleLabel);
  });
  document.getElementById("sap-sdata-toggle-selection")?.addEventListener("click", toggleSapSdataSelection);
  document.getElementById("sap-sdata-import-selected")?.addEventListener("click", importSelectedSapSdataDays);
  updateSapSelectionToggleLabel();
}

function renderSapSdataDay(day) {
  const disabled = !day.imported_segments?.length;
  const checked = day.selected && !disabled ? "checked" : "";
  const currentSummary = sapSegmentSummary(day.current_segments);
  const importSummary = sapSegmentSummary(day.imported_segments);
  return `
    <tr class="sap-preview-row sap-status-${escapeHtml(day.status || "changed")}">
      <td data-label="Import"><input type="checkbox" class="sap-day-select" value="${escapeHtml(day.date)}" ${checked} ${disabled ? "disabled" : ""}></td>
      <td data-label="Datum"><strong>${escapeHtml(day.date)}</strong><small>${weekdayShort(day.date)}</small></td>
      <td data-label="Status">
        <span class="sap-status-pill">${escapeHtml(day.status_label || day.status || "")}</span>
        <span class="sap-inline-comparison"><strong>Aktuell</strong>${currentSummary}</span>
        <span class="sap-inline-comparison"><strong>SAP</strong>${importSummary}</span>
      </td>
      <td data-label="Aktuell">${currentSummary}</td>
      <td data-label="SAP-Vorschlag">
        ${importSummary}
        <button class="secondary sap-day-toggle" type="button" data-date="${escapeHtml(day.date)}" aria-expanded="false">Details</button>
      </td>
    </tr>
    <tr id="sap-day-details-${escapeHtml(day.date)}" class="sap-preview-details" hidden>
      <td colspan="5">
        <div class="sap-preview-detail-grid">
          <section>
            <h4>Aktuell im Programm</h4>
            ${sapSegmentList(day.current_segments)}
          </section>
          <section>
            <h4>Aus SAP-SDATA</h4>
            ${sapSegmentList(day.imported_segments)}
          </section>
          <section>
            <h4>Hinweise</h4>
            ${day.warnings?.length ? `<ul>${day.warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : `<p class="muted">Keine Hinweise.</p>`}
          </section>
          <section>
            <h4>SAP-Events</h4>
            ${sapEventList(day.events)}
          </section>
        </div>
      </td>
    </tr>
  `;
}

function toggleSapSdataSelection() {
  const boxes = Array.from(document.querySelectorAll(".sap-day-select:not(:disabled)"));
  if (!boxes.length) return;
  const allChecked = boxes.every(input => input.checked);
  boxes.forEach(input => {
    input.checked = !allChecked;
  });
  updateSapSelectionToggleLabel();
}

function updateSapSelectionToggleLabel() {
  const button = document.getElementById("sap-sdata-toggle-selection");
  if (!button) return;
  const boxes = Array.from(document.querySelectorAll(".sap-day-select:not(:disabled)"));
  const allChecked = boxes.length > 0 && boxes.every(input => input.checked);
  button.textContent = allChecked ? "Alle abwählen" : "Alle auswählen";
}

async function importSelectedSapSdataDays() {
  const preview = state.sapSdataPreview;
  if (!preview) return;
  const selectedDates = Array.from(document.querySelectorAll(".sap-day-select:checked")).map(input => input.value);
  if (!selectedDates.length) {
    notify("Bitte mindestens einen SAP-Tag auswählen.", "error");
    return;
  }
  const button = document.getElementById("sap-sdata-import-selected");
  if (button) {
    button.disabled = true;
    button.textContent = "Importiere ...";
  }
  try {
    const result = await api("import_sap_sdata_preview", {
      source_name: `SAP SDATA ${preview.source_name || preview.name || ""}`.trim(),
      days: preview.days,
      selected_dates: selectedDates,
    });
    const warningText = Number(result.warnings || 0) ? ` · Warnungen ${result.warnings}` : "";
    const logButton = result.log_path ? `<button id="open-sap-import-log" class="secondary" type="button" data-path="${escapeHtml(result.log_path)}">Protokoll öffnen</button>` : "";
    const output = document.getElementById("sap-sdata-result");
    if (output) {
      output.innerHTML = `<div class="success-panel"><strong>SAP-SDATA importiert</strong><small>${escapeHtml(result.start_date)} bis ${escapeHtml(result.end_date)} · Segmente ${Number(result.segments || 0)}${escapeHtml(warningText)} · Protokoll ${escapeHtml(result.log_name || "")}</small>${logButton}</div>`;
      document.getElementById("open-sap-import-log")?.addEventListener("click", async event => {
        try {
          await api("open_import_log", event.currentTarget.dataset.path);
        } catch (error) {
          notify(error.message || String(error), "error");
        }
      });
    }
    state.sapSdataPreview = null;
    notify("SAP-SDATA importiert");
    await refreshCurrentView({ silent: true });
  } catch (error) {
    notify(error.message || String(error), "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Ausgewählte Tage importieren";
    }
  }
}

function sapSegmentSummary(segments = []) {
  if (!segments.length) return `<span class="muted">keine Segmente</span>`;
  return segments
    .map(segment => `${escapeHtml(segmentTypeLabel(segment.type))} ${escapeHtml(timeShort(segment.start_time))}-${escapeHtml(timeShort(segment.end_time))}`)
    .join("<br>");
}

function sapSegmentList(segments = []) {
  if (!segments.length) return `<p class="muted">Keine Segmente.</p>`;
  return `<ul>${segments.map(segment => `<li>${escapeHtml(segmentTypeLabel(segment.type))}: ${escapeHtml(timeShort(segment.start_time))} bis ${escapeHtml(timeShort(segment.end_time))}${segment.location ? ` · ${escapeHtml(locationLabel(segment.location))}` : ""}</li>`).join("")}</ul>`;
}

function sapEventList(events = []) {
  if (!events.length) return `<p class="muted">Keine Events.</p>`;
  return `<ul>${events.map(event => `<li>${escapeHtml(event.event_type)} · ${escapeHtml(event.time?.slice(0, 5) || "")} · Zeile ${Number(event.row_number || 0)}</li>`).join("")}</ul>`;
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
  bindSettingHelpButtons();
  document.getElementById("close-settings-detail")?.addEventListener("click", () => {
    state.settingsDetailKey = "";
    renderSettings();
  });
  const form = document.getElementById("settings-form");
  if (!form) return;
  bindConditionalFields(form);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const values = collectSettingsValues(form);
    try {
      const result = await api("save_settings", values);
      document.body.classList.toggle("dark", result.settings.darkmode === "1");
      resetCalculatorDefaults();
      startAutoRefresh(result.settings);
      notify(section?.setup ? "Einrichtung gespeichert" : "Einstellungen gespeichert");
      await renderSettings();
    } catch (error) {
      notify(error.message || String(error), "error");
    }
  });
}

function settingHelpButton(key) {
  return `<button class="help-icon-button" type="button" data-settings-help="${escapeHtml(key)}" aria-label="Hilfe öffnen">?</button>`;
}

function bindSettingHelpButtons() {
  document.querySelectorAll("[data-settings-help]").forEach(button => {
    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      openSettingsHelp(button.dataset.settingsHelp);
    });
  });
}

function openSettingsHelp(key) {
  const help = SETTINGS_HELP[key];
  if (!help) return;
  document.querySelector(".modal-backdrop")?.remove();
  const body = help.paragraphs.map(paragraph => `<p>${escapeHtml(paragraph)}</p>`).join("");
  content.insertAdjacentHTML("beforeend", `
    <div class="modal-backdrop" role="presentation">
      <section class="modal-panel help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title">
        <div class="page-head compact">
          <div>
            <h2 id="help-title">${escapeHtml(help.title)}</h2>
          </div>
          <button class="secondary" id="close-help-dialog" type="button">Schließen</button>
        </div>
        <div class="help-dialog-body">${body}</div>
      </section>
    </div>
  `);
  const close = () => document.querySelector(".modal-backdrop")?.remove();
  document.getElementById("close-help-dialog")?.addEventListener("click", close);
  document.querySelector(".modal-backdrop")?.addEventListener("click", event => {
    if (event.target.classList.contains("modal-backdrop")) close();
  });
}

function bindConditionalFields(scope) {
  scope.querySelectorAll("[data-show-when]").forEach(field => {
    const [name, expected] = String(field.dataset.showWhen || "").split(":");
    const control = scope.elements?.[name];
    if (!name || !control) return;
    const inputs = [...field.querySelectorAll("input, select, textarea, button")];
    const update = () => {
      const visible = String(control.value) === expected;
      field.hidden = !visible;
      inputs.forEach(input => {
        input.disabled = !visible;
      });
    };
    control.addEventListener("change", update);
    update();
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

function resetCalculatorDefaults() {
  state.calculatorDefaults = null;
  state.calculatorRows = [];
  state.calculatorStartBalanceHours = "";
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
      state.settingsDetailKey = mode === "all" ? "start" : "work";
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

function metric(label, value, signedValue = null, key = "") {
  const klass = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
  const dataAttr = key ? ` data-stats-metric="${escapeHtml(key)}"` : "";
  return `<article class="metric"${dataAttr}><span>${label}</span><strong class="${klass}" data-stats-value>${value}</strong></article>`;
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

function balanceMetric(label, value, status, signedValue = null, key = "") {
  const valueClass = signedValue === null ? "" : signedValue >= 0 ? "positive" : "negative";
  const dataAttr = key ? ` data-stats-metric="${escapeHtml(key)}"` : "";
  return `<article class="metric balance-card ${escapeHtml(status?.class || "")}"${dataAttr}>
    <span>${label}</span>
    <strong class="${valueClass}" data-stats-value>${escapeHtml(value)}</strong>
    <small class="balance-card-status" data-stats-status>${escapeHtml(status?.label || "0 bis 45 Stunden")}</small>
  </article>`;
}

function absenceCountdownValue(nextAbsence) {
  if (!nextAbsence?.date) return "Keine";
  return `${numberDe(nextAbsence.display_days)} ${nextAbsence.display_days === 1 ? "Tag" : "Tage"}`;
}

function todayWorkDetail(data) {
  const detail = data.today_detail || {};
  const remainingWork = Math.max(0, Number(detail.remaining_work_minutes || 0));
  const progress = data.target_minutes ? Math.min(100, Math.max(0, (Number(data.work_minutes || 0) / Number(data.target_minutes)) * 100)) : 100;
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Ist", fmtMinutes(data.work_minutes), "reine Arbeitszeit")}
        ${dashboardInsightCard("Soll", fmtMinutes(data.target_minutes), "für heute")}
        ${dashboardInsightCard("Rest netto", fmtMinutes(remainingWork), "ohne Restpause")}
        ${dashboardInsightCard("Saldo", signedMinutes(data.live_day.balance_minutes), "live heute", data.live_day.balance_minutes >= 0 ? "positive" : "negative")}
      </div>
      ${dashboardProgress("Tagesfortschritt", progress, `${numberDe(progress)} %`)}
      <p class="muted">Tagesfenster: ${escapeHtml(data.range)} · Arbeitsegmente: ${numberDe(detail.work_segment_count || 0)}</p>
    </div>
  `;
}

function todayBreakDetail(data) {
  const detail = data.today_detail || {};
  const minimum = Number(detail.minimum_break_minutes || data.settings?.daily_break_minutes || 0);
  const remainingBreak = Math.max(0, Number(detail.remaining_break_minutes || 0));
  const progress = minimum ? Math.min(100, Math.max(0, (Number(data.break_minutes || 0) / minimum) * 100)) : 100;
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Gemacht", fmtMinutes(data.break_minutes), "Pause heute")}
        ${dashboardInsightCard("Mindestpause", fmtMinutes(minimum), "aus Einstellungen")}
        ${dashboardInsightCard("Noch offen", fmtMinutes(remainingBreak), "bis Mindestpause")}
        ${dashboardInsightCard("Offen", detail.open_segment_label || "Keins", "laufendes Segment")}
      </div>
      ${dashboardProgress("Pausenstatus", progress, remainingBreak > 0 ? `${fmtMinutes(remainingBreak)} fehlen` : "Pause erfüllt", remainingBreak > 0 ? "warning" : "success")}
      <p class="muted">Automatische Pausen werden in der Arbeitszeitberechnung bereits berücksichtigt.</p>
    </div>
  `;
}

function liveDayDetail(data) {
  const remainingWork = Number(data.live_day?.remaining_work_minutes || data.today_detail?.remaining_work_minutes || 0);
  const zeroTime = data.live_day?.zero_time || "erreicht";
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Saldo jetzt", signedMinutes(data.live_day.balance_minutes), "heute live", data.live_day.balance_minutes >= 0 ? "positive" : "negative")}
        ${dashboardInsightCard("±0 ungefähr", zeroTime, "bei Weiterarbeit")}
        ${dashboardInsightCard("Restarbeit", fmtMinutes(remainingWork), "netto")}
        ${dashboardInsightCard("Segment", data.today_detail?.open_segment_label || "Keins", "aktueller Status")}
      </div>
      <p>${escapeHtml(data.live_day.detail)}</p>
      <p class="muted">${escapeHtml(data.live_day.note)}</p>
    </div>
  `;
}

function flextimeDetail(data) {
  const trends = Array.isArray(data.flextime_trends) ? data.flextime_trends : [];
  const last30 = trends.find(period => period.key === "last_30") || trends[0] || {};
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Kontostand", data.flextime_hours, "aktueller Stand", data.flextime >= 0 ? "positive" : "negative")}
        ${dashboardInsightCard("Status", data.flextime_status?.label || "0 bis 45 Stunden", "Grenzbereich")}
        ${dashboardInsightCard("30 Tage", signedMinutes(last30.balance_minutes || 0), "Veränderung", Number(last30.balance_minutes || 0) >= 0 ? "positive" : "negative")}
        ${dashboardInsightCard("Ø pro Tag", signedMinutes(last30.average_balance_minutes || 0), "Arbeitstage", Number(last30.average_balance_minutes || 0) >= 0 ? "positive" : "negative")}
      </div>
      <div class="dashboard-period-cards">
        ${trends.map(period => flextimePeriodCard(period)).join("")}
      </div>
      <p class="muted">Angefangene Tage zählen erst nach dem Schließen des offenen Segments ins Konto.</p>
    </div>
  `;
}

function flextimePeriodCard(period) {
  const balance = Number(period?.balance_minutes || 0);
  return `<article class="dashboard-period-card">
    <div>
      <span>${escapeHtml(period?.label || "Zeitraum")}</span>
      <strong class="${balance >= 0 ? "positive" : "negative"}">${signedMinutes(balance)}</strong>
      <small>${escapeHtml(period?.start_date || "")} bis ${escapeHtml(period?.end_date || "")}</small>
    </div>
    <dl>
      <div><dt>Ist</dt><dd>${fmtMinutes(period?.actual_minutes || 0)}</dd></div>
      <div><dt>Soll</dt><dd>${fmtMinutes(period?.target_minutes || 0)}</dd></div>
      <div><dt>Tage</dt><dd>${numberDe(period?.workday_count || 0)}</dd></div>
      <div><dt>Ø</dt><dd class="${Number(period?.average_balance_minutes || 0) >= 0 ? "positive" : "negative"}">${signedMinutes(period?.average_balance_minutes || 0)}</dd></div>
    </dl>
  </article>`;
}

function vacationDetail(data) {
  const stats = data.vacation_stats || {};
  const nextVacation = stats.next_vacation;
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Rest", `${numberDe(stats.remaining_days ?? data.remaining_vacation)} Tage`, `Jahr ${stats.year || new Date(data.today).getFullYear()}`)}
        ${dashboardInsightCard("Genommen", `${numberDe(stats.used_days || 0)} Tage`, "bis gestern")}
        ${dashboardInsightCard("Geplant", `${numberDe(stats.planned_days || 0)} Tage`, "ab heute")}
        ${dashboardInsightCard("Anspruch", `${numberDe((stats.entitlement_days || 0) + (stats.carry_over_days || 0))} Tage`, "inkl. Übertrag")}
      </div>
      <div class="dashboard-period-cards">
        ${dashboardMiniPeriodCard("Krank", `${numberDe(stats.sick_days || 0)} Tage`, "dieses Jahr")}
        ${dashboardMiniPeriodCard("Gleitzeittage", `${numberDe(stats.flextime_days || 0)} Tage`, "dieses Jahr")}
        ${dashboardMiniPeriodCard("Nächster Urlaub", nextVacation ? periodLabel(nextVacation.start_date, nextVacation.end_date) : "Keiner", nextVacation ? `${numberDe(nextVacation.counted_days)} Tage` : "nicht geplant")}
      </div>
    </div>
  `;
}

function absenceCountdownDetail(nextAbsence) {
  if (!nextAbsence?.date) {
    return detailText("Es ist keine zukünftige Abwesenheit eingetragen.", "muted");
  }
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Anzeige", `${numberDe(nextAbsence.display_days)} Tage`, nextAbsence.display_label)}
        ${dashboardInsightCard("Kalender", `${numberDe(nextAbsence.calendar_days)} Tage`, "inkl. Wochenenden")}
        ${dashboardInsightCard("Arbeitstage", `${numberDe(nextAbsence.workdays)} Tage`, "nach Einstellungen")}
        ${dashboardInsightCard("Dauer", `${numberDe(nextAbsence.counted_days)} Tage`, categoryLabel(nextAbsence.type))}
      </div>
      <div class="dashboard-period-cards">
        ${dashboardMiniPeriodCard("Zeitraum", periodLabel(nextAbsence.start_date, nextAbsence.end_date), nextAbsence.half_day ? "halber Tag" : "ganzer Tag")}
        ${dashboardMiniPeriodCard("Notiz", nextAbsence.note || "Keine", "hinterlegt")}
      </div>
    </div>
  `;
}

function dateDetail(data) {
  const date = new Date(`${data.today}T00:00:00`);
  const weekday = Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("de-DE", { weekday: "long" });
  const week = Number.isNaN(date.getTime()) ? "" : `KW ${isoWeekNumber(date)}`;
  return `
    <div class="dashboard-insight-detail">
      <div class="dashboard-insight-cards">
        ${dashboardInsightCard("Heute", data.today, weekday)}
        ${dashboardInsightCard("Kalenderwoche", week || "-", "ISO-Woche")}
        ${dashboardInsightCard("Standort", data.location, "aktuelle Erkennung")}
        ${dashboardInsightCard("Trackingstart", data.settings.tracking_start_date || "Automatisch", "ab erstem Eintrag")}
      </div>
    </div>
  `;
}

function dashboardInsightCard(label, value, hint, valueClass = "") {
  return `<article class="dashboard-insight-card">
    <span>${escapeHtml(label)}</span>
    <strong class="${escapeHtml(valueClass)}">${escapeHtml(value)}</strong>
    <small>${escapeHtml(hint || "")}</small>
  </article>`;
}

function dashboardMiniPeriodCard(label, value, hint) {
  return `<article class="dashboard-period-card compact">
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(hint || "")}</small>
    </div>
  </article>`;
}

function dashboardProgress(label, percent, text, status = "") {
  const width = Math.max(0, Math.min(100, Number(percent) || 0));
  return `<div class="dashboard-progress ${escapeHtml(status)}">
    <div><span>${escapeHtml(label)}</span><strong>${escapeHtml(text)}</strong></div>
    <div class="dashboard-progress-track" aria-hidden="true"><span style="width: ${width}%"></span></div>
  </div>`;
}

function officeQuotaDetail(stats) {
  const selected = stats?.selected_period || stats || {};
  const comparisonPeriods = Array.isArray(stats?.comparison_periods) ? stats.comparison_periods : [];
  const periods = [selected, ...comparisonPeriods].filter(Boolean);
  const selectedHint = officeQuotaPeriodHint(selected);
  const statusText = stats.office_requirement_met
    ? "Die eingestellte Büroquote ist aktuell erfüllt."
    : "Achtung: Die eingestellte Büroquote ist aktuell unterschritten.";
  return `
    <div class="office-quota-detail">
      <div class="office-quota-overview">
        ${officeQuotaSummaryCard("Mindestquote", `${numberDe(stats.target_percent)} %`, "Einstellung")}
        ${officeQuotaSummaryCard("Büro im Zeitraum", `${numberDe(selected.office_days)} Tage`, selectedHint)}
        ${officeQuotaSummaryCard("Homeoffice im Zeitraum", `${numberDe(selected.homeoffice_days)} Tage`, selectedHint)}
      </div>
      <div class="office-quota-periods">
        ${periods.map((period, index) => officeQuotaPeriodCard(period, index === 0)).join("")}
      </div>
      <p class="${stats.office_requirement_met ? "positive" : "negative"}">${escapeHtml(statusText)}</p>
      <p class="${officeBaselineStatusClass(selected)}">Manuelle Startwerte: ${escapeHtml(officeBaselinePeriodLabel(selected))}</p>
      <p class="muted">Gemischte Tage zählen als: ${escapeHtml(officeQuotaMixedModeLabel(stats.mixed_day_mode))}</p>
    </div>
  `;
}

function officeQuotaPeriodHint(period) {
  const label = String(period?.label || "Aktueller Zeitraum").replace(/^Eingestellt:\s*/, "");
  if (!period?.start_date || !period?.end_date) return label;
  return `${label} · ${period.start_date} bis ${period.end_date}`;
}

function officeBaselinePeriodLabel(period) {
  if (period?.manual_baseline_status === "empty") return "keine manuellen Startwerte hinterlegt";
  const range = `${period?.manual_baseline_start_date || ""} bis ${period?.manual_baseline_end_date || ""}`;
  if (period?.includes_manual_baseline) return `${range} · vollständig berücksichtigt`;
  return `${range} · nicht gezählt, weil keine tagesgenaue Zuordnung möglich ist`;
}

function officeBaselineStatusClass(period) {
  if (period?.manual_baseline_status === "excluded") return "negative";
  return "muted";
}

function officeQuotaSummaryCard(label, value, hint) {
  return `<article class="office-quota-summary-card">
    <span>${escapeHtml(label)}</span>
    <strong>${escapeHtml(value)}</strong>
    <small>${escapeHtml(hint)}</small>
  </article>`;
}

function officeQuotaPeriodCard(period, selected = false) {
  const office = Number(period?.office_days || 0);
  const home = Number(period?.homeoffice_days || 0);
  const total = Math.max(0, office + home);
  const officeShare = total ? Math.max(0, Math.min(100, (office / total) * 100)) : 0;
  const targetShare = Math.max(0, Math.min(100, Number(period?.target_percent || 0)));
  const warningStart = Math.max(0, targetShare - 5);
  const range = `${period?.start_date || ""} bis ${period?.end_date || ""}`;
  const quotaStatus = officeQuotaStatus(period);
  return `<article class="office-quota-period-card ${selected ? "selected" : ""} ${quotaStatus.cardClass}">
    <div>
      <span>${selected ? "Aktuelle Einstellung" : escapeHtml(period?.label || "Zeitraum")}</span>
      <strong class="${quotaStatus.textClass}">${numberDe(period?.office_percent || 0)} %</strong>
      <small>${escapeHtml(selected ? period?.label || "" : range)}</small>
    </div>
    <div class="office-quota-bar" style="--office-share: ${officeShare}%; --quota-warning-start: ${warningStart}%; --quota-target: ${targetShare}%;" aria-hidden="true"><span></span></div>
    <dl>
      <div><dt>Büro</dt><dd>${numberDe(office)} Tage</dd></div>
      <div><dt>Homeoffice</dt><dd>${numberDe(home)} Tage</dd></div>
      <div><dt>Manuell</dt><dd>${officeQuotaManualLabel(period)}</dd></div>
      <div><dt>Status</dt><dd class="${period?.manual_baseline_status === "excluded" ? "negative" : ""}">${officeQuotaManualStatusLabel(period)}</dd></div>
    </dl>
  </article>`;
}

function officeQuotaStatus(period) {
  const percent = Number(period?.office_percent || 0);
  const target = Number(period?.target_percent || 0);
  if (percent >= target) return { cardClass: "quota-ok", textClass: "positive" };
  if (percent >= Math.max(0, target - 5)) return { cardClass: "quota-warning", textClass: "warning" };
  return { cardClass: "quota-danger", textClass: "negative" };
}

function officeQuotaManualLabel(period) {
  if (period?.manual_baseline_status === "empty") return "Keine";
  if (period?.includes_manual_baseline) return `${numberDe((period?.manual_office_days || 0) + (period?.manual_homeoffice_days || 0))} Tage`;
  return `${numberDe(period?.manual_baseline_excluded_days || 0)} nicht gezählt`;
}

function officeQuotaManualStatusLabel(period) {
  if (period?.manual_baseline_status === "empty") return "Keine";
  return period?.includes_manual_baseline ? "Gezählt" : "Ohne";
}

function officeQuotaMixedModeLabel(mode) {
  if (mode === "office") return "Immer Bürotag";
  if (mode === "homeoffice") return "Immer Homeoffice-Tag";
  return "Anteilig Büro und Homeoffice";
}

function dashboardMetric(metricConfig) {
  const active = state.dashboardDetailKey === metricConfig.key;
  return `<article class="${dashboardCardClass(metricConfig, active)}" data-dashboard-card="${escapeHtml(metricConfig.key)}">
    <button class="metric-trigger" type="button" data-metric="${escapeHtml(metricConfig.key)}" aria-expanded="${active ? "true" : "false"}">
      <span>${escapeHtml(metricConfig.label)}</span>
      <strong class="${dashboardValueClass(metricConfig)}" data-dashboard-value>${escapeHtml(metricConfig.value)}</strong>
      <em aria-hidden="true"></em>
    </button>
  </article>`;
}

function dashboardCardClass(metricConfig, active = false) {
  return `metric dashboard-card ${escapeHtml(metricConfig.extraClass || "")} ${active ? "active" : ""}`.trim();
}

function dashboardValueClass(metricConfig) {
  if (metricConfig.signedValue === undefined) return "";
  return metricConfig.signedValue >= 0 ? "positive" : "negative";
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

function detailText(value, klass = "") {
  return `<p class="${klass}">${escapeHtml(value)}</p>`;
}

function balanceBadge(value, status) {
  const klass = escapeHtml(status?.class || "balance-ok");
  const label = escapeHtml(status?.label || "0 bis 45 Stunden");
  return `<span class="balance-badge ${klass}" title="${label}">${escapeHtml(value)} · ${label}</span>`;
}

function htmlElement(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

function sameStringList(left, right) {
  if (left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ß/g, "ss");
}

async function api(name, ...args) {
  if (!window.pywebview?.api?.[name]) throw new Error(`API nicht verfügbar: ${name}`);
  return window.pywebview.api[name](...args);
}

function startCommandPolling() {
  if (commandPollTimer) return;
  checkAppCommand();
  commandPollTimer = setInterval(checkAppCommand, 250);
}

function startAutoRefresh(settings = null) {
  state.autoRefreshIntervalSeconds = autoRefreshSeconds(settings?.auto_refresh_interval_seconds);
  if (!refreshFocusHandlerAttached) {
    window.addEventListener("focus", () => {
      runAutoRefresh();
    });
    refreshFocusHandlerAttached = true;
  }
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (state.autoRefreshIntervalSeconds <= 0) return;
  autoRefreshTimer = setInterval(() => {
    runAutoRefresh();
  }, state.autoRefreshIntervalSeconds * 1000);
}

async function runAutoRefresh() {
  if (autoRefreshInFlight || !shouldAutoRefresh()) return;
  autoRefreshInFlight = true;
  try {
    await refreshCurrentView({ silent: true });
  } finally {
    autoRefreshInFlight = false;
  }
}

async function refreshCurrentView({ silent = false } = {}) {
  try {
    if (state.view === "dashboard") {
      await refreshDashboard({ silent });
      return;
    }
    if (state.view === "calendar") {
      await refreshCalendar({ silent });
      return;
    }
    if (state.view === "entries") {
      await refreshEntries({ silent });
      return;
    }
    if (state.view === "statistics") {
      await refreshStatistics({ silent });
      return;
    }
    if (state.view === "vacation") {
      await refreshVacation({ silent });
      return;
    }
    if (!silent) await render();
  } catch (error) {
    if (!silent) showError(error);
    else console.warn(error);
  }
}

function shouldAutoRefresh() {
  const active = document.activeElement;
  if (document.hidden) return false;
  if (state.calendarDetailDate || state.entryEditDate) return false;
  if (document.querySelector(".modal-backdrop")) return false;
  if (active?.closest?.("form")) return false;
  if (active?.matches?.("input, select, textarea, [contenteditable='true']")) return false;
  return ["dashboard", "calendar", "entries", "statistics", "vacation"].includes(state.view);
}

function autoRefreshSeconds(rawValue) {
  const value = Number.parseInt(rawValue ?? "60", 10);
  if (!Number.isFinite(value) || Number.isNaN(value)) return 60;
  if (value <= 0) return 0;
  return Math.min(3600, Math.max(10, value));
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

function entryRangeLabel(row) {
  const start = timeShort(row.start) || "--:--";
  const end = timeShort(row.end) || "läuft";
  return `${start}–${end}`;
}

function weekdayShort(dateText) {
  const date = new Date(`${dateText}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("de-DE", { weekday: "short" });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Datei konnte nicht gelesen werden."));
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      const chunkSize = 0x8000;
      let binary = "";
      for (let index = 0; index < bytes.length; index += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
      }
      resolve(btoa(binary));
    };
    reader.readAsArrayBuffer(file);
  });
}

function isoToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function initialView() {
  const allowed = new Set(["dashboard", "calendar", "entries", "statistics", "vacation", "calculator", "settings"]);
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
