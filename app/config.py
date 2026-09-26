import os

from dotenv import load_dotenv

load_dotenv()

# Hosted Turso database, shared by every instance. When the URL is set the app uses it
# instead of the local SQLite file (see db.get_connection).
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

# Local SQLite file, used when Turso isn't configured. Vercel's filesystem is read-only
# apart from /tmp (which doesn't persist between instances), so default there when deployed.
DB_PATH = os.environ.get("DB_PATH", "/tmp/weather.db" if os.environ.get("VERCEL") else "weather.db")
ROLLING_WINDOW = int(os.environ.get("ROLLING_WINDOW", "5"))
ALERT_THRESHOLD_C = float(os.environ.get("ALERT_THRESHOLD_C", "5"))
