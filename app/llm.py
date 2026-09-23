import os

import requests

from .errors import ItineraryError

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 25
DEFAULT_MODEL = "liquid/lfm-2.5-2.6b:free"


def build_prompt(city_display_name, current, daily):
    lines = [
        f"City: {city_display_name}",
        f"Current: {current['temperature']}°C, windspeed {current['windspeed']} km/h, "
        f"weather code {current['weathercode']} (WMO code).",
    ]
    if daily and daily.get("time"):
        lines.append("Upcoming daily forecast:")
        for i, date in enumerate(daily["time"]):
            tmax = daily["temperature_2m_max"][i]
            tmin = daily["temperature_2m_min"][i]
            precip = daily["precipitation_sum"][i]
            code = daily["weathercode"][i]
            lines.append(
                f"- {date}: max {tmax}°C / min {tmin}°C, "
                f"precipitation {precip}mm, weather code {code}"
            )
    lines.append(
        "Suggest a short, practical day-by-day itinerary for a visitor, "
        "explaining briefly how the forecast shapes each day's plan."
    )
    return "\n".join(lines)


def suggest_itinerary(city_display_name, current, daily):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ItineraryError("OPENROUTER_API_KEY is not configured on the server")

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise local travel assistant. Base your suggestions only "
                    "on the weather data given. Keep the whole answer under 200 words."
                ),
            },
            {"role": "user", "content": build_prompt(city_display_name, current, daily)},
        ],
        # Generous headroom: some free-tier models are "reasoning" models that spend
        # a chunk of the budget on hidden chain-of-thought before the final answer.
        "max_tokens": 1600,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ItineraryError(f"OpenRouter request failed: {exc}") from exc

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ItineraryError("Unexpected response shape from OpenRouter") from exc

    if not content:
        # Some free-tier "reasoning" models can exhaust max_tokens on hidden
        # chain-of-thought and return no visible answer at all.
        raise ItineraryError("Model returned no content -- try again or pick a different model")

    return content.strip()
