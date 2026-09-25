# Weather Dashboard

This project is a small pipeline  dashboard built around [Open-Meteo](https://open-meteo.com)
that pulls weather for any city you type in, stores every pull as a
timestamped snapshot in SQLite, computes metrics between snapshots from the accumulated
history, and asks an open-source LLM via
[OpenRouter](https://openrouter.ai) to suggest a travel itinerary based on the
current forecast for the selected city.

## Stack

Python 3.10, FastAPI, SQLite (stdlib `sqlite3`), vanilla HTML/JS + Chart.js for the
dashboard, pytest for tests. 

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

**Run the tests:**

```bash
pytest
```

29 tests, all offline, covering the derived-metric
math, the pull pipeline's success/failure paths, the API endpoints end to end,
and the OpenRouter integration's error handling.

## File System

app/: Python backend
- main.py: Entry point, creates FastAPI, serves the dashboard page, defines API routes (/api/pull, /api/test, /api/history, /api/cities, /api/itinerary)
- config.py: Reads settings from .env (DB_PATH, ROLLING_WINDOW, ALERT_THRESHOLD_C)
- db.py: Opens SQLite connection and creates two tables (cities, snapshot) if not created already
- weather_api.py: Communicates with OpenMeteo, looks p city's coordinates, then fetches current weather and forecast
- pull.py: Looks up the city, fetches it weather, and saves a new snapshot row
- metrics.py: Pure math on a list of temperatures, changes since last pull, rolling average, min/max, alert check
- llm.py: Builds a prompt from the stored forecast, asks open router for an itinerary
- errors.py: Defines three custom error types: UpstreamError, occurs when Open-Meteo-API fails/times out or if OpenRouter fails, CityNotFoundError, raised in weather_api.py when geocoding returns no match for a city name, ItineraryError, raised in llm.py when the OpenRouter Itinerary call fails
- __init__.py: Empty file that marks app/ as a python package which lets code elsewhere do relative imports

static/: Frontend
- index.html: Frontend page for the dashboard, served by the FastAPI backend, defines a search form to lookup and track a city, a sidebar that lists tracked cities, a dashboard section with three cards, current conditions, derived metrics, and trip itinerary suggestion, and a temperature history chart
- app.js: Client side logic that drives index.html, wires up the UI to the FastAPI backend's endpoints
- style.css: Page styling

test/: Automated tests
- test_metrics.py: Unit tests for math functions in metrics.py
- test_pull.py: Tests pull.py's pull_city() function against a temp SQLite DB 
- test_api.py: End to end tests of the FastAPI routes using TestClient, covers pull --> latest --> history flow, change metrics across two pulls, 404 on Unknown city, the "stale data" fallback when a live pull fails but a prior valid snapshot exists, and a 503 when /api/intinerary is called without an OpenRouter API key

.venv/: Virtual Environment
- Folder that python3 -m venv .venv created
- Holds a private python interpreter, command line tools (bin/uvicorn, bin/pytest, bin/pip) and every installed library

weather.db: SQLite database file, created on first run

## app/
main.py
- App setup
  - lifespan() runs db_module.init_db() (init_db function in db.py) on startup to execute the schema defined in db.py, ensuring that the SQLite tables were created
  - Creates the FastAPI application instance, the object that the rest of the file attaches routes to (line 24)
  - Mounts static/ at /static and serves index.html at / (line 27)
- Helpers
  - _get_city_row(): Takes whatever city name the user typed and checks the database for a matching row by running a SQL query, if found it returns the row with the city's id, coordinates, display name, if not it returns None 
  - _snapshots_for_city(): Gets a city's history of weather readings, if a date range was given, it appends those dates to the where clause in order to pull data from the specified date range
  - _build_latest_payload(): Turns raw stored data into the finished answer that the frontend displays 
    - Fetches all snapshots for the city snapshots = _snapshots_for_city(conn, city_row["id"])
    - If snapshots is empty, raise a 404 error
    - Extract just the temperatures temps = [s["temperature"] for s in snapshots]
    - Grabs the most recent snapshot latest_snap = snapshots[-1] (list is cronological so the latest entry is the newest)
    - Calculates the change since law pull change = change_since_last_pull(temps), a function from metrics.py that diffs the last two temperatures 
    - Assembles the response dict in three parts: 
      - city: static identity copied from city_row (name, country, coordinates)
      - snapshot: The raw values from latest_snap (pulled_at, temperature, windspeed, weathercode)
      - metrics: The computed layer
    - Attach status/error: "status" defaults to "ok" but callers can pass "stale" if an error string was passed in
- Routes
  - GET /api/cities: Runs one query that selects every row in the cities table, sort alphabetically by display name, convert each sqlite3.Row to a plain dict and returns the list 
  - POST /api/pull: Passes off two arguments to pull_city() (pull.py), conn, the open SQLite connection, so pull_city() can read/write the database itself, city the raw city name string the user typed in
  - GET /api/latest: Determines what is the most recent weather datapoint exists for a city. Looks up whether the city already has a row in the DB, 404s if not, and otherwise runs the exact same _build_latest_payload() used by /api/pull on what is already stored
  - GET /api/history: Looks up the city, then calls _snapshots_for_city() (function that builds the date filtered SQL query). Insead of running it through _build_latest_payload(), it maps each raw row to a dict which serves as the time series data that loadHistory() (app.js) uses to plot the Chart.js graph
  - GET /api/itinerary: Looks up the city, queries directly for its most recent snapshot row. It pulls forecase_json the forecast blob that pull_city() saved to the DB when the pull request happened and decodes it. Calls suggest_itinerary() from llm.py, passing the city name, current conditions, and forecast, which builds a prompt and calls OpenRouters LLM API

metrics.py
- change_since_last_pull(): Compares the last two readings
  - If the input list has less than two values, it returns none
  - absolute change is calculated
  - percent change is calculated
- rolling_average(): Returns the average of the last window values
  - If there are fewer values than the window, returns all of them
  - If window is 0 or None, it averages the whole history
  - An empty list returns None
- min_max(): Finds the maximum and minimum values from the values in the window
- alert_triggered(): Takes the dict returned by change_since_last_pull (in main.py), returns True if the absolute change is at least threshold_c (set in config.py) in either direction

db.py
- Imports sqlite3
- Imports DB_PATH from config.py (defined in .env)
- Defines SCHEMA
  - Creates cities and snapshots table
- get_connection(): Opens a new connection and sets it up
- init_db()
  - Runs the whole schema with executescript
  - commits
  - closes the connection
  - Runs once when the app starts in the FastAPI lifespan hook (main.py)

llm.py
- Imports ItineraryError from errors.py
- Defines OpenRouter URL, timeout seconds, and default model
- build_prompt(): Turns the weather data into the text the model reads
  - Takes three inputs
    - city_display_name
    - current: dictionary of temperature, windspeed, weathercode from the latest snapshot
    - daily: OpenMeteo's forecast
  - Adds a City, Current, and weather code line to the lines dict
  - If daily (dict with three day OpenMeteo forecast) has dates, it loops over daily["time"] and reads position i from each list, giving one line per day
  - Appends max and min temperature, percipitation and weather code to lines
  - Adds the instructions
  - Joins lines with \n
- suggest_itinerary(): Takes same inputs as build_prompt(), calls the LLM, and returns the itinerary text
  - Defines api key from environmental variables, raises error if there is no key
  - Defines model from environment variables
  - Defines body portion of the HTTP request sent to OpenRouter
  - Defines headers portion of the HTTP request
  - Calls model, saves request.Response object to resp, raises error if response is not returned
  - Saves request.Response object body as a JSON
  - Takes the model's reply text out of the response JSON
  - Raises an error if the model returns a response object but no content

weather_api.py
- Imports CityNotFoundError and UpstreamError from errors.py
- Defines the addresses of the two OpenMeteo APIs the app calls
- geocode_city(): Turns a city name into coordinates and location details using OpenMeteo's geocoding API
  - Calls Geocoding API, saves response to resp
  - Saves response object as a JSON to data
  - Saves results (list of dicts) from data dict
  - Returns name, country, latitude, longitude, and time zone from first dict in results
- fetch_weather(): Gets current conditions and a 3 day forecast for a coordinate in a call to OpenMeteo's forecast API
  - Constructs params dict, holds the query string arguments, part of the URL that tells OpenMeteo what to return
  - Calls API, saves response object to resp
  - Raises error if the forecast request fails
  - Saves resp as JSON to data
  - Saves current_weather dict to variable current
  - Raises error if the current_weather dict was not returned by the API call
  - Returns temperature, windspeed and weathercode from current dict
  - Returns daily dict from data  

pull.py
- Imports fetch_weather and geocode_city functions from weather_api.py
- now_iso(): Returns current UTC time as an ISO-8601 string
- get_or_create_city(): Returns the cities row for a name
  - Sends a SQL through the database connection (conn) that returns every column from the cities table where query_name matches the name the user typed
  - Calls geocode_city function from weather_api.py, saving a dict to info 
  - Adds a new city to the cities table, filling in display_name, country, latitide, longitude, and timezone from the info dict
  - conn.commit() makes the insert permanent
  - Queries the cities table for the row whose latitude and longitude match the geocoded values in info 
- pull_city(): Finds the city, geocoding it and saving it first if it's new, fetches that city's current weather and 3-day forecast from OpenMeteo, saves them as a new row in the snapshots table, returns the city's row with the pull time
  - Calls get_or_create_city() function, saving one row from the cities table as a sqlite3.Row
  - Calls fetch_weather function from weather_api.py, saving a dict with four keys, temperature, windspeed, weathercode, and daily json
  - Calls now_iso to create variable pulled_at
  - Inserts a new row into the snapshots table that records the weather for the city at the moment of the pull
  - conn.commit() makes the snapshot insert permanent
  - Returns city, a sqlite3.Row holding one row of the cities table and pulled_at, the time that pull_city() was called