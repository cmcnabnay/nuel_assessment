import os

from dotenv import load_dotenv

load_dotenv()

# Vercel's filesystem is read-only apart from /tmp (which doesn't persist between
# instances), so default there when deployed. Set DB_PATH to override either way.
DB_PATH = os.environ.get("DB_PATH", "/tmp/weather.db" if os.environ.get("VERCEL") else "weather.db")
ROLLING_WINDOW = int(os.environ.get("ROLLING_WINDOW", "5"))
ALERT_THRESHOLD_C = float(os.environ.get("ALERT_THRESHOLD_C", "5"))
