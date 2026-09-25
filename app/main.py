import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db as db_module
from .config import ALERT_THRESHOLD_C, ROLLING_WINDOW
from .errors import CityNotFoundError, ItineraryError, UpstreamError
from .llm import suggest_itinerary
from .metrics import alert_triggered, change_since_last_pull, min_max, rolling_average
from .pull import pull_city
from .weather_api import search_cities


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_module.init_db()
    yield


app = FastAPI(title="Weather Dashboard", lifespan=lifespan)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def _get_city_row(conn, name):
    return conn.execute(
        "SELECT * FROM cities WHERE lower(query_name) = lower(?)", (name,)
    ).fetchone()


def _snapshots_for_city(conn, city_id, start=None, end=None, limit=None):
    query = "SELECT * FROM snapshots WHERE city_id = ?"
    params = [city_id]
    if start:
        query += " AND pulled_at >= ?"
        params.append(start)
    if end:
        query += " AND pulled_at <= ?"
        params.append(end)
    query += " ORDER BY pulled_at ASC, id ASC"

    rows = conn.execute(query, params).fetchall()
    if limit:
        rows = rows[-limit:]
    return rows


# Snapshot columns that get their own dashboard tab and derived metrics.
SERIES = ("temperature", "precipitation", "windspeed", "humidity")


def _series_metrics(values):
    # Rows stored before a column existed hold NULL; skip them rather than treat as 0.
    values = [v for v in values if v is not None]
    return {
        "change_since_last_pull": change_since_last_pull(values),
        "rolling_average": rolling_average(values, ROLLING_WINDOW),
        "min_max": min_max(values),
    }


def _build_latest_payload(conn, city_row, status="ok", error=None):
    snapshots = _snapshots_for_city(conn, city_row["id"])
    if not snapshots:
        raise HTTPException(status_code=404, detail="No snapshots stored for this city yet.")

    latest_snap = snapshots[-1]
    series = {name: _series_metrics([s[name] for s in snapshots]) for name in SERIES}

    payload = {
        "city": {
            "query_name": city_row["query_name"],
            "display_name": city_row["display_name"],
            "country": city_row["country"],
            "latitude": city_row["latitude"],
            "longitude": city_row["longitude"],
            "timezone": city_row["timezone"],
        },
        "snapshot": {
            "pulled_at": latest_snap["pulled_at"],
            **{name: latest_snap[name] for name in SERIES},
            "weathercode": latest_snap["weathercode"],
        },
        "metrics": {
            "pull_count": len(snapshots),
            "rolling_window": ROLLING_WINDOW,
            "series": series,
            "alert": {
                "threshold_c": ALERT_THRESHOLD_C,
                "triggered": alert_triggered(series["temperature"]["change_since_last_pull"], ALERT_THRESHOLD_C),
            },
        },
        "status": status,
    }
    if error:
        payload["error"] = error
    return payload


@app.get("/api/cities")
def list_cities():
    conn = db_module.get_connection()
    try:
        rows = conn.execute(
            "SELECT query_name, display_name, country, latitude, longitude FROM cities ORDER BY display_name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/search")
def search(q: str = Query(..., min_length=2)):
    """Search-as-you-type suggestions straight from Open-Meteo geocoding (not stored)."""
    try:
        return search_cities(q)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/pull")
def trigger_pull(city: str = Query(..., min_length=1), location_id: Optional[int] = None):
    """Trigger a fresh pull for `city` (geocoding + storing it if new). Falls back to
    the last known-good snapshot with status='stale' if the weather API is down."""
    conn = db_module.get_connection()
    try:
        try:
            city_row, _ = pull_city(conn, city, location_id)
        except CityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except UpstreamError as exc:
            existing = _get_city_row(conn, city)
            if existing:
                return JSONResponse(_build_latest_payload(conn, existing, status="stale", error=str(exc)))
            raise HTTPException(
                status_code=502,
                detail=f"Weather provider is unavailable and no cached data exists yet: {exc}",
            )
        return _build_latest_payload(conn, city_row)
    finally:
        conn.close()


@app.get("/api/latest")
def latest(city: str = Query(..., min_length=1)):
    conn = db_module.get_connection()
    try:
        row = _get_city_row(conn, city)
        if not row:
            raise HTTPException(status_code=404, detail=f"'{city}' has not been pulled yet. POST /api/pull first.")
        return _build_latest_payload(conn, row)
    finally:
        conn.close()


@app.get("/api/history")
def history(
    city: str = Query(..., min_length=1),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Optional[int] = None,
):
    conn = db_module.get_connection()
    try:
        row = _get_city_row(conn, city)
        if not row:
            raise HTTPException(status_code=404, detail=f"'{city}' has not been pulled yet.")
        snaps = _snapshots_for_city(conn, row["id"], start, end, limit)
        return [
            {
                "pulled_at": s["pulled_at"],
                **{name: s[name] for name in SERIES},
                "weathercode": s["weathercode"],
            }
            for s in snaps
        ]
    finally:
        conn.close()


@app.get("/api/forecast")
def forecast(city: str = Query(..., min_length=1)):
    """Hourly forecast stored with the most recent live pull, from that pull onward.
    Like /api/history this only reads the DB; a refresh (POST /api/pull) updates it."""
    conn = db_module.get_connection()
    try:
        row = _get_city_row(conn, city)
        if not row:
            raise HTTPException(status_code=404, detail=f"'{city}' has not been pulled yet.")

        snap = conn.execute(
            """
            SELECT pulled_at, forecast_hourly_json FROM snapshots
            WHERE city_id = ? AND forecast_hourly_json IS NOT NULL
            ORDER BY pulled_at DESC, id DESC LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        if not snap:
            return []

        # Keep the hour containing the pull so the forecast joins up with the history.
        pull_hour = snap["pulled_at"][:13]
        return [
            {"time": h["time"], **{name: h[name] for name in SERIES}}
            for h in json.loads(snap["forecast_hourly_json"])
            if h["time"][:13] >= pull_hour
        ]
    finally:
        conn.close()


@app.get("/api/itinerary")
def itinerary(city: str = Query(..., min_length=1)):
    """Suggest an itinerary from the most recently stored forecast. Reads from the
    DB rather than re-hitting Open-Meteo, since /api/pull already cached the daily
    forecast alongside the snapshot."""
    conn = db_module.get_connection()
    try:
        row = _get_city_row(conn, city)
        if not row:
            raise HTTPException(status_code=404, detail=f"'{city}' has not been pulled yet.")

        snap = conn.execute(
            "SELECT * FROM snapshots WHERE city_id = ? ORDER BY pulled_at DESC, id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if not snap:
            raise HTTPException(status_code=404, detail="No snapshot available for itinerary generation.")

        daily = json.loads(snap["forecast_json"]) if snap["forecast_json"] else None
        current = {
            "temperature": snap["temperature"],
            "windspeed": snap["windspeed"],
            "weathercode": snap["weathercode"],
        }
        try:
            result = suggest_itinerary(row["display_name"], current, daily, row["timezone"])
        except ItineraryError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

        # Either "days" (structured itinerary) or "text" (model's raw reply) is set.
        return {"city": row["display_name"], "days": result.get("days"), "text": result.get("text")}
    finally:
        conn.close()
