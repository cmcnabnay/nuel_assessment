import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import pull as pull_module

PARIS_GEOCODE = {
    "display_name": "Paris",
    "country": "France",
    "latitude": 48.85,
    "longitude": 2.35,
    "timezone": "Europe/Paris",
}


def make_weather(temperature):
    return {
        "temperature": temperature,
        "windspeed": 5.0,
        "humidity": 60.0,
        "precipitation": 0.0,
        "weathercode": 0,
        "daily": {
            "time": ["2026-09-22"],
            "temperature_2m_max": [temperature + 3],
            "temperature_2m_min": [temperature - 3],
            "precipitation_sum": [0],
            "weathercode": [0],
        },
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api_test.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    from app.main import app  # imported after DB_PATH is patched

    db_module.init_db(db_path)
    return TestClient(app)


def test_pull_then_latest_and_history(client, monkeypatch):
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: PARIS_GEOCODE)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(20.0))

    first = client.post("/api/pull", params={"city": "Paris"})
    assert first.status_code == 200
    body = first.json()
    assert body["snapshot"]["temperature"] == 20.0
    assert body["metrics"]["series"]["temperature"]["change_since_last_pull"] is None  # only one pull so far
    assert body["snapshot"]["humidity"] == 60.0
    assert body["status"] == "ok"

    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(23.0))
    second = client.post("/api/pull", params={"city": "Paris"})
    body2 = second.json()
    assert body2["metrics"]["series"]["temperature"]["change_since_last_pull"] == {"absolute": 3.0, "percent": 15.0}
    assert body2["metrics"]["series"]["humidity"]["change_since_last_pull"] == {"absolute": 0.0, "percent": 0.0}
    assert body2["metrics"]["pull_count"] == 2

    hist = client.get("/api/history", params={"city": "Paris"})
    assert hist.status_code == 200
    assert [r["temperature"] for r in hist.json()] == [20.0, 23.0]


def test_pull_of_unknown_city_returns_404(client, monkeypatch):
    from app.errors import CityNotFoundError

    def raise_not_found(name):
        raise CityNotFoundError(f"No location found for '{name}'")

    monkeypatch.setattr(pull_module, "geocode_city", raise_not_found)
    resp = client.post("/api/pull", params={"city": "Nowhereville"})
    assert resp.status_code == 404


def test_upstream_failure_falls_back_to_last_known_good_snapshot(client, monkeypatch):
    from app.errors import UpstreamError

    monkeypatch.setattr(pull_module, "geocode_city", lambda name: PARIS_GEOCODE)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(18.0))
    good = client.post("/api/pull", params={"city": "Paris"})
    assert good.status_code == 200

    def raise_upstream(lat, lon, tz):
        raise UpstreamError("provider down")

    monkeypatch.setattr(pull_module, "fetch_weather", raise_upstream)
    stale = client.post("/api/pull", params={"city": "Paris"})

    assert stale.status_code == 200
    body = stale.json()
    assert body["status"] == "stale"
    assert body["snapshot"]["temperature"] == 18.0  # still last known good value
    assert "error" in body


def test_latest_without_prior_pull_returns_404(client):
    resp = client.get("/api/latest", params={"city": "Nowhere"})
    assert resp.status_code == 404


def test_itinerary_without_api_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: PARIS_GEOCODE)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(20.0))
    client.post("/api/pull", params={"city": "Paris"})

    resp = client.get("/api/itinerary", params={"city": "Paris"})
    assert resp.status_code == 503
    assert "error" in resp.json()


def test_series_metrics_skip_rows_missing_a_column(client, monkeypatch):
    """Snapshots stored before humidity/precipitation existed have NULLs; metrics should ignore them."""
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: PARIS_GEOCODE)
    old = make_weather(20.0)
    del old["humidity"], old["precipitation"]
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: old)
    client.post("/api/pull", params={"city": "Paris"})

    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(21.0))
    body = client.post("/api/pull", params={"city": "Paris"}).json()

    humidity = body["metrics"]["series"]["humidity"]
    assert humidity["change_since_last_pull"] is None  # only one non-null reading
    assert humidity["rolling_average"] == 60.0
    assert body["metrics"]["series"]["temperature"]["change_since_last_pull"]["absolute"] == 1.0


def test_search_returns_suggestions(client, monkeypatch):
    from app import main as main_module

    results = [{"id": 1, "display_name": "Paris", "region": "Texas", "country": "United States",
                "latitude": 33.66, "longitude": -95.55, "timezone": "America/Chicago"}]
    monkeypatch.setattr(main_module, "search_cities", lambda q: results)

    resp = client.get("/api/search", params={"q": "Par"})
    assert resp.status_code == 200
    assert resp.json() == results


def test_search_upstream_failure_returns_502(client, monkeypatch):
    from app import main as main_module
    from app.errors import UpstreamError

    def raise_upstream(q):
        raise UpstreamError("geocoder down")

    monkeypatch.setattr(main_module, "search_cities", raise_upstream)
    assert client.get("/api/search", params={"q": "Par"}).status_code == 502


def test_pull_with_location_id_uses_exact_match(client, monkeypatch):
    texas = dict(PARIS_GEOCODE, country="United States", latitude=33.66, longitude=-95.55)
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: PARIS_GEOCODE)
    monkeypatch.setattr(pull_module, "geocode_by_id", lambda location_id: texas)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(30.0))

    body = client.post("/api/pull", params={"city": "Paris, Texas", "location_id": 4717560}).json()
    assert body["city"]["country"] == "United States"
    assert body["city"]["query_name"] == "Paris, Texas"
