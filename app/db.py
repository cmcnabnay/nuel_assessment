import sqlite3

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    country TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    timezone TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (latitude, longitude)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_id INTEGER NOT NULL REFERENCES cities (id),
    pulled_at TEXT NOT NULL,
    temperature REAL NOT NULL,
    windspeed REAL,
    humidity REAL,
    precipitation REAL,
    weathercode INTEGER,
    forecast_json TEXT,
    source TEXT NOT NULL DEFAULT 'live'
);

CREATE INDEX IF NOT EXISTS idx_snapshots_city_time ON snapshots (city_id, pulled_at);
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add them to
# an existing weather.db, so init_db adds any that are missing.
ADDED_SNAPSHOT_COLUMNS = {
    "humidity": "REAL",
    "precipitation": "REAL",
    # 'live' for real pulls, 'backfill' for hourly history loaded by scripts/backfill.py.
    "source": "TEXT NOT NULL DEFAULT 'live'",
}


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
        for column, col_type in ADDED_SNAPSHOT_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE snapshots ADD COLUMN {column} {col_type}")
        conn.commit()
    finally:
        conn.close()
