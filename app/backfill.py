from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_api import fetch_hourly_history


def _city_zone(city):
    """The city's own tzinfo, falling back to UTC if it has no usable timezone
    (geocoder returned "auto") or ZoneInfo doesn't recognize it."""
    try:
        return ZoneInfo(city["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


def local_midnight_utc(city, day):
    """00:00 on `day` in the city's own timezone, as a UTC datetime."""
    return datetime.combine(day, time(0), tzinfo=_city_zone(city)).astimezone(timezone.utc)


def local_today(city):
    """Today's date in the city's own timezone, right now."""
    return datetime.now(_city_zone(city)).date()


def trim_backfill_before(conn, city, since, dry_run=False):
    """Delete backfilled rows earlier than local midnight of `since`. Live pulls are
    never touched. Returns the number of rows removed (or that would be)."""
    cutoff = local_midnight_utc(city, since).isoformat(timespec="microseconds")
    where = "city_id = ? AND source = 'backfill' AND pulled_at < ?"
    count = conn.execute(f"SELECT COUNT(*) FROM snapshots WHERE {where}", (city["id"], cutoff)).fetchone()[0]
    if count and not dry_run:
        conn.execute(f"DELETE FROM snapshots WHERE {where}", (city["id"], cutoff))
        conn.commit()
    return count


def backfill_city(conn, city, since, dry_run=False):
    """Insert hourly history for `city` from local midnight on `since` (a date, in the
    city's timezone) up to its latest snapshot.

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
    start = local_midnight_utc(city, since)
    if latest <= start:
        return 0
    covered_hours = {row["pulled_at"][:13] for row in existing}  # "YYYY-MM-DDTHH" (UTC)

    # History is requested in UTC, so ask from the UTC date that local midnight falls on
    # (the day before, for cities east of UTC).
    readings = fetch_hourly_history(
        city["latitude"], city["longitude"], start.date().isoformat(), latest.date().isoformat()
    )

    rows = []
    for r in readings:
        if r["temperature"] is None:
            continue
        at = datetime.fromisoformat(r["time"]).replace(tzinfo=timezone.utc)
        if at < start or at >= latest or r["time"][:13] in covered_hours:
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
