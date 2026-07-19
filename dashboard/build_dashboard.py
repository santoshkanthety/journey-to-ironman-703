#!/usr/bin/env python3
"""Generate TriPeak — self-contained training-intelligence app (single HTML).

Pages: Dashboard / Statistics (boxplots + slice & dice) / Profile / Guide.

Usage:
    .venv/bin/python dashboard/build_dashboard.py            # real data
    .venv/bin/python dashboard/build_dashboard.py --demo     # fabricated preview
Output: dashboard/dashboard.html
Profile photo: dashboard/profile.png (optional, gitignored) is embedded
as a data URI at build time.
"""
import argparse
import base64
import json
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

OUT = Path(__file__).parent / "dashboard.html"
PHOTO = Path(__file__).parent / "profile.png"


def _num(v):
    from decimal import Decimal
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def rows(con, sql, keys):
    return [dict(zip(keys, [_num(v) for v in r])) for r in con.execute(sql).fetchall()]


def fetch(con):
    plan = rows(con, """
        SELECT week_num, phase, start_date::text, planned_hours, planned_tss,
               swim_m, bike_km, run_km, long_ride_min, long_run_min,
               is_recovery, focus, key_workouts
        FROM weekly_plan ORDER BY week_num""",
        ["week", "phase", "start", "hours", "tss", "swim_m", "bike_km", "run_km",
         "long_ride", "long_run", "recovery", "focus", "key"])
    comp = rows(con, """
        SELECT week_num, actual_hours, actual_tss, actual_swim_m,
               actual_bike_km, actual_run_km
        FROM v_weekly_compliance ORDER BY week_num""",
        ["week", "hours", "tss", "swim_m", "bike_km", "run_km"])
    pmc = [[str(r[0])] + [float(x) for x in r[1:]] for r in con.execute(
        """SELECT date::text, ctl, atl, tsb FROM daily_load
           WHERE date >= CURRENT_DATE - 182 ORDER BY date""").fetchall()]
    vitals = [[str(r[0])] + [float(x) if x is not None else None for x in r[1:]]
              for r in con.execute(
        """SELECT date::text, resting_hr, hrv_ms, sleep_hours, weight_kg
           FROM daily_vitals ORDER BY date""").fetchall()]
    eff = [[str(r[0]), r[1], float(r[2])] for r in con.execute(
        """SELECT date::text, sport, efficiency_factor FROM v_efficiency
           WHERE efficiency_factor IS NOT NULL
             AND date >= CURRENT_DATE - 182 ORDER BY date""").fetchall()]
    ready = con.execute(
        "SELECT readiness FROM v_readiness ORDER BY date DESC LIMIT 1").fetchone()
    acts = [[str(r[0]), r[1], _num(r[2]), _num(r[3]), _num(r[4])]
            for r in con.execute("""
        SELECT start_time::date, sport, ROUND(distance_m/1000.0, 2),
               ROUND(duration_s/3600.0, 2), avg_hr
        FROM activities WHERE duration_s > 0 ORDER BY start_time""").fetchall()]
    efforts = rows(con, """
        SELECT band, meters, year, date::text, est_time, pace_min_km,
               run_km, is_all_time_pr
        FROM v_best_efforts ORDER BY meters, year""",
        ["band", "meters", "year", "date", "time", "pace", "km", "pr"])
    records = rows(con, "SELECT record, value, date FROM v_records",
                   ["record", "value", "date"])
    return plan, comp, pmc, vitals, eff, (ready[0] if ready else None), acts, efforts, records


def demo_data(plan):
    import math
    rng = random.Random(42)
    k42, k7 = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    comp, pmc, vitals, acts = [], [], [], []
    start = date.today() - timedelta(weeks=5, days=3)
    ctl = atl = 20.0
    d = start
    while d <= date.today():
        wk = (d - start).days // 7
        target = plan[min(wk, 15)]["tss"] / 7.0
        tss = max(0, rng.gauss(target, target * 0.45)) if rng.random() > 0.25 else 0
        tsb = ctl - atl
        ctl += k42 * (tss - ctl)
        atl += k7 * (tss - atl)
        pmc.append([d.isoformat(), round(ctl, 1), round(atl, 1), round(tsb, 1)])
        vitals.append([d.isoformat(), round(rng.gauss(52, 2)),
                       round(rng.gauss(48 + wk, 6), 1),
                       round(rng.gauss(7.1, 0.7), 1),
                       round(78.5 - wk * 0.15 + rng.gauss(0, 0.3), 1)])
        d += timedelta(days=1)
    for i, p in enumerate(plan[:6]):
        f = rng.uniform(0.82, 1.05) if i < 5 else 0.45
        comp.append({"week": p["week"], "hours": round(p["hours"] * f, 1),
                     "tss": round(p["tss"] * f), "swim_m": round(p["swim_m"] * f),
                     "bike_km": round(p["bike_km"] * f, 1),
                     "run_km": round(p["run_km"] * f, 1)})
    eff = []
    d, ef_b, ef_r = start, 1.55, 1.05
    while d <= date.today():
        if rng.random() < 0.3:
            eff.append([d.isoformat(), "bike", round(ef_b + rng.gauss(0, .04), 3)])
            ef_b += 0.004
        if rng.random() < 0.3:
            eff.append([d.isoformat(), "run", round(ef_r + rng.gauss(0, .03), 3)])
            ef_r += 0.003
        d += timedelta(days=1)
    for y in range(2019, 2027):
        for _ in range(rng.randint(60, 120)):
            sp = rng.choices(["run", "bike", "swim"], [.6, .3, .1])[0]
            km = {"run": abs(rng.gauss(8, 4)), "bike": abs(rng.gauss(30, 15)),
                  "swim": abs(rng.gauss(1.5, .6))}[sp]
            hrs = km / {"run": 10, "bike": 25, "swim": 3}[sp]
            acts.append([f"{y}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
                         sp, round(km, 2), round(hrs, 2), round(rng.gauss(140, 12))])
    efforts = [{"band": b, "meters": m, "year": 2026, "date": "2026-05-01",
                "time": t, "pace": p, "km": round(m/1000, 1), "pr": True}
               for b, m, t, p in [("5k", 5000, "00:23:10", "04:38"),
                                  ("10k", 10000, "00:48:30", "04:51"),
                                  ("Half marathon", 21097, "01:51:20", "05:17")]]
    records = [{"record": "Longest run (km)", "value": "34.1", "date": "2025-10-12"},
               {"record": "Longest ride (km)", "value": "118.5", "date": "2024-08-03"},
               {"record": "Biggest week (hours)", "value": "12.4", "date": "2025-09-29"}]
    return comp, pmc, vitals, eff, "green", acts, efforts, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    con = connect()
    plan, comp, pmc, vitals, eff, ready, acts, efforts, records = fetch(con)
    con.close()
    photo = None
    if args.demo:
        comp, pmc, vitals, eff, ready, acts, efforts, records = demo_data(plan)
    elif PHOTO.exists():
        photo = "data:image/png;base64," + base64.b64encode(PHOTO.read_bytes()).decode()

    data = {
        "generated": date.today().isoformat(),
        "demo": args.demo,
        "readiness": ready,
        "profile": {"name": "Demo Athlete" if args.demo else "Santosh Kanthety",
                    "photo": photo,
                    "tagline": "Journey to Ironman 70.3"},
        "plan": plan,
        "actual": [c for c in comp if c.get("hours") is not None],
        "pmc": pmc, "vitals": vitals, "eff": eff,
        "acts": acts, "efforts": efforts, "records": records,
    }
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data))
    OUT.write_text(html)
    print(f"Wrote {OUT} ({'demo' if args.demo else 'live'}: "
          f"{len(acts)} activities, {len(efforts)} effort rows)")


TEMPLATE = r"""<title>TriPeak — Training Intelligence</title>
<style>
  :root {
    --surface-1:#fcfcfb; --page:#f6f6f3; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
    --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s5:#4a3aa7; --s6:#e34948;
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
    --plan-ghost:#e1e0d9; --brand:#2a78d6; --nav:#fcfcfb;
  }
  @media (prefers-color-scheme: dark) { :root {
    --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s5:#9085e9; --s6:#e66767;
    --plan-ghost:#2c2c2a; --brand:#3987e5; --nav:#161615;
  }}
  :root[data-theme="dark"] {
    --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s5:#9085e9; --s6:#e66767;
    --plan-ghost:#2c2c2a; --brand:#3987e5; --nav:#161615;
  }
  :root[data-theme="light"] {
    --surface-1:#fcfcfb; --page:#f6f6f3; --ink:#0b0b0b; --ink-2:#52514e;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s5:#4a3aa7; --s6:#e34948;
    --plan-ghost:#e1e0d9; --brand:#2a78d6; --nav:#fcfcfb;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui,-apple-system,"Segoe UI",sans-serif;
    background: var(--page); color: var(--ink); }
  nav { position: sticky; top: 0; z-index: 5; display: flex; align-items: center;
    gap: 26px; padding: 0 28px; height: 56px; background: var(--nav);
    border-bottom: 1px solid var(--border); }
  .logo { display: flex; align-items: center; gap: 9px; font-weight: 750;
    font-size: 16px; letter-spacing: -.2px; }
  .logo small { font-weight: 500; color: var(--muted); font-size: 11px;
    letter-spacing: .5px; text-transform: uppercase; margin-left: 2px; }
  .tabs { display: flex; gap: 4px; }
  .tabs a { padding: 7px 14px; border-radius: 8px; text-decoration: none;
    color: var(--ink-2); font-size: 13.5px; font-weight: 550; }
  .tabs a.on { background: color-mix(in srgb, var(--brand) 12%, transparent);
    color: var(--brand); }
  .tabs a:hover:not(.on) { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  .navright { margin-left: auto; font-size: 12px; color: var(--muted); }
  main { max-width: 1160px; margin: 0 auto; padding: 24px; }
  .page { display: none; } .page.on { display: block; }
  h1 { font-size: 21px; margin: 4px 0 2px; }
  .sub { color: var(--ink-2); font-size: 13px; margin-bottom: 18px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
    gap: 12px; margin-bottom: 18px; }
  .tile { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 16px; }
  .tile .lbl { font-size: 12px; color: var(--ink-2); margin-bottom: 4px; }
  .tile .val { font-size: 26px; font-weight: 680; }
  .tile .hint { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .card { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px; margin-bottom: 16px; }
  .card h2 { font-size: 14px; margin: 0 0 2px; }
  .card .note { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .legend { display: flex; gap: 16px; font-size: 12px; color: var(--ink-2);
    margin-bottom: 6px; flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .row3 { display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; }
  @media (max-width: 800px) { .row,.row3 { grid-template-columns: 1fr; } }
  svg { display: block; width: 100%; }
  svg text { font-family: inherit; font-size: 11px; fill: var(--muted); }
  svg .dl { font-size: 11px; font-weight: 600; }
  .tt { position: fixed; pointer-events: none; background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
    font-size: 12px; color: var(--ink); box-shadow: 0 4px 14px rgba(0,0,0,.15);
    display: none; z-index: 9; min-width: 130px; }
  .tt b { font-weight: 650; }
  .tt .r { display: flex; justify-content: space-between; gap: 14px; }
  .tt .r span:last-child { font-variant-numeric: tabular-nums; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th { text-align: left; color: var(--ink-2); font-weight: 600; padding: 7px 8px;
    border-bottom: 1px solid var(--axis); white-space: nowrap; }
  td { padding: 7px 8px; border-bottom: 1px solid var(--grid);
    font-variant-numeric: tabular-nums; }
  td.key { font-variant-numeric: normal; color: var(--ink-2); font-size: 12px; }
  tr.rec td { color: var(--muted); }
  .phase { font-size: 11px; font-weight: 650; padding: 2px 8px; border-radius: 99px;
    border: 1px solid var(--border); text-transform: uppercase; letter-spacing: .4px; }
  .badge { display: inline-flex; align-items: center; gap: 6px; font-weight: 650; }
  .tablewrap { overflow-x: auto; }
  .demo-banner { font-size: 12px; color: var(--ink-2); background: var(--surface-1);
    border: 1px dashed var(--axis); border-radius: 8px; padding: 8px 12px;
    margin-bottom: 16px; }
  .controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end;
    margin-bottom: 14px; }
  .ctrl { display: flex; flex-direction: column; gap: 4px; }
  .ctrl label { font-size: 11px; color: var(--muted); font-weight: 600;
    text-transform: uppercase; letter-spacing: .4px; }
  select { background: var(--surface-1); color: var(--ink); font: inherit;
    font-size: 13px; border: 1px solid var(--axis); border-radius: 8px;
    padding: 6px 10px; }
  .chips { display: flex; gap: 6px; }
  .chip { font-size: 12.5px; padding: 6px 12px; border-radius: 99px; cursor: pointer;
    border: 1px solid var(--axis); background: var(--surface-1); color: var(--ink-2);
    user-select: none; }
  .chip.on { border-color: var(--brand); color: var(--brand);
    background: color-mix(in srgb, var(--brand) 10%, transparent); font-weight: 600; }
  .profile-hero { display: flex; gap: 24px; align-items: center; }
  .avatar { width: 128px; height: 128px; border-radius: 50%; object-fit: cover;
    border: 3px solid var(--brand); flex: none; }
  .avatar-ph { width: 128px; height: 128px; border-radius: 50%; flex: none;
    background: color-mix(in srgb, var(--brand) 15%, transparent); color: var(--brand);
    display: flex; align-items: center; justify-content: center;
    font-size: 42px; font-weight: 700; border: 3px solid var(--brand); }
  .profile-hero h1 { margin: 0; font-size: 26px; }
  .profile-hero .tag { color: var(--brand); font-weight: 600; font-size: 14px;
    margin: 2px 0 10px; }
  .statchips { display: flex; gap: 10px; flex-wrap: wrap; }
  .statchip { background: var(--page); border: 1px solid var(--border);
    border-radius: 10px; padding: 8px 14px; font-size: 12px; color: var(--ink-2); }
  .statchip b { display: block; font-size: 17px; color: var(--ink);
    font-variant-numeric: tabular-nums; }
  .pr-star { color: var(--s3); }
  .guide h3 { font-size: 14px; margin: 18px 0 6px; }
  .guide p, .guide li { font-size: 13.5px; line-height: 1.55; color: var(--ink-2);
    max-width: 72ch; }
  .guide code { background: color-mix(in srgb, var(--ink) 7%, transparent);
    border-radius: 5px; padding: 1px 6px; font-size: 12.5px; }
  footer { text-align: center; color: var(--muted); font-size: 11.5px;
    padding: 18px 0 26px; }
</style>
<nav>
  <div class="logo">
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 19 L9 7 L12.5 13.5 L15 9 L21 19 Z" fill="var(--brand)"/>
      <path d="M3 19 L9 7 L11 11 L7 19 Z" fill="var(--brand)" opacity=".55"/>
    </svg>
    TriPeak<small>Training Intelligence</small>
  </div>
  <div class="tabs" id="tabs">
    <a href="#dash" data-p="dash" class="on">Dashboard</a>
    <a href="#stats" data-p="stats">Statistics</a>
    <a href="#profile" data-p="profile">Profile</a>
    <a href="#guide" data-p="guide">Guide</a>
  </div>
  <div class="navright" id="sub"></div>
</nav>
<main>
<div id="banner"></div>

<!-- ============================ DASHBOARD ============================ -->
<div class="page on" id="page-dash">
  <div class="tiles" id="tiles"></div>
  <div class="card">
    <h2>Performance Management — CTL / ATL / TSB</h2>
    <div class="note">Fitness (42-day load), Fatigue (7-day load), Form = Fitness − Fatigue. Race-day target: TSB +5 to +15. <a href="#guide" style="color:var(--brand)">How to read →</a></div>
    <div class="legend">
      <span><span class="sw" style="background:var(--s1)"></span>CTL Fitness</span>
      <span><span class="sw" style="background:var(--s2)"></span>ATL Fatigue</span>
      <span><span class="sw" style="background:var(--s3)"></span>TSB Form</span>
    </div>
    <div id="pmc"></div>
  </div>
  <div class="card">
    <h2>Weekly hours — plan vs actual</h2>
    <div class="note">Compliance target ≥ 85%. Gray = planned, blue = actual.</div>
    <div class="legend">
      <span><span class="sw" style="background:var(--plan-ghost)"></span>Planned</span>
      <span><span class="sw" style="background:var(--s1)"></span>Actual</span>
    </div>
    <div id="hours"></div>
  </div>
  <div class="card">
    <h2>Sport volume by week — plan vs actual</h2>
    <div class="note">Swim (m), Bike (km), Run (km).</div>
    <div class="row3" id="sports"></div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Efficiency factor</h2>
      <div class="note">Bike NP÷HR, Run speed÷HR on steady sessions. Upward drift = aerobic gain.</div>
      <div class="legend">
        <span><span class="sw" style="background:var(--s1)"></span>Bike</span>
        <span><span class="sw" style="background:var(--s2)"></span>Run</span>
      </div>
      <div id="eff"></div>
    </div>
    <div class="card">
      <h2>Vitals — recent</h2>
      <div class="note">Resting HR, HRV (SDNN), sleep, weight from Apple Health.</div>
      <div id="vitals" class="row" style="gap:8px"></div>
    </div>
  </div>
  <div class="card">
    <h2>16-week plan</h2>
    <div class="note">Race = Sunday of week 16. Recovery weeks dimmed.</div>
    <div class="tablewrap"><table id="plantable"></table></div>
  </div>
</div>

<!-- ============================ STATISTICS ============================ -->
<div class="page" id="page-stats">
  <h1>Statistics — slice &amp; dice</h1>
  <div class="sub">Distribution of your training across any cut. Box = middle 50% of sessions, line = median, whiskers = typical range, dots = outliers.</div>
  <div class="card">
    <div class="controls">
      <div class="ctrl"><label>Metric</label>
        <select id="st-metric">
          <option value="km">Distance (km)</option>
          <option value="hours">Duration (hours)</option>
          <option value="hr">Avg heart rate (bpm)</option>
        </select></div>
      <div class="ctrl"><label>Group by</label>
        <select id="st-group">
          <option value="sport">Sport</option>
          <option value="year">Year</option>
          <option value="sport_year">Year (one sport)</option>
        </select></div>
      <div class="ctrl" id="st-sportsel-wrap" style="display:none"><label>Sport</label>
        <select id="st-sportsel"></select></div>
      <div class="ctrl"><label>From</label><select id="st-y0"></select></div>
      <div class="ctrl"><label>To</label><select id="st-y1"></select></div>
      <div class="ctrl"><label>Sports</label><div class="chips" id="st-chips"></div></div>
    </div>
    <div id="boxplot"></div>
  </div>
  <div class="card">
    <h2>Group summary</h2>
    <div class="note">Same slice as above.</div>
    <div class="tablewrap"><table id="st-table"></table></div>
  </div>
</div>

<!-- ============================ PROFILE ============================ -->
<div class="page" id="page-profile">
  <div class="card profile-hero" id="hero"></div>
  <div class="card">
    <h2>Best efforts — all-time <span class="pr-star">★</span></h2>
    <div class="note">Fastest average pace among runs at or beyond each distance. Estimated from whole-run pace (conservative vs true splits).</div>
    <div class="tablewrap"><table id="pr-table"></table></div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Best efforts by year</h2>
      <div class="note">Progression per distance band.</div>
      <div class="tablewrap"><table id="efforts-table"></table></div>
    </div>
    <div class="card">
      <h2>Records</h2>
      <div class="note">All-time bests across the training history.</div>
      <div class="tablewrap"><table id="records-table"></table></div>
    </div>
  </div>
</div>

<!-- ============================ GUIDE ============================ -->
<div class="page" id="page-guide">
  <h1>Guide</h1>
  <div class="sub">Metric definitions, how to read each chart, and how to run the platform.</div>
  <div class="card guide">
    <h2>The metrics</h2>
    <div class="tablewrap"><table>
      <tr><th>Metric</th><th>Definition</th><th>How to use it</th></tr>
      <tr><td><b>TSS</b></td><td class="key">Training Stress Score = hours × IF² × 100. One number for how hard a workout was, comparable across swim/bike/run.</td><td class="key">A 1-hour all-out effort ≈ 100. Easy hour ≈ 50.</td></tr>
      <tr><td><b>IF</b></td><td class="key">Intensity Factor = workout intensity ÷ your threshold (FTP for bike, threshold pace for run, CSS for swim).</td><td class="key">0.70 easy · 0.85 = 70.3 race effort · 1.0 = 1-hour max.</td></tr>
      <tr><td><b>CTL</b></td><td class="key">Chronic Training Load — 42-day exponential average of daily TSS. Your <b>fitness</b>.</td><td class="key">Grow +3–6/week. Target 55–70 by race day.</td></tr>
      <tr><td><b>ATL</b></td><td class="key">Acute Training Load — 7-day exponential average. Your <b>fatigue</b>.</td><td class="key">Spikes after big weeks; fades in days.</td></tr>
      <tr><td><b>TSB</b></td><td class="key">Training Stress Balance = CTL − ATL. Your <b>form</b>.</td><td class="key">Build: −10..−25 · Overreached: &lt; −30 · Race-ready: +5..+15.</td></tr>
      <tr><td><b>EF</b></td><td class="key">Efficiency Factor — bike: power÷HR, run: speed÷HR on steady sessions.</td><td class="key">Rising EF at the same HR = aerobic engine growing.</td></tr>
      <tr><td><b>ACWR</b></td><td class="key">Acute:Chronic Workload Ratio — this week's run km ÷ 4-week average.</td><td class="key">Safe 0.8–1.3. Above 1.5 = injury-risk ramp.</td></tr>
      <tr><td><b>Readiness</b></td><td class="key">Daily green/yellow/red from resting HR, HRV, sleep and TSB vs your baselines.</td><td class="key">Red = recovery day. No exceptions.</td></tr>
    </table></div>

    <h2 style="margin-top:22px">How to read the charts</h2>
    <h3>Performance Management (Dashboard)</h3>
    <p>Blue CTL climbing steadily = plan working. Green ATL spikes with hard weeks — normal. Yellow TSB is the one to watch: it should live between −10 and −25 during build blocks, pop positive on recovery weeks, and be brought to +5..+15 by the taper for race day.</p>
    <h3>Plan vs actual bars</h3>
    <p>Gray bar is the plan, colored bar is what you did. Bars matching ≥ 85% week after week is exactly how you finish a 70.3 — consistency beats heroics. One low week is noise; two in a row is a trend that needs a plan adjustment.</p>
    <h3>Boxplots (Statistics)</h3>
    <p>Each box shows the middle 50% of your sessions for that group — the line inside is the median, whiskers reach to the typical extremes, and dots are outliers (epic days or cut-short sessions). Use the group-by and filters to compare sports, watch a year-over-year distance shift, or check whether long rides are actually getting longer through the build.</p>
    <h3>Efficiency factor</h3>
    <p>Only steady sessions ≥ 40 min count. The absolute number matters less than the drift: a rising line at the same heart rate is aerobic fitness you can bank for race day.</p>

    <h2 style="margin-top:22px">How to use the platform</h2>
    <p>Weekly rhythm (each command idempotent — safe to re-run):</p>
    <p><code>source ~/.ironman.env</code> then<br>
    <code>etl/strava_sync.py --days 14</code> → pull workouts ·
    <code>etl/apple_health_import.py export.zip</code> → vitals ·
    <code>etl/compute_metrics.py</code> → CTL/ATL/TSB ·
    <code>dashboard/build_dashboard.py</code> → this page ·
    <code>etl/replicate_to_databricks.py</code> → cloud marts</p>
    <p>Every 4 weeks: re-test FTP (20-min test × 0.95), CSS (400m/200m swim), run threshold (30-min solo). Insert into <code>athlete_profile</code> — every TSS and zone recalibrates from there.</p>
  </div>
</div>
<footer>TriPeak · Training Intelligence Platform · generated <span id="gen"></span></footer>
</main>
<div class="tt" id="tt"></div>
<script>
const DATA = /*DATA*/null;
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const NS = 'http://www.w3.org/2000/svg';
const el = (t, a) => { const e = document.createElementNS(NS, t);
  for (const k in a) e.setAttribute(k, a[k]); return e; };
const tt = document.getElementById('tt');
function showTT(html, x, y) { tt.innerHTML = html; tt.style.display = 'block';
  const w = tt.offsetWidth; tt.style.left = Math.min(x + 14, innerWidth - w - 8) + 'px';
  tt.style.top = (y + 14) + 'px'; }
function hideTT() { tt.style.display = 'none'; }

// ---- router ----
const tabs = [...document.querySelectorAll('#tabs a')];
function route() {
  const p = (location.hash || '#dash').slice(1);
  tabs.forEach(a => a.classList.toggle('on', a.dataset.p === p));
  document.querySelectorAll('.page').forEach(pg =>
    pg.classList.toggle('on', pg.id === 'page-' + p));
  window.scrollTo(0, 0);
}
addEventListener('hashchange', route);

// ---- chart helpers (line, paired bars, mini) ----
function lineChart(mount, series, opts) {
  const o = Object.assign({h: 220, pad: [10, 46, 24, 40], fmt: v => v.toFixed(0)}, opts);
  const W = 720, H = o.h, [pt, pr, pb, pl] = o.pad;
  const n = Math.max(...series.map(s => s.pts.length));
  let vals = series.flatMap(s => s.pts.map(p => p[1])).filter(v => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= 1; hi += 1; }
  const span = hi - lo; lo -= span * .06; hi += span * .06;
  const x = i => pl + (n < 2 ? 0 : i * (W - pl - pr) / (n - 1));
  const y = v => pt + (H - pt - pb) * (1 - (v - lo) / (hi - lo));
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let g = 0; g < 4; g++) {
    const v = lo + (hi - lo) * g / 3, yy = y(v);
    svg.append(el('line', {x1: pl, x2: W - pr, y1: yy, y2: yy,
      stroke: css('--grid'), 'stroke-width': 1}));
    const t = el('text', {x: pl - 6, y: yy + 3.5, 'text-anchor': 'end'});
    t.textContent = o.fmt(v); svg.append(t);
  }
  if (lo < 0 && hi > 0) svg.append(el('line', {x1: pl, x2: W - pr, y1: y(0), y2: y(0),
    stroke: css('--axis'), 'stroke-width': 1}));
  const labels = series[0].pts.map(p => p[0]);
  const step = Math.ceil(n / 8);
  labels.forEach((L, i) => { if (i % step === 0) {
    const t = el('text', {x: x(i), y: H - 6, 'text-anchor': 'middle'});
    t.textContent = o.xfmt ? o.xfmt(L) : L; svg.append(t); }});
  series.forEach(s => {
    const d = s.pts.map((p, i) => (p[1] == null ? '' :
      `${i === 0 || s.pts[i-1][1] == null ? 'M' : 'L'}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`)).join('');
    svg.append(el('path', {d, fill: 'none', stroke: css(s.color), 'stroke-width': 2,
      'stroke-linejoin': 'round'}));
  });
  if (series.length > 1) {
    const ends = series.map(s => {
      const last = [...s.pts].reverse().find(p => p[1] != null);
      return last && {name: s.name, color: s.color,
        x: x(s.pts.lastIndexOf(last)), y: y(last[1])};
    }).filter(Boolean).sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++)
      if (ends[i].y - ends[i-1].y < 13) ends[i].y = ends[i-1].y + 13;
    ends.forEach(e2 => {
      const t = el('text', {x: e2.x + 5, y: e2.y + 3.5, class: 'dl'});
      t.setAttribute('fill', css(e2.color)); t.textContent = e2.name; svg.append(t);
    });
  }
  const cross = el('line', {y1: pt, y2: H - pb, stroke: css('--axis'),
    'stroke-width': 1, 'stroke-dasharray': '3,3', visibility: 'hidden'});
  svg.append(cross);
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect();
    const mx = (e.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((mx - pl) / ((W - pl - pr) / (n - 1)))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('visibility', 'visible');
    let h = `<b>${o.xfmt ? o.xfmt(labels[i]) : labels[i]}</b>`;
    series.forEach(s => { const v = s.pts[i] && s.pts[i][1];
      if (v != null) h += `<div class="r"><span>${s.name}</span><span>${o.fmt(v)}</span></div>`; });
    showTT(h, e.clientX, e.clientY);
  });
  svg.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); hideTT(); });
  mount.append(svg);
}

function pairedBars(mount, rows, opts) {
  const o = Object.assign({h: 200, color: '--s1', fmt: v => v, unit: ''}, opts);
  const W = 720, H = o.h, pt = 10, pb = 24, pl = 40, pr = 8;
  const hi = Math.max(...rows.flatMap(r => [r.plan || 0, r.actual || 0])) * 1.08;
  const y = v => pt + (H - pt - pb) * (1 - v / hi);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let g = 0; g < 4; g++) {
    const v = hi * g / 3, yy = y(v);
    svg.append(el('line', {x1: pl, x2: W - pr, y1: yy, y2: yy, stroke: css('--grid')}));
    const t = el('text', {x: pl - 6, y: yy + 3.5, 'text-anchor': 'end'});
    t.textContent = o.fmt(v); svg.append(t);
  }
  const bw = (W - pl - pr) / rows.length;
  const bar = Math.min(14, bw * .28);
  rows.forEach((r, i) => {
    const cx = pl + bw * i + bw / 2;
    const t = el('text', {x: cx, y: H - 6, 'text-anchor': 'middle'});
    t.textContent = r.label; svg.append(t);
    const mk = (v, dx, fill) => { if (v == null) return null;
      const yy = y(v);
      return el('path', {d: `M${cx+dx-bar/2},${H-pb} L${cx+dx-bar/2},${yy+4}
        Q${cx+dx-bar/2},${yy} ${cx+dx-bar/2+4},${yy} L${cx+dx+bar/2-4},${yy}
        Q${cx+dx+bar/2},${yy} ${cx+dx+bar/2},${yy+4} L${cx+dx+bar/2},${H-pb} Z`
        .replace(/\s+/g,' '), fill}); };
    const p = mk(r.plan, -bar/2 - 1, css('--plan-ghost'));
    const a = mk(r.actual, bar/2 + 1, css(o.color));
    const zone = el('rect', {x: pl + bw*i, y: pt, width: bw, height: H - pt - pb,
      fill: 'transparent'});
    zone.addEventListener('mousemove', e => {
      const pct = r.plan && r.actual != null ? Math.round(100*r.actual/r.plan) : null;
      showTT(`<b>Week ${r.label}</b>
        <div class="r"><span>Planned</span><span>${o.fmt(r.plan)}${o.unit}</span></div>
        ${r.actual != null ? `<div class="r"><span>Actual</span><span>${o.fmt(r.actual)}${o.unit}</span></div>` : ''}
        ${pct != null ? `<div class="r"><span>Compliance</span><span>${pct}%</span></div>` : ''}`,
        e.clientX, e.clientY);
    });
    zone.addEventListener('mouseleave', hideTT);
    if (p) svg.append(p); if (a) svg.append(a); svg.append(zone);
  });
  mount.append(svg);
}

function miniLine(mount, title, pts, color, fmt) {
  const wrap = document.createElement('div');
  const h = document.createElement('div');
  h.style.cssText = 'font-size:11px;color:var(--ink-2);margin-bottom:2px;font-weight:600';
  h.textContent = title; wrap.append(h);
  lineChart(wrap, [{name: title, color, pts}], {h: 110, pad: [6, 10, 18, 34], fmt,
    xfmt: d => d.slice(5)});
  mount.append(wrap);
}

// ---- boxplot ----
function quartiles(v) {
  const a = [...v].sort((x, y) => x - y), n = a.length;
  const q = p => { const i = (n - 1) * p, f = Math.floor(i);
    return a[f] + (a[Math.min(f + 1, n - 1)] - a[f]) * (i - f); };
  const q1 = q(.25), med = q(.5), q3 = q(.75), iqr = q3 - q1;
  const loF = q1 - 1.5 * iqr, hiF = q3 + 1.5 * iqr;
  const inliers = a.filter(x => x >= loF && x <= hiF);
  return {n, min: a[0], max: a[n-1], q1, med, q3,
          wlo: inliers[0], whi: inliers[inliers.length - 1],
          out: a.filter(x => x < loF || x > hiF),
          mean: a.reduce((s, x) => s + x, 0) / n};
}

function boxplot(mount, groups, opts) {
  // groups: [{label, color(css var), stats}]
  mount.innerHTML = '';
  if (!groups.length) { mount.innerHTML = '<div class="note">No sessions in this slice.</div>'; return; }
  const o = Object.assign({fmt: v => v.toFixed(1), unit: ''}, opts);
  const W = 900, rowH = 46, pt = 8, pb = 30, pl = 120, pr = 30;
  const H = pt + pb + groups.length * rowH;
  let lo = Math.min(...groups.map(g => Math.min(g.stats.wlo, ...(g.stats.out.length ? [g.stats.min] : []))));
  let hi = Math.max(...groups.map(g => Math.max(g.stats.whi, ...(g.stats.out.length ? [g.stats.max] : []))));
  if (lo === hi) { lo -= 1; hi += 1; }
  const span = hi - lo; lo = Math.max(0, lo - span * .03); hi += span * .03;
  const x = v => pl + (W - pl - pr) * (v - lo) / (hi - lo);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi - lo) * g / 4, xx = x(v);
    svg.append(el('line', {x1: xx, x2: xx, y1: pt, y2: H - pb, stroke: css('--grid')}));
    const t = el('text', {x: xx, y: H - 10, 'text-anchor': 'middle'});
    t.textContent = o.fmt(v); svg.append(t);
  }
  groups.forEach((g, i) => {
    const cy = pt + i * rowH + rowH / 2, s = g.stats, col = css(g.color);
    const lab = el('text', {x: pl - 10, y: cy + 4, 'text-anchor': 'end', class: 'dl'});
    lab.setAttribute('fill', css('--ink-2')); lab.textContent = g.label; svg.append(lab);
    svg.append(el('line', {x1: x(s.wlo), x2: x(s.q1), y1: cy, y2: cy,
      stroke: col, 'stroke-width': 2}));
    svg.append(el('line', {x1: x(s.q3), x2: x(s.whi), y1: cy, y2: cy,
      stroke: col, 'stroke-width': 2}));
    [s.wlo, s.whi].forEach(v => svg.append(el('line', {x1: x(v), x2: x(v),
      y1: cy - 7, y2: cy + 7, stroke: col, 'stroke-width': 2})));
    svg.append(el('rect', {x: x(s.q1), y: cy - 11, width: Math.max(2, x(s.q3) - x(s.q1)),
      height: 22, rx: 4, fill: col, 'fill-opacity': .35, stroke: col, 'stroke-width': 1.5}));
    svg.append(el('line', {x1: x(s.med), x2: x(s.med), y1: cy - 11, y2: cy + 11,
      stroke: col, 'stroke-width': 2.5}));
    s.out.slice(0, 60).forEach(v => svg.append(el('circle',
      {cx: x(v), cy, r: 2.5, fill: col, 'fill-opacity': .55})));
    const zone = el('rect', {x: pl, y: cy - rowH/2, width: W - pl - pr, height: rowH,
      fill: 'transparent'});
    zone.addEventListener('mousemove', e => showTT(
      `<b>${g.label}</b> · ${s.n} sessions
       <div class="r"><span>Median</span><span>${o.fmt(s.med)}${o.unit}</span></div>
       <div class="r"><span>Middle 50%</span><span>${o.fmt(s.q1)}–${o.fmt(s.q3)}${o.unit}</span></div>
       <div class="r"><span>Typical range</span><span>${o.fmt(s.wlo)}–${o.fmt(s.whi)}${o.unit}</span></div>
       <div class="r"><span>Mean</span><span>${o.fmt(s.mean)}${o.unit}</span></div>
       <div class="r"><span>Max</span><span>${o.fmt(s.max)}${o.unit}</span></div>
       <div class="r"><span>Outliers</span><span>${s.out.length}</span></div>`,
      e.clientX, e.clientY));
    zone.addEventListener('mouseleave', hideTT);
    svg.append(zone);
  });
  mount.append(svg);
}

// ============================ render ============================
const D = DATA, P = D.plan;
const SPORT_COLOR = {run: '--s1', bike: '--s2', swim: '--s3', strength: '--s5', other: '--s6'};
document.getElementById('gen').textContent = D.generated;
const raceStart = P[15].start;
document.getElementById('sub').textContent = raceStart
  ? 'Race: ' + new Date(new Date(raceStart+'T00:00').getTime()+6*864e5).toDateString()
  : 'Race date not set';
if (D.demo) document.getElementById('banner').innerHTML =
  '<div class="demo-banner">DEMO DATA — run the ETL, then rebuild without --demo.</div>';

// ---- dashboard page ----
const lastLoad = D.pmc[D.pmc.length - 1];
const actual = D.actual;
const curWeek = actual.length ? actual[actual.length - 1] : null;
const curPlan = curWeek ? P[curWeek.week - 1] : P[0];
const READY = {green: ['●', '--good', 'Train as planned'],
  yellow: ['▲', '--warn', 'Reduce intensity'], red: ['■', '--crit', 'Recovery day']};
const tiles = [
  ['Current week', curWeek ? `W${curWeek.week}` : '—', curPlan ? `${curPlan.phase} phase` : ''],
  ['CTL · Fitness', lastLoad ? lastLoad[1].toFixed(0) : '—', 'target 55–70 by race'],
  ['ATL · Fatigue', lastLoad ? lastLoad[2].toFixed(0) : '—', '7-day load'],
  ['TSB · Form', lastLoad ? (lastLoad[3] > 0 ? '+' : '') + lastLoad[3].toFixed(0) : '—', 'race day: +5 to +15'],
  ['Week compliance', curWeek && curPlan ? Math.round(100 * curWeek.hours / curPlan.hours) + '%' : '—', 'hours vs plan · goal ≥85%'],
  ['Readiness', null, 'from RHR/HRV/sleep/TSB'],
];
document.getElementById('tiles').innerHTML = tiles.map(t => {
  let val = t[1] === null
    ? (D.readiness && READY[D.readiness]
        ? `<span class="badge" style="color:var(${READY[D.readiness][1]})">${READY[D.readiness][0]} ${D.readiness.toUpperCase()}</span>` : '—')
    : t[1];
  return `<div class="tile"><div class="lbl">${t[0]}</div><div class="val">${val}</div><div class="hint">${t[2]}</div></div>`;
}).join('');

if (D.pmc.length)
  lineChart(document.getElementById('pmc'), [
    {name: 'CTL', color: '--s1', pts: D.pmc.map(r => [r[0], r[1]])},
    {name: 'ATL', color: '--s2', pts: D.pmc.map(r => [r[0], r[2]])},
    {name: 'TSB', color: '--s3', pts: D.pmc.map(r => [r[0], r[3]])},
  ], {xfmt: d => d.slice(5)});
else document.getElementById('pmc').innerHTML = '<div class="note">No activities yet.</div>';

const byWeek = Object.fromEntries(actual.map(a => [a.week, a]));
pairedBars(document.getElementById('hours'),
  P.map(p => ({label: p.week, plan: p.hours, actual: byWeek[p.week] ? byWeek[p.week].hours : null})),
  {fmt: v => (+v).toFixed(0), unit: 'h'});

[['Swim', 'swim_m', ' m', '--s1'], ['Bike', 'bike_km', ' km', '--s2'],
 ['Run', 'run_km', ' km', '--s3']].forEach(([name, key, unit, color]) => {
  const wrap = document.createElement('div');
  const h = document.createElement('div');
  h.style.cssText = 'font-size:12px;font-weight:600;color:var(--ink-2);margin-bottom:4px';
  h.textContent = name; wrap.append(h);
  pairedBars(wrap, P.map(p => ({label: p.week, plan: p[key],
    actual: byWeek[p.week] ? byWeek[p.week][key] : null})),
    {h: 150, color, unit, fmt: v => (+v) >= 1000 ? ((+v)/1000).toFixed(1) + 'k' : (+v).toFixed(0)});
  document.getElementById('sports').append(wrap);
});

const effB = D.eff.filter(e => e[1] === 'bike').map(e => [e[0], e[2]]);
const effR = D.eff.filter(e => e[1] === 'run').map(e => [e[0], e[2]]);
const effDates = [...new Set(D.eff.map(e => e[0]))].sort();
if (effDates.length) {
  const idx = Object.fromEntries(effDates.map((d, i) => [d, i]));
  const mk = pts => { const a = effDates.map(() => null);
    pts.forEach(p => a[idx[p[0]]] = p[1]);
    return effDates.map((d, i) => [d, a[i]]); };
  lineChart(document.getElementById('eff'), [
    {name: 'Bike', color: '--s1', pts: mk(effB)},
    {name: 'Run', color: '--s2', pts: mk(effR)},
  ], {h: 180, fmt: v => v.toFixed(2), xfmt: d => d.slice(5)});
} else document.getElementById('eff').innerHTML =
  '<div class="note">Needs steady sessions ≥40min with HR.</div>';

const vm = document.getElementById('vitals');
if (D.vitals.length) {
  const V = D.vitals.slice(-28);
  miniLine(vm, 'Resting HR (bpm)', V.map(r => [r[0], r[1]]), '--s1', v => v.toFixed(0));
  miniLine(vm, 'HRV SDNN (ms)', V.map(r => [r[0], r[2]]), '--s2', v => v.toFixed(0));
  miniLine(vm, 'Sleep (h)', V.map(r => [r[0], r[3]]), '--s1', v => v.toFixed(1));
  miniLine(vm, 'Weight (kg)', V.map(r => [r[0], r[4]]), '--s2', v => v.toFixed(1));
} else vm.innerHTML = '<div class="note">No vitals — import Apple Health export.</div>';

document.getElementById('plantable').innerHTML =
  `<tr><th>Wk</th><th>Phase</th><th>Hours</th><th>TSS</th><th>Swim</th>
   <th>Bike</th><th>Run</th><th>Long ride</th><th>Long run</th><th>Key workouts</th></tr>` +
  P.map(p => `<tr class="${p.recovery ? 'rec' : ''}">
    <td>${p.week}</td>
    <td><span class="phase">${p.phase}</span>${p.recovery ? ' 💤' : ''}</td>
    <td>${p.hours}</td><td>${p.tss}</td>
    <td>${(p.swim_m/1000).toFixed(1)}k</td><td>${p.bike_km} km</td><td>${p.run_km} km</td>
    <td>${Math.floor(p.long_ride/60)}:${String(p.long_ride%60).padStart(2,'0')}</td>
    <td>${p.long_run}min</td>
    <td class="key">${p.key}</td></tr>`).join('');

// ---- statistics page ----
const ACTS = D.acts;   // [date, sport, km, hours, hr]
const YEARS = [...new Set(ACTS.map(a => +a[0].slice(0, 4)))].sort();
const SPORTS = [...new Set(ACTS.map(a => a[1]))]
  .sort((a, b) => Object.keys(SPORT_COLOR).indexOf(a) - Object.keys(SPORT_COLOR).indexOf(b));
const st = {metric: 'km', group: 'sport', sport: 'run',
            y0: YEARS[0], y1: YEARS[YEARS.length - 1], on: new Set(SPORTS)};
const M = {km: {i: 2, fmt: v => v.toFixed(1), unit: ' km', min: 0.2},
           hours: {i: 3, fmt: v => v.toFixed(1), unit: ' h', min: 0.05},
           hr: {i: 4, fmt: v => v.toFixed(0), unit: ' bpm', min: 60}};
const y0s = document.getElementById('st-y0'), y1s = document.getElementById('st-y1');
YEARS.forEach(y => { y0s.add(new Option(y, y)); y1s.add(new Option(y, y)); });
y0s.value = st.y0; y1s.value = st.y1;
const spsel = document.getElementById('st-sportsel');
SPORTS.forEach(s => spsel.add(new Option(s, s)));
spsel.value = st.sport;
const chips = document.getElementById('st-chips');
SPORTS.forEach(s => {
  const c = document.createElement('span');
  c.className = 'chip on'; c.textContent = s;
  c.onclick = () => { st.on.has(s) ? st.on.delete(s) : st.on.add(s);
    c.classList.toggle('on'); renderStats(); };
  chips.append(c);
});
['st-metric', 'st-group', 'st-sportsel', 'st-y0', 'st-y1'].forEach(id =>
  document.getElementById(id).addEventListener('change', () => {
    st.metric = document.getElementById('st-metric').value;
    st.group = document.getElementById('st-group').value;
    st.sport = spsel.value;
    st.y0 = +y0s.value; st.y1 = +y1s.value;
    document.getElementById('st-sportsel-wrap').style.display =
      st.group === 'sport_year' ? '' : 'none';
    renderStats();
  }));

function renderStats() {
  const m = M[st.metric];
  let pool = ACTS.filter(a => {
    const y = +a[0].slice(0, 4), v = a[m.i];
    return y >= st.y0 && y <= st.y1 && v != null && v >= m.min && st.on.has(a[1]);
  });
  let groups;
  if (st.group === 'sport') {
    groups = SPORTS.filter(s => st.on.has(s)).map(s => ({
      label: s, color: SPORT_COLOR[s] || '--s6',
      vals: pool.filter(a => a[1] === s).map(a => a[m.i])}));
  } else if (st.group === 'year') {
    groups = YEARS.filter(y => y >= st.y0 && y <= st.y1).map(y => ({
      label: '' + y, color: '--s1',
      vals: pool.filter(a => +a[0].slice(0, 4) === y).map(a => a[m.i])}));
  } else {
    groups = YEARS.filter(y => y >= st.y0 && y <= st.y1).map(y => ({
      label: `${st.sport} ${y}`, color: SPORT_COLOR[st.sport] || '--s1',
      vals: pool.filter(a => a[1] === st.sport && +a[0].slice(0, 4) === y).map(a => a[m.i])}));
  }
  groups = groups.filter(g => g.vals.length >= 3)
                 .map(g => ({...g, stats: quartiles(g.vals)}));
  boxplot(document.getElementById('boxplot'), groups, {fmt: m.fmt, unit: m.unit});
  document.getElementById('st-table').innerHTML =
    `<tr><th>Group</th><th>Sessions</th><th>Total</th><th>Mean</th>
     <th>Median</th><th>Middle 50%</th><th>Max</th><th>Outliers</th></tr>` +
    groups.map(g => { const s = g.stats;
      return `<tr><td class="key">${g.label}</td><td>${s.n}</td>
        <td>${m.fmt(g.vals.reduce((a, b) => a + b, 0))}${m.unit}</td>
        <td>${m.fmt(s.mean)}${m.unit}</td><td>${m.fmt(s.med)}${m.unit}</td>
        <td>${m.fmt(s.q1)}–${m.fmt(s.q3)}${m.unit}</td>
        <td>${m.fmt(s.max)}${m.unit}</td><td>${s.out.length}</td></tr>`; }).join('');
}
renderStats();

// ---- profile page ----
const pr = D.profile;
const first = ACTS.length ? ACTS[0][0].slice(0, 4) : '—';
const tot = {n: ACTS.length,
  hours: ACTS.reduce((s, a) => s + (a[3] || 0), 0),
  runkm: ACTS.filter(a => a[1] === 'run').reduce((s, a) => s + (a[2] || 0), 0),
  bikekm: ACTS.filter(a => a[1] === 'bike').reduce((s, a) => s + (a[2] || 0), 0),
  longrun: Math.max(0, ...ACTS.filter(a => a[1] === 'run').map(a => a[2] || 0)),
  longride: Math.max(0, ...ACTS.filter(a => a[1] === 'bike').map(a => a[2] || 0))};
document.getElementById('hero').innerHTML = `
  ${pr.photo ? `<img class="avatar" src="${pr.photo}" alt="${pr.name}">`
             : `<div class="avatar-ph">${pr.name.split(' ').map(w => w[0]).join('')}</div>`}
  <div>
    <h1>${pr.name}</h1>
    <div class="tag">▲ ${pr.tagline} · training since ${first}</div>
    <div class="statchips">
      <div class="statchip"><b>${tot.n.toLocaleString()}</b>activities</div>
      <div class="statchip"><b>${Math.round(tot.hours).toLocaleString()}</b>hours</div>
      <div class="statchip"><b>${Math.round(tot.runkm).toLocaleString()} km</b>run</div>
      <div class="statchip"><b>${Math.round(tot.bikekm).toLocaleString()} km</b>bike</div>
      <div class="statchip"><b>${tot.longrun.toFixed(1)} km</b>longest run</div>
      <div class="statchip"><b>${tot.longride.toFixed(1)} km</b>longest ride</div>
    </div>
  </div>`;

const prs = D.efforts.filter(e => e.pr);
document.getElementById('pr-table').innerHTML =
  `<tr><th>Distance</th><th>Activity</th><th>Time</th><th>Pace</th><th>Date</th><th>Full run</th></tr>` +
  prs.map(e => `<tr><td><b>${e.band}</b> <span class="pr-star">★</span></td>
    <td class="key">run</td><td>${e.time}</td><td>${e.pace} /km</td>
    <td>${e.date}</td><td>${e.km} km</td></tr>`).join('') ||
  '<tr><td class="key">No efforts yet.</td></tr>';

const bands = [...new Set(D.efforts.map(e => e.band))];
document.getElementById('efforts-table').innerHTML =
  `<tr><th>Distance</th><th>Year</th><th>Time</th><th>Pace</th><th>Date</th></tr>` +
  bands.map(b => D.efforts.filter(e => e.band === b)
    .sort((a, c) => c.year - a.year).slice(0, 4)
    .map((e, i) => `<tr>
      <td>${i === 0 ? `<b>${b}</b>` : ''}</td>
      <td>${e.year}${e.pr ? ' <span class="pr-star">★</span>' : ''}</td>
      <td>${e.time}</td><td>${e.pace} /km</td><td class="key">${e.date}</td></tr>`)
    .join('')).join('');

document.getElementById('records-table').innerHTML =
  `<tr><th>Record</th><th>Value</th><th>When</th></tr>` +
  D.records.map(r => `<tr><td class="key">${r.record}</td>
    <td><b>${r.value}</b></td><td>${r.date || '—'}</td></tr>`).join('');

route();
</script>
"""

if __name__ == "__main__":
    main()
