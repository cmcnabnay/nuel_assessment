import os

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", "weather.db")
ROLLING_WINDOW = int(os.environ.get("ROLLING_WINDOW", "5"))
ALERT_THRESHOLD_C = float(os.environ.get("ALERT_THRESHOLD_C", "5"))
