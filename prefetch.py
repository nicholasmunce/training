"""
One-time prefetch script — run this after /sync to pre-cache activity details.
Respects Strava's 100 req/15min rate limit with a 10s delay between requests.

Usage:
    python prefetch.py           # prefetch details only (recommended)
    python prefetch.py --streams # also prefetch streams (slow, many API calls)
"""
import sys
import time
import json
import sqlite3
from app import StravaAPI

DELAY = 10  # seconds between requests — stays well under 100 req/15min

def count_cached(db_name, table, id_col):
    with sqlite3.connect(db_name) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

def main():
    fetch_streams = "--streams" in sys.argv
    strava = StravaAPI()

    # Load activity list from DB (must have run /sync first)
    activities = strava.get_activities()
    if not activities:
        print("No activities in DB. Hit /sync in the app first, then re-run this.")
        return

    total = len(activities)
    print(f"Found {total} activities in DB.")
    print(f"Mode: {'details + streams' if fetch_streams else 'details only'}")
    print(f"Delay: {DELAY}s between requests\n")

    # --- Details ---
    details_cached = count_cached(strava.db_name, "activity_details", "activity_id")
    to_fetch = [a for a in activities]
    already_cached = {
        row[0] for row in sqlite3.connect(strava.db_name).execute(
            "SELECT activity_id FROM activity_details"
        ).fetchall()
    }
    to_fetch_details = [a for a in activities if str(a["id"]) not in already_cached]

    print(f"Details: {details_cached}/{total} already cached, {len(to_fetch_details)} to fetch.")

    for i, activity in enumerate(to_fetch_details, 1):
        aid = activity["id"]
        name = activity.get("name", aid)
        print(f"  [{i}/{len(to_fetch_details)}] Fetching detail: {name} ({aid})")
        result = strava.get_activity_detail(aid)
        if result is None:
            print(f"    ⚠ Failed (rate limited or missing). Waiting 60s...")
            time.sleep(60)
        else:
            print(f"    ✓")
            if i < len(to_fetch_details):
                time.sleep(DELAY)

    print(f"\nDetails done.")

    # --- Streams (optional) ---
    if fetch_streams:
        already_streams = {
            row[0] for row in sqlite3.connect(strava.db_name).execute(
                "SELECT activity_id FROM activity_streams"
            ).fetchall()
        }
        to_fetch_streams = [a for a in activities if str(a["id"]) not in already_streams]
        streams_cached = total - len(to_fetch_streams)
        print(f"\nStreams: {streams_cached}/{total} already cached, {len(to_fetch_streams)} to fetch.")
        print(f"Estimated time: ~{len(to_fetch_streams) * DELAY // 60} minutes\n")

        for i, activity in enumerate(to_fetch_streams, 1):
            aid = activity["id"]
            name = activity.get("name", aid)
            print(f"  [{i}/{len(to_fetch_streams)}] Fetching streams: {name} ({aid})")
            result = strava.get_activity_streams(aid)
            if result is None:
                print(f"    ⚠ Failed (rate limited or no GPS data). Waiting 60s...")
                time.sleep(60)
            else:
                print(f"    ✓")
                if i < len(to_fetch_streams):
                    time.sleep(DELAY)

        print(f"\nStreams done.")

    print("\nAll done! Start the app with: python app.py")

if __name__ == "__main__":
    main()
