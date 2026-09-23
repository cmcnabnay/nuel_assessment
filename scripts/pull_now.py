"""Simulate a scheduled pull: re-pulls weather for every city already tracked in
the database (or specific cities passed as arguments), storing a new snapshot for
each. Meant to be re-run manually (e.g. from cron) to accumulate history over time.

Usage:
    python scripts/pull_now.py                 # refresh every tracked city
    python scripts/pull_now.py London Tokyo     # refresh (or add) specific cities
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as db_module  # noqa: E402
from app.errors import CityNotFoundError, UpstreamError  # noqa: E402
from app.pull import pull_city  # noqa: E402


def main():
    db_module.init_db()
    conn = db_module.get_connection()
    try:
        if len(sys.argv) > 1:
            names = sys.argv[1:]
        else:
            rows = conn.execute("SELECT query_name FROM cities").fetchall()
            names = [r["query_name"] for r in rows]
            if not names:
                print("No tracked cities yet. Usage: python scripts/pull_now.py <city> [<city> ...]")
                return

        for name in names:
            try:
                city, pulled_at = pull_city(conn, name)
                print(f"Pulled {city['display_name']} at {pulled_at}")
            except CityNotFoundError as exc:
                print(f"Skipping '{name}': {exc}")
            except UpstreamError as exc:
                print(f"Pull failed for '{name}' (leaving prior history intact): {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
