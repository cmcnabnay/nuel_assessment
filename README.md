# Clearday Weather Dashboard

This project is a small pipeline dashboard built around [Open-Meteo](https://open-meteo.com)
that pulls weather data (Temperature, Percipitation, Wind Speed, Humidity) for any city you type in, stores every pull as a timestamped snapshot in SQLite, computes metrics between snapshots from the accumulated
history, and asks an open-source LLM via [OpenRouter](https://openrouter.ai) to suggest a travel itinerary based on the current forecast for the selected city.

## Stack

Python 3.10, FastAPI, SQLite (stdlib `sqlite3`), HTML/JS, Chart.js for the
dashboard graph, pytest for tests. 

## First Time Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then optionally paste an OpenRouter key into .env
```

`OPENROUTER_API_KEY` is needed for the itinerary feature, set as an environmental variable in vercel 

## Setup After First Time
If you have already set up the app previously then closed the terminal in which the virtual environment was running, in order to open the dashboard again, you do not need to re-install the requirements.txt or copy the .env file. However, you must reconfiure the virtual environment 

```bash
source .venv/bin/activate
```

## Running it

**Start the app:**

```bash
uvicorn app.main:app --reload
```

**View the dashboard:** open `http://localhost:8000`. 

**Dashboard** 
- Several cities are currently added under the Tracked Cities Banner
- Tabs at the top allow the user to select Temperature, Percipitation, Wind Speed, Humidity or the Itinerary
- First box shows the current conditions (e.g., Sunny, Cloudy, Rain) along with the current local time for the selected city
- Box below and to the left shows the current temperature in that city (Celcius and Farenheit can be toggled)
- Box below and to the right shows calculated metrics using the previous data 
  - Change in weather metric (Temperatuere, Percipitation, Windspeed, Humidity) from user selected date and time in the past week to date and time of the the most recent pull
  - Average of weather metric from user selected date and time in the past week to date and time of the the most recent pull
  - Minimum and Maximum value of weather metric from user selected date and time in the past week to date and time of the the most recent pull
- Box below shows chart plotting history over the past 7 days for the selected metric along with cards showing the high and low values of the metric for that day 
- Box below shows the chart plotting forecast of the selected metric along with cards that showing the high and low values of the metric for that day

**How data gets updated**
Weather data gets updated four ways: 
- Click Refresh on a city already on the dashboard to re-pull its current weather
- Type a new city into the search bar to add and pull it for the first time
- Run the pipeline manually from the terminal to pull one or more cities without opening the dashboard
- Let a scheduled pull (e.g. a cron job or systemd timer running the pipeline on an interval) keep every tracked city's history up to date automatically.

**Scheduled Pull**
The scheduled pull uses a systemd user timer (`weather-pull.timer`, saved to my machine) 
- Runs `scripts/pull_now.py` automatically every 15 minutes, re-pulling every currently tracked city and storing a fresh snapshot for each. 
- Each pull inserts one new row into the `snapshots` table (temperature, windspeed, humidity, precipitation, weathercode, is_day, the daily and hourly forecast JSON, and the pull's timestamp), tagged `source='live'`. It doesn't touch the `cities` table, since the city was already added there the first time it was pulled or searched.
- It also fires once 5 minutes after login/boot, `Persistent=true` means a missed run (e.g. when the machine was off) fires as soon as the user systemd session is back up. 

- Check status: `systemctl --user status weather-pull.timer`
- View logs from each run: `journalctl --user -u weather-pull.service`
- Change the interval: edit `OnUnitActiveSec` in the timer file, then `systemctl --user daemon-reload`
- Disable it: `systemctl --user disable --now weather-pull.timer`

**Running the pull manually**

```bash
python scripts/pull_now.py                # re-pulls every city already tracked
python scripts/pull_now.py London Tokyo    # re-pulls (or adds) specific cities
```

**Refresh Button**
The Refresh button re-pulls the currently selected city's weather from Open-Meteo and stores it as a new snapshot, without changing which city is selected or which tab is open
This is what happens when the button is clicked:

static/index.html
- The button is #refresh-btn, in the status bar next to the "pulled at" time and pull count
static/app.js (click handler on #refresh-btn)
- Input: none directly; reads state.currentCity, the city currently on screen
- Returns early (does nothing) if no city has been selected yet
- Calls pullCity(state.currentCity) with no location_id, since the city is already known and doesn't need to be re-geocoded
- pullCity()
  - Input: city (state.currentCity)
  - Calls api() with POST /api/pull?city=..., saves the response dict to the variable payload
    - If the response isn't OK, api() throws an Error with the backend's error message
  - Calls renderLatest(payload) to redraw the dashboard with the fresh data
  - On error, calls showBanner(err.message, "error") instead
- renderLatest() (since payload is a new object, not the same one already on screen, this runs the full path rather than just re-rendering charts)
  - Updates the city name, weather description, and condition visuals (icon, sky, day/night) from the new snapshot
  - Updates the "pulled at" time and pull count
  - Calls renderSeries() to update the current value and metrics for whichever tab is selected
  - Shows the stale-data banner if payload.status is "stale", otherwise hides it
  - Resets the itinerary panel (clears content/status, hides the Save button) and reloads that city's saved itineraries
  - Calls loadHistory(), loadForecast(), and refreshCityList() to reload the chart data, forecast data, and re-highlight the active city in the sidebar
app/main.py (POST /api/pull)
- Input: city, the city name from the query string, and location_id (None here, since the Refresh button doesn't pass one)
- Opens a database connection, saves it to the variable conn
- Calls pull_city(conn, city, location_id)
  - If it raises CityNotFoundError, returns a 404 (shouldn't normally happen for a city that's already tracked)
  - If it raises UpstreamError, looks up the city's existing row with _get_city_row(); if found, returns _build_latest_payload() with status="stale" and the error message, so a failed refresh falls back to the last good snapshot instead of clearing the dashboard; if no existing row is found, returns a 502
- On success, returns _build_latest_payload(conn, city_row), the newly stored snapshot's data plus recomputed metrics
- Closes conn
app/pull.py (pull_city())
- Input: conn, city, location_id (None)
- Calls get_or_create_city(), which finds the city's existing row by query_name rather than geocoding it again
- Calls fetch_weather() from weather_api.py with the city's stored latitude, longitude, and timezone; this is the network call to Open-Meteo for current conditions and the forecast
- Inserts a new row into the snapshots table with the fresh reading (temperature, windspeed, humidity, precipitation, weathercode, is_day) and the current timestamp, along with the updated daily and hourly forecast JSON
- Commits, then returns the city row and the new pull's timestamp
- Output: the dashboard's conditions, metrics, and charts now reflect the newly stored snapshot, and the city's pull history has one more row than before

**Backfill Process**
Backfill is the step that fills in a city's weather history for the days before it was ever pulled, by fetching Open-Meteo's archived hourly readings instead of just the current conditions. Without it, a city that was just added would show up with exactly one data point, so its chart would be a single dot and its metrics would all read "n/a (need 2+ pulls)". The moment a new city is pulled for the first time, the app automatically runs this to fetch and store the past week of hourly history too, so the chart and metrics look the same as they would for a city that's been tracked for a week.
This is what happens when a new city is added (typed into the search bar, chosen from a suggestion, or added via `scripts/pull_now.py`):

app/pull.py
- pull_city()
  - Input: conn, name, location_id
  - Calls get_or_create_city(), which now returns (city, is_new) instead of just city -- is_new is True only when this call inserted a brand-new row into the cities table, False when the city was already tracked
  - Calls fetch_weather() and inserts the live snapshot, exactly like any other pull (Refresh included)
  - If is_new is True:
    - Calls local_today(city) (backfill.py) to get today's date in the city's own timezone (not the server's), then subtracts BACKFILL_DAYS (7) to get the variable since
    - Calls backfill_city(conn, city, since)
    - Wraps that call in try/except UpstreamError: pass, so a failed backfill (e.g. Open-Meteo's history endpoint is down) doesn't fail the pull that already succeeded -- the city still ends up added with at least its live snapshot
  - Output: city, pulled_at, same as before; as a side effect, a brand-new city now also has roughly a week of hourly history stored alongside its live snapshot

app/backfill.py
- backfill_city()
  - Input: conn, city, since (a date), dry_run
  - Reads the city's existing snapshot timestamps; right after a new city's first pull, that's exactly the one live row just committed
  - Computes start, local midnight of since in the city's own timezone, with local_midnight_utc()
  - Calls fetch_hourly_history() from weather_api.py for the UTC date range from start's date through the live pull's date
  - For each hourly reading returned: skips it if the temperature is null, if it falls before start or at/after the live pull's timestamp, or if that hour is already covered by an existing snapshot
  - Inserts one row per remaining hour into the snapshots table, tagged source='backfill', and commits
  - Output: the number of rows inserted; the newest row for the city is always left as the live pull, since nothing is ever inserted at or after it

app/weather_api.py
- fetch_hourly_history()
  - Input: latitude, longitude, start_date, end_date
  - Calls Open-Meteo's forecast endpoint with the hourly parameter set (temperature, humidity, precipitation, wind speed, weather code) instead of the current/daily ones fetch_weather() uses
  - Raises UpstreamError if the request fails or the response has no "hourly" block
  - Output: one dict per hour in the range (in UTC), shaped like fetch_weather()'s current reading plus a time field

**Database**
The app stores everything in a single SQLite file `weather.db`,It has three tables:

- **cities** -- one row per tracked city
  - id, query_name (what the user typed or searched), display_name, country, latitude, longitude, timezone, created_at
- **snapshots** -- one row per weather reading for a city, live or backfilled
  - id, city_id (references cities), pulled_at, temperature, windspeed, humidity, precipitation, weathercode, is_day, forecast_json (the 3-day daily forecast), forecast_hourly_json (the next ~3 days hourly, for the forecast chart), source ('live' for a real pull, 'backfill' for hourly history filled in by app/backfill.py)
  - Indexed on (city_id, pulled_at), since almost every query filters by city and orders by time
- **saved_itineraries** -- one row per itinerary the user chose to keep
  - id, city_id (references cities), saved_at, days_json (a structured itinerary) and text (the model's raw prose reply) -- exactly one of the two is set per row
  - Indexed on (city_id, saved_at)

**Itinerary**
The Itinerary tab generates a travel itinerary for the selected city that takes into account the three day OpenMeteo forecast when the user clicks the "Suggest an itinerary" button
This is what happens when the button is clicked:

static/index.html
- The button is #itinerary-btn
static/app.js (click handler on #itinerary-btn)
- Input: state.currentCity, the city the user has selected
- Returns early if no city is selected
- Saves state.currentCity to the variable city so it can check later whether the user switched cities during the wait
- Saves the button to the variable btn and disables it so the user can't send a second request
- Sets the status line to "Asking the model...", clears the old content and hides the Save button (showSaveButton(null))
- Calls api() with /api/itinerary?city=..., which sends a GET request with fetch, saves the response dict to the variable payload
  - If the response isn't OK, api() throws an Error with the backend's error message
- If city no longer matches state.currentCity (user switched cities), it stops and ignores payload
- Clears the status and calls showItinerary() on payload
  - If payload.days is set, renderItineraryDays() draws each day (date, title, weather note, list of timed stops)
  - If only payload.text is set, renderMarkdown() draws the model's prose reply
  - If payload.truncated is True, adds a note saying the reply was cut off
- Calls showSaveButton() with the result so the user can save it through POST /api/itineraries
- On error, sets the status line to "Itinerary unavailable: <message>"
- Re-enables btn whether the request worked or failed
- Output: the itinerary drawn in #itinerary-content
app/main.py (GET /api/itinerary)
- Input: city, the city name from the query string
- Opens a database connection, saves it to the variable conn
- Calls _get_city_row() on city, saves the city's row to the variable row, returns a 404 if the city hasn't been pulled yet
- Queries the snapshots table for the city's most recent row (ORDER BY pulled_at DESC, id DESC LIMIT 1), saves it to the variable snap, returns a 404 if there isn't one
- Decodes snap["forecast_json"] into the variable daily, the 3-day forecast pull_city() saved during the last pull. It doesn't call Open-Meteo again
- Builds the variable current, a dict of the snapshot's temperature, windspeed and weathercode
- Calls suggest_itinerary() from llm.py with the display name, current, daily and the city's timezone, saves the returned dict to the variable result
  - If suggest_itinerary() raises ItineraryError, returns a 503 with the error message
- Closes conn
- Output: a dict with city, days, truncated and text. Either days or text is set, not both
app/llm.py
- suggest_itinerary()
  - Input: city_display_name, current, daily and tz_name (the city's timezone)
  - Reads the API key from the environment, saves it to the variable api_key, raises ItineraryError if it's missing
  - Reads the model from OPENROUTER_MODEL in .env, falling back to DEFAULT_MODEL, saves it to the variable model
  - Builds the request body, saved to the variable body, consisting of the system prompt (SYSTEM_PROMPT) and the prompt to generate an itinerary for the selected city (from build_prompt())
  - Builds the request headers (API key and content type), saved to the variable headers
  - Sends POST to OpenRouter, saves the response object to the variable resp, raises ItineraryError if the request fails
  - Saves resp as a JSON to the variable data
  - Gets the reply text from data["choices"][0]["message"]["content"], saves it to the variable content, raises ItineraryError if content is empty
  - Calls parse_itinerary() on content, saves the result to the variables days and truncated
  - Output:
    - If days has stops, returns {"days": days, "truncated": truncated}
    - If content looked like JSON but couldn't be used, raises ItineraryError so the page never shows raw JSON
    - Otherwise returns {"text": content}, the model's prose reply
- build_prompt()
  - Input: city_display_name, current, daily and tz_name
  - Starts the list lines with the city name
  - Adds the current local time in the city's timezone
  - Adds the current weather (temperature in °C and °F, weather code turned into words, wind speed)
  - Adds one line per forecast day from daily (high, low, precipitation, weather)
  - Adds the instruction "Plan one day per forecast day."
  - Output: lines joined with \n into one string
- parse_itinerary()
  - Input: content, the model's reply as a raw string
  - Calls _load_itinerary_json() on content, saves the result to the variables data and truncated
  - If data is None, returns (None, False)
  - Calls normalize_days() on data["days"], saves the cleaned list to the variable days
  - Output: (days, truncated), or (None, False) if days is empty
- _load_itinerary_json()
  - Input: content, the model's reply as a raw string
  - Finds where the JSON starts (start = content.find("{")), if there is no "{", the reply is not a JSON so it returns (None, False)
  - Finds the last "}" (end = content.rfind("}")), if one exists after start, it slices content which drops the text and code fences before and after
  - Calls json.loads() on the slice, if that works, it returns (data, False)
  - If the JSON is broken it catches JSONDecodeError and moves on
  - Calls _close_truncated_json() on content from start onward, saves the fixed string to the variable repaired
  - Calls json.loads() on repaired, if that works, it returns (data, True)
  - Output: (data, truncated), data is the itinerary dict and truncated says whether it had to be repaired, or (None, False) if nothing worked
- _close_truncated_json()
  - Input: text, the model's reply from the first "{" onward
  - Goes through text one character at a time, keeping a list of open "{" and "[" in the variable stack (ignoring brackets inside strings)
  - Each time a bracket closes, saves that position and the brackets still open to the variable last_cut
  - Drops everything after the last closed bracket, then adds the closing brackets that are still missing
  - Output: the repaired JSON string, or None if no bracket ever closed
- normalize_days()
  - Input: raw_days, the "days" list from the model's JSON
  - Skips any day that isn't a dict
  - For each day, builds the list events, turning each stop's time, title, place, category and details into trimmed strings, dropping stops with no title or place
  - Skips days with no events
  - Output: days, a list of cleaned days each with date, title, weather_note and events

**Tests**

```bash
pytest
```

29 tests, all offline, covering the derived-metric
math, the pull pipeline's success/failure paths, the API endpoints end to end, and the OpenRouter integration's error handling.

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
- backfill.py: Fills in missing hourly history for a tracked city between a given start date and its latest snapshot, tagging inserted rows as source='backfill'

static/: Frontend
- index.html: Frontend page for the dashboard, served by the FastAPI backend, defines a search form to lookup and track a city, a sidebar that lists tracked cities, a dashboard section with three cards, current conditions, derived metrics, and trip itinerary suggestion, and a temperature history chart
- app.js: Client side logic that drives index.html, wires up the UI to the FastAPI backend's endpoints
- style.css: Page styling

scripts/: Command-line entry points for running the pipeline manually or scheduled
- pull_now.py: Re-pulls every tracked city, or specific cities passed as arguments, storing a fresh snapshot for each
- backfill.py: Loads hourly history for tracked cities from a given start date up to each city's latest pull, with --dry-run and --trim options

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
  - DELETE /api/cities?city=...: Stops tracking a city. Looks up the city, 404s if it is not tracked, otherwise deletes its snapshots, its saved itineraries, and then the city row itself. The sidebar's × button calls this
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

errors.py
- Defines three exception classes, each a bare subclass of Exception carrying only a docstring
- UpstreamError: Raised when an external API (Open-Meteo or OpenRouter) fails, times out, or is unreachable
  - Raised in weather_api.py's _geocode_get(), fetch_hourly_history(), and fetch_weather() when the request itself fails or the response is missing expected data
  - Raised in llm.py's suggest_itinerary() when the request to OpenRouter fails
- CityNotFoundError: Raised when geocoding finds no match for the requested city name
  - Raised in weather_api.py's geocode_city() and geocode_by_id() (via _geocode_get())
- ItineraryError: Raised when the OpenRouter itinerary suggestion cannot be produced
  - Raised in llm.py's suggest_itinerary() when the API key is missing, the request fails, or the model returns no content
- Caught by name in main.py's route handlers and translated into the matching HTTP status code (404 for CityNotFoundError, 502/503 for UpstreamError/ItineraryError) instead of a generic 500

config.py
- Imports os and load_dotenv from python-dotenv
- Calls load_dotenv(), which reads the .env file in the project root and loads its key/value pairs into the environment, so the os.environ.get() calls below can see values set there
- Defines DB_PATH: the SQLite file path, read from the DB_PATH environment variable, defaulting to "weather.db" if not set
- Defines ROLLING_WINDOW: how many recent pulls the rolling average covers, read as an int from ROLLING_WINDOW, defaulting to 5
- Defines ALERT_THRESHOLD_C: the temperature-change threshold (in °C) that triggers the alert flag, read as a float from ALERT_THRESHOLD_C, defaulting to 5
- These three values are imported wherever a setting is needed (DB_PATH in db.py; ROLLING_WINDOW and ALERT_THRESHOLD_C in main.py) instead of each module reading os.environ directly

backfill.py
- Imports datetime, time, and timezone from the standard library, plus ZoneInfo and ZoneInfoNotFoundError for timezone lookups
- Imports fetch_hourly_history from weather_api.py
- local_midnight_utc(): Converts local midnight on a given day for a city into a UTC datetime
  - Takes city (a cities row) and day (a date)
  - Looks up the city's IANA timezone with ZoneInfo(city["timezone"]), falling back to UTC if the stored timezone string is missing or invalid (e.g. "auto")
  - Combines day with midnight (time(0)) in that timezone, then converts the result to UTC
- trim_backfill_before(): Deletes backfilled rows earlier than local midnight of a given date
  - Takes conn, city, since (a date), and dry_run
  - Computes cutoff with local_midnight_utc()
  - Counts snapshot rows for the city where source = 'backfill' and pulled_at is before cutoff
  - If any exist and dry_run is False, deletes them and commits
  - Returns the count either way, so a dry run can report what would be removed without writing anything
  - Never touches rows with source = 'live'
- backfill_city(): Fills in missing hourly history for a city between local midnight of a start date and its latest snapshot
  - Takes conn, city, since (a date), and dry_run
  - Reads all existing snapshot timestamps for the city; returns 0 immediately if there are none (nothing to anchor the backfill to)
  - Takes the latest existing snapshot's timestamp as the upper bound, and local midnight of since as the lower bound; returns 0 if the latest snapshot is already at or before that lower bound
  - Builds covered_hours, a set of "YYYY-MM-DDTHH" strings for hours that already have a snapshot, so those hours are skipped
  - Calls fetch_hourly_history() for the UTC date range spanning start to latest
  - For each hourly reading: skips it if temperature is null, if it falls before start or at/after latest, or if its hour is already in covered_hours
  - Builds one row tuple per remaining reading (city_id, timestamp, temperature, windspeed, humidity, precipitation, weathercode), using the same timestamp format as live pulls so ordering still holds
  - If any rows were built and dry_run is False, inserts them all with source = 'backfill' and commits
  - Returns the number of rows inserted (or that would be, in dry-run mode)
  - Never inserts at or after the latest existing snapshot, so the newest row for a city is always a real live pull (the one with a forecast_json for the itinerary feature)
  - Driven by scripts/backfill.py, which loops this over every tracked city (or specific ones passed as arguments)

__init__.py
- Empty file
- Its only purpose is to mark app/ as a regular Python package, which is what lets other modules use relative imports like "from .errors import UpstreamError" and lets the app be run as app.main (e.g. uvicorn app.main:app)

## scripts/
pull_now.py
- Adds the project root to sys.path so `app` is importable when the file is run directly (not as a package), then imports db_module, CityNotFoundError, UpstreamError, and pull_city
- main()
  - Calls db_module.init_db(), opens a connection
  - Builds names: the command-line arguments if any were given, otherwise every query_name already in the cities table; if there are none of either, prints a usage message and returns
  - For each name, calls pull_city(conn, name)
    - On success, prints "Pulled {display_name} at {pulled_at}"
    - If it raises CityNotFoundError, prints "Skipping '{name}': {error}" and moves on to the next name
    - If it raises UpstreamError, prints "Pull failed for '{name}' (leaving prior history intact): {error}" and moves on
  - Closes conn in a finally block
  - Run via `if __name__ == "__main__"`, so it does nothing when imported
- This is the exact script the systemd timer runs on a schedule (see **Scheduled Pull**), and since pull_city() now backfills any brand-new city's first week of history, adding a never-seen city through this script gets backfilled the same as adding one through the search bar

backfill.py
- Same sys.path setup as pull_now.py, then imports db_module, backfill_city, trim_backfill_before, and UpstreamError
- main()
  - Splits sys.argv into the --dry-run / --trim flags and the remaining positional arguments
  - If there are no positional arguments, prints the module's own docstring (usage) and returns
  - Parses the first argument as since, an ISO date; the rest are city names
  - Calls db_module.init_db(), opens a connection
  - Looks up cities: a case-insensitive match against the given names if any were passed, otherwise every row in the cities table
  - For each city:
    - Calls backfill_city(conn, city, since, dry_run=dry_run)
    - If it raises UpstreamError, prints "Skipping {city}: {error}" and continues to the next city
    - Adds the returned count to a running total, and builds a message like "{city}: {n} hourly rows added"
    - If --trim was passed, also calls trim_backfill_before(conn, city, since, dry_run=dry_run) and appends its count to the same message
    - Prints the message
  - Prints a final "Total: {n}" line, noting when it was a dry run
  - Closes conn in a finally block
- Lets a user manually backfill further back, re-run a backfill, or clean up backfilled rows before a date -- the automatic backfill in pull_city() only ever covers the last 7 days for a brand-new city

## static/
index.html
- Head: sets the page title, preconnects to and loads the Inter font from Google Fonts, links style.css, and loads Chart.js from a CDN
- Header: the brand mark (#brand-icon, filled in by app.js on load), the search form (#city-input plus its #suggestions dropdown for search-as-you-type), a °C/°F toggle (#unit-toggle), and a km/h/mph toggle (#wind-toggle, hidden until the wind speed tab is active)
- Sidebar: #city-list, the tracked-cities list app.js's refreshCityList() fills in, each entry with a remove (×) button
- Content section
  - #tabs: five tab buttons (temperature, precipitation, windspeed, humidity, itinerary), hidden until a city is loaded
  - #banner: shown for stale-data or error messages
  - #empty-state: shown before any city has been picked
  - #dashboard (hidden until a city is loaded)
    - .status-bar: the last-pulled time (#pulled-at), pull count (#metric-count), and #refresh-btn
    - .conditions card: city name (#city-name), weather description (#weather-desc), local time (#local-time), and the condition icon (#condition-icon)
    - #metric-panel: a current-value card (#series-name, #series-current), a metrics card with three base-picker buttons (change/average/min-max, each opening the #pull-picker popup) and their values (#metric-change, #metric-avg, #metric-minmax), and two chart cards (#history-chart, #forecast-chart), each with a title, a timezone note, and a daily high/low chip row (#history-hl / #forecast-hl)
    - #itinerary-panel (hidden until the Itinerary tab is selected): a header with the city name (#itinerary-city) and the Save/Suggest buttons, a status line (#itinerary-status), the itinerary content area (#itinerary-content), and the saved-itineraries list (#saved-list, #saved-empty)
- #pull-picker: the pop-up calendar dialog; empty in the markup, positioned and filled entirely by app.js
- Loads app.js last, with a `?v=` query string on both it and style.css used as a manual cache-buster when either file changes

app.js
- Constants and shared state
  - WEATHER_CODES: maps Open-Meteo weather codes to readable descriptions
  - state: the single mutable object holding the current city, active Chart.js instances, the last payload from the server, the loaded history/forecast rows, the user's chosen baseline pull for each metric, the saved unit/wind-unit/series preferences (via loadPref()), and the active tab
  - el(id): shorthand for document.getElementById
- api(path, options)
  - Calls fetch(), parses the JSON body
  - Throws an Error using the backend's detail/error message if the response wasn't ok
  - Otherwise returns the parsed body
- showBanner() / hideBanner(): show or hide the status banner
- weatherDescription(code): looks up a weather code in WEATHER_CODES, falling back to "Weather code N"
- Sets Chart.js's default colors and font once, to match the page's dark theme
- Weather icons and sky
  - conditionGroup(code): buckets a WMO weather code into one of eight groups (clear, partly, cloudy, fog, drizzle, rain, snow, storm)
  - ICON_PARTS: small functions, one per icon piece (sun, moon, cloud, rain, drizzle, snow, bolt, fog), each returning a snippet of inline SVG
  - weatherIcon(group, isDay): composes the right ICON_PARTS pieces for a condition group, using a sun or moon depending on isDay
  - isDaytime(snapshot): reads the snapshot's is_day field when present, else falls back to the city's local hour (6 AM-7 PM counts as day) for snapshots pulled before is_day was recorded
  - renderConditionVisuals(snapshot): sets the page's sky-* body class and fills #condition-icon from weatherIcon()
  - dominantGroup(readings): the most common condition group across a day's daytime readings (8 AM-8 PM local), used for a daily high/low chip's icon
- Preferences
  - loadPref(key, allowed) / savePref(key, value): read or write a value in localStorage, constrained to an allowed list, wrapped in try/catch so a blocked or private-mode localStorage doesn't crash the page
- Unit conversion
  - round1(), toUnit(), deltaToUnit(), deg(): Celsius/Fahrenheit conversion for absolute readings and for differences (a difference skips the +32 offset)
  - toWindUnit(), windUnitLabel(): km/h/mph conversion for wind speed
  - identity(): a no-op converter for series that need no conversion (precipitation, humidity)
  - SERIES: per-tab config (label, unit function, value/delta converters, chart type, color) that every chart- and metric-rendering function reads from
- Time formatting
  - cityTimeZone(): the selected city's IANA timezone from the last payload, or undefined if it's unset/"auto" (so the browser's own zone is used instead)
  - formatCityTime(iso, options): formats an ISO timestamp in the city's timezone, falling back to the browser's if the zone name isn't recognized
  - timeZoneNote(): a "(City/Zone time, ABBR)" or "(your local time)" string shown next to chart titles
  - cityDayAndHour(iso): the local calendar day and hour of a timestamp in the city's timezone, used to group readings by day
- fmt(cfg, v) / formatChange(cfg, change): format a raw value, or a change dict (arrow, sign, percent), using a SERIES config
- renderLatest(payload)
  - Input: payload, the dict returned by /api/pull or /api/latest
  - Detects whether this is a re-render of the same payload object (e.g. after switching units) or a genuinely new pull
  - Updates the city name, weather description, condition visuals, pulled-at time, and pull count, then calls renderSeries()
  - Shows the stale-data banner if payload.status is "stale", otherwise hides it
  - On a new payload: resets the itinerary panel, reloads saved itineraries, and calls loadHistory(), loadForecast(), and refreshCityList()
  - On a re-render: just calls renderCharts() to redraw with the new unit or series
  - Always updates the local-time clock
- renderSeries(): fills in the current value and the three metrics for whichever tab is selected, using the server's numbers until history has finished loading, then switching to renderSelectedMetrics()
- loadHistory(city): fetches /api/history into state.history, then calls populateBaseSelects(), renderSeries(), and renderHistoryChart()
- User-selected baselines (the three base-picker buttons)
  - populateBaseSelects(): resets each metric's chosen "since" pull to a default (the previous pull for change, ROLLING_WINDOW pulls back for average, the oldest for min/max) if the previous choice no longer applies, then relabels the buttons
  - updatePickerLabels(): writes each base-picker button's label from state.bases
- Pop-up calendar (the picker object and #pull-picker dialog)
  - pullsByDay(): groups every pull except the latest by local day
  - openPicker(btn) / closePicker(): show or hide the dialog for a given metric button
  - positionPicker(): places the dialog under its button, clamped to stay on-screen
  - renderPicker(): renders either the month calendar or a day's list of times, depending on picker.day
  - calendarView(byDay, selected): builds the month grid, with days that have pulls clickable, previous/next navigation, and today/selected highlighting
  - timesView(times, selected): builds the list of pull times for one selected day
  - Click handler on #pull-picker: navigates months, opens a day, goes back to the calendar, or picks a time (storing it as the new baseline and closing the dialog)
  - Click handlers on each .base-picker button, and document-level mousedown/keydown/resize listeners, to open/close the picker and keep it positioned
- seriesValuesSince(since) / renderSelectedMetrics(cfg): compute change/average/min-max for the current series from the user's chosen baselines, entirely client-side from the already-loaded history, instead of the server's defaults
- loadForecast(city): fetches /api/forecast (emptying the array on failure) and calls renderForecastChart()
- renderCharts(): redraws both charts (used after a re-render, e.g. a unit switch)
- Daily history window
  - HISTORY_DAYS_BACK / historyStartDay(): the history chart always shows the last 7 local days through now
  - renderHistoryChart(): filters state.history to that window, builds chart points, and calls drawChart() and renderDailyHighLow()
- Daily high/low chips
  - dailyHighLow(points): groups chart points by local day, finding each day's highest and lowest reading, and flags a day "partial" if its coverage doesn't span roughly midnight to 11 PM
  - renderDailyHighLow(containerId, points): shown only on the temperature tab; renders one chip per day with its name, dominant condition icon, high, and low
- renderForecastChart(): builds chart points from state.forecast and calls drawChart() and renderDailyHighLow(), or shows the "no forecast stored yet" empty state
- drawChart(canvasId, points, opts)
  - Builds full-time labels for tooltips and short weekday/hour ticks for the axis
  - Converts values through the series' unit converter, leaving nulls as gaps rather than plotting 0
  - Destroys any existing chart on that canvas, then creates a new line or bar chart styled from the series' color, sizing bars so an isolated reading (e.g. one rainy hour) stays visible
- refreshCityList(activeCity): fetches /api/cities and rebuilds the sidebar list, each entry with a name, a remove (×) button, and a click handler to select that city
- removeCity(c): confirms, then calls DELETE /api/cities; if the removed city was the one on screen, switches to another tracked city or back to the empty state, otherwise just refreshes the sidebar
- selectCity(city) / pullCity(city, locationId): call /api/latest or POST /api/pull respectively and pass the result to renderLatest(), or the error to showBanner()
- Search-as-you-type
  - suggest: state for the current results, the highlighted index, a debounce timer, and a sequence counter (to discard stale responses)
  - suggestionLabel(r): a "City, Region, Country" label for a suggestion
  - hideSuggestions() / renderSuggestions(query): hide or rebuild the suggestions dropdown, including keyboard-selection highlighting
  - fetchSuggestions(query): calls /api/search, guarded by the sequence counter so a slow earlier request can't overwrite a newer one
  - chooseSuggestion(i): picks a suggestion and calls pullCity() with its exact location_id, so an ambiguous name (e.g. "Paris") resolves to the one the user actually picked
  - Input listeners on #city-input: debounce typing into fetchSuggestions(), handle arrow keys/Enter/Escape for keyboard navigation, and hide the list on blur
  - #search-form submit: calls pullCity() with the typed text and no location_id, so it geocodes to the top match
- #refresh-btn click: re-pulls state.currentCity
- Unit and wind-unit toggles
  - setUnit(unit) / setWindUnit(unit): update state, save the preference, update the pressed button, and re-render if a city is loaded
  - Click handlers wire each toggle's buttons to these; both are initialized once from the saved preference on load
- setTab(tab): switches the active tab, shows the metrics or itinerary panel, shows the unit/wind toggles only on the tabs they apply to, closes the picker, and re-renders (so a chart hidden while its panel was hidden gets redrawn at a real size)
  - Tab buttons are wired to this; the saved tab preference is restored on load
- Itinerary tab
  - escapeHtml(s): escapes HTML special characters before interpolating any model-provided text
  - renderItineraryDays(days): builds the structured itinerary markup, one section per day with a formatted date, weather note, and a list of timed stops
  - renderMarkdown(text): a fallback renderer for a prose reply that isn't structured JSON, converting basic markdown (headings, bullets, bold/italic) to HTML
  - showItinerary(itinerary, note): fills #itinerary-content with either the structured days or the markdown fallback, plus a disclaimer
  - showSaveButton(itinerary): shows or hides the Save button for a freshly generated, unsaved itinerary
  - #itinerary-btn click: calls /api/itinerary, guards against the user switching cities while waiting, then calls showItinerary() and showSaveButton() (or shows an error in the status line)
- Saved itineraries
  - loadSavedItineraries(city): fetches /api/itineraries for the city (leaving the list empty on failure) and calls renderSavedList()
  - savedSummary(s): a one-line summary of a saved itinerary (day titles, or "Text itinerary")
  - renderSavedList(): rebuilds #saved-list with each saved itinerary's date, summary, and View/Delete buttons
  - markViewing(id): highlights whichever saved itinerary is currently shown in the panel
  - #itinerary-save-btn click: POSTs the unsaved itinerary to /api/itineraries and adds the result to the top of the saved list
  - #saved-list click: View shows that saved itinerary in the panel; Delete confirms, then DELETEs it and removes it from the list (clearing the panel only if it was the one being viewed)
- City's current local time
  - updateLocalTime(): writes the city's current local time into #local-time; ticks every second via setInterval
- On load: calls refreshCityList(null), then auto-selects the first tracked city if any exist, so a page refresh doesn't lose context; fills #brand-icon with a static "partly cloudy" icon

style.css
- No logic to trace, so this is organized by the file's own section comments rather than function by function:
  - Sky backgrounds: one gradient class per weather-group/day-night pair (sky-clear-day, sky-rain-night, etc.), applied to <body> by renderConditionVisuals() in app.js
  - Shared controls: buttons, inputs, and the empty-state message
  - Segmented pill control: the shared look for the unit toggles and the tabs
  - Header: the brand, the search box, and its suggestions dropdown
  - Layout: the sidebar/content grid, including a rule that keeps the remove-city (×) button visible on touch devices
  - Cards, the current-conditions hero, and the daily high/low chips
  - Pull picker: the pop-up calendar dialog
  - Itinerary: the itinerary panel and its markdown-fallback styling
  - Narrow screens: two @media breakpoints (860px, 520px) that collapse the layout for tablet and phone widths

