import pytest

from app import db as db_module
from app import pull as pull_module
from app.errors import CityNotFoundError, UpstreamError

LONDON_GEOCODE = {
    "display_name": "London",
    "country": "United Kingdom",
    "latitude": 51.5,
    "longitude": -0.12,
    "timezone": "Europe/London",
}


def make_weather(temperature):
    return {
        "temperature": temperature,
        "windspeed": 10.0,
        "humidity": 60.0,
        "precipitation": 0.0,
        "weathercode": 1,
        "daily": {
            "time": ["2026-09-22"],
            "temperature_2m_max": [temperature + 2],
            "temperature_2m_min": [temperature - 2],
            "precipitation_sum": [0],
            "weathercode": [1],
        },
    }


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db_module.init_db(path)
    connection = db_module.get_connection(path)
    yield connection
    connection.close()


def test_pull_city_creates_city_and_snapshot(conn, monkeypatch):
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: LONDON_GEOCODE)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(15.0))

    city, pulled_at = pull_module.pull_city(conn, "London")

    assert city["display_name"] == "London"
    assert pulled_at
    snapshots = conn.execute("SELECT * FROM snapshots WHERE city_id = ?", (city["id"],)).fetchall()
    assert len(snapshots) == 1
    assert snapshots[0]["temperature"] == 15.0


def test_pull_city_reuses_existing_city_row(conn, monkeypatch):
    geocode_calls = []
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: geocode_calls.append(name) or LONDON_GEOCODE)
    monkeypatch.setattr(pull_module, "fetch_weather", lambda lat, lon, tz: make_weather(15.0))

    pull_module.pull_city(conn, "London")
    pull_module.pull_city(conn, "London")

    cities = conn.execute("SELECT * FROM cities").fetchall()
    assert len(cities) == 1
    assert len(geocode_calls) == 1  # second pull should reuse the cached city row

    snapshots = conn.execute("SELECT * FROM snapshots").fetchall()
    assert len(snapshots) == 2


def test_pull_city_propagates_city_not_found(conn, monkeypatch):
    def raise_not_found(name):
        raise CityNotFoundError(f"No location found for '{name}'")

    monkeypatch.setattr(pull_module, "geocode_city", raise_not_found)

    with pytest.raises(CityNotFoundError):
        pull_module.pull_city(conn, "Nowhereville")

    assert conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0] == 0


def test_pull_city_propagates_upstream_error_without_writing_partial_snapshot(conn, monkeypatch):
    monkeypatch.setattr(pull_module, "geocode_city", lambda name: LONDON_GEOCODE)

    def raise_upstream(lat, lon, tz):
        raise UpstreamError("timed out")

    monkeypatch.setattr(pull_module, "fetch_weather", raise_upstream)

    with pytest.raises(UpstreamError):
        pull_module.pull_city(conn, "London")

    # the city row may have been created, but no snapshot should exist
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0


def test_init_db_adds_new_columns_to_existing_database(tmp_path):
    path = str(tmp_path / "old.db")
    old = db_module.get_connection(path)
    old.execute(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY, city_id INTEGER, pulled_at TEXT, "
        "temperature REAL, windspeed REAL, weathercode INTEGER, forecast_json TEXT)"
    )
    old.close()

    db_module.init_db(path)

    conn = db_module.get_connection(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
    conn.close()
    assert {"humidity", "precipitation"} <= columns


def test_geocode_by_id_maps_unknown_id_to_city_not_found(monkeypatch):
    from app import weather_api

    class Resp:
        status_code = 400

        def raise_for_status(self):
            raise AssertionError("should not be reached")

    monkeypatch.setattr(weather_api.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(CityNotFoundError):
        weather_api.geocode_by_id(999999999)
