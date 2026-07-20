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
ATHLETE_ID = int(os.environ.get("ATHLETE_ID", "1"))


def _num(v):
    from decimal import Decimal
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def rows(con, sql, keys, params=()):
    return [dict(zip(keys, [_num(v) for v in r]))
            for r in con.execute(sql, params).fetchall()]


def fetch(con):
    A = (ATHLETE_ID,)
    # distances converted to miles at the query edge; storage stays metric
    plan = rows(con, """
        SELECT week_num, phase, start_date::text, planned_hours, planned_tss,
               swim_m, ROUND(bike_km / 1.609344, 1), ROUND(run_km / 1.609344, 1),
               long_ride_min, long_run_min, is_recovery, focus, key_workouts
        FROM weekly_plan WHERE athlete_id = %s ORDER BY week_num""",
        ["week", "phase", "start", "hours", "tss", "swim_m", "bike_mi", "run_mi",
         "long_ride", "long_run", "recovery", "focus", "key"], A)
    comp = rows(con, """
        SELECT week_num, actual_hours, actual_tss, actual_swim_m,
               ROUND(actual_bike_km / 1.609344, 1), ROUND(actual_run_km / 1.609344, 1)
        FROM v_weekly_compliance WHERE athlete_id = %s ORDER BY week_num""",
        ["week", "hours", "tss", "swim_m", "bike_mi", "run_mi"], A)
    pmc = [[str(r[0])] + [float(x) for x in r[1:]] for r in con.execute(
        """SELECT date::text, ctl, atl, tsb FROM daily_load
           WHERE athlete_id = %s AND date >= CURRENT_DATE - 182
           ORDER BY date""", A).fetchall()]
    vitals = [[str(r[0])] + [float(x) if x is not None else None for x in r[1:]]
              for r in con.execute(
        """SELECT date::text, resting_hr, hrv_ms, sleep_hours, weight_kg
           FROM daily_vitals WHERE athlete_id = %s ORDER BY date""", A).fetchall()]
    eff = [[str(r[0]), r[1], float(r[2])] for r in con.execute(
        """SELECT date::text, sport, efficiency_factor FROM v_efficiency
           WHERE athlete_id = %s AND efficiency_factor IS NOT NULL
             AND date >= CURRENT_DATE - 182 ORDER BY date""", A).fetchall()]
    ready = con.execute(
        """SELECT readiness FROM v_readiness WHERE athlete_id = %s
           ORDER BY date DESC LIMIT 1""", A).fetchone()
    acts = [[str(r[0]), r[1], _num(r[2]), _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6])]
            for r in con.execute("""
        SELECT start_time::date, sport, ROUND(distance_m/1609.344, 2),
               ROUND(duration_s/3600.0, 2), avg_hr,
               EXTRACT(HOUR FROM start_time)::int, ROUND(COALESCE(tss, 0), 0)
        FROM activities WHERE athlete_id = %s AND duration_s > 0
        ORDER BY start_time""", A).fetchall()]
    efforts = rows(con, """
        SELECT band, meters, year, date::text, est_time, pace_min_mi,
               run_mi, is_all_time_pr
        FROM v_best_efforts WHERE athlete_id = %s ORDER BY meters, year""",
        ["band", "meters", "year", "date", "time", "pace", "mi", "pr"], A)
    records = rows(con, "SELECT record, value, date FROM v_records WHERE athlete_id = %s",
                   ["record", "value", "date"], A)
    st_row = con.execute("""
        SELECT a.name, a.tagline, s.race_date::text, s.ctl_target,
               s.compliance_goal_pct, s.tsb_race_lo, s.tsb_race_hi
        FROM athletes a JOIN athlete_settings s ON s.athlete_id = a.id
        WHERE a.id = %s""", A).fetchone()
    settings = dict(zip(["name", "tagline", "race_date", "ctl_target",
                         "compliance_goal_pct", "tsb_race_lo", "tsb_race_hi"],
                        st_row)) if st_row else {}
    vm = [[str(r[0])] + [_num(x) for x in r[1:]] for r in con.execute("""
        SELECT month::text, rhr_avg, rhr_min, rhr_max, hrv_avg, sleep_avg,
               weight_lb_avg, vo2_avg
        FROM v_vitals_monthly WHERE athlete_id = %s ORDER BY month""", A).fetchall()]
    whr = [[str(r[0]), r[1], _num(r[2])] for r in con.execute("""
        SELECT month::text, sport, avg_hr FROM v_workout_hr_monthly
        WHERE athlete_id = %s AND sessions >= 3 ORDER BY month""", A).fetchall()]
    return (plan, comp, pmc, vitals, eff, (ready[0] if ready else None),
            acts, efforts, records, settings, vm, whr)


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
                     "bike_mi": round(p["bike_mi"] * f, 1),
                     "run_mi": round(p["run_mi"] * f, 1)})
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
            mi = {"run": abs(rng.gauss(5, 2.5)), "bike": abs(rng.gauss(18, 9)),
                  "swim": abs(rng.gauss(0.9, .35))}[sp]
            hrs = mi / {"run": 6, "bike": 15.5, "swim": 2}[sp]
            acts.append([f"{y}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
                         sp, round(mi, 2), round(hrs, 2), round(rng.gauss(140, 12)),
                         rng.choice([6, 7, 8, 12, 17, 18]), round(hrs * 55)])
    efforts = [{"band": b, "meters": m, "year": 2026, "date": "2026-05-01",
                "time": t, "pace": p, "mi": round(m/1609.344, 1), "pr": True}
               for b, m, t, p in [("5k", 5000, "00:23:10", "07:28"),
                                  ("10k", 10000, "00:48:30", "07:48"),
                                  ("Half marathon", 21097, "01:51:20", "08:30")]]
    records = [{"record": "Longest run (mi)", "value": "21.2", "date": "2025-10-12"},
               {"record": "Longest ride (mi)", "value": "73.6", "date": "2024-08-03"},
               {"record": "Biggest week (hours)", "value": "12.4", "date": "2025-09-29"}]
    return comp, pmc, vitals, eff, "green", acts, efforts, records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    con = connect()
    (plan, comp, pmc, vitals, eff, ready, acts, efforts, records,
     settings, vm, whr) = fetch(con)
    con.close()
    photo = None
    if args.demo:
        comp, pmc, vitals, eff, ready, acts, efforts, records = demo_data(plan)
        vm, whr = [], []
        settings = {"name": "Demo Athlete", "tagline": "Journey to Ironman 70.3",
                    "race_date": None, "ctl_target": 65, "compliance_goal_pct": 85,
                    "tsb_race_lo": 5, "tsb_race_hi": 15}
    elif PHOTO.exists():
        photo = "data:image/png;base64," + base64.b64encode(PHOTO.read_bytes()).decode()

    data = {
        "generated": date.today().isoformat(),
        "demo": args.demo,
        "readiness": ready,
        "profile": {"name": settings.get("name", "Athlete"), "photo": photo,
                    "tagline": settings.get("tagline") or "Journey to Ironman 70.3"},
        "settings": settings,
        "plan": plan,
        "actual": [c for c in comp if c.get("hours") is not None],
        "pmc": pmc, "vitals": vitals, "eff": eff,
        "acts": acts, "efforts": efforts, "records": records,
        "vm": vm, "whr": whr,
    }
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data))
    OUT.write_text(html)
    print(f"Wrote {OUT} ({'demo' if args.demo else 'live'}: "
          f"{len(acts)} activities, {len(efforts)} effort rows)")


TEMPLATE = r"""<title>TriPeak — Training Intelligence</title>
<style>
  :root {
    --surface-1:#ffffff; --page:#f2f4fa; --ink:#111527; --ink-2:#4c5570;
    --muted:#7d86a3; --grid:#e5e8f2; --axis:#c7cee0;
    --border:rgba(30,50,120,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s5:#4a3aa7; --s6:#e34948;
    --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
    --plan-ghost:#e5e8f2; --brand:#0d7ec2; --nav:#ffffff;
  }
  @media (prefers-color-scheme: dark) { :root {
    --surface-1:#151a33; --page:#0b0f22; --ink:#f2f5ff; --ink-2:#aab3d6;
    --grid:#252c4e; --axis:#333b63; --border:rgba(140,160,255,0.14);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s5:#9085e9; --s6:#e66767;
    --plan-ghost:#252c4e; --brand:#38bdf8; --nav:#10142a;
  }}
  :root[data-theme="dark"] {
    --surface-1:#151a33; --page:#0b0f22; --ink:#f2f5ff; --ink-2:#aab3d6;
    --grid:#252c4e; --axis:#333b63; --border:rgba(140,160,255,0.14);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500; --s5:#9085e9; --s6:#e66767;
    --plan-ghost:#252c4e; --brand:#38bdf8; --nav:#10142a;
  }
  :root[data-theme="light"] {
    --surface-1:#ffffff; --page:#f2f4fa; --ink:#111527; --ink-2:#4c5570;
    --muted:#7d86a3; --grid:#e5e8f2; --axis:#c7cee0;
    --border:rgba(30,50,120,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100; --s5:#4a3aa7; --s6:#e34948;
    --plan-ghost:#e5e8f2; --brand:#0d7ec2; --nav:#ffffff;
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
  .tiles { display: grid; grid-template-columns: repeat(auto-fit,minmax(175px,1fr));
    gap: 12px; margin-bottom: 18px; }
  .tile { min-width: 0; overflow: hidden; }
  .tile { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 14px; padding: 14px 16px; display: flex; gap: 10px;
    align-items: center; justify-content: space-between; }
  .tile .lbl { font-size: 10.5px; color: var(--ink-2); margin-bottom: 4px;
    text-transform: uppercase; letter-spacing: .7px; font-weight: 650; }
  .tile .val { font-size: 25px; font-weight: 700; }
  .tile .hint { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .ring { flex: none; }
  .ring text { font-size: 13px; font-weight: 700; fill: var(--ink); }
  .planbar { height: 8px; border-radius: 99px; background: var(--plan-ghost);
    overflow: hidden; margin-top: 8px; }
  .planbar i { display: block; height: 100%; border-radius: 99px;
    background: linear-gradient(90deg, var(--brand), var(--s5)); }
  .sesscards { display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr));
    gap: 12px; }
  .sess { background: var(--page); border: 1px solid var(--border);
    border-radius: 12px; padding: 12px 14px; }
  .sess .sp { font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .6px; }
  .sess .big { font-size: 19px; font-weight: 700; margin: 4px 0 2px; }
  .sess .meta { font-size: 11.5px; color: var(--muted); }
  .card { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .card h2 { font-size: 11.5px; margin: 0 0 3px; text-transform: uppercase;
    letter-spacing: .8px; color: var(--brand); font-weight: 700; }
  .card h2::after { content: ""; display: block; width: 34px; height: 2px;
    background: var(--brand); border-radius: 2px; margin-top: 4px; opacity: .8; }
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
  select:focus-visible, .chip:focus-visible, .tabs a:focus-visible {
    outline: 2px solid var(--brand); outline-offset: 1px; }
  select:hover { border-color: var(--brand); }
  .phase { color: var(--brand);
    background: color-mix(in srgb, var(--brand) 8%, transparent); }
  a { color: var(--brand); }
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
  .btn { display: inline-flex; align-items: center; font: inherit; font-size: 13px;
    font-weight: 650; color: #fff; background: var(--brand); border: none;
    border-radius: 9px; padding: 8px 16px; cursor: pointer; text-decoration: none; }
  .btn:hover { filter: brightness(1.1); }
  .btn.ghost { background: transparent; color: var(--brand);
    border: 1px solid var(--brand); }
  .btn:disabled { opacity: .5; cursor: default; }
  #plan-edit input, #plan-edit select { background: var(--page); color: var(--ink);
    font: inherit; font-size: 12.5px; border: 1px solid var(--grid);
    border-radius: 6px; padding: 4px 6px; width: 100%; box-sizing: border-box; }
  #plan-edit input:focus { border-color: var(--brand); outline: none; }
  #plan-edit input.num { width: 64px; text-align: right;
    font-variant-numeric: tabular-nums; }
  #plan-edit td { padding: 4px 4px; }
  .report { border-radius: 10px; padding: 10px 14px; margin-top: 12px;
    font-size: 12.5px; }
  .report.err { border: 1px solid var(--crit);
    background: color-mix(in srgb, var(--crit) 8%, transparent); }
  .report.warn { border: 1px solid var(--warn);
    background: color-mix(in srgb, var(--warn) 8%, transparent); }
  .report.ok { border: 1px solid var(--good);
    background: color-mix(in srgb, var(--good) 8%, transparent); }
  .report ul { margin: 4px 0 0 18px; padding: 0; }
  .report li { margin: 2px 0; }
  .num-in { background: var(--page); color: var(--ink); font: inherit;
    font-size: 13px; border: 1px solid var(--axis); border-radius: 8px;
    padding: 5px 8px; width: 76px; text-align: right; }
  .ic { width: 15px; height: 15px; flex: none; vertical-align: -2px; }
  .lbl .ic, .sp .ic { margin-right: 4px; opacity: .85; }
  .tile .lbl { display: flex; align-items: center; gap: 5px; }
  .sess .sp { display: flex; align-items: center; gap: 4px; }
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
    <a href="#health" data-p="health">Health</a>
    <a href="#profile" data-p="profile">Profile</a>
    <a href="#plan" data-p="plan">Plan</a>
    <a href="#admin" data-p="admin">Admin</a>
    <a href="#guide" data-p="guide">Guide</a>
  </div>
  <div class="navright" id="sub"></div>
</nav>
<main>
<div id="banner"></div>

<!-- ============================ DASHBOARD ============================ -->
<div class="page on" id="page-dash">
  <div class="tiles" id="tiles"></div>
  <div class="row" style="grid-template-columns:2fr 1fr">
    <div class="card">
      <h2>Latest sessions</h2>
      <div class="sesscards" id="sesscards"></div>
    </div>
    <div class="card">
      <h2>Plan progress</h2>
      <div class="note" id="planprog-note"></div>
      <div class="planbar"><i id="planprog" style="width:0%"></i></div>
      <div class="hint" style="font-size:11px;color:var(--muted);margin-top:8px"
           id="planprog-hint"></div>
    </div>
  </div>
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
    <div class="note">Swim (m), Bike (mi), Run (mi).</div>
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
  <div class="sub">One filter bar drives every chart and the summary below.</div>
  <div class="card" style="position:sticky;top:64px;z-index:4">
    <div class="controls" style="margin-bottom:0">
      <div class="ctrl"><label>Metric</label>
        <select id="st-metric">
          <option value="mi">Distance (mi)</option>
          <option value="hours">Duration (hours)</option>
          <option value="n">Sessions</option>
          <option value="tss">Training stress (TSS)</option>
        </select></div>
      <div class="ctrl"><label>Grain</label>
        <select id="st-grain">
          <option value="month">Month</option>
          <option value="year">Year</option>
        </select></div>
      <div class="ctrl"><label>From</label><select id="st-y0"></select></div>
      <div class="ctrl"><label>To</label><select id="st-y1"></select></div>
      <div class="ctrl"><label>Sports</label><div class="chips" id="st-chips"></div></div>
    </div>
  </div>
  <div class="card">
    <h2>Volume over time</h2>
    <div class="note">Stacked by sport at the selected grain. Hover any bar for the split.</div>
    <div class="legend" id="st-vol-legend"></div>
    <div id="st-volume"></div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Year-over-year cumulative</h2>
      <div class="note">Running total through the year — are you ahead of last year? (5 most recent years in range)</div>
      <div id="st-yoy"></div>
    </div>
    <div class="card">
      <h2>Session size distribution</h2>
      <div class="note">How big your sessions are, bucketed. Filters apply.</div>
      <div id="st-hist"></div>
    </div>
  </div>
  <div class="card">
    <h2>When you train</h2>
    <div class="note">Sessions by day of week × start hour. Darker = more sessions.</div>
    <div id="st-heat"></div>
  </div>
  <div class="card">
    <h2>Summary by sport</h2>
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

<!-- ============================ HEALTH ============================ -->
<div class="page" id="page-health">
  <h1>Health — heart &amp; recovery</h1>
  <div class="sub">Apple Health vitals across the years. Bands show your good / watch / risk zones — aim to keep the line in the green.</div>
  <div class="controls" style="margin-bottom:14px">
    <div class="ctrl"><label>Range</label><div class="chips" id="h-range">
      <span class="chip" data-r="12">1 year</span>
      <span class="chip" data-r="36">3 years</span>
      <span class="chip on" data-r="999">All</span>
    </div></div>
    <span class="note" style="margin:0;align-self:flex-end">Zones: <span style="color:var(--good)">● good</span> · <span style="color:var(--warn)">▲ watch</span> · <span style="color:var(--crit)">■ risk</span> — details in the <a href="#guide">Guide</a></span>
  </div>
  <div class="tiles" id="h-tiles"></div>
  <div class="row">
    <div class="card">
      <h2>Resting heart rate — monthly, all years</h2>
      <div class="note">Monthly average. Hover for the month's min–max spread.</div>
      <div id="h-rhr"></div>
    </div>
    <div class="card">
      <h2>Resting HR — year over year</h2>
      <div class="note">Same months compared across years. Lower line = fitter year.</div>
      <div id="h-rhr-yoy"></div>
    </div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Workout heart rate by sport</h2>
      <div class="note">Monthly average HR on runs and rides (months with ≥3 sessions). Falling at the same pace = efficiency.</div>
      <div class="legend">
        <span><span class="sw" style="background:var(--s1)"></span>Run</span>
        <span><span class="sw" style="background:var(--s2)"></span>Bike</span>
      </div>
      <div id="h-whr"></div>
    </div>
    <div class="card">
      <h2>HRV — heart rate variability</h2>
      <div class="note">Monthly average SDNN. Higher = better recovery capacity.</div>
      <div id="h-hrv"></div>
    </div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Sleep — hours per night</h2>
      <div class="note">Monthly average with your zones: <span style="color:var(--good)">● ≥7h good</span> · <span style="color:var(--warn)">▲ 6–7h watch</span> · <span style="color:var(--crit)">■ &lt;6h risk</span>. Build weeks need the green band.</div>
      <div id="h-sleep"></div>
    </div>
    <div class="card">
      <h2>VO₂max estimate</h2>
      <div class="note">Apple Watch estimate from outdoor runs/walks. 40+ = "excellent" for 40s age group.</div>
      <div id="h-vo2"></div>
    </div>
  </div>
</div>

<!-- ============================ ADMIN ============================ -->
<div class="page" id="page-admin">
  <h1>Admin — athletes</h1>
  <div class="sub">Create athletes and import their Apple Health data. Each athlete gets the template 16-week plan, their own settings, and isolated data.</div>
  <div id="admin-offline" class="demo-banner" style="display:none">
    Read-only view — you're looking at the static file. Admin needs the app
    server: open <a href="http://localhost:8703/#admin">http://localhost:8703/#admin</a>.
    The server auto-starts via launchd; if it's down:
    <code>.venv/bin/uvicorn server:app --port 8703</code>
  </div>
  <div class="card" id="admin-main" style="display:none">
    <h2>Athletes</h2>
    <div class="tablewrap"><table id="admin-list"></table></div>
    <div class="controls" style="margin-top:14px">
      <div class="ctrl"><label>New athlete name</label>
        <input type="text" id="adm-name" style="background:var(--page);color:var(--ink);
          font:inherit;font-size:13px;border:1px solid var(--axis);border-radius:8px;padding:6px 10px"></div>
      <button class="btn" id="adm-create">Create athlete</button>
    </div>
    <div class="controls" style="margin-top:6px">
      <div class="ctrl"><label>Import Apple Health for</label><select id="adm-athlete"></select></div>
      <div class="ctrl" style="flex:1;min-width:280px"><label>Path to export.zip / export.xml on this machine</label>
        <input type="text" id="adm-path" placeholder="~/Downloads/apple_health_export/export.xml"
          style="background:var(--page);color:var(--ink);font:inherit;font-size:13px;
          border:1px solid var(--axis);border-radius:8px;padding:6px 10px"></div>
      <button class="btn ghost" id="adm-import">Import</button>
    </div>
    <div class="note" style="margin-top:6px">Exports run to 100s of MB, so the import reads a file path on this machine rather than a browser upload. Duplicates of Strava workouts are detected and skipped automatically.</div>
    <div id="admin-report"></div>
  </div>
</div>

<!-- ============================ PLAN EDITOR ============================ -->
<div class="page" id="page-plan">
  <h1>Plan editor</h1>
  <div class="sub">Edit inline or upload a CSV — every save runs data-quality checks against the backend before anything lands.</div>
  <div id="plan-offline" class="demo-banner" style="display:none">
    Read-only view — you're looking at the static file. Plan editing needs the
    app server: open <a href="http://localhost:8703/#plan">http://localhost:8703/#plan</a>.
    The server auto-starts via launchd; if it's down:
    <code>.venv/bin/uvicorn server:app --port 8703</code>
  </div>
  <div class="card" id="kickoff-card" style="display:none">
    <h2>Kickoff — anchor the plan &amp; set your targets</h2>
    <div class="note">Setting the race date stamps every week's start date so plan-vs-actual, current week, and the countdown all come alive. Targets drive the dashboard rings and the plan checks.</div>
    <div class="controls">
      <div class="ctrl"><label>Race date</label>
        <input type="date" id="ko-race" style="background:var(--page);color:var(--ink);
          font:inherit;font-size:13px;border:1px solid var(--axis);border-radius:8px;padding:5px 8px"></div>
      <div class="ctrl"><label>CTL target</label><input type="number" class="num-in" id="ko-ctl"></div>
      <div class="ctrl"><label>Compliance goal %</label><input type="number" class="num-in" id="ko-comp"></div>
      <div class="ctrl"><label>Ramp limit %</label><input type="number" class="num-in" id="ko-ramp"></div>
      <div class="ctrl"><label>Long run cap (min)</label><input type="number" class="num-in" id="ko-lr"></div>
      <button class="btn" id="ko-save">Kickoff / update</button>
    </div>
    <div id="ko-report"></div>
  </div>
  <div class="card" id="plan-tools" style="display:none">
    <div class="controls" style="margin-bottom:0;align-items:center">
      <button class="btn" id="plan-save">Save changes</button>
      <a class="btn ghost" href="/api/plan.csv" download>Download CSV</a>
      <label class="btn ghost" style="cursor:pointer">Upload CSV
        <input type="file" id="plan-file" accept=".csv" style="display:none"></label>
      <span class="note" style="margin:0">Distances in miles · swim in meters · CSV round-trips through Download → edit → Upload</span>
    </div>
    <div id="plan-report"></div>
  </div>
  <div class="card">
    <h2>16-week plan</h2>
    <div class="note">Hard errors block the save; warnings save but flag coaching risks (ramp &gt;30%, long run &gt;150min, empty swim weeks…).</div>
    <div class="tablewrap"><table id="plan-edit"></table></div>
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
      <tr><td><b>ACWR</b></td><td class="key">Acute:Chronic Workload Ratio — this week's run miles ÷ 4-week average.</td><td class="key">Safe 0.8–1.3. Above 1.5 = injury-risk ramp.</td></tr>
      <tr><td><b>Readiness</b></td><td class="key">Daily green/yellow/red from resting HR, HRV, sleep and TSB vs your baselines.</td><td class="key">Red = recovery day. No exceptions.</td></tr>
      <tr><td><b>Resting HR</b></td><td class="key">Morning heart rate from Apple Watch. The cheapest fitness and fatigue signal there is.</td><td class="key">Trending down over months = fitter. +5–7 bpm overnight = incoming illness or under-recovery.</td></tr>
      <tr><td><b>HRV (SDNN)</b></td><td class="key">Beat-to-beat variability. Reflects autonomic recovery capacity.</td><td class="key">Compare only against your own range — absolute numbers vary hugely between people.</td></tr>
      <tr><td><b>VO₂max</b></td><td class="key">Apple's estimate of aerobic ceiling from outdoor runs.</td><td class="key">42+ is excellent for the 40s age group. Slow-moving — check monthly, not daily.</td></tr>
      <tr><td><b>Sleep</b></td><td class="key">Nightly hours from the watch. The #1 recovery lever.</td><td class="key">≥7h in build weeks. Under 6h, adaptation from hard training simply doesn't happen.</td></tr>
    </table></div>

    <h2 style="margin-top:22px">Color language — green · yellow · red</h2>
    <p>One consistent status vocabulary everywhere, always paired with a shape so it never relies on color alone:
    <span style="color:var(--good)">● green = good</span> — carry on ·
    <span style="color:var(--warn)">▲ yellow = watch</span> — fine today, a trend if it persists ·
    <span style="color:var(--crit)">■ red = risk</span> — act now (recover, sleep, back off).</p>
    <p>Where you'll see it: the <b>Readiness tile</b> (daily train/easy/rest call from RHR, HRV, sleep, TSB), <b>Health tile badges</b> (7-day state per vital), and <b>zone bands behind the health charts</b>. Sleep and VO₂max bands are fixed standards (7h/6h; 42/35); resting HR and HRV bands are personal — computed from your own history's percentiles, so they recalibrate as you get fitter.</p>

    <h2 style="margin-top:22px">Kickoff &amp; settings (Plan page)</h2>
    <p>Kickoff anchors everything: set the race date and every week gets a start date so week 16 contains race day. From that moment the current-week tile, plan-vs-actual compliance, countdown, and plan progress all run off the calendar. The targets are yours to tune per athlete: <b>CTL target</b> (fitness goal by race), <b>compliance goal</b> (default ≥85%), <b>ramp limit</b> (weekly hours growth warning, default 30%), <b>long-run cap</b> (injury guard, default 150min). Every save is validated — impossible values are blocked, risky ones warn.</p>

    <h2 style="margin-top:22px">The connected workflow</h2>
    <p><b>Admin</b> — create an athlete, import their Apple Health export (Strava duplicates are detected and skipped automatically). → <b>Plan</b> — kickoff the race date, tune targets, edit weeks inline or by CSV round-trip; every save passes data-quality checks. → <b>Dashboard</b> — the daily view: readiness before training, rings and compliance after. → <b>Health</b> — the weekly recovery review: are the lines in the green bands? → <b>Statistics</b> — the monthly deep-dive with slice &amp; dice. → <b>Profile</b> — the trophy case: best efforts and records.</p>

    <h2 style="margin-top:22px">How to read the charts</h2>
    <h3>Performance Management (Dashboard)</h3>
    <p>Blue CTL climbing steadily = plan working. Green ATL spikes with hard weeks — normal. Yellow TSB is the one to watch: it should live between −10 and −25 during build blocks, pop positive on recovery weeks, and be brought to +5..+15 by the taper for race day.</p>
    <h3>Plan vs actual bars</h3>
    <p>Gray bar is the plan, colored bar is what you did. Bars matching ≥ 85% week after week is exactly how you finish a 70.3 — consistency beats heroics. One low week is noise; two in a row is a trend that needs a plan adjustment.</p>
    <h3>Statistics page</h3>
    <p>The filter bar at the top drives everything: pick the metric (miles, hours, sessions, TSS), the grain (month/year), a year range, and toggle sports on or off. The stacked bars show where volume came from; the year-over-year lines show cumulative totals so you can see instantly whether you're ahead of last year; the histogram shows how big your typical session is; and the day × hour grid shows when you actually train.</p>
    <h3>Efficiency factor</h3>
    <p>Only steady sessions ≥ 40 min count. The absolute number matters less than the drift: a rising line at the same heart rate is aerobic fitness you can bank for race day.</p>

    <h2 style="margin-top:22px">How to use the platform</h2>
    <p>Mostly hands-off: the backend server and the 06:30 daily sync both run
    automatically via launchd. Open <code>http://localhost:8703</code> — plan
    editing, kickoff, and admin need that URL (the standalone HTML file is
    read-only). Manual refresh when wanted:</p>
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
// Inline icon set (Lucide-style, MIT) — CSP-safe, consistent 24px stroke grid
const ICONS = {
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
  trend: '<path d="M22 7 13.5 15.5 8.5 10.5 2 17"/><path d="M16 7h6v6"/>',
  flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
  battery: '<rect x="2" y="7" width="16" height="10" rx="2"/><path d="M22 11v2M6 11v2M10 11v2"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
  heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
  pulse: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/><path d="M3.5 12h4l1.5-3 2 5 2-4 1 2h4.5" stroke-width="1.6"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  wind: '<path d="M17.7 7.7a2.5 2.5 0 1 1 1.8 4.3H2"/><path d="M9.6 4.6A2 2 0 1 1 11 8H2"/><path d="M12.6 19.4A2 2 0 1 0 14 16H2"/>',
  waves: '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
  bike: '<circle cx="5.5" cy="17.5" r="3.5"/><circle cx="18.5" cy="17.5" r="3.5"/><path d="M15 6a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm-3 11.5V14l-3-3 4-3 2 3h2"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  trophy: '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22M14 14.66V17c0 .55.47.98.97 1.21 1.18.54 2.03 2.03 2.03 3.79"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
  ruler: '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.4 2.4 0 0 1 0-3.4l2.6-2.6a2.4 2.4 0 0 1 3.4 0Z"/><path d="m14.5 12.5 2-2M11.5 9.5l2-2M8.5 6.5l2-2M17.5 15.5l2-2"/>',
  dumbbell: '<path d="m6.5 6.5 11 11M21 21l-1-1M3 3l1 1M18 22l4-4M2 6l4-4M3 10l7-7M14 21l7-7"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5M12 3v12"/>',
};
const SPORT_ICON = {run: 'zap', bike: 'bike', swim: 'waves', strength: 'dumbbell',
                    other: 'activity', brick: 'flame', rest: 'moon'};
function icon(name, size) {
  return `<svg class="ic" style="${size ? `width:${size}px;height:${size}px` : ''}"
    viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}
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
  // reference zones: good/watch/risk bands behind the data (status colors,
  // low opacity, each with a dot+label so meaning never rides on color alone)
  (o.zones || []).forEach(z => {
    const zlo = Math.max(lo, z.from ?? lo), zhi = Math.min(hi, z.to ?? hi);
    if (zhi <= zlo) return;
    svg.append(el('rect', {x: pl, y: y(zhi), width: W - pl - pr,
      height: y(zlo) - y(zhi), fill: css(z.color), 'fill-opacity': 0.07}));
    if (z.label) {
      const ty = Math.min(H - pb - 4, Math.max(pt + 9, (y(zhi) + y(zlo)) / 2 + 3));
      const t = el('text', {x: W - pr - 4, y: ty, 'text-anchor': 'end',
        'font-size': 10, 'font-weight': 650});
      t.setAttribute('fill', css(z.color)); t.setAttribute('fill-opacity', 0.9);
      t.textContent = z.label; svg.append(t);
    }
  });
  for (let g = 0; g < 4; g++) {
    const v = lo + (hi - lo) * g / 3, yy = y(v);
    svg.append(el('line', {x1: pl, x2: W - pr, y1: yy, y2: yy,
      stroke: css('--grid'), 'stroke-width': 1}));
    const t = el('text', {x: pl - 6, y: yy + 3.5, 'text-anchor': 'end'});
    t.textContent = o.fmt(v); svg.append(t);
  }
  if (lo < 0 && hi > 0) svg.append(el('line', {x1: pl, x2: W - pr, y1: y(0), y2: y(0),
    stroke: css('--axis'), 'stroke-width': 1}));
  if (o.area && series.length === 1) {
    const s0 = series[0];
    const pts2 = s0.pts.map((p, i) => p[1] == null ? null : [x(i), y(p[1])]).filter(Boolean);
    if (pts2.length > 1) {
      const d2 = 'M' + pts2.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('L')
        + `L${pts2[pts2.length-1][0].toFixed(1)},${H - pb}L${pts2[0][0].toFixed(1)},${H - pb}Z`;
      svg.append(el('path', {d: d2, fill: css(s0.color), 'fill-opacity': 0.1}));
    }
  }
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

// ---- stats renderers ----
function median(v) {
  const a = [...v].sort((x, y) => x - y), n = a.length;
  return n ? (n % 2 ? a[(n-1)/2] : (a[n/2-1] + a[n/2]) / 2) : 0;
}

function stackedBars(mount, periods, opts) {
  // periods: [{label, segs: [{name, color, val}]}]
  mount.innerHTML = '';
  if (!periods.length) { mount.innerHTML = '<div class="note">No sessions in this slice.</div>'; return; }
  const o = Object.assign({h: 240, fmt: v => v.toFixed(0), unit: ''}, opts);
  const W = 900, H = o.h, pt = 10, pb = 26, pl = 46, pr = 10;
  const hi = Math.max(...periods.map(p => p.segs.reduce((s, g) => s + g.val, 0))) * 1.06 || 1;
  const y = v => pt + (H - pt - pb) * (1 - v / hi);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let g = 0; g < 4; g++) {
    const v = hi * g / 3, yy = y(v);
    svg.append(el('line', {x1: pl, x2: W - pr, y1: yy, y2: yy, stroke: css('--grid')}));
    const t = el('text', {x: pl - 6, y: yy + 3.5, 'text-anchor': 'end'});
    t.textContent = o.fmt(v); svg.append(t);
  }
  const bw = (W - pl - pr) / periods.length;
  const bar = Math.min(26, bw * .62);
  const lstep = Math.ceil(periods.length / 12);
  periods.forEach((p, i) => {
    const cx = pl + bw * i + bw / 2;
    if (i % lstep === 0) {
      const t = el('text', {x: cx, y: H - 6, 'text-anchor': 'middle'});
      t.textContent = p.label; svg.append(t);
    }
    let acc = 0;
    p.segs.forEach((s, si) => {
      if (!s.val) return;
      const y1 = y(acc + s.val), y2 = y(acc);
      const top = si === p.segs.filter(g => g.val).length - 1;
      svg.append(el('rect', {x: cx - bar/2, y: y1, width: bar,
        height: Math.max(1, y2 - y1 - 2),          // 2px surface gap between segments
        rx: top ? 3 : 0, fill: css(s.color)}));
      acc += s.val;
    });
    const zone = el('rect', {x: pl + bw*i, y: pt, width: bw, height: H - pt - pb,
      fill: 'transparent'});
    zone.addEventListener('mousemove', e => {
      const tot = p.segs.reduce((s, g) => s + g.val, 0);
      showTT(`<b>${p.label}</b>` +
        p.segs.filter(g => g.val).map(g =>
          `<div class="r"><span>${g.name}</span><span>${o.fmt(g.val)}${o.unit}</span></div>`).join('') +
        `<div class="r" style="border-top:1px solid var(--grid);margin-top:3px;padding-top:3px"><span>Total</span><span>${o.fmt(tot)}${o.unit}</span></div>`,
        e.clientX, e.clientY);
    });
    zone.addEventListener('mouseleave', hideTT);
    svg.append(zone);
  });
  mount.append(svg);
}

function barsSimple(mount, rows, opts) {
  // rows: [{label, val}] single-series histogram/bars
  mount.innerHTML = '';
  if (!rows.length || !rows.some(r => r.val)) {
    mount.innerHTML = '<div class="note">No sessions in this slice.</div>'; return; }
  const o = Object.assign({h: 200, color: '--s1', fmt: v => v, unit: ''}, opts);
  const W = 460, H = o.h, pt = 8, pb = 24, pl = 38, pr = 6;
  const hi = Math.max(...rows.map(r => r.val)) * 1.08 || 1;
  const y = v => pt + (H - pt - pb) * (1 - v / hi);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let g = 0; g < 4; g++) {
    const v = hi * g / 3, yy = y(v);
    svg.append(el('line', {x1: pl, x2: W - pr, y1: yy, y2: yy, stroke: css('--grid')}));
    const t = el('text', {x: pl - 5, y: yy + 3.5, 'text-anchor': 'end'});
    t.textContent = Math.round(v); svg.append(t);
  }
  const bw = (W - pl - pr) / rows.length;
  const bar = Math.max(3, Math.min(18, bw * .7));
  const lstep = Math.ceil(rows.length / 10);
  rows.forEach((r, i) => {
    const cx = pl + bw * i + bw / 2, yy = y(r.val);
    if (i % lstep === 0) {
      const t = el('text', {x: cx, y: H - 6, 'text-anchor': 'middle'});
      t.textContent = r.label; svg.append(t);
    }
    svg.append(el('rect', {x: cx - bar/2, y: yy, width: bar,
      height: Math.max(0, H - pb - yy), rx: 3, fill: css(o.color)}));
    const zone = el('rect', {x: pl + bw*i, y: pt, width: bw, height: H - pt - pb,
      fill: 'transparent'});
    zone.addEventListener('mousemove', e => showTT(
      `<b>${o.tip ? o.tip(r.label) : r.label}</b>
       <div class="r"><span>Sessions</span><span>${r.val}</span></div>`,
      e.clientX, e.clientY));
    zone.addEventListener('mouseleave', hideTT);
    svg.append(zone);
  });
  mount.append(svg);
}

function heatGrid(mount, counts) {
  // counts[dow 0-6][hour 0-23]
  mount.innerHTML = '';
  const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const W = 900, cell = 30, pl = 44, pt = 22, gap = 2;
  const cw = (W - pl - 10 - 23 * gap) / 24;
  const H = pt + 7 * (cell + gap) + 8;
  const hi = Math.max(1, ...counts.flat());
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`});
  for (let h = 0; h < 24; h += 3) {
    const t = el('text', {x: pl + h * (cw + gap) + cw/2, y: pt - 8, 'text-anchor': 'middle'});
    t.textContent = h; svg.append(t);
  }
  for (let d = 0; d < 7; d++) {
    const t = el('text', {x: pl - 8, y: pt + d * (cell + gap) + cell/2 + 4, 'text-anchor': 'end'});
    t.textContent = DOW[d]; svg.append(t);
    for (let h = 0; h < 24; h++) {
      const v = counts[d][h];
      const r = el('rect', {x: pl + h * (cw + gap), y: pt + d * (cell + gap),
        width: cw, height: cell, rx: 4,
        fill: v ? css('--s1') : css('--grid'),
        'fill-opacity': v ? (0.18 + 0.82 * v / hi).toFixed(2) : 0.5});
      r.addEventListener('mousemove', e => showTT(
        `<b>${DOW[d]} ${h}:00–${h+1}:00</b>
         <div class="r"><span>Sessions</span><span>${v}</span></div>`,
        e.clientX, e.clientY));
      r.addEventListener('mouseleave', hideTT);
      svg.append(r);
    }
  }
  mount.append(svg);
}

// ============================ render ============================
const D = DATA, P = D.plan, SET = D.settings || {};
const SPORT_COLOR = {run: '--s1', bike: '--s2', swim: '--s3', strength: '--s5', other: '--s6'};
document.getElementById('gen').textContent = D.generated;
const daysToRace = SET.race_date
  ? Math.round((new Date(SET.race_date+'T00:00') - new Date(D.generated+'T00:00')) / 864e5)
  : null;
document.getElementById('sub').textContent = SET.race_date
  ? `Race ${SET.race_date} · ${daysToRace} days out`
  : 'Race date not set — kickoff on the Plan page';
if (D.demo) document.getElementById('banner').innerHTML =
  '<div class="demo-banner">DEMO DATA — run the ETL, then rebuild without --demo.</div>';

// ---- dashboard page ----
const lastLoad = D.pmc[D.pmc.length - 1];
const actual = D.actual;
// current week comes from the kickoff-anchored dates; fallback: latest actuals
const dateWeek = P.find(p => p.start && p.start <= D.generated &&
  D.generated < new Date(new Date(p.start+'T00:00').getTime()+7*864e5).toISOString().slice(0,10));
const actualWeek = actual.length ? actual[actual.length - 1] : null;
const curPlan = dateWeek || (actualWeek ? P[actualWeek.week - 1] : P[0]);
const curWeek = dateWeek
  ? (actual.find(a => a.week === dateWeek.week) || {week: dateWeek.week, hours: 0})
  : actualWeek;
const READY = {green: ['--good', 'GO'], yellow: ['--warn', 'EASY'], red: ['--crit', 'REST']};
function ringSvg(frac, color, txt) {
  const r = 19, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac));
  return `<svg class="ring" width="48" height="48" viewBox="0 0 48 48" aria-hidden="true">
    <circle cx="24" cy="24" r="${r}" fill="none" stroke="var(--plan-ghost)" stroke-width="5"/>
    <circle cx="24" cy="24" r="${r}" fill="none" stroke="var(${color})" stroke-width="5"
      stroke-linecap="round" stroke-dasharray="${(f * c).toFixed(1)} ${c.toFixed(1)}"
      transform="rotate(-90 24 24)"/>
    <text x="24" y="28.5" text-anchor="middle">${txt}</text></svg>`;
}
const compPct = curWeek && curPlan ? Math.round(100 * curWeek.hours / curPlan.hours) : null;
const rdy = D.readiness && READY[D.readiness];
const ctlT = SET.ctl_target || 65, compG = SET.compliance_goal_pct || 85;
const tiles = [
  ['Current week', curWeek ? `W${curWeek.week}` : '—',
   curPlan ? `${curPlan.phase} phase` + (daysToRace != null ? ` · ${daysToRace}d to race` : '') : '',
   curWeek ? ringSvg(curWeek.week / 16, '--brand', `${16 - curWeek.week}`) : '', 'calendar'],
  ['CTL · Fitness', lastLoad ? lastLoad[1].toFixed(0) : '—', `target ${ctlT} by race`,
   lastLoad ? ringSvg(lastLoad[1] / ctlT, '--s1', Math.round(100 * lastLoad[1] / ctlT) + '%') : '', 'trend'],
  ['ATL · Fatigue', lastLoad ? lastLoad[2].toFixed(0) : '—', '7-day load', '', 'flame'],
  ['TSB · Form', lastLoad ? (lastLoad[3] > 0 ? '+' : '') + lastLoad[3].toFixed(0) : '—',
   `race day: +${SET.tsb_race_lo ?? 5} to +${SET.tsb_race_hi ?? 15}`, '', 'battery'],
  ['Week compliance', compPct !== null ? compPct + '%' : '—',
   `hours vs plan · goal ≥${compG}%`,
   compPct !== null ? ringSvg(compPct / 100, compPct >= compG ? '--s2' : '--s3', '') : '', 'check'],
  ['Readiness', rdy ? `<span class="badge" style="color:var(${rdy[0]})">${D.readiness.toUpperCase()}</span>` : '—',
   'from RHR/HRV/sleep/TSB', rdy ? ringSvg(1, rdy[0], rdy[1]) : '', 'heart'],
];
document.getElementById('tiles').innerHTML = tiles.map(t =>
  `<div class="tile"><div><div class="lbl">${icon(t[4])} ${t[0]}</div><div class="val">${t[1]}</div>
   <div class="hint">${t[2]}</div></div>${t[3]}</div>`).join('');

// latest sessions + plan progress
const last4 = [...D.acts].slice(-4).reverse();
document.getElementById('sesscards').innerHTML = last4.map(a => `
  <div class="sess">
    <div class="sp" style="color:var(${SPORT_COLOR[a[1]] || '--s6'})">${icon(SPORT_ICON[a[1]] || 'activity')} ${a[1]}</div>
    <div class="big">${a[2] ? a[2].toFixed(1) + ' mi' : Math.round(a[3] * 60) + ' min'}</div>
    <div class="meta">${a[0]} · ${a[3] ? a[3].toFixed(1) + ' h' : ''}${a[4] ? ' · ' + a[4] + ' bpm' : ''}</div>
  </div>`).join('') || '<div class="note">No sessions yet.</div>';
const week = curWeek ? curWeek.week : 0;
document.getElementById('planprog').style.width = Math.round(100 * week / 16) + '%';
document.getElementById('planprog-note').textContent =
  week ? `Week ${week} of 16 — ${curPlan.phase} phase` : 'Plan not started';
document.getElementById('planprog-hint').textContent = SET.race_date
  ? `Race ${SET.race_date} · ${daysToRace} days out`
  : 'Kickoff the race date on the Plan page';

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

[['Swim', 'swim_m', ' m', '--s1'], ['Bike', 'bike_mi', ' mi', '--s2'],
 ['Run', 'run_mi', ' mi', '--s3']].forEach(([name, key, unit, color]) => {
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
    <td>${(p.swim_m/1000).toFixed(1)}k</td><td>${p.bike_mi} mi</td><td>${p.run_mi} mi</td>
    <td>${Math.floor(p.long_ride/60)}:${String(p.long_ride%60).padStart(2,'0')}</td>
    <td>${p.long_run}min</td>
    <td class="key">${p.key}</td></tr>`).join('');

// ---- statistics page ----
const ACTS = D.acts;   // [date, sport, mi, hours, hr, hour, tss]
const YEARS = [...new Set(ACTS.map(a => +a[0].slice(0, 4)))].sort();
const SPORTS = [...new Set(ACTS.map(a => a[1]))]
  .sort((a, b) => Object.keys(SPORT_COLOR).indexOf(a) - Object.keys(SPORT_COLOR).indexOf(b));
const st = {metric: 'mi', grain: 'month',
            y0: YEARS[0], y1: YEARS[YEARS.length - 1], on: new Set(SPORTS)};
// metric: i = column in ACTS (null = count), agg over sessions
const M = {mi:    {i: 2, fmt: v => v >= 100 ? v.toFixed(0) : v.toFixed(1), unit: ' mi'},
           hours: {i: 3, fmt: v => v >= 100 ? v.toFixed(0) : v.toFixed(1), unit: ' h'},
           n:     {i: null, fmt: v => v.toFixed(0), unit: ''},
           tss:   {i: 6, fmt: v => v.toFixed(0), unit: ''}};
const y0s = document.getElementById('st-y0'), y1s = document.getElementById('st-y1');
YEARS.forEach(y => { y0s.add(new Option(y, y)); y1s.add(new Option(y, y)); });
y0s.value = st.y0; y1s.value = st.y1;
const chips = document.getElementById('st-chips');
SPORTS.forEach(s => {
  const c = document.createElement('span');
  c.className = 'chip on'; c.textContent = s;
  c.onclick = () => { st.on.has(s) ? st.on.delete(s) : st.on.add(s);
    c.classList.toggle('on'); renderStats(); };
  chips.append(c);
});
['st-metric', 'st-grain', 'st-y0', 'st-y1'].forEach(id =>
  document.getElementById(id).addEventListener('change', () => {
    st.metric = document.getElementById('st-metric').value;
    st.grain = document.getElementById('st-grain').value;
    st.y0 = +y0s.value; st.y1 = +y1s.value;
    renderStats();
  }));

const val = (a, m) => m.i === null ? 1 : (a[m.i] || 0);

function renderStats() {
  const m = M[st.metric];
  const pool = ACTS.filter(a => {
    const y = +a[0].slice(0, 4);
    return y >= st.y0 && y <= st.y1 && st.on.has(a[1]);
  });
  const sportsOn = SPORTS.filter(s => st.on.has(s));

  // 1) stacked volume over time
  const keyOf = a => st.grain === 'month' ? a[0].slice(0, 7) : a[0].slice(0, 4);
  const perKey = {};
  pool.forEach(a => {
    const k = keyOf(a);
    (perKey[k] = perKey[k] || {})[a[1]] = (perKey[k][a[1]] || 0) + val(a, m);
  });
  const keys = Object.keys(perKey).sort();
  stackedBars(document.getElementById('st-volume'),
    keys.map(k => ({label: st.grain === 'month' ? k.slice(2) : k,
      segs: sportsOn.map(s => ({name: s, color: SPORT_COLOR[s] || '--s6',
                                val: perKey[k][s] || 0}))})),
    {fmt: m.fmt, unit: m.unit});
  document.getElementById('st-vol-legend').innerHTML = sportsOn.map(s =>
    `<span><span class="sw" style="background:var(${SPORT_COLOR[s] || '--s6'})"></span>${s}</span>`).join('');

  // 2) year-over-year cumulative lines (5 most recent years in range)
  const yoyYears = YEARS.filter(y => y >= st.y0 && y <= st.y1).slice(-5);
  const YO_COLORS = ['--s1', '--s2', '--s3', '--s5', '--s6'];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const series = yoyYears.map((y, i) => {
    const perM = Array(12).fill(0);
    pool.forEach(a => { if (+a[0].slice(0, 4) === y) perM[+a[0].slice(5, 7) - 1] += val(a, m); });
    let acc = 0;
    const lastM = y === +D.generated.slice(0, 4) ? +D.generated.slice(5, 7) - 1 : 11;
    return {name: '' + y, color: YO_COLORS[i],
      pts: MONTHS.map((mo, mi2) => [mo, mi2 <= lastM ? (acc += perM[mi2]) : null])};
  });
  const yoyMount = document.getElementById('st-yoy');
  yoyMount.innerHTML = '';
  if (series.length) lineChart(yoyMount, series, {h: 230, fmt: m.fmt, xscale: 'cat'});

  // 3) session-size histogram
  const sizes = pool.map(a => val(a, {i: st.metric === 'n' ? 2 : m.i}))
                    .filter(v => v > 0);
  const histMount = document.getElementById('st-hist');
  if (sizes.length) {
    const cap = sizes.slice().sort((a, b) => a - b)[Math.floor(sizes.length * .98)] || 1;
    const step = cap > 40 ? 10 : cap > 16 ? 4 : cap > 8 ? 2 : cap > 3 ? 1 : 0.5;
    const nb = Math.max(1, Math.ceil(cap / step));
    const buckets = Array(nb + 1).fill(0);
    sizes.forEach(v => buckets[Math.min(nb, Math.floor(v / step))]++);
    const bu = st.metric === 'n' ? ' mi' : m.unit;
    barsSimple(histMount,
      buckets.map((v, i) => ({label: i === nb ? (i*step) + '+' : '' + (i*step), val: v})),
      {tip: L => `${L}${bu.trim() ? ' ' + bu.trim() : ''} bucket`});
  } else histMount.innerHTML = '<div class="note">No sessions in this slice.</div>';

  // 4) when-you-train heatmap
  const counts = Array.from({length: 7}, () => Array(24).fill(0));
  pool.forEach(a => {
    const dow = (new Date(a[0] + 'T12:00').getDay() + 6) % 7;   // Mon=0
    const hr = a[5] == null ? 12 : a[5];
    counts[dow][Math.max(0, Math.min(23, hr))]++;
  });
  heatGrid(document.getElementById('st-heat'), counts);

  // 5) summary table by sport
  document.getElementById('st-table').innerHTML =
    `<tr><th>Sport</th><th>Sessions</th><th>Total mi</th><th>Total h</th>
     <th>Median mi</th><th>Longest mi</th><th>Avg HR</th><th>Total TSS</th></tr>` +
    sportsOn.map(s => {
      const rows2 = pool.filter(a => a[1] === s);
      if (!rows2.length) return '';
      const mis = rows2.map(a => a[2] || 0).filter(v => v > 0);
      const hrs2 = rows2.map(a => a[4]).filter(Boolean);
      return `<tr><td class="key">${s}</td><td>${rows2.length}</td>
        <td>${Math.round(rows2.reduce((t, a) => t + (a[2] || 0), 0)).toLocaleString()}</td>
        <td>${Math.round(rows2.reduce((t, a) => t + (a[3] || 0), 0)).toLocaleString()}</td>
        <td>${mis.length ? median(mis).toFixed(1) : '—'}</td>
        <td>${mis.length ? Math.max(...mis).toFixed(1) : '—'}</td>
        <td>${hrs2.length ? Math.round(hrs2.reduce((t, v) => t + v, 0) / hrs2.length) : '—'}</td>
        <td>${Math.round(rows2.reduce((t, a) => t + (a[6] || 0), 0)).toLocaleString()}</td></tr>`;
    }).join('');
}
renderStats();

// ---- profile page ----
const pr = D.profile;
const first = ACTS.length ? ACTS[0][0].slice(0, 4) : '—';
const tot = {n: ACTS.length,
  hours: ACTS.reduce((s, a) => s + (a[3] || 0), 0),
  runmi: ACTS.filter(a => a[1] === 'run').reduce((s, a) => s + (a[2] || 0), 0),
  bikemi: ACTS.filter(a => a[1] === 'bike').reduce((s, a) => s + (a[2] || 0), 0),
  longrun: Math.max(0, ...ACTS.filter(a => a[1] === 'run').map(a => a[2] || 0)),
  longride: Math.max(0, ...ACTS.filter(a => a[1] === 'bike').map(a => a[2] || 0))};
document.getElementById('hero').innerHTML = `
  ${pr.photo ? `<img class="avatar" src="${pr.photo}" alt="${pr.name}">`
             : `<div class="avatar-ph">${pr.name.split(' ').map(w => w[0]).join('')}</div>`}
  <div>
    <h1>${pr.name}</h1>
    <div class="tag">▲ ${pr.tagline} · training since ${first}</div>
    <div class="statchips">
      <div class="statchip"><span style="color:var(--brand)">${icon('activity')}</span><b>${tot.n.toLocaleString()}</b>activities</div>
      <div class="statchip"><span style="color:var(--s5)">${icon('clock')}</span><b>${Math.round(tot.hours).toLocaleString()}</b>hours</div>
      <div class="statchip"><span style="color:var(--s1)">${icon('zap')}</span><b>${Math.round(tot.runmi).toLocaleString()} mi</b>run</div>
      <div class="statchip"><span style="color:var(--s2)">${icon('bike')}</span><b>${Math.round(tot.bikemi).toLocaleString()} mi</b>bike</div>
      <div class="statchip"><span style="color:var(--s1)">${icon('ruler')}</span><b>${tot.longrun.toFixed(1)} mi</b>longest run</div>
      <div class="statchip"><span style="color:var(--s2)">${icon('ruler')}</span><b>${tot.longride.toFixed(1)} mi</b>longest ride</div>
    </div>
  </div>`;

const prs = D.efforts.filter(e => e.pr);
document.getElementById('pr-table').innerHTML =
  `<tr><th>Distance</th><th>Activity</th><th>Time</th><th>Pace</th><th>Date</th><th>Full run</th></tr>` +
  prs.map(e => `<tr><td><b>${e.band}</b> <span class="pr-star">★</span></td>
    <td class="key"><span style="color:var(--s1)">${icon('zap')}</span> run</td>
    <td>${e.time}</td><td>${e.pace} /mi</td>
    <td>${e.date}</td><td>${e.mi} mi</td></tr>`).join('') ||
  '<tr><td class="key">No efforts yet.</td></tr>';

const bands = [...new Set(D.efforts.map(e => e.band))];
document.getElementById('efforts-table').innerHTML =
  `<tr><th>Distance</th><th>Year</th><th>Time</th><th>Pace</th><th>Date</th></tr>` +
  bands.map(b => D.efforts.filter(e => e.band === b)
    .sort((a, c) => c.year - a.year).slice(0, 4)
    .map((e, i) => `<tr>
      <td>${i === 0 ? `<b>${b}</b>` : ''}</td>
      <td>${e.year}${e.pr ? ' <span class="pr-star">★</span>' : ''}</td>
      <td>${e.time}</td><td>${e.pace} /mi</td><td class="key">${e.date}</td></tr>`)
    .join('')).join('');

document.getElementById('records-table').innerHTML =
  `<tr><th>Record</th><th>Value</th><th>When</th></tr>` +
  D.records.map(r => `<tr><td class="key">${r.record}</td>
    <td><b>${r.value}</b></td><td>${r.date || '—'}</td></tr>`).join('');

// ---- plan editor (backend-hooked) ----
const PCOLS = [
  ['week_num','Wk',null], ['phase','Phase','phase'],
  ['planned_hours','Hours','num'], ['planned_tss','TSS','num'],
  ['swim_m','Swim m','num'], ['bike_mi','Bike mi','num'], ['run_mi','Run mi','num'],
  ['swim_sessions','S#','num'], ['bike_sessions','B#','num'], ['run_sessions','R#','num'],
  ['strength_sessions','St#','num'], ['long_ride_min','Long ride','num'],
  ['long_run_min','Long run','num'], ['is_recovery','Rec','chk'],
  ['focus','Focus','txt'], ['key_workouts','Key workouts','txt']];

function planReport(r) {
  const box = document.getElementById('plan-report');
  if (!r) { box.innerHTML = ''; return; }
  let cls = r.applied ? (r.warnings.length ? 'warn' : 'ok') : 'err';
  let h = r.applied
    ? `<b>Saved — ${r.weeks_updated} weeks updated.</b>`
    : `<b>Blocked — fix ${r.errors.length} error(s), nothing was written.</b>`;
  if (r.errors.length) h += `<ul>${r.errors.map(e => `<li>${e}</li>`).join('')}</ul>`;
  if (r.warnings.length) h += `<div style="margin-top:4px"><b>Warnings</b> (saved anyway):</div>
    <ul>${r.warnings.map(w => `<li>${w}</li>`).join('')}</ul>`;
  box.innerHTML = `<div class="report ${cls}">${h}</div>`;
}

function renderPlanGrid(rows) {
  const t = document.getElementById('plan-edit');
  t.innerHTML = `<tr>${PCOLS.map(c => `<th>${c[1]}</th>`).join('')}</tr>` +
    rows.map(r => `<tr>${PCOLS.map(([f, _, ty]) => {
      const v = r[f] == null ? '' : r[f];
      if (!ty) return `<td>${v}</td>`;
      if (ty === 'phase') return `<td><select data-wk="${r.week_num}" data-f="${f}">
        ${['base','build','peak','taper','race'].map(p =>
          `<option${p === v ? ' selected' : ''}>${p}</option>`).join('')}</select></td>`;
      if (ty === 'chk') return `<td style="text-align:center">
        <input type="checkbox" data-wk="${r.week_num}" data-f="${f}" ${+v ? 'checked' : ''}></td>`;
      if (ty === 'txt') return `<td style="min-width:170px">
        <input type="text" data-wk="${r.week_num}" data-f="${f}" value="${String(v).replace(/"/g, '&quot;')}"></td>`;
      return `<td><input class="num" data-wk="${r.week_num}" data-f="${f}" value="${v}"></td>`;
    }).join('')}</tr>`).join('');
}

function collectPlan() {
  const rows = {};
  document.querySelectorAll('#plan-edit [data-wk]').forEach(inp => {
    const wk = inp.dataset.wk;
    rows[wk] = rows[wk] || {week_num: wk};
    rows[wk][inp.dataset.f] = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value;
  });
  return Object.values(rows);
}

async function initPlan() {
  let rows = null;
  try {
    const resp = await fetch('/api/plan');
    if (resp.ok) rows = await resp.json();
  } catch (e) {}
  const offline = !rows;
  document.getElementById('plan-offline').style.display = offline ? '' : 'none';
  document.getElementById('plan-tools').style.display = offline ? 'none' : '';
  if (offline) {
    document.getElementById('plan-edit').innerHTML =
      `<tr><th>Wk</th><th>Phase</th><th>Hours</th><th>TSS</th><th>Focus</th></tr>` +
      P.map(p => `<tr><td>${p.week}</td><td class="key">${p.phase}</td>
        <td>${p.hours}</td><td>${p.tss}</td><td class="key">${p.focus || ''}</td></tr>`).join('');
    return;
  }
  renderPlanGrid(rows);
  const save = document.getElementById('plan-save');
  save.onclick = async () => {
    save.disabled = true; save.textContent = 'Checking…';
    try {
      const resp = await fetch('/api/plan', {method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(collectPlan())});
      planReport(await resp.json());
      if (resp.ok) renderPlanGrid(await (await fetch('/api/plan')).json());
    } catch (e) { planReport({applied: false, errors: ['Backend unreachable: ' + e], warnings: []}); }
    save.disabled = false; save.textContent = 'Save changes';
  };
  document.getElementById('plan-file').onchange = async ev => {
    const f = ev.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    try {
      const resp = await fetch('/api/plan/upload', {method: 'POST', body: fd});
      planReport(await resp.json());
      if (resp.ok) renderPlanGrid(await (await fetch('/api/plan')).json());
    } catch (e) { planReport({applied: false, errors: ['Backend unreachable: ' + e], warnings: []}); }
    ev.target.value = '';
  };
}
initPlan();

// ---- health page ----
const pctl = (vals, p) => { const a = [...vals].sort((x, y) => x - y);
  return a.length ? a[Math.min(a.length - 1, Math.floor(a.length * p))] : null; };
const STATUS = {good: ['●', '--good', 'GOOD'], watch: ['▲', '--warn', 'WATCH'],
                risk: ['■', '--crit', 'RISK']};
const badge = k => { const s = STATUS[k];
  return `<span class="badge" style="font-size:12px;color:var(${s[1]})">${s[0]} ${s[2]}</span>`; };

function renderHealth(months) {
  const cut = new Date(new Date(D.generated).getTime() - months * 30.44 * 864e5)
    .toISOString().slice(0, 10);
  const VM = D.vm.filter(r => r[0] >= cut);
  const WHR = D.whr.filter(r => r[0] >= cut);
  const ht = document.getElementById('h-tiles');
  if (!VM.length) {
    ht.innerHTML = '<div class="demo-banner">No Apple Health data yet — import on the Admin page.</div>';
    return;
  }
  // personal zones from your own distribution (all-time, stable across ranges)
  const rhrAll = D.vm.map(r => r[1]).filter(Boolean);
  const hrvAll = D.vm.map(r => r[4]).filter(Boolean);
  const rhrZ = {good: pctl(rhrAll, .4), watch: pctl(rhrAll, .8)};
  const hrvZ = {risk: pctl(hrvAll, .25), good: pctl(hrvAll, .6)};
  const stRhr = v => v == null ? null : v <= rhrZ.good ? 'good' : v <= rhrZ.watch ? 'watch' : 'risk';
  const stHrv = v => v == null ? null : v >= hrvZ.good ? 'good' : v >= hrvZ.risk ? 'watch' : 'risk';
  const stSlp = v => v == null ? null : v >= 7 ? 'good' : v >= 6 ? 'watch' : 'risk';
  const stVo2 = v => v == null ? null : v >= 42 ? 'good' : v >= 35 ? 'watch' : 'risk';

  const V = D.vitals;
  const last7 = V.slice(-7);
  const avg = (arr, i) => { const v = arr.map(r => r[i]).filter(x => x != null);
    return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; };
  const rhr7 = avg(last7, 1), hrv7 = avg(last7, 2), slp7 = avg(last7, 3);
  const yearAgo = D.vm.filter(r => r[0].slice(0, 4) === '' + (+D.generated.slice(0, 4) - 1));
  const rhrYA = yearAgo.length ? yearAgo.reduce((s, r) => s + (r[1] || 0), 0) / yearAgo.length : null;
  const vo2 = [...D.vm].reverse().find(r => r[7] != null);
  const delta = rhr7 && rhrYA ? (rhr7 - rhrYA).toFixed(0) : null;
  ht.innerHTML = [
    ['Resting HR · 7d', rhr7 ? rhr7.toFixed(0) : '—',
     delta != null ? `${delta > 0 ? '+' : ''}${delta} bpm vs last year` : 'bpm',
     stRhr(rhr7), 'pulse'],
    ['HRV · 7d', hrv7 ? hrv7.toFixed(0) : '—', 'ms SDNN · vs your range',
     stHrv(hrv7), 'activity'],
    ['Sleep · 7d', slp7 ? slp7.toFixed(1) : '—', 'hours/night · goal ≥7',
     stSlp(slp7), 'moon'],
    ['VO₂max', vo2 ? vo2[7].toFixed(1) : '—',
     vo2 ? `Apple est. · ${vo2[0].slice(0, 7)}` : '', stVo2(vo2 && vo2[7]), 'wind'],
  ].map(t => `<div class="tile"><div><div class="lbl">${icon(t[4])} ${t[0]}</div>
    <div class="val">${t[1]}</div><div class="hint">${t[2]}</div></div>
    ${t[3] ? `<div>${badge(t[3])}</div>` : ''}</div>`).join('');

  const xf = d => d.slice(0, 7);
  document.getElementById('h-rhr').innerHTML = '';
  lineChart(document.getElementById('h-rhr'), [{name: 'RHR', color: '--s1',
    pts: VM.map(r => [r[0], r[1]])}], {h: 210, fmt: v => v.toFixed(0), xfmt: xf,
    area: true, zones: [
      {to: rhrZ.good, color: '--good', label: '● low'},
      {from: rhrZ.good, to: rhrZ.watch, color: '--warn', label: '▲ typical'},
      {from: rhrZ.watch, color: '--crit', label: '■ elevated'}]});

  const years = [...new Set(VM.map(r => r[0].slice(0, 4)))].slice(-5);
  const YO = ['--s1', '--s2', '--s3', '--s5', '--s6'];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  document.getElementById('h-rhr-yoy').innerHTML = '';
  lineChart(document.getElementById('h-rhr-yoy'),
    years.map((y, i) => ({name: y, color: YO[i], pts: MONTHS.map((mo, mi2) => {
      const row = VM.find(r => r[0].slice(0, 4) === y && +r[0].slice(5, 7) === mi2 + 1);
      return [mo, row ? row[1] : null];
    })})), {h: 210, fmt: v => v.toFixed(0)});

  const whrDates = [...new Set(WHR.map(r => r[0]))].sort();
  document.getElementById('h-whr').innerHTML = '';
  if (whrDates.length) {
    const mk = sp => whrDates.map(d => {
      const row = WHR.find(r => r[0] === d && r[1] === sp);
      return [d, row ? row[2] : null];
    });
    lineChart(document.getElementById('h-whr'), [
      {name: 'Run', color: '--s1', pts: mk('run')},
      {name: 'Bike', color: '--s2', pts: mk('bike')},
    ], {h: 210, fmt: v => v.toFixed(0), xfmt: xf});
  }
  const hrvPts = VM.filter(r => r[4] != null).map(r => [r[0], r[4]]);
  document.getElementById('h-hrv').innerHTML = '';
  if (hrvPts.length) lineChart(document.getElementById('h-hrv'),
    [{name: 'HRV', color: '--s2', pts: hrvPts}],
    {h: 210, fmt: v => v.toFixed(0), xfmt: xf, area: true, zones: [
      {from: hrvZ.good, color: '--good', label: '● strong'},
      {from: hrvZ.risk, to: hrvZ.good, color: '--warn', label: '▲ typical'},
      {to: hrvZ.risk, color: '--crit', label: '■ suppressed'}]});
  else document.getElementById('h-hrv').innerHTML = '<div class="note">No HRV data in range.</div>';

  const slpPts = VM.filter(r => r[5] != null).map(r => [r[0], r[5]]);
  document.getElementById('h-sleep').innerHTML = '';
  if (slpPts.length) lineChart(document.getElementById('h-sleep'),
    [{name: 'Sleep', color: '--s1', pts: slpPts}],
    {h: 210, fmt: v => v.toFixed(1), xfmt: xf, area: true, zones: [
      {from: 7, color: '--good', label: '● ≥7h'},
      {from: 6, to: 7, color: '--warn', label: '▲ 6–7h'},
      {to: 6, color: '--crit', label: '■ <6h'}]});

  const vo2Pts = VM.filter(r => r[7] != null).map(r => [r[0], r[7]]);
  document.getElementById('h-vo2').innerHTML = '';
  if (vo2Pts.length) lineChart(document.getElementById('h-vo2'),
    [{name: 'VO2max', color: '--s5', pts: vo2Pts}],
    {h: 210, fmt: v => v.toFixed(1), xfmt: xf, area: true, zones: [
      {from: 42, color: '--good', label: '● 42+'},
      {from: 35, to: 42, color: '--warn', label: '▲ 35–42'},
      {to: 35, color: '--crit', label: '■ <35'}]});
  else document.getElementById('h-vo2').innerHTML = '<div class="note">No VO₂max data in range.</div>';
}
renderHealth(999);
document.querySelectorAll('#h-range .chip').forEach(c => c.onclick = () => {
  document.querySelectorAll('#h-range .chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on');
  renderHealth(+c.dataset.r);
});

// ---- kickoff (plan page) ----
async function initKickoff() {
  let s = null;
  try {
    const r = await fetch('/api/settings');
    if (r.ok) s = await r.json();
  } catch (e) {}
  const card = document.getElementById('kickoff-card');
  if (!s) return;                       // offline: card stays hidden
  card.style.display = '';
  document.getElementById('ko-race').value = s.race_date || '';
  document.getElementById('ko-ctl').value = s.ctl_target;
  document.getElementById('ko-comp').value = s.compliance_goal_pct;
  document.getElementById('ko-ramp').value = s.ramp_limit_pct;
  document.getElementById('ko-lr').value = s.long_run_cap_min;
  const btn = document.getElementById('ko-save');
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = 'Saving…';
    const body = {
      race_date: document.getElementById('ko-race').value || null,
      ctl_target: document.getElementById('ko-ctl').value,
      compliance_goal_pct: document.getElementById('ko-comp').value,
      ramp_limit_pct: document.getElementById('ko-ramp').value,
      long_run_cap_min: document.getElementById('ko-lr').value,
    };
    try {
      const r = await fetch('/api/settings', {method: 'PUT',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const rep = await r.json();
      const box = document.getElementById('ko-report');
      box.innerHTML = `<div class="report ${rep.applied ? 'ok' : 'err'}">
        <b>${rep.applied ? 'Kickoff saved — reloading with new dates…' : 'Blocked:'}</b>
        <ul>${[...rep.errors, ...rep.warnings].map(m => `<li>${m}</li>`).join('')}</ul></div>`;
      if (rep.applied) setTimeout(() => location.reload(), 1600);
    } catch (e) {}
    btn.disabled = false; btn.textContent = 'Kickoff / update';
  };
}
initKickoff();

// ---- admin page ----
async function initAdmin() {
  let list = null;
  try {
    const r = await fetch('/api/admin/athletes');
    if (r.ok) list = await r.json();
  } catch (e) {}
  const offline = !list;
  document.getElementById('admin-offline').style.display = offline ? '' : 'none';
  document.getElementById('admin-main').style.display = offline ? 'none' : '';
  if (offline) return;
  const render = rows => {
    document.getElementById('admin-list').innerHTML =
      `<tr><th>ID</th><th>Name</th><th>Race date</th><th>Activities</th>
       <th>Vitals days</th><th>Plan weeks</th></tr>` +
      rows.map(a => `<tr><td>${a.id}</td><td class="key">${a.name}</td>
        <td>${a.race_date || '—'}</td><td>${a.activities.toLocaleString()}</td>
        <td>${a.vitals_days.toLocaleString()}</td><td>${a.plan_weeks}</td></tr>`).join('');
    const sel = document.getElementById('adm-athlete');
    sel.innerHTML = '';
    rows.forEach(a => sel.add(new Option(`${a.id} · ${a.name}`, a.id)));
  };
  render(list);
  const report = rep => {
    document.getElementById('admin-report').innerHTML =
      `<div class="report ${rep.applied ? 'ok' : 'err'}">
       <b>${rep.applied ? 'Done.' : 'Blocked:'}</b>
       <ul>${[...rep.errors, ...rep.warnings].map(m => `<li>${m}</li>`).join('')}</ul></div>`;
  };
  document.getElementById('adm-create').onclick = async () => {
    const name = document.getElementById('adm-name').value;
    const r = await fetch('/api/admin/athletes', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
    report(await r.json());
    const l = await (await fetch('/api/admin/athletes')).json();
    render(l);
  };
  document.getElementById('adm-import').onclick = async () => {
    const btn = document.getElementById('adm-import');
    btn.disabled = true; btn.textContent = 'Importing… (large exports take minutes)';
    try {
      const r = await fetch('/api/admin/import/apple', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          athlete_id: document.getElementById('adm-athlete').value,
          path: document.getElementById('adm-path').value})});
      report(await r.json());
      render(await (await fetch('/api/admin/athletes')).json());
    } catch (e) {}
    btn.disabled = false; btn.textContent = 'Import';
  };
}
initAdmin();

route();
</script>
"""

if __name__ == "__main__":
    main()
