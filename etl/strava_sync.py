#!/usr/bin/env python3
"""Sync Strava activities into ironman.activities.

Setup (one time):
  1. https://www.strava.com/settings/api -> create app, note client_id/secret.
  2. Authorize in browser (scope activity:read_all):
     https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=activity:read_all
     Copy `code` from redirect URL.
  3. Exchange once:
     curl -X POST https://www.strava.com/oauth/token \
       -d client_id=... -d client_secret=... -d code=... -d grant_type=authorization_code
     Save refresh_token.
  4. export STRAVA_CLIENT_ID=... STRAVA_CLIENT_SECRET=... STRAVA_REFRESH_TOKEN=...
     (or put them in ~/.ironman.env and `source` it)

Run:  .venv/bin/python etl/strava_sync.py [--days 30]
Idempotent: upserts on (source='strava', external_id).
"""
import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

SPORT_MAP = {
    "Swim": "swim", "Ride": "bike", "VirtualRide": "bike", "GravelRide": "bike",
    "Run": "run", "TrailRun": "run", "VirtualRun": "run",
    "WeightTraining": "strength", "Workout": "strength", "Yoga": "strength",
}


def access_token():
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def thresholds(con):
    row = con.execute(
        """SELECT ftp_watts, run_threshold_pace_s_km, css_pace_s_100m, lthr_bike, lthr_run
           FROM athlete_profile ORDER BY test_date DESC LIMIT 1"""
    ).fetchone()
    return row or (None, None, None, None, None)


def compute_tss(sport, dur_s, np_w, avg_hr, pace_s_km, pace_s_100m, thr):
    """TSS: power-based (bike), pace-based (run/swim), HR fallback."""
    ftp, run_thr, css, lthr_bike, lthr_run = thr
    hours = dur_s / 3600.0
    if sport == "bike" and np_w and ftp:
        intensity = np_w / ftp
        return round(hours * intensity * intensity * 100, 1), round(intensity, 2)
    if sport == "run" and pace_s_km and run_thr:
        intensity = run_thr / pace_s_km          # faster than threshold -> IF > 1
        return round(hours * intensity ** 2 * 100, 1), round(intensity, 2)
    if sport == "swim" and pace_s_100m and css:
        intensity = css / pace_s_100m
        return round(hours * intensity ** 3 * 100, 1), round(intensity, 2)
    lthr = lthr_bike if sport == "bike" else lthr_run
    if avg_hr and lthr:
        intensity = avg_hr / lthr
        return round(hours * intensity * intensity * 100, 1), round(intensity, 2)
    return round(hours * 50, 1), None            # crude fallback: 50 TSS/h


def main(days):
    tok = access_token()
    con = connect()
    thr = thresholds(con)
    after = int(time.time()) - days * 86400
    page, upserts = 1, 0
    while True:
        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {tok}"},
            params={"after": after, "per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for a in batch:
            sport = SPORT_MAP.get(a.get("sport_type") or a.get("type"), "other")
            dist = a.get("distance") or 0
            mov = a.get("moving_time") or a.get("elapsed_time")
            pace_km = (mov / (dist / 1000)) if sport == "run" and dist else None
            pace_100 = (mov / (dist / 100)) if sport == "swim" and dist else None
            np_w = a.get("weighted_average_watts")
            tss, if_ = compute_tss(sport, mov, np_w, a.get("average_heartrate"),
                                   pace_km, pace_100, thr)
            con.execute(
                """INSERT INTO activities
                   (source, external_id, sport, start_time, duration_s, moving_s,
                    distance_m, elevation_gain_m, avg_hr, max_hr, avg_power_w,
                    norm_power_w, avg_cadence, avg_pace_s_km, avg_pace_s_100m,
                    intensity_factor, tss, calories, title, raw_json)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source, external_id) DO UPDATE SET
                     tss = EXCLUDED.tss, intensity_factor = EXCLUDED.intensity_factor,
                     avg_hr = EXCLUDED.avg_hr, raw_json = EXCLUDED.raw_json""",
                ("strava", str(a["id"]), sport, a["start_date_local"],
                 a.get("elapsed_time"), mov, dist, a.get("total_elevation_gain"),
                 a.get("average_heartrate"), a.get("max_heartrate"),
                 a.get("average_watts"), np_w, a.get("average_cadence"),
                 pace_km, pace_100, if_, tss, a.get("calories"),
                 a.get("name"), json.dumps(a)))
            upserts += 1
        page += 1
    con.commit()
    con.close()
    print(f"Upserted {upserts} Strava activities (last {days} days)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    main(p.parse_args().days)
