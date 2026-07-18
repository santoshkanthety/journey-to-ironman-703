#!/usr/bin/env python3
"""Generate self-contained dashboard.html from the ironman schema.

Usage:
    .venv/bin/python dashboard/build_dashboard.py            # real data
    .venv/bin/python dashboard/build_dashboard.py --demo     # fabricate 5 weeks
                                                             # of actuals to preview
Output: dashboard/dashboard.html  (open in any browser)
Run after each sync: strava_sync -> apple_health_import -> compute_metrics -> this.
"""
import argparse
import json
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

OUT = Path(__file__).parent / "dashboard.html"


def _num(v):
    from decimal import Decimal
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def fetch(con):
    plan = [dict(zip(
        ["week", "phase", "start", "hours", "tss", "swim_m", "bike_km", "run_km",
         "long_ride", "long_run", "recovery", "focus", "key"],
        [_num(v) for v in r]))
        for r in con.execute(
            """SELECT week_num, phase, start_date::text, planned_hours, planned_tss,
                      swim_m, bike_km, run_km, long_ride_min, long_run_min,
                      is_recovery, focus, key_workouts
               FROM weekly_plan ORDER BY week_num""").fetchall()]
    comp = [dict(zip(
        ["week", "hours", "tss", "swim_m", "bike_km", "run_km"],
        [_num(v) for v in r]))
        for r in con.execute(
            """SELECT week_num, actual_hours, actual_tss, actual_swim_m,
                      actual_bike_km, actual_run_km
               FROM v_weekly_compliance ORDER BY week_num""").fetchall()]
    pmc = [[str(r[0]), float(r[1]), float(r[2]), float(r[3])] for r in con.execute(
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
    return plan, comp, pmc, vitals, eff, (ready[0] if ready else None)


def demo_data(plan):
    """Deterministic fake actuals: weeks 1-5 done, mid-week 6."""
    rng = random.Random(42)
    comp, pmc, vitals = [], [], []
    start = date.today() - timedelta(weeks=5, days=3)
    ctl = atl = 20.0
    import math
    k42, k7 = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    d = start
    while d <= date.today():
        wk_idx = (d - start).days // 7
        target = plan[min(wk_idx, 15)]["tss"] / 7.0
        tss = max(0, rng.gauss(target, target * 0.45)) if rng.random() > 0.25 else 0
        tsb = ctl - atl
        ctl += k42 * (tss - ctl)
        atl += k7 * (tss - atl)
        pmc.append([d.isoformat(), round(ctl, 1), round(atl, 1), round(tsb, 1)])
        vitals.append([d.isoformat(), round(rng.gauss(52, 2)),
                       round(rng.gauss(48 + wk_idx, 6), 1),
                       round(rng.gauss(7.1, 0.7), 1),
                       round(78.5 - wk_idx * 0.15 + rng.gauss(0, 0.3), 1)])
        d += timedelta(days=1)
    for i, p in enumerate(plan[:6]):
        f = rng.uniform(0.82, 1.05) if i < 5 else 0.45
        comp.append({"week": p["week"], "hours": round(p["hours"] * f, 1),
                     "tss": round(p["tss"] * f), "swim_m": round(p["swim_m"] * f),
                     "bike_km": round(p["bike_km"] * f, 1),
                     "run_km": round(p["run_km"] * f, 1)})
    eff = []
    d = start
    ef_b, ef_r = 1.55, 1.05
    while d <= date.today():
        if rng.random() < 0.3:
            eff.append([d.isoformat(), "bike", round(ef_b + rng.gauss(0, 0.04), 3)])
            ef_b += 0.004
        if rng.random() < 0.3:
            eff.append([d.isoformat(), "run", round(ef_r + rng.gauss(0, 0.03), 3)])
            ef_r += 0.003
        d += timedelta(days=1)
    return comp, pmc, vitals, eff, "green"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    con = connect()
    plan, comp, pmc, vitals, eff, ready = fetch(con)
    con.close()
    if args.demo:
        comp, pmc, vitals, eff, ready = demo_data(plan)

    data = {
        "generated": date.today().isoformat(),
        "demo": args.demo,
        "readiness": ready,
        "plan": plan,
        "actual": [c for c in comp if c.get("hours") is not None],
        "pmc": pmc,
        "vitals": vitals,
        "eff": eff,
    }
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data))
    OUT.write_text(html)
    print(f"Wrote {OUT} ({'demo' if args.demo else 'live'} data, "
          f"{len(data['actual'])} weeks actuals, {len(pmc)} load days)")


TEMPLATE = r"""<title>Ironman 70.3 Training Dashboard</title>
<style>
  .viz-root {
    --surface-1:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
    --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100;
    --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
    --good-text:#006300; --plan-ghost:#e1e0d9;
  }
  @media (prefers-color-scheme: dark) { .viz-root {
    --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835;
    --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500;
    --good-text:#0ca30c; --plan-ghost:#2c2c2a;
  }}
  :root[data-theme="dark"] .viz-root {
    --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#199e70; --s3:#c98500;
    --good-text:#0ca30c; --plan-ghost:#2c2c2a;
  }
  :root[data-theme="light"] .viz-root {
    --surface-1:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e;
    --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
    --s1:#2a78d6; --s2:#1baf7a; --s3:#eda100;
    --good-text:#006300; --plan-ghost:#e1e0d9;
  }
  .viz-root { font-family: system-ui,-apple-system,"Segoe UI",sans-serif;
    background: var(--page); color: var(--ink); padding: 24px;
    max-width: 1160px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 2px; }
  .sub { color: var(--ink-2); font-size: 13px; margin-bottom: 20px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
    gap: 12px; margin-bottom: 20px; }
  .tile { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; }
  .tile .lbl { font-size: 12px; color: var(--ink-2); margin-bottom: 4px; }
  .tile .val { font-size: 26px; font-weight: 650; }
  .tile .hint { font-size: 11px; color: var(--muted); margin-top: 3px; }
  .card { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
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
  svg .dl { font-size: 11px; font-weight: 600; fill: var(--ink-2); }
  .tt { position: fixed; pointer-events: none; background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
    font-size: 12px; color: var(--ink); box-shadow: 0 4px 14px rgba(0,0,0,.15);
    display: none; z-index: 9; min-width: 120px; }
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
</style>
<div class="viz-root">
  <h1>Ironman 70.3 — Training Dashboard</h1>
  <div class="sub" id="sub"></div>
  <div id="banner"></div>
  <div class="tiles" id="tiles"></div>
  <div class="card">
    <h2>Performance Management — CTL / ATL / TSB</h2>
    <div class="note">Fitness (42-day load), Fatigue (7-day load), Form = Fitness − Fatigue. Race-day target: TSB +5 to +15.</div>
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
      <h2>Vitals — 7-day view</h2>
      <div class="note">Resting HR, HRV (SDNN), sleep, weight from Apple Health.</div>
      <div id="vitals" class="row" style="gap:8px"></div>
    </div>
  </div>
  <div class="card">
    <h2>16-week plan</h2>
    <div class="note">Race = Sunday of week 16. Recovery weeks dimmed.</div>
    <div class="tablewrap"><table id="plantable"></table></div>
  </div>
  <div class="tt" id="tt"></div>
</div>
<script>
const DATA = /*DATA*/null;
const css = v => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(v).trim();
const NS = 'http://www.w3.org/2000/svg';
const el = (t, a) => { const e = document.createElementNS(NS, t);
  for (const k in a) e.setAttribute(k, a[k]); return e; };
const tt = document.getElementById('tt');
function showTT(html, x, y) { tt.innerHTML = html; tt.style.display = 'block';
  const w = tt.offsetWidth; tt.style.left = Math.min(x + 14, innerWidth - w - 8) + 'px';
  tt.style.top = (y + 14) + 'px'; }
function hideTT() { tt.style.display = 'none'; }

function lineChart(mount, series, opts) {
  // series: [{name,color,pts:[[label,value],...]}], shared x by index
  const o = Object.assign({h: 220, pad: [10, 46, 24, 40], zero: false, fmt: v => v.toFixed(0)}, opts);
  const W = 720, H = o.h, [pt, pr, pb, pl] = o.pad;
  const n = Math.max(...series.map(s => s.pts.length));
  let vals = series.flatMap(s => s.pts.map(p => p[1])).filter(v => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (o.zero) lo = Math.min(lo, 0);
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
  // rows: [{label, plan, actual, extra}]  gray plan bar + colored actual bar
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
      const yy = y(v), hh = Math.max(0, H - pb - yy);
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

// ---- render ----
const D = DATA;
const P = D.plan;
const raceStart = P[15].start;
document.getElementById('sub').textContent =
  `Generated ${D.generated}` + (raceStart
    ? ` · race ${new Date(new Date(raceStart+'T00:00').getTime()+6*864e5).toDateString()}`
    : ' · set race date: seed_plan.py YYYY-MM-DD');
if (D.demo) document.getElementById('banner').innerHTML =
  '<div class="demo-banner">DEMO DATA — run the ETL scripts, then rebuild without --demo.</div>';

const lastLoad = D.pmc[D.pmc.length - 1];
const actual = D.actual;
const curWeek = actual.length ? actual[actual.length - 1] : null;
const curPlan = curWeek ? P[curWeek.week - 1] : P[0];
const READY = {green: ['●', '--good', 'Train as planned'],
  yellow: ['▲', '--warn', 'Reduce intensity today'],
  red: ['■', '--crit', 'Recovery day']};
const tiles = [
  ['Current week', curWeek ? `W${curWeek.week}` : '—',
   curPlan ? `${curPlan.phase} phase` : ''],
  ['CTL · Fitness', lastLoad ? lastLoad[1].toFixed(0) : '—', 'target 55–70 by race'],
  ['ATL · Fatigue', lastLoad ? lastLoad[2].toFixed(0) : '—', '7-day load'],
  ['TSB · Form', lastLoad ? (lastLoad[3] > 0 ? '+' : '') + lastLoad[3].toFixed(0) : '—',
   'race day: +5 to +15'],
  ['Week compliance', curWeek && curPlan ?
     Math.round(100 * curWeek.hours / curPlan.hours) + '%' : '—', 'hours vs plan · goal ≥85%'],
  ['Readiness', null, 'from RHR/HRV/sleep/TSB'],
];
document.getElementById('tiles').innerHTML = tiles.map(t => {
  let val = t[1] === null
    ? (D.readiness && READY[D.readiness]
        ? `<span class="badge" style="color:var(${READY[D.readiness][1]})">
             ${READY[D.readiness][0]} ${D.readiness.toUpperCase()}</span>`
        : '—')
    : t[1];
  return `<div class="tile"><div class="lbl">${t[0]}</div>
    <div class="val">${val}</div><div class="hint">${t[2]}</div></div>`;
}).join('');

if (D.pmc.length)
  lineChart(document.getElementById('pmc'), [
    {name: 'CTL', color: '--s1', pts: D.pmc.map(r => [r[0], r[1]])},
    {name: 'ATL', color: '--s2', pts: D.pmc.map(r => [r[0], r[2]])},
    {name: 'TSB', color: '--s3', pts: D.pmc.map(r => [r[0], r[3]])},
  ], {xfmt: d => d.slice(5)});
else document.getElementById('pmc').innerHTML =
  '<div class="note">No activities yet — run ETL + compute_metrics.py.</div>';

const byWeek = Object.fromEntries(actual.map(a => [a.week, a]));
pairedBars(document.getElementById('hours'),
  P.map(p => ({label: p.week, plan: p.hours,
    actual: byWeek[p.week] ? byWeek[p.week].hours : null})),
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
const dates = [...new Set(D.eff.map(e => e[0]))].sort();
if (dates.length) {
  const idx = Object.fromEntries(dates.map((d, i) => [d, i]));
  const mk = pts => { const a = dates.map(() => null);
    pts.forEach(p => a[idx[p[0]]] = p[1]);
    return dates.map((d, i) => [d, a[i]]); };
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
</script>
"""

if __name__ == "__main__":
    main()
