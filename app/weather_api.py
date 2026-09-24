import requests

from .errors import CityNotFoundError, UpstreamError

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 8


def geocode_city(name):
    """Resolve a free-text city name to coordinates via Open-Meteo's geocoding API."""
    try:
        resp = requests.get(GEOCODE_URL, params={"name": name, "count": 1}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamError(f"Geocoding request failed: {exc}") from exc

    data = resp.json()
    results = data.get("results")
    if not results:
        raise CityNotFoundError(f"No location found for '{name}'")

    match = results[0]
    return {
        "display_name": match["name"],
        "country": match.get("country"),
        "latitude": match["latitude"],
        "longitude": match["longitude"],
        "timezone": match.get("timezone") or "auto",
    }


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
