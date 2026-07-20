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


def validate(rows):
    """Data-quality checks. Returns (errors, warnings, clean_rows)."""
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
                if c["planned_hours"] > prev["planned_hours"] * 1.3:
                    warnings.append(
                        f"week {wk}: hours ramp +{c['planned_hours'] / prev['planned_hours'] - 1:.0%} "
                        f"vs week {wk - 1} — over the 30% safe ramp")
            if c["is_recovery"] and prev and c["planned_hours"] >= prev["planned_hours"]:
                warnings.append(f"week {wk}: marked recovery but hours >= week {wk - 1}")
            if c["long_run_min"] > 150:
                warnings.append(f"week {wk}: long run {c['long_run_min']:g}min > 150 — injury risk")
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
               WHERE week_num=%s""",
            (c["phase"], c["planned_hours"], c["planned_tss"], c["swim_m"],
             round(c["bike_mi"] * MI, 1), round(c["run_mi"] * MI, 1),
             c["swim_sessions"], c["bike_sessions"], c["run_sessions"],
             c["strength_sessions"], c["long_ride_min"], c["long_run_min"],
             c["is_recovery"], c["focus"], c["key_workouts"], c["week_num"]))
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
        FROM weekly_plan ORDER BY week_num""")
    from decimal import Decimal
    rows = [dict(zip(COLS, [float(v) if isinstance(v, Decimal) else v for v in r]))
            for r in cur.fetchall()]
    con.close()
    return rows


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
    errors, warnings, clean = validate(rows)
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
