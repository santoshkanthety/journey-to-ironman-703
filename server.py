#!/usr/bin/env python3
"""TriPeak local server — serves the dashboard and a plan-editing API.

Run:   source ~/.ironman.env && .venv/bin/uvicorn server:app --port 8703
Open:  http://localhost:8703

Endpoints:
  GET  /                    dashboard (rebuilt from DB on startup)
  GET  /api/plan            current 16-week plan (miles interface)
  PUT  /api/plan            replace plan rows (JSON list) — validated first
  POST /api/plan/upload     CSV upload — validated first
  GET  /api/plan.csv        download current plan as CSV (round-trip editing)

Every write runs data-quality checks: hard errors block the write entirely
(HTTP 422 with a row-level report); warnings are returned but the write lands.
Distances cross the API in miles (user-facing); storage stays km/meters.
"""
import csv
import io
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

sys.path.insert(0, str(Path(__file__).parent))
from db import connect

app = FastAPI(title="TriPeak")
DASH = Path(__file__).parent / "dashboard" / "dashboard.html"
MI = 1.609344
ATHLETE_ID = int(__import__("os").environ.get("ATHLETE_ID", "1"))

PHASES = {"base", "build", "peak", "taper", "race"}
COLS = ["week_num", "phase", "planned_hours", "planned_tss", "swim_m",
        "bike_mi", "run_mi", "swim_sessions", "bike_sessions", "run_sessions",
        "strength_sessions", "long_ride_min", "long_run_min", "is_recovery",
        "focus", "key_workouts"]
NUMERIC = {  # field: (min, max) — hard bounds, inclusive
    "planned_hours": (0.5, 25), "planned_tss": (50, 1200),
    "swim_m": (0, 30000), "bike_mi": (0, 310), "run_mi": (0, 75),
    "swim_sessions": (0, 14), "bike_sessions": (0, 14), "run_sessions": (0, 14),
    "strength_sessions": (0, 7), "long_ride_min": (0, 480), "long_run_min": (0, 240),
}


def validate(rows, ramp_limit_pct=30, long_run_cap_min=150):
    """Data-quality checks. Returns (errors, warnings, clean_rows).
    Thresholds come from athlete_settings — configurable per athlete."""
    errors, warnings, clean = [], [], []
    seen = set()
    for i, r in enumerate(rows):
        loc = f"row {i + 1}"
        out = {}
        # week number
        try:
            wk = int(float(r.get("week_num", "")))
        except (TypeError, ValueError):
            errors.append(f"{loc}: week_num '{r.get('week_num')}' is not a number")
            continue
        loc = f"week {wk}"
        if not 1 <= wk <= 16:
            errors.append(f"{loc}: week_num must be 1–16")
            continue
        if wk in seen:
            errors.append(f"{loc}: duplicate week")
            continue
        seen.add(wk)
        out["week_num"] = wk
        # phase
        ph = str(r.get("phase", "")).strip().lower()
        if ph not in PHASES:
            errors.append(f"{loc}: phase '{r.get('phase')}' not one of {sorted(PHASES)}")
        out["phase"] = ph
        # numerics with bounds
        for f, (lo, hi) in NUMERIC.items():
            raw = r.get(f, "")
            try:
                v = float(raw)
            except (TypeError, ValueError):
                errors.append(f"{loc}: {f} '{raw}' is not a number")
                continue
            if not lo <= v <= hi:
                errors.append(f"{loc}: {f}={v:g} outside sane range [{lo}, {hi}]")
            out[f] = v
        # recovery flag
        rec = str(r.get("is_recovery", "0")).strip().lower()
        out["is_recovery"] = rec in ("1", "true", "yes", "t")
        out["focus"] = str(r.get("focus") or "")[:500]
        out["key_workouts"] = str(r.get("key_workouts") or "")[:1000]
        clean.append(out)

    missing = sorted(set(range(1, 17)) - seen)
    if missing:
        errors.append(f"missing weeks: {missing} — plan must cover all 16 weeks")

    if not errors:
        by_wk = {c["week_num"]: c for c in clean}
        prev = None
        for wk in range(1, 17):
            c = by_wk[wk]
            if prev and not c["is_recovery"] and not prev["is_recovery"]:
                if c["planned_hours"] > prev["planned_hours"] * (1 + ramp_limit_pct / 100):
                    warnings.append(
                        f"week {wk}: hours ramp +{c['planned_hours'] / prev['planned_hours'] - 1:.0%} "
                        f"vs week {wk - 1} — over the {ramp_limit_pct}% safe ramp")
            if c["is_recovery"] and prev and c["planned_hours"] >= prev["planned_hours"]:
                warnings.append(f"week {wk}: marked recovery but hours >= week {wk - 1}")
            if c["long_run_min"] > long_run_cap_min:
                warnings.append(f"week {wk}: long run {c['long_run_min']:g}min > "
                                f"{long_run_cap_min} — injury risk")
            if wk < 16 and c["swim_m"] == 0:
                warnings.append(f"week {wk}: no swim volume — swim is the limiter")
            if wk == 16 and c["planned_hours"] > 8:
                warnings.append(f"week 16: {c['planned_hours']:g}h in race week — taper harder")
            prev = c
    return errors, warnings, clean


def apply_plan(clean):
    con = connect()
    for c in clean:
        con.execute(
            """UPDATE weekly_plan SET phase=%s, planned_hours=%s, planned_tss=%s,
               swim_m=%s, bike_km=%s, run_km=%s, swim_sessions=%s, bike_sessions=%s,
               run_sessions=%s, strength_sessions=%s, long_ride_min=%s,
               long_run_min=%s, is_recovery=%s, focus=%s, key_workouts=%s
               WHERE athlete_id=%s AND week_num=%s""",
            (c["phase"], c["planned_hours"], c["planned_tss"], c["swim_m"],
             round(c["bike_mi"] * MI, 1), round(c["run_mi"] * MI, 1),
             c["swim_sessions"], c["bike_sessions"], c["run_sessions"],
             c["strength_sessions"], c["long_ride_min"], c["long_run_min"],
             c["is_recovery"], c["focus"], c["key_workouts"],
             ATHLETE_ID, c["week_num"]))
    con.commit()
    con.close()


def rebuild_dashboard():
    subprocess.run([sys.executable, str(Path(__file__).parent / "dashboard" / "build_dashboard.py")],
                   capture_output=True)


def fetch_plan():
    con = connect()
    cur = con.execute("""
        SELECT week_num, phase, planned_hours, planned_tss, swim_m,
               ROUND(bike_km / 1.609344, 1), ROUND(run_km / 1.609344, 1),
               swim_sessions, bike_sessions, run_sessions, strength_sessions,
               long_ride_min, long_run_min, is_recovery::int, focus, key_workouts
        FROM weekly_plan WHERE athlete_id = %s ORDER BY week_num""", (ATHLETE_ID,))
    from decimal import Decimal
    rows = [dict(zip(COLS, [float(v) if isinstance(v, Decimal) else v for v in r]))
            for r in cur.fetchall()]
    con.close()
    return rows


SETTING_BOUNDS = {  # configurable per-athlete KPI targets: (min, max)
    "ctl_target": (30, 120), "compliance_goal_pct": (50, 100),
    "ramp_limit_pct": (10, 60), "long_run_cap_min": (60, 240),
    "tsb_race_lo": (-10, 25), "tsb_race_hi": (0, 30),
}


def fetch_settings():
    con = connect()
    row = con.execute("""
        SELECT a.name, a.tagline, s.race_date::text, s.plan_weeks, s.ctl_target,
               s.compliance_goal_pct, s.ramp_limit_pct, s.long_run_cap_min,
               s.tsb_race_lo, s.tsb_race_hi, s.units
        FROM athletes a JOIN athlete_settings s ON s.athlete_id = a.id
        WHERE a.id = %s""", (ATHLETE_ID,)).fetchone()
    con.close()
    keys = ["name", "tagline", "race_date", "plan_weeks", "ctl_target",
            "compliance_goal_pct", "ramp_limit_pct", "long_run_cap_min",
            "tsb_race_lo", "tsb_race_hi", "units"]
    return dict(zip(keys, row))


@app.get("/api/settings")
def get_settings():
    return fetch_settings()


@app.put("/api/settings")
def put_settings(body: dict):
    """Kickoff: validate targets + race date, write settings, anchor the plan.

    Setting race_date stamps weekly_plan.start_date so week 16 contains the
    race (weeks start Monday). Clearing it un-anchors the plan.
    """
    from datetime import date, timedelta
    errors, warnings = [], []
    clean = {}
    for f, (lo, hi) in SETTING_BOUNDS.items():
        if f not in body:
            continue
        try:
            v = int(float(body[f]))
        except (TypeError, ValueError):
            errors.append(f"{f} '{body[f]}' is not a number")
            continue
        if not lo <= v <= hi:
            errors.append(f"{f}={v} outside sane range [{lo}, {hi}]")
        clean[f] = v
    if clean.get("tsb_race_lo") is not None and clean.get("tsb_race_hi") is not None \
            and clean["tsb_race_lo"] >= clean["tsb_race_hi"]:
        errors.append("tsb_race_lo must be below tsb_race_hi")

    race = body.get("race_date") or None
    if race:
        try:
            race = date.fromisoformat(str(race))
        except ValueError:
            errors.append(f"race_date '{body['race_date']}' is not YYYY-MM-DD")
            race = None
        if race:
            days_out = (race - date.today()).days
            if days_out < 0:
                errors.append("race_date is in the past")
            elif days_out < 7 * 16:
                warnings.append(
                    f"race is {days_out // 7} weeks out — a 16-week plan is compressed; "
                    "early weeks will already be in the past")
            if race.weekday() != 6:
                warnings.append("race_date is not a Sunday — most 70.3s race Sunday; "
                                "week 16 will still contain it")
            if days_out > 365:
                warnings.append("race is over a year out — plan anchors anyway")
    if errors:
        return JSONResponse(status_code=422, content={
            "applied": False, "errors": errors, "warnings": warnings})

    con = connect()
    if clean:
        sets = ", ".join(f"{f} = %s" for f in clean)
        con.execute(f"UPDATE athlete_settings SET {sets} WHERE athlete_id = %s",
                    [*clean.values(), ATHLETE_ID])
    if "race_date" in body:
        con.execute("UPDATE athlete_settings SET race_date = %s WHERE athlete_id = %s",
                    (race, ATHLETE_ID))
        if race:
            monday_w16 = race - timedelta(days=race.weekday())
            con.execute("""
                UPDATE weekly_plan
                SET start_date = %s::date - ((16 - week_num) * 7)
                WHERE athlete_id = %s""", (monday_w16, ATHLETE_ID))
            warnings.append(f"plan anchored: week 1 starts "
                            f"{(monday_w16 - timedelta(weeks=15)).isoformat()}, "
                            f"race week starts {monday_w16.isoformat()}")
        else:
            con.execute("UPDATE weekly_plan SET start_date = NULL WHERE athlete_id = %s",
                        (ATHLETE_ID,))
            warnings.append("race date cleared — plan un-anchored")
    con.commit()
    con.close()
    rebuild_dashboard()
    return {"applied": True, "errors": [], "warnings": warnings,
            "settings": fetch_settings()}


# ---------------------------------------------------------------- admin
@app.get("/api/admin/athletes")
def admin_athletes():
    con = connect()
    rows = con.execute("""
        SELECT a.id, a.name, a.tagline, s.race_date::text,
               (SELECT COUNT(*) FROM activities x WHERE x.athlete_id = a.id),
               (SELECT COUNT(*) FROM daily_vitals v WHERE v.athlete_id = a.id),
               (SELECT COUNT(*) FROM weekly_plan p WHERE p.athlete_id = a.id)
        FROM athletes a LEFT JOIN athlete_settings s ON s.athlete_id = a.id
        ORDER BY a.id""").fetchall()
    con.close()
    keys = ["id", "name", "tagline", "race_date", "activities", "vitals_days", "plan_weeks"]
    return [dict(zip(keys, r)) for r in rows]


@app.post("/api/admin/athletes")
def admin_create_athlete(body: dict):
    name = str(body.get("name", "")).strip()
    if not 2 <= len(name) <= 80:
        return JSONResponse(status_code=422, content={
            "applied": False, "errors": ["name must be 2–80 characters"], "warnings": []})
    tagline = str(body.get("tagline") or "Journey to Ironman 70.3")[:120]
    con = connect()
    aid = con.execute(
        "INSERT INTO athletes (name, tagline) VALUES (%s, %s) RETURNING id",
        (name, tagline)).fetchone()[0]
    con.execute("INSERT INTO athlete_settings (athlete_id) VALUES (%s)", (aid,))
    con.commit()
    con.close()
    import seed_plan
    seed_plan.seed(None, athlete_id=aid)     # template 16-week plan, un-anchored
    return {"applied": True, "errors": [],
            "warnings": [f"athlete {aid} '{name}' created with template plan; "
                         "kickoff their race date from their session"],
            "athlete_id": aid}


@app.post("/api/admin/import/apple")
def admin_import_apple(body: dict):
    """Import an Apple Health export for any athlete.
    Path-based (exports are 100s of MB — browser upload impractical):
    body = {athlete_id, path} where path is export.zip or export.xml on this machine.
    """
    import os
    import subprocess
    errors = []
    try:
        aid = int(body.get("athlete_id"))
    except (TypeError, ValueError):
        errors.append("athlete_id must be an integer")
        aid = None
    path = os.path.expanduser(str(body.get("path", "")).strip())
    if not path.endswith((".zip", ".xml")):
        errors.append("path must point to export.zip or export.xml")
    elif not os.path.isfile(path):
        errors.append(f"file not found: {path}")
    if aid is not None:
        con = connect()
        ok = con.execute("SELECT 1 FROM athletes WHERE id = %s", (aid,)).fetchone()
        con.close()
        if not ok:
            errors.append(f"athlete {aid} does not exist")
    if errors:
        return JSONResponse(status_code=422, content={
            "applied": False, "errors": errors, "warnings": []})
    env = {**os.environ, "ATHLETE_ID": str(aid)}
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "etl" / "apple_health_import.py"), path],
        capture_output=True, text=True, env=env, timeout=1800)
    if r.returncode != 0:
        return JSONResponse(status_code=500, content={
            "applied": False, "errors": [r.stderr[-500:]], "warnings": []})
    subprocess.run([sys.executable, str(Path(__file__).parent / "etl" / "compute_metrics.py")],
                   capture_output=True, env=env)
    rebuild_dashboard()
    return {"applied": True, "errors": [], "warnings": [r.stdout.strip()]}


@app.get("/")
def index():
    return FileResponse(DASH)


@app.get("/api/plan")
def get_plan():
    return fetch_plan()


@app.get("/api/plan.csv")
def get_plan_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    w.writerows(fetch_plan())
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=tripeak_plan.csv"})


def _process(rows):
    s = fetch_settings()
    errors, warnings, clean = validate(rows, s["ramp_limit_pct"], s["long_run_cap_min"])
    if errors:
        return JSONResponse(status_code=422, content={
            "applied": False, "errors": errors, "warnings": warnings})
    apply_plan(clean)
    rebuild_dashboard()
    return {"applied": True, "errors": [], "warnings": warnings,
            "weeks_updated": len(clean)}


@app.put("/api/plan")
def put_plan(rows: list[dict]):
    return _process(rows)


@app.post("/api/plan/upload")
async def upload_plan(file: UploadFile):
    text = (await file.read()).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return JSONResponse(status_code=422, content={
            "applied": False, "errors": ["CSV is empty or has no header row. "
                                         f"Expected columns: {', '.join(COLS)}"],
            "warnings": []})
    missing_cols = [c for c in COLS if c not in rows[0]]
    if missing_cols:
        return JSONResponse(status_code=422, content={
            "applied": False,
            "errors": [f"CSV missing columns: {', '.join(missing_cols)}"],
            "warnings": []})
    return _process(rows)


@app.on_event("startup")
def startup():
    rebuild_dashboard()
