import json
from datetime import datetime, timedelta, timezone

from .backfill import backfill_city, local_today
from .errors import UpstreamError
from .weather_api import fetch_weather, geocode_by_id, geocode_city

# How many days of hourly history to backfill the first time a city is pulled, matching
# the dashboard's history chart window (HISTORY_DAYS_BACK in app.js).
BACKFILL_DAYS = 7


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_or_create_city(conn, name, location_id=None):
    """Returns (city_row, is_new): is_new is True only when this call inserted the row."""
    row = conn.execute(
        "SELECT * FROM cities WHERE lower(query_name) = lower(?)", (name,)
    ).fetchone()
    if row:
        return row, False

    # may raise CityNotFoundError / UpstreamError
    info = geocode_by_id(location_id) if location_id else geocode_city(name)
    conn.execute(
        """
        INSERT INTO cities (query_name, display_name, country, latitude, longitude, timezone, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (latitude, longitude) DO NOTHING
        """,
        (
            name,
            info["display_name"],
            info["country"],
            info["latitude"],
            info["longitude"],
            info["timezone"],
            now_iso(),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM cities WHERE latitude = ? AND longitude = ?",
        (info["latitude"], info["longitude"]),
    ).fetchone()
    return row, True


def pull_city(conn, name, location_id=None):
    """Pull one fresh snapshot for `name`, storing it and returning (city_row, pulled_at).

    `location_id` is an Open-Meteo geocoding id from a search suggestion; when given,
    it picks the exact place instead of geocoding `name` to its top match.

    The first time a city is added, this also backfills the last BACKFILL_DAYS of hourly
    history so its chart isn't a single point. That's best-effort: a backfill failure is
    swallowed rather than raised, since the live snapshot below has already been stored.

    Raises CityNotFoundError if the name can't be geocoded, or UpstreamError if the
    weather API itself fails. Callers decide how to degrade (e.g. serve cached data).
    """
    city, is_new = get_or_create_city(conn, name, location_id)
    weather = fetch_weather(city["latitude"], city["longitude"], city["timezone"])

    pulled_at = now_iso()
    conn.execute(
        """
        INSERT INTO snapshots
            (city_id, pulled_at, temperature, windspeed, humidity, precipitation, weathercode,
             is_day, forecast_json, forecast_hourly_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city["id"],
            pulled_at,
            weather["temperature"],
            weather["windspeed"],
            weather.get("humidity"),
            weather.get("precipitation"),
            weather["weathercode"],
            weather.get("is_day"),
            json.dumps(weather["daily"]) if weather["daily"] else None,
            json.dumps(weather["hourly"]) if weather.get("hourly") else None,
        ),
    )
    conn.commit()

    if is_new:
        try:
            since = local_today(city) - timedelta(days=BACKFILL_DAYS)
            backfill_city(conn, city, since)
        except UpstreamError:
            pass

    return city, pulled_at
