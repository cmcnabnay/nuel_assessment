from datetime import datetime, timedelta, timezone

import requests

from .errors import CityNotFoundError, UpstreamError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_BY_ID_URL = "https://geocoding-api.open-meteo.com/v1/get"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 8
HOURLY_VARIABLES = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code"


def _geocode_get(url, params):
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise UpstreamError(f"Geocoding request failed: {exc}") from exc
    # Open-Meteo answers an unknown location id with 400 {"reason": "Location ID not found."}.
    if resp.status_code == 400 and "id" in params:
        raise CityNotFoundError(f"No location found for id {params['id']}")
    try:
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamError(f"Geocoding request failed: {exc}") from exc
    return resp.json()


def _location(match):
    return {
        "id": match.get("id"),
        "display_name": match["name"],
        "region": match.get("admin1"),
        "country": match.get("country"),
        "latitude": match["latitude"],
        "longitude": match["longitude"],
        "timezone": match.get("timezone") or "auto",
    }


def search_cities(name, count=5):
    """Top geocoding matches for a partial city name, for search-as-you-type."""
    data = _geocode_get(GEOCODE_URL, {"name": name, "count": count})
    return [_location(m) for m in data.get("results") or []]


def geocode_city(name):
    """Resolve a free-text city name to coordinates via Open-Meteo's geocoding API."""
    results = search_cities(name, count=1)
    if not results:
        raise CityNotFoundError(f"No location found for '{name}'")
    return results[0]


def geocode_by_id(location_id):
    """Resolve a location picked from search_cities() results, so an ambiguous name
    (e.g. Paris, Texas) doesn't fall back to the top match."""
    return _location(_geocode_get(GEOCODE_BY_ID_URL, {"id": location_id}))


def fetch_hourly_history(latitude, longitude, start_date, end_date):
    """Hourly readings (UTC) between two dates, inclusive, for backfilling history.

    Returns a list of dicts shaped like fetch_weather()'s current reading, plus `time`.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_VARIABLES,
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "UTC",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamError(f"History request failed: {exc}") from exc

    hourly = resp.json().get("hourly")
    if not hourly:
        raise UpstreamError("History response was missing 'hourly'")

    return _hourly_rows(hourly)


def _hourly_rows(hourly, utc_offset_seconds=0):
    """Flatten Open-Meteo's column-per-variable hourly block into one dict per hour.
    `time` is converted to a UTC ISO string (same format as snapshots.pulled_at)."""
    rows = []
    for i, t in enumerate(hourly["time"]):
        local = datetime.fromisoformat(t)
        at = (local - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
        rows.append({
            "time": at.isoformat(timespec="microseconds"),
            "temperature": hourly["temperature_2m"][i],
            "humidity": hourly["relative_humidity_2m"][i],
            "precipitation": hourly["precipitation"][i],
            "windspeed": hourly["wind_speed_10m"][i],
            "weathercode": hourly["weather_code"][i],
        })
    return rows


# Hourly forecast kept for the chart: today plus the next two days.
HOURLY_FORECAST_HOURS = 72


def fetch_weather(latitude, longitude, tz="auto"):
    """Fetch current conditions plus a short daily and hourly forecast for a coordinate."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "hourly": HOURLY_VARIABLES,
        # One day more than the hourly chart shows, so an itinerary made late in the
        # evening (which starts tomorrow) still has a forecast for all three of its days.
        "forecast_days": 4,
        "timezone": tz or "auto",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamError(f"Forecast request failed: {exc}") from exc

    data = resp.json()
    current = data.get("current")
    if not current:
        raise UpstreamError("Forecast response was missing 'current'")

    return {
        "temperature": current["temperature_2m"],
        "windspeed": current.get("wind_speed_10m"),
        "humidity": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "weathercode": current.get("weather_code"),
        "is_day": current.get("is_day"),
        "daily": data.get("daily"),
        # Hourly times come back in the city's timezone; _hourly_rows converts to UTC.
        "hourly": _hourly_rows(data["hourly"], data.get("utc_offset_seconds", 0))[:HOURLY_FORECAST_HOURS]
        if data.get("hourly") else None,
    }
