from datetime import date

import pytest

from app import backfill as backfill_module
from app import db as db_module


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    db_module.init_db(path)
    connection = db_module.get_connection(path)
    connection.execute(
        "INSERT INTO cities (id, query_name, display_name, latitude, longitude, created_at) "
        "VALUES (1, 'Houston', 'Houston', 29.76, -95.36, '2026-09-18T00:00:00+00:00')"
    )
    # One live pull at 02:30 on 9/18.
    connection.execute(
        "INSERT INTO snapshots (city_id, pulled_at, temperature) VALUES (1, '2026-09-18T02:30:00.000000+00:00', 30.0)"
    )
    connection.commit()
    yield connection
    connection.close()


def hourly(times):
    return [
        {"time": t, "temperature": 20.0, "humidity": 50, "precipitation": 0.0, "windspeed": 5.0, "weathercode": 0}
        for t in times
    ]


def test_backfill_fills_hours_before_latest_pull_only(conn, monkeypatch):
    times = [f"2026-09-18T{h:02d}:00" for h in range(6)]
    monkeypatch.setattr(backfill_module, "fetch_hourly_history", lambda *a: hourly(times))
    city = conn.execute("SELECT * FROM cities").fetchone()

    added = backfill_module.backfill_city(conn, city, date(2026, 9, 18))

    # 00:00 and 01:00 are added; 02:00 is covered by the live pull; 03:00+ is after it.
    assert added == 2
    rows = conn.execute("SELECT pulled_at, source FROM snapshots ORDER BY pulled_at").fetchall()
    assert [(r["pulled_at"], r["source"]) for r in rows] == [
        ("2026-09-18T00:00:00.000000+00:00", "backfill"),
        ("2026-09-18T01:00:00.000000+00:00", "backfill"),
        ("2026-09-18T02:30:00.000000+00:00", "live"),
    ]


def test_backfill_is_idempotent_and_dry_run_writes_nothing(conn, monkeypatch):
    times = ["2026-09-18T00:00", "2026-09-18T01:00"]
    monkeypatch.setattr(backfill_module, "fetch_hourly_history", lambda *a: hourly(times))
    city = conn.execute("SELECT * FROM cities").fetchone()

    assert backfill_module.backfill_city(conn, city, date(2026, 9, 18), dry_run=True) == 2
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1

    backfill_module.backfill_city(conn, city, date(2026, 9, 18))
    assert backfill_module.backfill_city(conn, city, date(2026, 9, 18)) == 0


def test_backfill_starts_at_local_midnight(tmp_path, monkeypatch):
    path = str(tmp_path / "tz.db")
    db_module.init_db(path)
    conn = db_module.get_connection(path)
    conn.execute(
        "INSERT INTO cities (id, query_name, display_name, latitude, longitude, timezone, created_at) "
        "VALUES (1, 'Tokyo', 'Tokyo', 35.7, 139.7, 'Asia/Tokyo', '2026-09-18T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO snapshots (city_id, pulled_at, temperature) VALUES (1, '2026-09-18T00:30:00.000000+00:00', 25.0)"
    )
    conn.commit()
    requested = []

    def fake_history(lat, lon, start_date, end_date):
        requested.append(start_date)
        return hourly([f"2026-09-17T{h:02d}:00" for h in range(13, 17)])

    monkeypatch.setattr(backfill_module, "fetch_hourly_history", fake_history)
    city = conn.execute("SELECT * FROM cities").fetchone()

    backfill_module.backfill_city(conn, city, date(2026, 9, 18))

    # 9/18 00:00 in Tokyo (UTC+9) is 9/17 15:00 UTC, so history is requested from 9/17
    # and 13:00/14:00 UTC (still 9/17 locally) are skipped.
    assert requested == ["2026-09-17"]
    times = [r[0] for r in conn.execute("SELECT pulled_at FROM snapshots WHERE source = 'backfill' ORDER BY pulled_at")]
    assert times == ["2026-09-17T15:00:00.000000+00:00", "2026-09-17T16:00:00.000000+00:00"]
    conn.close()


def test_trim_removes_only_backfill_rows_before_local_midnight(conn):
    # Houston is UTC-5 in September, so local midnight 9/18 is 05:00 UTC.
    conn.execute("UPDATE cities SET timezone = 'America/Chicago'")
    conn.executemany(
        "INSERT INTO snapshots (city_id, pulled_at, temperature, source) VALUES (1, ?, 20.0, ?)",
        [
            ("2026-09-18T01:00:00.000000+00:00", "backfill"),
            ("2026-09-18T01:30:00.000000+00:00", "live"),
            ("2026-09-18T05:00:00.000000+00:00", "backfill"),
        ],
    )
    city = conn.execute("SELECT * FROM cities").fetchone()

    assert backfill_module.trim_backfill_before(conn, city, date(2026, 9, 18)) == 1
    remaining = conn.execute("SELECT pulled_at, source FROM snapshots ORDER BY pulled_at").fetchall()
    assert ("2026-09-18T01:00:00.000000+00:00", "backfill") not in [tuple(r) for r in remaining]
    assert len(remaining) == 3
