import json
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .errors import ItineraryError

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# A full multi-day itinerary is a long answer; free-tier models can be slow to finish it.
TIMEOUT_SECONDS = 60
DEFAULT_MODEL = "inclusionai/ling-3.0-flash-sante:free"

# WMO weather codes -> plain words, so the model reasons about "rain" rather than "63".
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "violent showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with heavy hail",
}

CATEGORIES = ("food", "museum", "park", "event", "sightseeing", "activity", "shopping", "nightlife")

SYSTEM_PROMPT = f"""You are an expert local travel planner. Plan a detailed itinerary for a visitor.

Rules:
- Name specific, real places: the actual museum, the actual restaurant, the actual park or
  venue, with its neighborhood. Never write generic entries like "a local cafe".
- Each full day runs from morning to evening with 5 to 7 timed stops, including breakfast or
  coffee, lunch and dinner at named restaurants.
- Fit the plan to that day's weather: indoor stops for rain, storms or extreme heat or cold,
  outdoor stops for good weather.
- Only include a sports game, concert or other scheduled event if the venue really hosts
  them; say "check the schedule" in its details.
- Plan exactly the days listed in the request, in order. The first one may be only part of a
  day: follow the instruction given for it about when to start.
- Within each day, list stops in time order. Every stop happens on that calendar day;
  never schedule anything after midnight.
- No summary, no key takeaways, no closing remarks, no markdown.

Reply with only a JSON object, no other text, in exactly this shape:
{{"days": [{{"date": "YYYY-MM-DD", "title": "short theme for the day",
  "weather_note": "one sentence on how the weather shapes the day",
  "events": [{{"time": "9:00 AM", "title": "what you do", "place": "specific place name, neighborhood",
    "category": "one of: {", ".join(CATEGORIES)}", "details": "one or two sentences"}}]}}]}}"""


def _describe(code):
    return WEATHER_CODES.get(code, f"weather code {code}")


def _c_and_f(c):
    return f"{c}°C / {round(c * 9 / 5 + 32, 1)}°F"


def _local_now(tz_name):
    try:
        return datetime.now(ZoneInfo(tz_name)) if tz_name and tz_name != "auto" else None
    except ZoneInfoNotFoundError:
        return None


# From this local hour on, today is too far gone to plan: the itinerary starts tomorrow.
LATE_START_HOUR = 20


def build_prompt(city_display_name, current, daily, tz_name=None, now=None):
    lines = [f"City: {city_display_name}"]
    now = now or _local_now(tz_name)
    if now:
        lines.append(f"Current local time: {now.strftime('%A %Y-%m-%d, %I:%M %p')} ({tz_name})")
    lines.append(
        f"Current weather: {_c_and_f(current['temperature'])}, {_describe(current['weathercode'])}, "
        f"wind {current['windspeed']} km/h."
    )

    days = list(enumerate(daily["time"])) if daily and daily.get("time") else []
    if now:
        # A stored forecast can be a day or more old; drop the days that are over, and
        # today too once it's late in the evening.
        today = now.date().isoformat()
        late = now.hour >= LATE_START_HOUR
        days = [(i, d) for i, d in days if d > today or (d == today and not late)]

    if days:
        lines.append("Days to plan, with their forecast:")
        for i, day in days:
            weekday = date.fromisoformat(day).strftime("%A")
            lines.append(
                f"- {weekday} {day}: high {_c_and_f(daily['temperature_2m_max'][i])}, "
                f"low {_c_and_f(daily['temperature_2m_min'][i])}, "
                f"precipitation {daily['precipitation_sum'][i]} mm, {_describe(daily['weathercode'][i])}"
            )
        first = days[0][1]
        if now and first == now.date().isoformat():
            lines.append(
                f"{date.fromisoformat(first).strftime('%A')} is today: plan only what fits between now "
                f"({now.strftime('%I:%M %p')}) and about 10 PM, even if that is just one or two stops. "
                "The other days run from morning to evening as usual."
            )
        else:
            lines.append("Each day runs from morning to evening.")
    elif daily and daily.get("time"):
        lines.append("The stored forecast has no days left; plan one day starting tomorrow morning.")
    return "\n".join(lines)


def _close_truncated_json(text):
    """Make a cut-off JSON reply parseable by keeping everything up to the last
    object or array that closed, then closing whatever was still open.

    e.g. '{"days": [{"events": [{"a": 1}, {"b": "cut of' -> '{"days": [{"events": [{"a": 1}]}]}'
    Returns None if nothing ever closed.
    """
    stack, in_string, escaped = [], False, False
    last_cut = None  # (index just after a closing bracket, brackets still open there)
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            last_cut = (i + 1, list(stack))
    if not last_cut:
        return None
    end, still_open = last_cut
    return text[:end] + "".join("}" if b == "{" else "]" for b in reversed(still_open))


def _load_itinerary_json(content):
    """Returns (data, truncated). Small models often wrap JSON in ```fences``` or add a
    sentence around it, so this starts at the first "{". If the reply was cut off
    (max_tokens, dropped connection), the complete part of it is recovered."""
    start = content.find("{")
    if start < 0:
        return None, False
    end = content.rfind("}")
    if end > start:
        try:
            return json.loads(content[start : end + 1]), False
        except json.JSONDecodeError:
            pass
    repaired = _close_truncated_json(content[start:])
    if repaired:
        try:
            return json.loads(repaired), True
        except json.JSONDecodeError:
            pass
    return None, False


def parse_itinerary(content):
    """Pull the {"days": [...]} object out of the model's reply and normalize it.

    Returns (days, truncated): days is None if there's no usable itinerary in the reply;
    truncated is True when only the complete part of a cut-off reply was recovered.
    """
    data, truncated = _load_itinerary_json(content)
    if data is None:
        return None, False
    days = normalize_days(data.get("days") if isinstance(data, dict) else None)
    days = [d for d in (_drop_out_of_order(d) for d in days) if d["events"]]
    return (days, truncated) if days else (None, False)


def _parse_clock(text):
    """'9:00 AM' / '9 AM' / '21:30' -> datetime.time, or None if it isn't a clock time."""
    text = text.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    return None


def _drop_out_of_order(day):
    """Keep a day's stops only up to the first one whose time goes backwards (a model
    running past midnight into the next morning); stops without a parseable time are
    kept as they are."""
    kept, last = [], time.min
    for event in day["events"]:
        t = _parse_clock(event["time"])
        if t is not None:
            if t < last:
                break
            last = t
        kept.append(event)
    return {**day, "events": kept}


def normalize_days(raw_days):
    """Coerce an itinerary's days into the expected shape, dropping anything malformed
    (non-dict entries, stops with neither title nor place, days with no stops). Used for
    model replies and for itineraries the page asks to save. Returns a list (maybe empty)."""
    days = []
    for day in raw_days if isinstance(raw_days, list) else []:
        if not isinstance(day, dict):
            continue
        events = [
            {
                "time": str(e.get("time") or "").strip(),
                "title": str(e.get("title") or "").strip(),
                "place": str(e.get("place") or "").strip(),
                "category": str(e.get("category") or "").strip().lower(),
                "details": str(e.get("details") or "").strip(),
            }
            for e in day.get("events") or []
            if isinstance(e, dict) and (e.get("title") or e.get("place"))
        ]
        if events:
            days.append({
                "date": str(day.get("date") or "").strip(),
                "title": str(day.get("title") or "").strip(),
                "weather_note": str(day.get("weather_note") or "").strip(),
                "events": events,
            })
    return days


def suggest_itinerary(city_display_name, current, daily, tz_name=None):
    """Ask the model for an itinerary. Returns {"days": [...], "truncated": bool} when
    it replied with usable JSON, else {"text": "..."} with its prose reply so the page
    can still show it. A reply that is JSON but unusable raises ItineraryError, so the
    page never shows raw JSON."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ItineraryError("OPENROUTER_API_KEY is not configured on the server")

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(city_display_name, current, daily, tz_name)},
        ],
        # Generous headroom: a 3-day, 5-7 stop itinerary runs to a few thousand tokens,
        # and some free-tier models are "reasoning" models that spend part of the budget
        # on hidden chain-of-thought before the final answer. Cut-off replies are still
        # recovered up to the last complete stop (see parse_itinerary).
        "max_tokens": 8000,
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

    days, truncated = parse_itinerary(content)
    if days:
        finish_reason = (data["choices"][0].get("finish_reason") or "").lower()
        return {"days": days, "truncated": truncated or finish_reason == "length"}
    if content.lstrip("` \n").lower().startswith(("{", "json")):
        raise ItineraryError("The model's reply was cut off or malformed -- try again")
    return {"text": content.strip()}
