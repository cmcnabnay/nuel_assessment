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
  forecast: [],
  unit: loadPref("unit", ["C", "F"]),
  series: loadPref("series", ["temperature", "precipitation", "windspeed", "humidity"]),
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
const identity = (v) => v;

// Per-tab display config. `value` converts an absolute reading, `delta` a difference.
const SERIES = {
  temperature: { label: "Temperature", unit: deg, value: toUnit, delta: deltaToUnit, chart: "line", color: "#2563eb" },
  precipitation: { label: "Precipitation", unit: () => " mm", value: identity, delta: identity, chart: "bar", color: "#0369a1" },
  windspeed: { label: "Wind speed", unit: () => " km/h", value: identity, delta: identity, chart: "line", color: "#7c3aed" },
  humidity: { label: "Humidity", unit: () => "%", value: identity, delta: identity, chart: "line", color: "#059669" },
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
  el("pulled-at").textContent = formatCityTime(payload.snapshot.pulled_at, { timeZoneName: "short" });
  el("metric-count").textContent = payload.metrics.pull_count;
  renderSeries();

  if (payload.status === "stale") {
    showBanner(`Live pull failed, showing last known good data. (${payload.error || ""})`, "stale");
  } else {
    hideBanner();
  }

  if (!isRerender) {
    el("itinerary-text").textContent = "";
    el("itinerary-status").textContent = "";
  }

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
  el("metric-change").innerHTML = formatChange(cfg, s.change_since_last_pull);
  el("metric-avg-label").textContent = `Rolling average (last ${m.rolling_window} pulls)`;
  el("metric-avg").textContent = fmt(cfg, s.rolling_average);
  el("metric-minmax").textContent = s.min_max ? `${fmt(cfg, s.min_max.min)} / ${fmt(cfg, s.min_max.max)}` : "n/a";

  if (state.series === "temperature" && m.alert.triggered) {
    el("metric-change").innerHTML += ` <span class="alert-flag">⚠ moved ≥ ${cfg.delta(m.alert.threshold_c)}${cfg.unit()}</span>`;
  }
}

async function loadHistory(city) {
  state.history = await api(`/api/history?city=${encodeURIComponent(city)}`);
  renderHistoryChart();
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

function renderHistoryChart() {
  el("chart-title").textContent = SERIES[state.series].label;
  el("history-tz").textContent = timeZoneNote();
  drawChart("history-chart", state.history.map((r) => ({ at: r.pulled_at, value: r[state.series] })));
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
  drawChart("forecast-chart", state.forecast.map((r) => ({ at: r.time, value: r[state.series] })), { dashed: true });
}

// Draws the selected series into `canvasId`. `points` is [{at: ISO time, value}].
function drawChart(canvasId, points, { dashed = false } = {}) {
  const cfg = SERIES[state.series];
  const ctx = el(canvasId).getContext("2d");
  const labels = points.map((p) => formatCityTime(p.at));
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
              backgroundColor: `${cfg.color}33`,
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
      scales: { y: { beginAtZero: state.series !== "temperature", title: { display: true, text: unit } } },
    },
  });
}

async function refreshCityList(activeCity) {
  const cities = await api("/api/cities");
  const list = el("city-list");
  list.innerHTML = "";
  cities.forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c.display_name;
    if (c.query_name === activeCity) li.classList.add("active");
    li.addEventListener("click", () => selectCity(c.query_name));
    list.appendChild(li);
  });
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
  document.querySelectorAll(".unit-toggle button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.unit === unit));
  });
  if (state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll(".unit-toggle button").forEach((b) => {
  b.addEventListener("click", () => setUnit(b.dataset.unit));
});
setUnit(state.unit);

function setSeries(series) {
  state.series = series;
  savePref("series", series);
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.series === series));
  });
  el("unit-toggle").classList.toggle("hidden", series !== "temperature");
  if (state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => setSeries(b.dataset.series));
});
setSeries(state.series);

el("itinerary-btn").addEventListener("click", async () => {
  if (!state.currentCity) return;
  const btn = el("itinerary-btn");
  btn.disabled = true;
  el("itinerary-status").textContent = "Asking the model...";
  el("itinerary-text").textContent = "";
  try {
    const payload = await api(`/api/itinerary?city=${encodeURIComponent(state.currentCity)}`);
    el("itinerary-status").textContent = "";
    el("itinerary-text").textContent = payload.itinerary;
  } catch (err) {
    el("itinerary-status").textContent = `Itinerary unavailable: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

// On load, show any previously tracked cities so a refresh doesn't lose context.
refreshCityList(null).then(async () => {
  const cities = await api("/api/cities");
  if (cities.length > 0) selectCity(cities[0].query_name);
});
