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


def test_suggest_itinerary_returns_stripped_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **k: FakeResponse({"choices": [{"message": {"content": "  Day 1: walk.  "}}]}),
    )
    result = llm_module.suggest_itinerary("Paris", CURRENT, None)
    assert result == "Day 1: walk."


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
