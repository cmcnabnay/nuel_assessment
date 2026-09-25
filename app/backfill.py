from datetime import datetime, timezone

from .weather_api import fetch_hourly_history


def backfill_city(conn, city, since, dry_run=False):
    """Insert hourly history for `city` from `since` (a date) up to its latest snapshot.

    Hours that already have a snapshot are skipped, and nothing is added after the
    latest snapshot, so the newest row is always a real pull (with its forecast for
    the itinerary). Rows are tagged source='backfill'. Returns the number inserted
    (or that would be, with dry_run).
    """
    existing = conn.execute(
        "SELECT pulled_at FROM snapshots WHERE city_id = ? ORDER BY pulled_at", (city["id"],)
    ).fetchall()
    if not existing:
        return 0
    latest = datetime.fromisoformat(existing[-1]["pulled_at"])
    if latest.date() < since:
        return 0
    covered_hours = {row["pulled_at"][:13] for row in existing}  # "YYYY-MM-DDTHH"

    readings = fetch_hourly_history(
        city["latitude"], city["longitude"], since.isoformat(), latest.date().isoformat()
    )

    rows = []
    for r in readings:
        if r["temperature"] is None:
            continue
        at = datetime.fromisoformat(r["time"]).replace(tzinfo=timezone.utc)
        if at >= latest or r["time"][:13] in covered_hours:
            continue
        rows.append((
            city["id"],
            at.isoformat(timespec="microseconds"),  # same format as live pulls, so ordering holds
            r["temperature"],
            r["windspeed"],
            r["humidity"],
            r["precipitation"],
            r["weathercode"],
        ))

    if rows and not dry_run:
        conn.executemany(
            """
            INSERT INTO snapshots
                (city_id, pulled_at, temperature, windspeed, humidity, precipitation, weathercode, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'backfill')
            """,
            rows,
        )
        conn.commit()
    return len(rows)
