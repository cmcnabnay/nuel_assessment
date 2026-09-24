import requests

from .errors import CityNotFoundError, UpstreamError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_BY_ID_URL = "https://geocoding-api.open-meteo.com/v1/get"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 8


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


def fetch_weather(latitude, longitude, tz="auto"):
    """Fetch current conditions plus a short daily forecast for a coordinate."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "forecast_days": 3,
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
        "daily": data.get("daily"),
    }
