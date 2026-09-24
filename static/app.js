const WEATHER_CODES = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Depositing rime fog",
  51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
  61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
  71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
  80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
  95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
};

const state = { currentCity: null, chart: null, lastPayload: null, unit: loadUnit() };

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

function loadUnit() {
  try {
    return localStorage.getItem("unit") === "F" ? "F" : "C";
  } catch {
    return "C";
  }
}

// Backend stores Celsius; Fahrenheit is converted here for display only.
const round1 = (n) => Math.round(n * 10) / 10;
const toUnit = (c) => (state.unit === "F" ? round1(c * 9 / 5 + 32) : c);
// A temperature *difference* converts without the +32 offset.
const deltaToUnit = (dc) => (state.unit === "F" ? round1(dc * 9 / 5) : dc);
const deg = () => `°${state.unit}`;

function formatChange(change) {
  if (!change) return "n/a (need 2+ pulls)";
  const arrow = change.absolute > 0 ? "↑" : change.absolute < 0 ? "↓" : "→";
  const pct = change.percent === null ? "" : ` (${change.percent > 0 ? "+" : ""}${change.percent}%)`;
  return `${arrow} ${change.absolute > 0 ? "+" : ""}${deltaToUnit(change.absolute)}${deg()}${pct}`;
}

function renderLatest(payload) {
  const isRerender = payload === state.lastPayload;
  state.lastPayload = payload;
  state.currentCity = payload.city.query_name;
  el("dashboard").classList.remove("hidden");
  el("empty-state").classList.add("hidden");

  el("city-name").textContent = payload.city.display_name + (payload.city.country ? `, ${payload.city.country}` : "");
  el("temp").textContent = toUnit(payload.snapshot.temperature);
  el("temp-unit").textContent = deg();
  el("weather-desc").textContent = weatherDescription(payload.snapshot.weathercode);
  el("windspeed").textContent = payload.snapshot.windspeed;
  el("pulled-at").textContent = new Date(payload.snapshot.pulled_at).toLocaleString();

  const m = payload.metrics;
  el("metric-change").innerHTML = formatChange(m.change_since_last_pull);
  el("metric-avg-label").textContent = `Rolling average (last ${m.rolling_average.window} pulls)`;
  el("metric-avg").textContent = m.rolling_average.value !== null ? `${toUnit(m.rolling_average.value)}${deg()}` : "n/a";
  el("metric-minmax").textContent = m.min_max ? `${toUnit(m.min_max.min)}${deg()} / ${toUnit(m.min_max.max)}${deg()}` : "n/a";
  el("metric-count").textContent = m.pull_count;

  if (m.alert.triggered) {
    el("metric-change").innerHTML += ` <span class="alert-flag">⚠ moved ≥ ${deltaToUnit(m.alert.threshold_c)}${deg()}</span>`;
  }

  if (payload.status === "stale") {
    showBanner(`Live pull failed, showing last known good data. (${payload.error || ""})`, "stale");
  } else {
    hideBanner();
  }

  if (!isRerender) {
    el("itinerary-text").textContent = "";
    el("itinerary-status").textContent = "";
  }

  loadHistory(payload.city.query_name);
  refreshCityList(payload.city.query_name);
}

async function loadHistory(city) {
  const rows = await api(`/api/history?city=${encodeURIComponent(city)}`);
  const ctx = el("history-chart").getContext("2d");
  const labels = rows.map((r) => new Date(r.pulled_at).toLocaleString());
  const temps = rows.map((r) => toUnit(r.temperature));

  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: `Temperature (${deg()})`,
        data: temps,
        borderColor: "#2563eb",
        backgroundColor: "rgba(37,99,235,0.1)",
        tension: 0.25,
        pointRadius: 3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { title: { display: true, text: deg() } } },
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

async function pullCity(city) {
  try {
    const payload = await api(`/api/pull?city=${encodeURIComponent(city)}`, { method: "POST" });
    renderLatest(payload);
  } catch (err) {
    showBanner(err.message, "error");
  }
}

el("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const city = el("city-input").value.trim();
  if (!city) return;
  pullCity(city);
  el("city-input").value = "";
});

el("refresh-btn").addEventListener("click", () => {
  if (state.currentCity) pullCity(state.currentCity);
});

function setUnit(unit) {
  state.unit = unit;
  try {
    localStorage.setItem("unit", unit);
  } catch {}
  document.querySelectorAll(".unit-toggle button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.unit === unit));
  });
  if (state.lastPayload) renderLatest(state.lastPayload);
}

document.querySelectorAll(".unit-toggle button").forEach((b) => {
  b.addEventListener("click", () => setUnit(b.dataset.unit));
});
setUnit(state.unit);

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
