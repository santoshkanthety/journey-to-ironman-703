#!/usr/bin/env python3
"""Import Apple Health export into ironman schema.

Get the export: iPhone Health app -> profile photo -> Export All Health Data
-> AirDrop export.zip to Mac.

Run:  .venv/bin/python etl/apple_health_import.py ~/Downloads/export.zip
      (also accepts an already-extracted export.xml path)

Loads:
  daily_vitals  <- RestingHeartRate, HeartRateVariabilitySDNN, SleepAnalysis,
                   BodyMass, BodyFatPercentage, OxygenSaturation, VO2Max,
                   StepCount, ActiveEnergyBurned
  activities    <- Workouts (source='apple'), skipped when a Strava activity
                   already exists within 10 min of the same start (dedupe).
Idempotent: vitals upsert by date; workouts upsert on (source, external_id).
"""
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

RECORD_MAP = {
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv_ms",
    "HKQuantityTypeIdentifierBodyMass": "weight_kg",
    "HKQuantityTypeIdentifierBodyFatPercentage": "body_fat_pct",
    "HKQuantityTypeIdentifierOxygenSaturation": "spo2_pct",
    "HKQuantityTypeIdentifierVO2Max": "vo2max_est",
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_kcal",
}
SUM_FIELDS = {"steps", "active_kcal"}
PCT_FIELDS = {"body_fat_pct", "spo2_pct"}

WORKOUT_MAP = {
    "HKWorkoutActivityTypeSwimming": "swim",
    "HKWorkoutActivityTypeCycling": "bike",
    "HKWorkoutActivityTypeRunning": "run",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "strength",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "strength",
    "HKWorkoutActivityTypeYoga": "strength",
}


def open_xml(path):
    if path.endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.endswith("export.xml"))
        return z.open(name)
    return open(path, "rb")


def main(path):
    daily = defaultdict(dict)          # date -> {col: [values]}
    sleep = defaultdict(float)         # date -> asleep hours
    deep = defaultdict(float)
    workouts = []

    for _, el in ET.iterparse(open_xml(path), events=("end",)):
        if el.tag == "Record":
            rtype = el.get("type")
            d = el.get("startDate", "")[:10]
            if rtype in RECORD_MAP:
                col = RECORD_MAP[rtype]
                try:
                    v = float(el.get("value"))
                except (TypeError, ValueError):
                    el.clear(); continue
                if col in PCT_FIELDS and v <= 1:
                    v *= 100
                daily[d].setdefault(col, []).append(v)
            elif rtype == "HKCategoryTypeIdentifierSleepAnalysis":
                val = el.get("value", "")
                if "Asleep" in val:
                    try:
                        s = datetime.fromisoformat(el.get("startDate"))
                        e = datetime.fromisoformat(el.get("endDate"))
                        # count sleep toward wake-day
                        day = e.date().isoformat()
                        hrs = (e - s).total_seconds() / 3600
                        sleep[day] += hrs
                        if "Deep" in val:
                            deep[day] += hrs
                    except ValueError:
                        pass
            el.clear()
        elif el.tag == "Workout":
            sport = WORKOUT_MAP.get(el.get("workoutActivityType"))
            if sport:
                stats = {s.get("type"): s for s in el.findall("WorkoutStatistics")}
                dist_el = stats.get("HKQuantityTypeIdentifierDistanceWalkingRunning") \
                    or stats.get("HKQuantityTypeIdentifierDistanceCycling") \
                    or stats.get("HKQuantityTypeIdentifierDistanceSwimming")
                dist_km = float(dist_el.get("sum")) if dist_el is not None else None
                kcal_el = stats.get("HKQuantityTypeIdentifierActiveEnergyBurned")
                hr_el = stats.get("HKQuantityTypeIdentifierHeartRate")
                workouts.append({
                    "sport": sport,
                    "start": el.get("startDate"),
                    "dur_s": int(float(el.get("duration", 0)) * 60),
                    "dist_m": dist_km * 1000 if dist_km else None,
                    "kcal": float(kcal_el.get("sum")) if kcal_el is not None else None,
                    "avg_hr": int(float(hr_el.get("average"))) if hr_el is not None and hr_el.get("average") else None,
                    "max_hr": int(float(hr_el.get("maximum"))) if hr_el is not None and hr_el.get("maximum") else None,
                })
            el.clear()

    con = connect()
    for d in sleep:
        daily[d]["sleep_hours"] = [sleep[d]]
        if d in deep:
            daily[d]["sleep_deep_hours"] = [deep[d]]

    n_days = 0
    for d, cols in sorted(daily.items()):
        vals = {}
        for col, vs in cols.items():
            vals[col] = round(sum(vs), 1) if col in SUM_FIELDS else round(sum(vs) / len(vs), 2)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in vals)
        cols_sql = ", ".join(vals)
        ph = ", ".join(["%s"] * len(vals))
        con.execute(
            f"INSERT INTO daily_vitals (date, {cols_sql}) VALUES (%s, {ph}) "
            f"ON CONFLICT (date) DO UPDATE SET {sets}",
            [d, *vals.values()])
        n_days += 1

    n_wk, n_skip = 0, 0
    for w in workouts:
        start = datetime.fromisoformat(w["start"])
        dup = con.execute(
            """SELECT 1 FROM activities WHERE source = 'strava' AND sport = %s
               AND start_time BETWEEN %s AND %s""",
            (w["sport"], start - timedelta(minutes=10), start + timedelta(minutes=10)),
        ).fetchone()
        if dup:
            n_skip += 1
            continue
        ext_id = f"{w['start']}_{w['sport']}"
        con.execute(
            """INSERT INTO activities
               (source, external_id, sport, start_time, duration_s, distance_m,
                avg_hr, max_hr, calories, title)
               VALUES ('apple',%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source, external_id) DO UPDATE SET
                 duration_s = EXCLUDED.duration_s, avg_hr = EXCLUDED.avg_hr""",
            (ext_id, w["sport"], w["start"], w["dur_s"], w["dist_m"],
             w["avg_hr"], w["max_hr"], w["kcal"], f"Apple {w['sport']}"))
        n_wk += 1

    con.commit()
    con.close()
    print(f"Vitals: {n_days} days. Workouts: {n_wk} added, {n_skip} skipped (Strava dupes).")


if __name__ == "__main__":
    main(sys.argv[1])
