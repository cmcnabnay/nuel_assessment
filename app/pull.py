import json
from datetime import datetime, timezone

from .weather_api import fetch_weather, geocode_city


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_or_create_city(conn, name):
    row = conn.execute(
        "SELECT * FROM cities WHERE lower(query_name) = lower(?)", (name,)
    ).fetchone()
    if row:
        return row

    info = geocode_city(name)  # may raise CityNotFoundError / UpstreamError
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
    return conn.execute(
        "SELECT * FROM cities WHERE latitude = ? AND longitude = ?",
        (info["latitude"], info["longitude"]),
    ).fetchone()


def pull_city(conn, name):
    """Pull one fresh snapshot for `name`, storing it and returning (city_row, pulled_at).

    Raises CityNotFoundError if the name can't be geocoded, or UpstreamError if the
    weather API itself fails. Callers decide how to degrade (e.g. serve cached data).
    """
    city = get_or_create_city(conn, name)
    weather = fetch_weather(city["latitude"], city["longitude"], city["timezone"])

    pulled_at = now_iso()
    conn.execute(
        """
        INSERT INTO snapshots
            (city_id, pulled_at, temperature, windspeed, humidity, precipitation, weathercode, forecast_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city["id"],
            pulled_at,
            weather["temperature"],
            weather["windspeed"],
            weather.get("humidity"),
            weather.get("precipitation"),
            weather["weathercode"],
            json.dumps(weather["daily"]) if weather["daily"] else None,
        ),
    )
    conn.commit()
    return city, pulled_at
