# Weather Dashboard

A small pipeline + dashboard built around [Open-Meteo](https://open-meteo.com) (free,
no API key) that pulls weather for any city you type in, stores every pull as a
timestamped snapshot in SQLite, computes a few derived metrics from the accumulated
history, and — on top of the base assignment — asks an open-source LLM via
[OpenRouter](https://openrouter.ai) to suggest a short itinerary based on the
current forecast.

## Stack

Python 3.10, FastAPI, SQLite (stdlib `sqlite3`), vanilla HTML/JS + Chart.js for the
dashboard, pytest for tests. No frontend build step.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then optionally paste an OpenRouter key into .env
```

`OPENROUTER_API_KEY` is only needed for the itinerary feature — everything else
(pulling, storing, metrics, dashboard, charts) works with zero configuration.

## Running it

**Start the app:**

```bash
uvicorn app.main:app --reload
```

**View the dashboard:** open `http://localhost:8000`. Type a city name and hit
Search — this triggers a live pull, stores a snapshot, and renders the current
conditions, derived metrics, and the history chart. Hit "Refresh" to pull again
for the same city (simulating the next scheduled run) and watch metrics like
"change since last pull" and the rolling average update.

**Run the pipeline manually / simulate a scheduled pull:**

```bash
python scripts/pull_now.py                # re-pulls every city already tracked
python scripts/pull_now.py London Tokyo    # re-pulls (or adds) specific cities
```

Run this a few times over a few minutes (or wire it to real cron: `* * * * * cd
/path/to/repo && .venv/bin/python scripts/pull_now.py`) to watch real history
accumulate in the chart.

**Run the tests:**

```bash
pytest
```

29 tests, all offline (external calls are mocked) — covering the derived-metric
math, the pull pipeline's success/failure paths, the API endpoints end to end,
and the OpenRouter integration's error handling.

## How it's put together

- `app/weather_api.py` — talks to Open-Meteo (geocoding + current/forecast),
  wraps every network call so failures surface as typed exceptions instead of
  raw `requests` errors.
- `app/pull.py` — the pipeline step: resolves a city to coordinates (once, then
  cached in the `cities` table), fetches weather, and inserts one row into
  `snapshots`. This is the only place that writes to the DB.
- `app/metrics.py` — pure functions over a plain list of floats (no DB, no
  HTTP). Computes change-since-last-pull, rolling average, and min/max. Kept
  deliberately dumb and dependency-free so it's easy to unit test — this is
  exactly the code the assignment calls out as "easy to get subtly wrong."
- `app/main.py` — the API layer. `/api/pull` is the only endpoint that touches
  the external API; `/api/latest` and `/api/history` are pure DB reads, so
  repeated dashboard loads or page refreshes never re-hit Open-Meteo.
- `app/llm.py` — builds a prompt from the latest stored forecast and calls
  OpenRouter. Isolated from the metrics pipeline on purpose: itinerary
  generation is a presentation-layer add-on, not part of the data model.
- `static/` — a single-page dashboard (no build step) using Chart.js from a CDN.

### Data model

```
cities (id, query_name, display_name, country, latitude, longitude, timezone, created_at)
snapshots (id, city_id, pulled_at, temperature, windspeed, weathercode, forecast_json)
```

`cities` is geocoded once and reused; `snapshots` accumulates one row per pull,
so history actually builds up over time instead of being overwritten. `forecast_json`
stores the 3-day daily forecast fetched alongside the current reading, so the
itinerary endpoint can reuse it without a second external call.

## Decisions and tradeoffs (the "deliberately left open" parts)

**How a pull is triggered.** There's no real cron here — a pull happens either
when someone searches/refreshes a city on the dashboard (`POST /api/pull`), or
when `scripts/pull_now.py` is run manually. That script is written to be
idempotent and cron-friendly (it just re-pulls whatever's already tracked), so
with more time I'd wire it to actual cron or an APScheduler job inside the app
rather than relying on a human or a page click to trigger it.

**Derived metrics.** I track: change since the last pull (absolute + %),
a rolling average over the last N pulls (`ROLLING_WINDOW`, default 5), and
min/max over all stored history for that city. Two judgment calls worth
flagging:
- **Percent change is `null` when the previous value is 0** (or returns `None`
  generally when computing against a baseline of 0). This matters for
  Celsius specifically — "22% colder" is meaningless when going from 0°C to
  -2°C — so I return `null` rather than an infinite or nonsensical percentage.
- **The alert threshold uses absolute degrees, not percent** (`ALERT_THRESHOLD_C`,
  default 5). Percent swings are noisy near 0°C for temperature in a way they
  aren't for, say, an exchange rate, so "moved 5+ degrees since last pull" is a
  more meaningful flag than "moved 20%."

**"Data pull failed" UX.** `POST /api/pull` never lets an Open-Meteo outage
take down the app. If the live pull fails and a prior snapshot exists, it
returns the last known-good snapshot with `status: "stale"` plus an `error`
field, and the dashboard shows a yellow banner rather than breaking. If there's
no prior data at all, it returns a 502 with a clear message. The same
resilience applies to the itinerary feature: a missing key, a rate-limited
model, a network timeout, or a model that returns no content (see below) all
degrade to a clear "itinerary unavailable: ..." message instead of a crash.

**Multi-city tracking (stretch goal).** Implemented as a first-class part of
the data model rather than an add-on — every searched city gets its own row in
`cities` and its own accumulating history in `snapshots`. The sidebar lists
every previously-pulled city; clicking one reads its `/api/latest` +
`/api/history` without triggering a new external pull.

**Caching (stretch goal).** `/api/latest` and `/api/history` only ever read
from SQLite — the only endpoint that calls Open-Meteo is `/api/pull`. The
itinerary endpoint also avoids a second Open-Meteo call by reusing the daily
forecast JSON already stored alongside the latest snapshot.

**Alert/threshold (stretch goal).** `/api/latest`'s `metrics.alert.triggered`
flags when the absolute temperature change since the last pull is ≥
`ALERT_THRESHOLD_C` (default 5°C); the dashboard shows a warning next to the
change figure when that fires.

**Chart library.** Chart.js via CDN — function over form, and it's the
smallest amount of code to get an interactive line chart with tooltips.

**Itinerary feature (added on top of the base assignment).** This is the
piece I added beyond the spec: typing a city not only pulls/stores weather,
it also lets you ask an OpenRouter model (default:
`liquid/lfm-2.5-2.6b:free` — no cost, configurable via `OPENROUTER_MODEL`) to
suggest a short day-by-day itinerary based on the stored current conditions
and 3-day forecast. One non-obvious bug I hit and fixed while testing live:
some free-tier OpenRouter models are "reasoning" models that spend part of
`max_tokens` on hidden chain-of-thought before writing a visible answer — with
a low token budget they can return `content: null` entirely. `app/llm.py`
uses a generous `max_tokens` (1600) and explicitly checks for empty/`None`
content, raising a clear `ItineraryError` instead of crashing on `.strip()`.
This is covered by a regression test in `tests/test_llm.py`.

## What I'd do differently with more time

- A real scheduler (APScheduler in-process, or actual cron) instead of manual/
  dashboard-triggered pulls.
- Store per-city rolling-window/threshold config instead of one global env var.
- A small in-memory or Redis cache in front of `/api/latest` for high-traffic
  cities, on top of the DB-only reads that already avoid re-hitting Open-Meteo.
- Debounce/cache itinerary requests per city+snapshot so re-clicking "Suggest
  an itinerary" for unchanged data doesn't re-spend LLM tokens.
- Swap the free OpenRouter model for a paid one behind a feature flag, since
  free-tier models can be rate-limited or capacity-constrained (observed this
  firsthand while testing — see above).

## AI tool usage

Built with Claude (Claude Code) as a pair-programming assistant, per the
assignment's stated allowance. I'm happy to walk through any part of the
implementation.

## Time spent

_Fill in before submitting — an honest number, no penalty either way._
