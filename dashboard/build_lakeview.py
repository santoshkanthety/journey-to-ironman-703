#!/usr/bin/env python3
"""Generate + deploy the Lakeview (AI/BI) dashboard for workspace.ironman.

Creates 3 pages: Overview / Activities / Runner Profile.
Run:  .venv/bin/python dashboard/build_lakeview.py
Re-running updates the existing dashboard (id cached in .lakeview_id).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

WAREHOUSE = os.environ.get("DATABRICKS_WAREHOUSE_ID")
CATALOG = os.environ.get("DATABRICKS_CATALOG", "workspace")
T = f"{CATALOG}.ironman"
ID_FILE = Path(__file__).parent / ".lakeview_id"

if not WAREHOUSE and __name__ == "__main__":
    sys.exit("DATABRICKS_WAREHOUSE_ID not set (SQL warehouse id from your workspace).")

# ---------------------------------------------------------------- datasets
DATASETS = [
    ("kpis", f"""
        SELECT l.ctl, l.atl, l.tsb,
               (SELECT COUNT(*) FROM {T}.activities)                AS total_activities,
               (SELECT ROUND(SUM(hours),0) FROM {T}.activities)     AS total_hours,
               (SELECT ROUND(SUM(mi),0) FROM {T}.activities WHERE sport='run')  AS run_mi,
               (SELECT ROUND(SUM(mi),0) FROM {T}.activities WHERE sport='bike') AS bike_mi
        FROM {T}.daily_load l ORDER BY l.date DESC LIMIT 1"""),
    ("pmc", f"SELECT date, ctl, atl, tsb FROM {T}.daily_load WHERE date >= date_sub(current_date(), 182) ORDER BY date"),
    ("compliance", f"SELECT week_num, phase, planned_hours, actual_hours, hours_pct, planned_tss, actual_tss FROM {T}.weekly_compliance ORDER BY week_num"),
    ("records", f"SELECT record, value, date FROM {T}.records"),
    ("monthly", f"SELECT month, sport, hours, mi, sessions, tss FROM {T}.monthly_volume WHERE month >= date_sub(current_date(), 1830) ORDER BY month"),
    ("dowhour", f"SELECT dow, dow_name, hour, SUM(sessions) AS sessions FROM {T}.dow_hour GROUP BY dow, dow_name, hour"),
    ("yearly", f"SELECT year, sport, mi, hours, sessions, longest_mi FROM {T}.yearly_summary ORDER BY year"),
    ("distdist", f"SELECT mi_bucket, runs FROM {T}.run_distance_dist ORDER BY mi_bucket"),
    ("efforts", f"SELECT band, meters, year, date, est_time, pace_min_mi, pace_s_mi, run_mi, is_all_time_pr FROM {T}.best_efforts ORDER BY meters, year"),
    ("efforts_pr", f"SELECT band, est_time, pace_min_mi, date, run_mi FROM {T}.best_efforts WHERE is_all_time_pr ORDER BY meters"),
    ("acwr", f"SELECT date, mi_7d, mi_28d_weekly_avg, acwr FROM {T}.run_load_ratio WHERE date >= date_sub(current_date(), 365) ORDER BY date"),
    ("recent", f"SELECT date, sport, title, ROUND(mi,1) AS mi, ROUND(hours,2) AS hours, avg_hr, tss FROM {T}.activities ORDER BY start_time DESC LIMIT 100"),
]


def fld(name, expr=None):
    return {"name": name, "expression": expr or f"`{name}`"}


def widget(name, dataset, fields, spec, x, y, w, h, disagg=True):
    return {"widget": {
        "name": name,
        "queries": [{"name": "main_query", "query": {
            "datasetName": dataset, "fields": fields, "disaggregated": disagg}}],
        "spec": spec},
        "position": {"x": x, "y": y, "width": w, "height": h}}


def counter(name, dataset, field, title, x, y, w=1, h=3):
    return widget(name, dataset, [fld(field)], {
        "version": 2, "widgetType": "counter",
        "frame": {"title": title, "showTitle": True},
        "encodings": {"value": {"fieldName": field, "displayName": title}}},
        x, y, w, h)


def line(name, dataset, xf, series, title, x, y, w, h, xscale="temporal"):
    # series: [(field, label)]
    if len(series) == 1:
        enc_y = {"fieldName": series[0][0], "scale": {"type": "quantitative"},
                 "displayName": series[0][1]}
    else:
        enc_y = {"fields": [{"fieldName": f, "displayName": lbl} for f, lbl in series],
                 "scale": {"type": "quantitative"}}
    return widget(name, dataset, [fld(xf)] + [fld(f) for f, _ in series], {
        "version": 3, "widgetType": "line",
        "frame": {"title": title, "showTitle": True},
        "encodings": {"x": {"fieldName": xf, "scale": {"type": xscale},
                            "displayName": xf}, "y": enc_y}},
        x, y, w, h)


def bar(name, dataset, xf, yf, title, x, y, w, h, color=None, xscale="categorical"):
    enc = {"x": {"fieldName": xf, "scale": {"type": xscale}, "displayName": xf},
           "y": {"fieldName": yf, "scale": {"type": "quantitative"}, "displayName": yf}}
    if color:
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"},
                        "displayName": color}
    return widget(name, dataset, [fld(xf), fld(yf)] + ([fld(color)] if color else []), {
        "version": 3, "widgetType": "bar",
        "frame": {"title": title, "showTitle": True}, "encodings": enc},
        x, y, w, h)


def heatmap(name, dataset, xf, yf, cf, title, x, y, w, h):
    return widget(name, dataset, [fld(xf), fld(yf), fld(cf)], {
        "version": 3, "widgetType": "heatmap",
        "frame": {"title": title, "showTitle": True},
        "encodings": {
            "x": {"fieldName": xf, "scale": {"type": "categorical"}, "displayName": xf},
            "y": {"fieldName": yf, "scale": {"type": "categorical"}, "displayName": yf},
            "color": {"fieldName": cf, "scale": {"type": "quantitative"},
                      "displayName": cf}}},
        x, y, w, h)


def table(name, dataset, cols, title, x, y, w, h):
    return widget(name, dataset, [fld(c) for c in cols], {
        "version": 1, "widgetType": "table",
        "frame": {"title": title, "showTitle": True},
        "encodings": {"columns": [
            {"fieldName": c, "displayName": c.replace("_", " ")} for c in cols]}},
        x, y, w, h)


overview = [
    counter("c_ctl", "kpis", "ctl", "CTL · Fitness", 0, 0),
    counter("c_atl", "kpis", "atl", "ATL · Fatigue", 1, 0),
    counter("c_tsb", "kpis", "tsb", "TSB · Form", 2, 0),
    counter("c_tot", "kpis", "total_activities", "Activities", 3, 0),
    counter("c_hrs", "kpis", "total_hours", "Total hours", 4, 0),
    counter("c_rkm", "kpis", "run_mi", "Run mi (all time)", 5, 0),
    line("l_pmc", "pmc", "date",
         [("ctl", "CTL Fitness"), ("atl", "ATL Fatigue"), ("tsb", "TSB Form")],
         "Performance Management — last 26 weeks", 0, 3, 6, 8),
    bar("b_plan", "compliance", "week_num", "planned_hours",
        "Planned hours by week (16-week 70.3 plan)", 0, 11, 3, 7),
    bar("b_act", "compliance", "week_num", "actual_hours",
        "Actual hours by week", 3, 11, 3, 7),
    table("t_rec", "records", ["record", "value", "date"],
          "Personal records", 0, 18, 6, 6),
]

activities = [
    bar("b_month", "monthly", "month", "hours",
        "Monthly training hours by sport — 5 years", 0, 0, 6, 8,
        color="sport", xscale="temporal"),
    heatmap("h_dow", "dowhour", "hour", "dow_name", "sessions",
            "When you train — day × hour", 0, 8, 3, 8),
    bar("b_year", "yearly", "year", "mi",
        "Distance by year and sport", 3, 8, 3, 8, color="sport"),
    bar("b_dist", "distdist", "mi_bucket", "runs",
        "Run distance distribution (mile buckets)", 0, 16, 3, 7),
    table("t_recent", "recent",
          ["date", "sport", "title", "mi", "hours", "avg_hr", "tss"],
          "Last 100 activities", 3, 16, 3, 7),
]

profile = [
    table("t_pr", "efforts_pr", ["band", "est_time", "pace_min_mi", "date", "run_mi"],
          "Best efforts — all-time (est. from avg run pace)", 0, 0, 6, 6),
    line("l_eff", "efforts", "year", [("pace_s_mi", "Pace (s/mi)")],
         "Best pace by year (lower = faster) — split by band below", 0, 6, 6, 7,
         xscale="categorical"),
    line("l_acwr", "acwr", "date", [("acwr", "Acute:chronic ratio")],
         "Run load ratio — safe band 0.8–1.3, injury risk > 1.5", 0, 13, 3, 7),
    line("l_vol", "acwr", "date", [("mi_7d", "7-day mi"),
                                   ("mi_28d_weekly_avg", "28-day weekly avg mi")],
         "Run volume — acute vs chronic", 3, 13, 3, 7),
    table("t_efforts", "efforts",
          ["band", "year", "est_time", "pace_min_mi", "date", "run_mi"],
          "Best efforts by year", 0, 20, 6, 8),
]

dashboard = {
    "datasets": [{"name": n, "displayName": n, "queryLines": [q.strip()]}
                 for n, q in DATASETS],
    "pages": [
        {"name": "overview", "displayName": "Overview", "layout": overview},
        {"name": "activities", "displayName": "Activities", "layout": activities},
        {"name": "profile", "displayName": "Runner Profile", "layout": profile},
    ],
}


def main():
    ser = json.dumps(dashboard)
    if ID_FILE.exists():
        did = ID_FILE.read_text().strip()
        body = {"display_name": "Ironman 70.3 — Training Analytics",
                "serialized_dashboard": ser, "warehouse_id": WAREHOUSE}
        r = subprocess.run(["databricks", "lakeview", "update", did,
                            "--json", json.dumps(body)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr)
        print(f"Updated dashboard {did}")
    else:
        body = {"display_name": "Ironman 70.3 — Training Analytics",
                "serialized_dashboard": ser, "warehouse_id": WAREHOUSE}
        r = subprocess.run(["databricks", "lakeview", "create",
                            "--json", json.dumps(body), "-o", "json"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr)
        out = json.loads(r.stdout)
        did = out["dashboard_id"]
        ID_FILE.write_text(did)
        print(f"Created dashboard {did}")
    # publish (embedded credentials so it renders for you directly)
    r = subprocess.run(["databricks", "lakeview", "publish", did,
                        "--json", json.dumps({"embed_credentials": True,
                                              "warehouse_id": WAREHOUSE})],
                       capture_output=True, text=True)
    print("Published." if r.returncode == 0 else f"Publish warn: {r.stderr[:200]}")
    host = os.environ.get("DATABRICKS_HOST", "https://<your-workspace>")
    print(f"URL: {host}/dashboardsv3/{did}/published")


if __name__ == "__main__":
    main()
