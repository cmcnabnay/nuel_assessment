import sqlite3

from .config import DB_PATH, TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

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
    forecast_hourly_json TEXT,
    is_day INTEGER,
    source TEXT NOT NULL DEFAULT 'live'
);

CREATE INDEX IF NOT EXISTS idx_snapshots_city_time ON snapshots (city_id, pulled_at);

-- Itineraries the user chose to keep. Exactly one of days_json (structured) or text
-- (the model's prose reply) is set.
CREATE TABLE IF NOT EXISTS saved_itineraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_id INTEGER NOT NULL REFERENCES cities (id),
    saved_at TEXT NOT NULL,
    days_json TEXT,
    text TEXT
);

CREATE INDEX IF NOT EXISTS idx_saved_itineraries_city ON saved_itineraries (city_id, saved_at);
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add them to
# an existing weather.db, so init_db adds any that are missing.
ADDED_SNAPSHOT_COLUMNS = {
    "humidity": "REAL",
    "precipitation": "REAL",
    # 'live' for real pulls, 'backfill' for hourly history loaded by scripts/backfill.py.
    "source": "TEXT NOT NULL DEFAULT 'live'",
    # Next ~3 days of hourly readings fetched with a live pull, for the forecast chart.
    "forecast_hourly_json": "TEXT",
    # 1 if the sun was up at pull time (Open-Meteo), for the page's day/night sky.
    "is_day": "INTEGER",
}


class _Row:
    """Stands in for sqlite3.Row on Turso connections: row["col"], row[0] and dict(row)."""

    __slots__ = ("_keys", "_values")

    def __init__(self, keys, values):
        self._keys = keys
        self._values = values

    def keys(self):
        return list(self._keys)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._keys.index(key)]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class _TursoCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._keys = tuple(d[0] for d in cursor.description or ())
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        return _Row(self._keys, row) if row is not None else None

    def fetchall(self):
        return [_Row(self._keys, r) for r in self._cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _TursoConnection:
    """The slice of the sqlite3.Connection API the app uses, over a libsql connection
    to Turso, returning rows that behave like sqlite3.Row."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _TursoCursor(self._conn.execute(sql, params))

    def executemany(self, sql, seq_of_params):
        return self._conn.executemany(sql, seq_of_params)

    def executescript(self, script):
        return self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_connection(db_path=None):
    # An explicit path (tests, scripts) always means a local SQLite file.
    if db_path is None and TURSO_DATABASE_URL:
        import libsql  # only needed when Turso is configured

        return _TursoConnection(libsql.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN or ""))
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
