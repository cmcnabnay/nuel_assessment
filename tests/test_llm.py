import pytest
import requests

from app import llm as llm_module
from app.errors import ItineraryError

CURRENT = {"temperature": 20.0, "windspeed": 5.0, "weathercode": 0}


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data


def test_suggest_itinerary_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ItineraryError, match="not configured"):
        llm_module.suggest_itinerary("Paris", CURRENT, None)


def test_suggest_itinerary_raises_on_null_content(monkeypatch):
    # Some free-tier reasoning models return content=None if they exhaust
    # max_tokens on hidden chain-of-thought before writing a final answer.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **k: FakeResponse({"choices": [{"message": {"content": None}}]}),
    )
    with pytest.raises(ItineraryError, match="no content"):
        llm_module.suggest_itinerary("Paris", CURRENT, None)


def test_suggest_itinerary_falls_back_to_text_when_reply_is_not_json(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **k: FakeResponse({"choices": [{"message": {"content": "  Day 1: walk.  "}}]}),
    )
    result = llm_module.suggest_itinerary("Paris", CURRENT, None)
    assert result == {"text": "Day 1: walk."}


def test_suggest_itinerary_parses_fenced_json(monkeypatch):
    reply = """Here you go:
```json
{"days": [{"date": "2026-09-25", "title": "Art and a ballgame", "weather_note": "Sunny.",
  "events": [
    {"time": "10:00 AM", "title": "Impressionist wing", "place": "Musee d'Orsay, 7th arr.",
     "category": "Museum", "details": "Go early."},
    {"time": "", "title": "", "place": "", "category": "", "details": "dropped: no title or place"}
  ]}]}
```"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_module.requests, "post", lambda *a, **k: FakeResponse({"choices": [{"message": {"content": reply}}]})
    )
    result = llm_module.suggest_itinerary("Paris", CURRENT, None)
    assert result["truncated"] is False
    (day,) = result["days"]
    assert day["title"] == "Art and a ballgame"
    assert day["events"] == [{"time": "10:00 AM", "title": "Impressionist wing", "place": "Musee d'Orsay, 7th arr.",
                              "category": "museum", "details": "Go early."}]


def test_parse_itinerary_rejects_json_without_events():
    assert llm_module.parse_itinerary('{"days": [{"date": "2026-09-25", "events": []}]}') == (None, False)
    assert llm_module.parse_itinerary("{not json}") == (None, False)


def test_parse_itinerary_recovers_cut_off_reply():
    # Real shape of a reply that hit max_tokens mid-string.
    reply = (
        '{"days": [{"date": "2026-09-25", "title": "Classic Left Bank & Seine", "weather_note": "Warm.", '
        '"events": [{"time": "8:00 AM", "title": "Breakfast at Caf\\u00e9 de Flore", "place": "Caf\\u00e9 de Flore", '
        '"category": "food", "details": "Say \\"bonjour\\" {politely}."}, '
        '{"time": "11:00 AM", "title": "Mus\\u00e9e d\'Orsay", "place": "7th arrondissement", "category": "museum", '
        '"details": "Impressionists."}, '
        '{"time": "5:30 PM", "title": "Seine river cruise", "place": "Bateaux-Mouches, Pont de l\'Alma, 7th arrondissement'
    )
    days, truncated = llm_module.parse_itinerary(reply)
    assert truncated is True
    (day,) = days
    assert [e["time"] for e in day["events"]] == ["8:00 AM", "11:00 AM"]  # the cut-off stop is dropped
    assert day["events"][0]["details"] == 'Say "bonjour" {politely}.'  # braces/quotes in strings are not brackets


def test_unusable_json_reply_raises_instead_of_showing_raw_json(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **k: FakeResponse({"choices": [{"message": {"content": '{"days": [{"date": "2026-09-25", "tit'}}]}),
    )
    with pytest.raises(ItineraryError, match="cut off or malformed"):
        llm_module.suggest_itinerary("Paris", CURRENT, None)


def test_build_prompt_describes_weather_in_words_and_both_units():
    daily = {"time": ["2026-09-25"], "temperature_2m_max": [30.0], "temperature_2m_min": [20.0],
             "precipitation_sum": [4.2], "weathercode": [63]}
    prompt = llm_module.build_prompt("Houston", CURRENT, daily, "America/Chicago")
    assert "Friday 2026-09-25" in prompt
    assert "high 30.0°C / 86.0°F" in prompt
    assert "rain" in prompt and "code 63" not in prompt
    assert "Current local time:" in prompt


def test_suggest_itinerary_wraps_network_errors(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(llm_module.requests, "post", raise_connection_error)
    with pytest.raises(ItineraryError, match="request failed"):
        llm_module.suggest_itinerary("Paris", CURRENT, None)


def test_suggest_itinerary_wraps_malformed_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_module.requests, "post", lambda *a, **k: FakeResponse({"choices": []}))
    with pytest.raises(ItineraryError, match="Unexpected response shape"):
        llm_module.suggest_itinerary("Paris", CURRENT, None)
