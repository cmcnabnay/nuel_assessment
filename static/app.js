const WEATHER_CODES = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Depositing rime fog",
  51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
  61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
  71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
  80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
  95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
};

const state = {
  currentCity: null,
  charts: {},
  lastPayload: null,
  history: [],
  // pulled_at of the pull each derived metric is measured from (see renderSeries).
  bases: { change: null, avg: null, minmax: null },
  forecast: [],
  unit: loadPref("unit", ["C", "F"]),
  windUnit: loadPref("windUnit", ["kmh", "mph"]),
  series: loadPref("series", ["temperature", "precipitation", "windspeed", "humidity"]),
  // Selected tab: one of the series above, or "itinerary".
  tab: null,
};

const el = (id) => document.getElementById(id);

async function api(path, options) {
  const resp = await fetch(path, options);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = body.detail || body.error || resp.statusText;
    throw new Error(detail);
  }
  return body;
}

function showBanner(message, kind) {
  const banner = el("banner");
  banner.textContent = message;
  banner.className = `banner ${kind}`;
}

function hideBanner() {
  el("banner").className = "banner hidden";
}

function weatherDescription(code) {
  return WEATHER_CODES[code] ?? `Weather code ${code}`;
}

// Chart.js styling for white-on-glass charts.
if (window.Chart) {
  Chart.defaults.color = "rgba(255, 255, 255, 0.78)";
  Chart.defaults.borderColor = "rgba(255, 255, 255, 0.12)";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
}

// --- Weather icons and sky ---

// The condition families the icons and sky backgrounds cover, from a WMO code.
function conditionGroup(code) {
  if (code === 0) return "clear";
  if (code === 1 || code === 2) return "partly";
  if (code === 3) return "cloudy";
  if (code === 45 || code === 48) return "fog";
  if (code >= 51 && code <= 57) return "drizzle";
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if (code >= 95) return "storm";
  return "cloudy";
}

// Inline SVG pieces on a 64x64 canvas; composed per condition in weatherIcon().
const ICON_PARTS = {
  sun: (cx, cy, r) => {
    const rays = [...Array(8)].map((_, i) => {
      const a = (i * Math.PI) / 4;
      const [x1, y1, x2, y2] = [r + 4, r + 4, r + 9, r + 9].map((d, j) =>
        (j % 2 ? cy + Math.sin(a) * d : cx + Math.cos(a) * d).toFixed(1));
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
    }).join("");
    return `<g stroke="#FFD23F" stroke-width="3.5" stroke-linecap="round">${rays}</g>` +
      `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#FFD23F"/>`;
  },
  moon: (cx, cy, s) =>
    `<path transform="translate(${cx - 32 * s} ${cy - 32 * s}) scale(${s})" fill="#F6F1C7" ` +
    `d="M38 12a21 21 0 1 0 15 33A17 17 0 0 1 38 12z"/>`,
  cloud: (dx, dy, s, fill) =>
    `<path transform="translate(${dx} ${dy}) scale(${s})" fill="${fill}" ` +
    `d="M19 48h27a11 11 0 0 0 1.5-21.9A15 15 0 0 0 19.4 25 11.5 11.5 0 0 0 19 48z"/>`,
  rain: (color = "#7CC4FF") =>
    `<g stroke="${color}" stroke-width="3.5" stroke-linecap="round">` +
    `<line x1="24" y1="49" x2="20.5" y2="57"/><line x1="33" y1="49" x2="29.5" y2="57"/>` +
    `<line x1="42" y1="49" x2="38.5" y2="57"/></g>`,
  drizzle: () =>
    `<g fill="#9DD3FF"><circle cx="23" cy="52" r="2"/><circle cx="32" cy="55" r="2"/>` +
    `<circle cx="41" cy="52" r="2"/><circle cx="27.5" cy="59" r="2"/><circle cx="36.5" cy="59" r="2"/></g>`,
  snow: () =>
    `<g fill="#FFFFFF"><circle cx="23" cy="52" r="2.6"/><circle cx="33" cy="55" r="2.6"/>` +
    `<circle cx="43" cy="52" r="2.6"/><circle cx="28" cy="60" r="2.6"/><circle cx="38" cy="60" r="2.6"/></g>`,
  bolt: () => `<path fill="#FFD23F" d="M34 42l-8 12h6l-3 9 11-14h-6l4-7z"/>`,
  fog: () =>
    `<g stroke="#FFFFFF" stroke-opacity="0.85" stroke-width="3.5" stroke-linecap="round">` +
    `<line x1="14" y1="50" x2="50" y2="50"/><line x1="20" y1="57" x2="44" y2="57"/></g>`,
};

// Returns an SVG string for a condition group, with a sun or moon where it shows.
function weatherIcon(group, isDay) {
  const P = ICON_PARTS;
  const orb = (cx, cy, r) => (isDay ? P.sun(cx, cy, r) : P.moon(cx, cy, r / 12));
  const precipCloud = (fill) => P.cloud(-1, -8, 1, fill);
  const body = {
    clear: orb(32, 32, 13),
    partly: orb(24, 22, 9) + P.cloud(4, 4, 0.92, "#FFFFFF"),
    cloudy: P.cloud(10, -6, 0.72, "#C3CFDD") + P.cloud(0, 2, 1, "#F3F6FA"),
    fog: P.cloud(0, -6, 1, "#E6ECF3") + P.fog(),
    drizzle: precipCloud("#EEF2F7") + P.drizzle(),
    rain: precipCloud("#DCE3EC") + P.rain(),
    snow: precipCloud("#F3F6FA") + P.snow(),
    storm: precipCloud("#9AA6B8") + P.bolt(),
  }[group] ?? P.cloud(0, 2, 1, "#F3F6FA");
  return `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${body}</svg>`;
}

// Day or night for a snapshot: Open-Meteo's is_day when stored, else the city's local
// hour (6 AM to 7 PM counts as day) for pulls made before is_day was recorded.
function isDaytime(snapshot) {
  if (snapshot.is_day === 0 || snapshot.is_day === 1) return snapshot.is_day === 1;
  const { hour } = cityDayAndHour(snapshot.pulled_at);
  return hour >= 6 && hour < 19;
}

function renderConditionVisuals(snapshot) {
  const group = conditionGroup(snapshot.weathercode);
  const day = isDaytime(snapshot);
  const sky = group === "drizzle" ? "rain" : group;
  document.body.className = document.body.className.replace(/\bsky-\S+/g, "").trim();
  document.body.classList.add(`sky-${sky}-${day ? "day" : "night"}`);
  el("condition-icon").innerHTML = weatherIcon(group, day);
  el("condition-icon").setAttribute("aria-label", weatherDescription(snapshot.weathercode));
}

// Most common condition over a day's daytime readings (8 AM to 8 PM local), so a chip's
// icon reflects the day rather than the night. Falls back to all readings.
function dominantGroup(readings) {
  const daytime = readings.filter((r) => r.hour >= 8 && r.hour <= 20);
  const counts = {};
  (daytime.length ? daytime : readings).forEach((r) => {
    if (r.code === null || r.code === undefined) return;
    const g = conditionGroup(r.code);
    counts[g] = (counts[g] || 0) + 1;
  });
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

// Returns the stored value if it's one of `allowed`, else the first allowed value.
function loadPref(key, allowed) {
  try {
    const v = localStorage.getItem(key);
    return allowed.includes(v) ? v : allowed[0];
  } catch {
    return allowed[0];
  }
}

function savePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}

// Backend stores Celsius; Fahrenheit is converted here for display only.
const round1 = (n) => Math.round(n * 10) / 10;
const toUnit = (c) => (state.unit === "F" ? round1(c * 9 / 5 + 32) : c);
// A temperature *difference* converts without the +32 offset.
const deltaToUnit = (dc) => (state.unit === "F" ? round1(dc * 9 / 5) : dc);
const deg = () => `°${state.unit}`;
// Wind is stored in km/h; mph is converted here for display only (a speed difference
// converts with the same factor, since there's no offset).
const toWindUnit = (kmh) => (state.windUnit === "mph" ? round1(kmh * 0.621371) : kmh);
const windUnitLabel = () => (state.windUnit === "mph" ? " mph" : " km/h");
const identity = (v) => v;

// Per-tab display config. `value` converts an absolute reading, `delta` a difference.
const SERIES = {
  temperature: { label: "Temperature", unit: deg, value: toUnit, delta: deltaToUnit, chart: "line", color: "#ffd479" },
  precipitation: { label: "Precipitation", unit: () => " mm", value: identity, delta: identity, chart: "bar", color: "#7cc4ff" },
  windspeed: { label: "Wind speed", unit: windUnitLabel, value: toWindUnit, delta: toWindUnit, chart: "line", color: "#d3c4ff" },
  humidity: { label: "Humidity", unit: () => "%", value: identity, delta: identity, chart: "line", color: "#86e8c0" },
};

// Times are stored in UTC; show them in the selected city's own timezone so a
// forecast for Tokyo reads in Tokyo time regardless of where the viewer is.
// "auto" (geocoder gave no zone) falls back to the browser's timezone.
function cityTimeZone() {
  const tz = state.lastPayload?.city.timezone;
  return tz && tz !== "auto" ? tz : undefined;
}

function formatCityTime(iso, options = {}) {
  try {
    return new Date(iso).toLocaleString(undefined, { timeZone: cityTimeZone(), ...options });
  } catch {
    return new Date(iso).toLocaleString(undefined, options); // unknown zone name
  }
}

function timeZoneNote() {
  const tz = cityTimeZone();
  if (!tz) return "(your local time)";
  const abbr = formatCityTime(new Date().toISOString(), { timeZoneName: "short" }).split(" ").pop();
  return `(${tz.replace(/_/g, " ")} time, ${abbr})`;
}

function fmt(cfg, v) {
  return v === null || v === undefined ? "n/a" : `${cfg.value(v)}${cfg.unit()}`;
}

function formatChange(cfg, change) {
  if (!change) return "n/a (need 2+ pulls)";
  const arrow = change.absolute > 0 ? "↑" : change.absolute < 0 ? "↓" : "→";
  const pct = change.percent === null ? "" : ` (${change.percent > 0 ? "+" : ""}${change.percent}%)`;
  return `${arrow} ${change.absolute > 0 ? "+" : ""}${cfg.delta(change.absolute)}${cfg.unit()}${pct}`;
}

function renderLatest(payload) {
  const isRerender = payload === state.lastPayload;
  state.lastPayload = payload;
  state.currentCity = payload.city.query_name;
  el("dashboard").classList.remove("hidden");
  el("tabs").classList.remove("hidden");
  el("empty-state").classList.add("hidden");

  el("city-name").textContent = payload.city.display_name + (payload.city.country ? `, ${payload.city.country}` : "");
  el("weather-desc").textContent = weatherDescription(payload.snapshot.weathercode);
  renderConditionVisuals(payload.snapshot);
  el("pulled-at").textContent = formatCityTime(payload.snapshot.pulled_at, { timeZoneName: "short" });
  el("metric-count").textContent = payload.metrics.pull_count;
  renderSeries();

  if (payload.status === "stale") {
    showBanner(`Live pull failed, showing last known good data. (${payload.error || ""})`, "stale");
  } else {
    hideBanner();
  }

  if (!isRerender) {
    el("itinerary-city").textContent = payload.city.display_name;
    el("itinerary-content").innerHTML = "";
    el("itinerary-status").textContent = "";
    showSaveButton(null);
    loadSavedItineraries(payload.city.query_name);
  }
  updateLocalTime();

  if (!isRerender) {
    loadHistory(payload.city.query_name);
    loadForecast(payload.city.query_name);
    refreshCityList(payload.city.query_name);
  } else {
    renderCharts();
  }
}

// Renders the selected tab's current value and derived metrics.
function renderSeries() {
  const payload = state.lastPayload;
  if (!payload) return;
  const cfg = SERIES[state.series];
  const m = payload.metrics;
  const s = m.series[state.series];

  el("series-name").textContent = cfg.label.toLowerCase();
  el("series-current").textContent = fmt(cfg, payload.snapshot[state.series]);

  const h = state.history;
  if (h.length === 0 || h[h.length - 1].pulled_at !== payload.snapshot.pulled_at) {
    // History for this pull hasn't loaded yet; fall back to the server's defaults.
    el("metric-change").innerHTML = formatChange(cfg, s.change_since_last_pull);
    el("metric-avg").textContent = fmt(cfg, s.rolling_average);
    el("metric-minmax").textContent = s.min_max ? `${fmt(cfg, s.min_max.min)} / ${fmt(cfg, s.min_max.max)}` : "n/a";
  } else {
    renderSelectedMetrics(cfg);
  }

  if (state.series === "temperature" && m.alert.triggered) {
    el("metric-change").innerHTML += ` <span class="alert-flag">⚠ moved ≥ ${cfg.delta(m.alert.threshold_c)}${cfg.unit()}</span>`;
  }
}

async function loadHistory(city) {
  state.history = await api(`/api/history?city=${encodeURIComponent(city)}`);
  populateBaseSelects();
  renderSeries();
  renderHistoryChart();
}

// --- User-selected baselines for the derived metrics ---

const round2 = (n) => Math.round(n * 100) / 100;

// Resets each metric's "since" pull if it no longer applies, then relabels the buttons.
// Keeps the user's choice if that pull still exists, else picks the default:
// previous pull (change), the server's ROLLING_WINDOW pulls back (average), oldest (min/max).
function populateBaseSelects() {
  const h = state.history;
  const n = h.length;
  const defaults = {
    change: h[n - 2]?.pulled_at,
    avg: h[Math.max(0, n - (state.lastPayload?.metrics.rolling_window ?? 5))]?.pulled_at,
    minmax: h[0]?.pulled_at,
  };
  const known = new Set(h.map((r) => r.pulled_at));

  document.querySelectorAll(".base-picker").forEach((btn) => {
    const metric = btn.dataset.metric;
    if (!known.has(state.bases[metric]) || state.bases[metric] === h[n - 1]?.pulled_at) {
      state.bases[metric] = defaults[metric] ?? null;
    }
    btn.disabled = n < 2;
  });
  closePicker();
  updatePickerLabels();
}

function updatePickerLabels() {
  document.querySelectorAll(".base-picker").forEach((btn) => {
    const at = state.bases[btn.dataset.metric];
    btn.textContent = at ? formatCityTime(at) : "n/a (need 2+ pulls)";
  });
}

// --- Pop-up calendar for choosing a pull: pick a day, then one of that day's times ---

const picker = { metric: null, anchor: null, year: 0, month: 0, day: null };

// Every pull except the latest, grouped by local day: {"YYYY-MM-DD": [pulled_at, ...]}.
function pullsByDay() {
  const byDay = {};
  state.history.slice(0, -1).forEach((r) => {
    (byDay[cityDayAndHour(r.pulled_at).day] ??= []).push(r.pulled_at);
  });
  return byDay;
}

const pad2 = (n) => String(n).padStart(2, "0");
const dayKey = (y, m, d) => `${y}-${pad2(m)}-${pad2(d)}`;

function openPicker(btn) {
  const selected = state.bases[btn.dataset.metric];
  const [y, m] = cityDayAndHour(selected).day.split("-").map(Number);
  Object.assign(picker, { metric: btn.dataset.metric, anchor: btn, year: y, month: m, day: null });
  document.querySelectorAll(".base-picker").forEach((b) => b.setAttribute("aria-expanded", String(b === btn)));
  renderPicker();
  el("pull-picker").classList.remove("hidden");
  positionPicker();
}

function closePicker() {
  picker.metric = null;
  el("pull-picker").classList.add("hidden");
  document.querySelectorAll(".base-picker").forEach((b) => b.setAttribute("aria-expanded", "false"));
}

// Places the pop-up under its button, kept inside the viewport on narrow screens.
function positionPicker() {
  const pop = el("pull-picker");
  const r = picker.anchor.getBoundingClientRect();
  const width = pop.offsetWidth;
  const left = Math.min(Math.max(8, r.left), document.documentElement.clientWidth - width - 8);
  pop.style.left = `${left + window.scrollX}px`;
  pop.style.top = `${r.bottom + window.scrollY + 4}px`;
}

function renderPicker() {
  const pop = el("pull-picker");
  const byDay = pullsByDay();
  const selected = state.bases[picker.metric];
  pop.innerHTML = picker.day ? timesView(byDay[picker.day] ?? [], selected) : calendarView(byDay, selected);
}

function calendarView(byDay, selected) {
  const { year: y, month: m } = picker;
  const days = Object.keys(byDay).sort();
  const monthKey = `${y}-${pad2(m)}`;
  const canPrev = days.length && days[0].slice(0, 7) < monthKey;
  const canNext = days.length && days[days.length - 1].slice(0, 7) > monthKey;
  const title = new Date(Date.UTC(y, m - 1, 1)).toLocaleString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const selectedDay = selected ? cityDayAndHour(selected).day : null;
  const today = cityDayAndHour(new Date().toISOString()).day;

  const cells = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((d) => `<div class="pp-dow">${d}</div>`);
  const firstWeekday = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  for (let i = 0; i < firstWeekday; i++) cells.push("<div></div>");
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let d = 1; d <= daysInMonth; d++) {
    const key = dayKey(y, m, d);
    const count = byDay[key]?.length ?? 0;
    const cls = ["pp-day", count && "has-pulls", key === selectedDay && "selected", key === today && "today"]
      .filter(Boolean).join(" ");
    const title = count ? `${count} pull${count === 1 ? "" : "s"}` : "No pulls";
    cells.push(`<button type="button" class="${cls}" data-day="${key}" title="${title}" ${count ? "" : "disabled"}>${d}</button>`);
  }

  return `
    <div class="pp-head">
      <button type="button" class="pp-nav" data-nav="-1" aria-label="Previous month" ${canPrev ? "" : "disabled"}>‹</button>
      <span class="pp-title">${title}</span>
      <button type="button" class="pp-nav" data-nav="1" aria-label="Next month" ${canNext ? "" : "disabled"}>›</button>
    </div>
    <div class="pp-grid">${cells.join("")}</div>
    <div class="pp-hint">Pick a day with pulls, then a time. Times are ${timeZoneNote().slice(1, -1)}.</div>`;
}

function timesView(times, selected) {
  const [y, m, d] = picker.day.split("-").map(Number);
  const title = new Date(Date.UTC(y, m - 1, d)).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", timeZone: "UTC",
  });
  const buttons = times
    .map((at) => {
      const label = formatCityTime(at, { hour: "numeric", minute: "2-digit", second: "2-digit" });
      return `<button type="button" class="pp-time${at === selected ? " selected" : ""}" data-at="${at}">${label}</button>`;
    })
    .join("");
  return `
    <div class="pp-head">
      <button type="button" class="pp-nav" data-back aria-label="Back to calendar">‹</button>
      <span class="pp-title">${title}</span>
      <span></span>
    </div>
    <div class="pp-times">${buttons}</div>`;
}

el("pull-picker").addEventListener("click", (e) => {
  const target = e.target.closest("button");
  if (!target || target.disabled) return;
  if (target.dataset.nav) {
    const next = new Date(Date.UTC(picker.year, picker.month - 1 + Number(target.dataset.nav), 1));
    picker.year = next.getUTCFullYear();
    picker.month = next.getUTCMonth() + 1;
  } else if (target.dataset.day) {
    picker.day = target.dataset.day;
  } else if ("back" in target.dataset) {
    picker.day = null;
  } else if (target.dataset.at) {
    state.bases[picker.metric] = target.dataset.at;
    closePicker();
    updatePickerLabels();
    renderSeries();
    return;
  }
  renderPicker();
});

document.querySelectorAll(".base-picker").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (picker.metric === btn.dataset.metric) closePicker();
    else openPicker(btn);
  });
});

// Close on a click outside the pop-up (and its buttons) or on Escape.
document.addEventListener("mousedown", (e) => {
  if (picker.metric && !e.target.closest("#pull-picker, .base-picker")) closePicker();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && picker.metric) closePicker();
});
window.addEventListener("resize", () => picker.metric && positionPicker());

// Values of the current series from the pull at `since` through the latest, in °C
// (unit conversion happens at display time), skipping readings that are missing.
function seriesValuesSince(since) {
  const start = state.history.findIndex((r) => r.pulled_at === since);
  if (start < 0) return [];
  return state.history.slice(start).map((r) => r[state.series]).filter((v) => v !== null && v !== undefined);
}

function renderSelectedMetrics(cfg) {
  const latest = seriesValuesSince(state.history[state.history.length - 1].pulled_at)[0];

  // Change: latest vs the selected pull. Percent is null against a 0 baseline, same as
  // the server (it's meaningless there, e.g. going from 0°C to -2°C).
  const base = state.history.find((r) => r.pulled_at === state.bases.change)?.[state.series];
  let change = null;
  if (base !== null && base !== undefined && latest !== undefined) {
    change = {
      absolute: round2(latest - base),
      percent: base === 0 ? null : round2(((latest - base) / Math.abs(base)) * 100),
    };
  }
  el("metric-change").innerHTML = formatChange(cfg, change);

  const avgValues = seriesValuesSince(state.bases.avg);
  const avg = avgValues.length ? round2(avgValues.reduce((a, b) => a + b, 0) / avgValues.length) : null;
  el("metric-avg").textContent = avg === null ? "n/a" : `${fmt(cfg, avg)} (${avgValues.length} pulls)`;

  const mmValues = seriesValuesSince(state.bases.minmax);
  el("metric-minmax").textContent = mmValues.length
    ? `${fmt(cfg, round2(Math.min(...mmValues)))} / ${fmt(cfg, round2(Math.max(...mmValues)))}`
    : "n/a";
}


async function loadForecast(city) {
  try {
    state.forecast = await api(`/api/forecast?city=${encodeURIComponent(city)}`);
  } catch {
    state.forecast = [];
  }
  renderForecastChart();
}

function renderCharts() {
  renderHistoryChart();
  renderForecastChart();
}

// The history chart shows a rolling window: local midnight 7 days ago through now, so
// on 9/25 it starts at 9/18 12:00 AM (city time) and shows 8 days including today.
const HISTORY_DAYS_BACK = 7;

function historyStartDay() {
  const [y, m, d] = cityDayAndHour(new Date().toISOString()).day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - HISTORY_DAYS_BACK)).toISOString().slice(0, 10);
}

function renderHistoryChart() {
  el("chart-title").textContent = SERIES[state.series].label;
  el("history-tz").textContent = timeZoneNote();
  const startDay = historyStartDay(); // "YYYY-MM-DD", compares correctly as a string
  const points = state.history
    .filter((r) => cityDayAndHour(r.pulled_at).day >= startDay)
    .map((r) => ({ at: r.pulled_at, value: r[state.series], code: r.weathercode }));
  drawChart("history-chart", points);
  renderDailyHighLow("history-hl", points);
}

// --- Daily high / low (temperature tab) ---

// Local calendar day ("YYYY-MM-DD") and hour (0-23) of `iso` in the city's timezone.
// formatToParts keeps this independent of the viewer's locale date format.
function cityDayAndHour(iso) {
  const opts = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" };
  let parts;
  try {
    parts = new Intl.DateTimeFormat("en-US", { ...opts, timeZone: cityTimeZone() }).formatToParts(new Date(iso));
  } catch {
    parts = new Intl.DateTimeFormat("en-US", opts).formatToParts(new Date(iso)); // unknown zone name
  }
  const get = (type) => parts.find((p) => p.type === type).value;
  return { day: `${get("year")}-${get("month")}-${get("day")}`, hour: Number(get("hour")) };
}

// Groups points by local day; each day gets the index of its highest and lowest reading.
// A day is "partial" when the data doesn't cover it from ~midnight to ~11 PM, e.g. the
// forecast's first day starts at the pull time, so its high/low may not be the real one.
function dailyHighLow(points) {
  const days = [];
  let current = null;
  points.forEach((p, i) => {
    if (p.value === null || p.value === undefined) return;
    const { day: key, hour } = cityDayAndHour(p.at);
    if (!current || current.key !== key) {
      current = { key, at: p.at, first: p.at, last: p.at, high: i, low: i, readings: [] };
      days.push(current);
    }
    current.last = p.at;
    current.readings.push({ hour, code: p.code });
    if (p.value > points[current.high].value) current.high = i;
    if (p.value < points[current.low].value) current.low = i;
  });
  const hour = (iso) => cityDayAndHour(iso).hour;
  days.forEach((d) => { d.partial = hour(d.first) > 1 || hour(d.last) < 22; });
  return days;
}

function renderDailyHighLow(containerId, points) {
  const box = el(containerId);
  box.innerHTML = "";
  const show = state.series === "temperature" && points.length > 0;
  box.classList.toggle("hidden", !show);
  if (!show) return;

  const cfg = SERIES.temperature;
  dailyHighLow(points).forEach((d) => {
    const chip = document.createElement("div");
    chip.className = `day${d.partial ? " partial" : ""}`;
    if (d.partial) chip.title = "Partial day: only some hours are covered, so the true high/low may differ.";
    const name = formatCityTime(d.at, { weekday: "short", month: "numeric", day: "numeric" });
    const group = dominantGroup(d.readings);
    chip.innerHTML =
      `<span class="day-name">${name}${d.partial ? " (partial)" : ""}</span>` +
      (group ? `<span class="day-icon" title="${group}">${weatherIcon(group, true)}</span>` : "") +
      `<span class="high">H ${fmt(cfg, points[d.high].value)}</span>` +
      `<span class="low">L ${fmt(cfg, points[d.low].value)}</span>`;
    box.appendChild(chip);
  });
}

function renderForecastChart() {
  const empty = state.forecast.length === 0;
  el("forecast-title").textContent = SERIES[state.series].label;
  el("forecast-tz").textContent = timeZoneNote();
  el("forecast-chart").classList.toggle("hidden", empty);
  el("forecast-empty").classList.toggle("hidden", !empty);
  if (empty) {
    state.charts["forecast-chart"]?.destroy();
    delete state.charts["forecast-chart"];
    return;
  }
  const points = state.forecast.map((r) => ({ at: r.time, value: r[state.series], code: r.weathercode }));
  drawChart("forecast-chart", points, { dashed: true });
  renderDailyHighLow("forecast-hl", points);
}

// Draws the selected series into `canvasId`. `points` is [{at: ISO time, value}].
function drawChart(canvasId, points, { dashed = false } = {}) {
  const cfg = SERIES[state.series];
  const ctx = el(canvasId).getContext("2d");
  const labels = points.map((p) => formatCityTime(p.at)); // full time, shown in tooltips
  // Short axis ticks ("Fri 6 AM") so the axis stays readable with hundreds of points.
  const ticks = points.map((p) => formatCityTime(p.at, { weekday: "short", hour: "numeric" }));
  // null (pre-migration rows) leaves a gap in the chart rather than plotting 0.
  const values = points.map((p) => (p.value === null ? null : cfg.value(p.value)));
  const unit = cfg.unit().trim();

  state.charts[canvasId]?.destroy();
  state.charts[canvasId] = new Chart(ctx, {
    type: cfg.chart,
    data: {
      labels,
      datasets: [{
        label: `${cfg.label} (${unit})`,
        data: values,
        borderColor: cfg.color,
        ...(cfg.chart === "bar"
          ? // Hundreds of hourly bars would each be ~2px wide; keep them solid and at least
            // 6px so an isolated rainy hour stays visible (neighbors are usually 0 mm).
            {
              backgroundColor: cfg.color,
              barThickness: Math.max(6, Math.floor(ctx.canvas.parentElement.clientWidth / values.length)),
            }
          : {
              backgroundColor: `${cfg.color}2e`,
              pointBackgroundColor: cfg.color,
              pointBorderColor: cfg.color,
              tension: 0.25,
              pointRadius: dashed ? 2 : 3,
              fill: true,
              borderDash: dashed ? [6, 4] : [],
            }),
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8, callback: (_, i) => ticks[i] },
        },
        y: { beginAtZero: state.series !== "temperature", title: { display: true, text: unit } },
      },
    },
  });
}

async function refreshCityList(activeCity) {
  const cities = await api("/api/cities");
  const list = el("city-list");
  list.innerHTML = "";
  cities.forEach((c) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = c.display_name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-city";
    remove.textContent = "×";
    remove.title = `Remove ${c.display_name}`;
    remove.setAttribute("aria-label", `Remove ${c.display_name}`);
    remove.addEventListener("click", (e) => {
      e.stopPropagation();
      removeCity(c);
    });
    li.append(name, remove);
    if (c.query_name === activeCity) li.classList.add("active");
    li.addEventListener("click", () => selectCity(c.query_name));
    list.appendChild(li);
  });
}

async function removeCity(c) {
  if (!confirm(`Remove ${c.display_name}? Its stored pulls and saved itineraries will be deleted.`)) return;
  try {
    await api(`/api/cities?city=${encodeURIComponent(c.query_name)}`, { method: "DELETE" });
  } catch (err) {
    showBanner(`Couldn't remove ${c.display_name}: ${err.message}`, "error");
    return;
  }
  if (c.query_name !== state.currentCity) {
    refreshCityList(state.currentCity);
    return;
  }
  // Removed the city on screen: switch to the next tracked one, or back to the empty state.
  const cities = await api("/api/cities");
  if (cities.length > 0) {
    selectCity(cities[0].query_name);
  } else {
    state.currentCity = null;
    state.lastPayload = null;
    el("dashboard").classList.add("hidden");
    el("tabs").classList.add("hidden");
    el("empty-state").classList.remove("hidden");
    hideBanner();
    refreshCityList(null);
  }
}

async function selectCity(city) {
  try {
    const payload = await api(`/api/latest?city=${encodeURIComponent(city)}`);
    renderLatest(payload);
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function pullCity(city, locationId) {
  try {
    const idParam = locationId ? `&location_id=${locationId}` : "";
    const payload = await api(`/api/pull?city=${encodeURIComponent(city)}${idParam}`, { method: "POST" });
    renderLatest(payload);
  } catch (err) {
    showBanner(err.message, "error");
  }
}

// --- Search-as-you-type suggestions ---

const suggest = { results: [], active: -1, timer: null, seq: 0 };

function suggestionLabel(r) {
  return [r.display_name, r.region, r.country].filter(Boolean).join(", ");
}

function hideSuggestions() {
  suggest.results = [];
  suggest.active = -1;
  el("suggestions").classList.add("hidden");
  el("city-input").setAttribute("aria-expanded", "false");
}

function renderSuggestions(query) {
  const list = el("suggestions");
  list.innerHTML = "";
  if (suggest.results.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = `No matches for "${query}"`;
    list.appendChild(li);
  }
  suggest.results.forEach((r, i) => {
    const li = document.createElement("li");
    li.id = `suggestion-${i}`;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(i === suggest.active));
    li.textContent = r.display_name;
    const region = [r.region, r.country].filter(Boolean).join(", ");
    if (region) {
      const span = document.createElement("span");
      span.className = "region";
      span.textContent = ` ${region}`;
      li.appendChild(span);
    }
    // mousedown (not click) fires before the input's blur hides the list.
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      chooseSuggestion(i);
    });
    list.appendChild(li);
  });
  list.classList.remove("hidden");
  el("city-input").setAttribute("aria-expanded", "true");
  el("city-input").setAttribute("aria-activedescendant", suggest.active >= 0 ? `suggestion-${suggest.active}` : "");
}

async function fetchSuggestions(query) {
  const seq = ++suggest.seq;
  try {
    const results = await api(`/api/search?q=${encodeURIComponent(query)}`);
    if (seq !== suggest.seq) return; // a newer keystroke already fired
    suggest.results = results;
    suggest.active = -1;
    renderSuggestions(query);
  } catch {
    if (seq === suggest.seq) hideSuggestions();
  }
}

function chooseSuggestion(i) {
  const r = suggest.results[i];
  if (!r) return;
  hideSuggestions();
  el("city-input").value = "";
  pullCity(suggestionLabel(r), r.id);
}

el("city-input").addEventListener("input", (e) => {
  const query = e.target.value.trim();
  clearTimeout(suggest.timer);
  if (query.length < 2) {
    suggest.seq++; // drop any in-flight response
    hideSuggestions();
    return;
  }
  suggest.timer = setTimeout(() => fetchSuggestions(query), 250);
});

el("city-input").addEventListener("keydown", (e) => {
  const open = !el("suggestions").classList.contains("hidden") && suggest.results.length > 0;
  if (!open) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = suggest.results.length;
    suggest.active = (suggest.active + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
    renderSuggestions(el("city-input").value.trim());
  } else if (e.key === "Enter" && suggest.active >= 0) {
    e.preventDefault();
    chooseSuggestion(suggest.active);
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
});

el("city-input").addEventListener("blur", hideSuggestions);

el("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const city = el("city-input").value.trim();
  if (!city) return;
  clearTimeout(suggest.timer);
  suggest.seq++;
  hideSuggestions();
  pullCity(city);
  el("city-input").value = "";
});

el("refresh-btn").addEventListener("click", () => {
  if (state.currentCity) pullCity(state.currentCity);
});

function setUnit(unit) {
  state.unit = unit;
  savePref("unit", unit);
  document.querySelectorAll("#unit-toggle button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.unit === unit));
  });
  if (state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll("#unit-toggle button").forEach((b) => {
  b.addEventListener("click", () => setUnit(b.dataset.unit));
});
setUnit(state.unit);

function setWindUnit(unit) {
  state.windUnit = unit;
  savePref("windUnit", unit);
  document.querySelectorAll("#wind-toggle button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.wind === unit));
  });
  if (state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll("#wind-toggle button").forEach((b) => {
  b.addEventListener("click", () => setWindUnit(b.dataset.wind));
});
setWindUnit(state.windUnit);

function setTab(tab) {
  const isItinerary = tab === "itinerary";
  state.tab = tab;
  savePref("tab", tab);
  if (!isItinerary) {
    state.series = tab;
    savePref("series", tab);
  }
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  });
  el("metric-panel").classList.toggle("hidden", isItinerary);
  el("itinerary-panel").classList.toggle("hidden", !isItinerary);
  el("unit-toggle").classList.toggle("hidden", tab !== "temperature");
  el("wind-toggle").classList.toggle("hidden", tab !== "windspeed");
  closePicker();
  // Charts drawn while their panel was hidden have no size; redraw on the way back.
  if (!isItinerary && state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => setTab(b.dataset.tab));
});
setTab(loadPref("tab", ["temperature", "precipitation", "windspeed", "humidity", "itinerary"]) === "itinerary"
  ? "itinerary"
  : state.series);

// --- Itinerary tab ---

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function renderItineraryDays(days) {
  return days
    .map((day) => {
      let dateLabel = "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(day.date)) {
        const [y, m, d] = day.date.split("-").map(Number);
        dateLabel = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
          weekday: "long", month: "short", day: "numeric", timeZone: "UTC",
        });
      }
      const events = day.events
        .map((e) => `
          <li>
            <span class="it-time">${escapeHtml(e.time)}</span>
            <div>
              <div class="it-title">${escapeHtml(e.title || e.place)}${
                e.category ? `<span class="it-tag">${escapeHtml(e.category)}</span>` : ""}</div>
              ${e.place && e.title ? `<div class="it-place">${escapeHtml(e.place)}</div>` : ""}
              ${e.details ? `<div class="it-details">${escapeHtml(e.details)}</div>` : ""}
            </div>
          </li>`)
        .join("");
      return `
        <section class="it-day">
          <h3>${dateLabel ? `<span class="it-date">${escapeHtml(dateLabel)}</span>` : ""}${escapeHtml(day.title)}</h3>
          ${day.weather_note ? `<p class="it-weather">${escapeHtml(day.weather_note)}</p>` : ""}
          <ol>${events}</ol>
        </section>`;
    })
    .join("");
}

// Fallback for a reply that wasn't structured JSON: render the common markdown bits
// (headings, bullet lists, bold/italic) instead of showing raw ** and # characters.
function renderMarkdown(text) {
  const inline = (s) =>
    escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|\W)\*(?!\s)(.+?)\*(?=\W|$)/g, "$1<em>$2</em>");
  const out = [];
  let list = false;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)/);
    if (!bullet && list) { out.push("</ul>"); list = false; }
    if (!line) continue;
    const heading = line.match(/^(#{1,6})\s+(.*)/);
    if (heading) out.push(`<h${heading[1].length <= 2 ? 3 : 4}>${inline(heading[2])}</h${heading[1].length <= 2 ? 3 : 4}>`);
    else if (bullet) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(bullet[1])}</li>`); }
    else out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

// Shows an itinerary ({days} or {text}) in the panel. `note` is an optional line above it.
function showItinerary(itinerary, note = "") {
  el("itinerary-content").innerHTML =
    (note ? `<p class="it-disclaimer">${note}</p>` : "") +
    (itinerary.days ? renderItineraryDays(itinerary.days) : renderMarkdown(itinerary.text || "")) +
    `<p class="it-disclaimer">AI-generated suggestions. Check opening hours, event schedules and reservations before you go.</p>`;
}

// The Save button applies to a freshly generated itinerary; hidden otherwise (e.g. while
// viewing one that's already saved).
function showSaveButton(itinerary) {
  state.unsavedItinerary = itinerary;
  const btn = el("itinerary-save-btn");
  btn.classList.toggle("hidden", !itinerary);
  btn.disabled = false;
  btn.textContent = "Save itinerary";
}

el("itinerary-btn").addEventListener("click", async () => {
  if (!state.currentCity) return;
  const city = state.currentCity;
  const btn = el("itinerary-btn");
  btn.disabled = true;
  el("itinerary-status").textContent = "Asking the model...";
  el("itinerary-content").innerHTML = "";
  showSaveButton(null);
  markViewing(null);
  try {
    const payload = await api(`/api/itinerary?city=${encodeURIComponent(city)}`);
    if (city !== state.currentCity) return; // user switched cities while waiting
    el("itinerary-status").textContent = "";
    showItinerary(payload, payload.truncated
      ? "The model's reply was cut off, so this shows the stops that came through. Try again for the full plan."
      : "");
    showSaveButton({ days: payload.days, text: payload.text });
  } catch (err) {
    if (city === state.currentCity) el("itinerary-status").textContent = `Itinerary unavailable: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

// --- Saved itineraries ---

async function loadSavedItineraries(city) {
  let saved = [];
  try {
    saved = await api(`/api/itineraries?city=${encodeURIComponent(city)}`);
  } catch {
    // leave the list empty; the rest of the page still works
  }
  if (city !== state.currentCity) return;
  state.savedItineraries = saved;
  renderSavedList();
}

function savedSummary(s) {
  if (!s.days) return "Text itinerary";
  const titles = s.days.map((d) => d.title).filter(Boolean);
  const count = `${s.days.length} day${s.days.length === 1 ? "" : "s"}`;
  return titles.length ? `${count}: ${titles.join(" · ")}` : count;
}

function renderSavedList() {
  const list = el("saved-list");
  list.innerHTML = "";
  const saved = state.savedItineraries || [];
  el("saved-empty").classList.toggle("hidden", saved.length > 0);
  saved.forEach((s) => {
    const li = document.createElement("li");
    li.dataset.id = s.id;
    if (s.id === state.viewingSavedId) li.classList.add("viewing");
    li.innerHTML = `
      <div>
        <div class="saved-when">Saved ${escapeHtml(new Date(s.saved_at).toLocaleString())}</div>
        <div class="saved-summary">${escapeHtml(savedSummary(s))}</div>
      </div>
      <div class="saved-buttons">
        <button type="button" class="secondary" data-action="view">View</button>
        <button type="button" class="danger" data-action="delete">Delete</button>
      </div>`;
    list.appendChild(li);
  });
}

function markViewing(id) {
  state.viewingSavedId = id;
  document.querySelectorAll("#saved-list li").forEach((li) => li.classList.toggle("viewing", Number(li.dataset.id) === id));
}

el("itinerary-save-btn").addEventListener("click", async () => {
  const itinerary = state.unsavedItinerary;
  if (!itinerary || !state.currentCity) return;
  const city = state.currentCity;
  const btn = el("itinerary-save-btn");
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    const saved = await api(`/api/itineraries?city=${encodeURIComponent(city)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(itinerary),
    });
    if (city !== state.currentCity) return;
    state.unsavedItinerary = null;
    btn.textContent = "Saved ✓";
    state.savedItineraries = [saved, ...(state.savedItineraries || [])];
    state.viewingSavedId = saved.id;
    renderSavedList();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Save itinerary";
    el("itinerary-status").textContent = `Couldn't save: ${err.message}`;
  }
});

el("saved-list").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  const id = Number(button.closest("li").dataset.id);
  const saved = (state.savedItineraries || []).find((s) => s.id === id);
  if (!saved) return;

  if (button.dataset.action === "view") {
    el("itinerary-status").textContent = "";
    showItinerary(saved, `Saved ${escapeHtml(new Date(saved.saved_at).toLocaleString())}`);
    showSaveButton(null);
    markViewing(id);
    return;
  }

  if (!confirm("Delete this saved itinerary? This can't be undone.")) return;
  button.disabled = true;
  try {
    await api(`/api/itineraries/${id}`, { method: "DELETE" });
    state.savedItineraries = state.savedItineraries.filter((s) => s.id !== id);
    if (state.viewingSavedId === id) {
      state.viewingSavedId = null;
      // Only clear the panel if it was showing the deleted one (not a fresh, unsaved plan).
      if (!state.unsavedItinerary) el("itinerary-content").innerHTML = "";
    }
    renderSavedList();
  } catch (err) {
    button.disabled = false;
    el("itinerary-status").textContent = `Couldn't delete: ${err.message}`;
  }
});

// --- City's current local time (conditions card) ---

function updateLocalTime() {
  if (!state.lastPayload) return;
  el("local-time").textContent = formatCityTime(new Date().toISOString(), {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short",
  });
}
setInterval(updateLocalTime, 1000);

// On load, show any previously tracked cities so a refresh doesn't lose context.
refreshCityList(null).then(async () => {
  const cities = await api("/api/cities");
  if (cities.length > 0) selectCity(cities[0].query_name);
});

el("brand-icon").innerHTML = weatherIcon("partly", true);
