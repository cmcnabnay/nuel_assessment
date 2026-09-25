"""Load hourly history for tracked cities from a start date up to each city's latest
pull, filling hours that have no snapshot. Backfilled rows are tagged
source='backfill' and can be removed with:

    DELETE FROM snapshots WHERE source = 'backfill';

Usage:
    python scripts/backfill.py 2026-09-17                    # every tracked city
    python scripts/backfill.py 2026-09-17 Houston Detroit    # specific cities
    python scripts/backfill.py 2026-09-17 --dry-run          # count rows, write nothing
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as db_module  # noqa: E402
from app.backfill import backfill_city  # noqa: E402
from app.errors import UpstreamError  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        return
    since = date.fromisoformat(args[0])
    names = args[1:]

    db_module.init_db()
    conn = db_module.get_connection()
    try:
        if names:
            placeholders = ",".join("?" * len(names))
            cities = conn.execute(
                f"SELECT * FROM cities WHERE lower(query_name) IN ({placeholders})",
                [n.lower() for n in names],
            ).fetchall()
        else:
            cities = conn.execute("SELECT * FROM cities ORDER BY id").fetchall()

        total = 0
        for city in cities:
            try:
                n = backfill_city(conn, city, since, dry_run=dry_run)
            except UpstreamError as exc:
                print(f"Skipping {city['query_name']}: {exc}")
                continue
            total += n
            print(f"{city['query_name']}: {n} hourly rows {'would be ' if dry_run else ''}added")
        print(f"Total: {total}{' (dry run, nothing written)' if dry_run else ''}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
