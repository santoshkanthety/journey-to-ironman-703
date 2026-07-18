#!/usr/bin/env python3
"""Replicate ironman schema marts to Databricks (workspace catalog).

SECURITY: exports ONLY the objects in WHITELIST below — every query is
`SELECT * FROM ironman.<name>`. No pg_dump, no cross-schema access: if the
database hosts other schemas, nothing from them ever leaves the machine.
The exact file list uploaded is printed at the end for audit.

Flow: Postgres -> CSV (scratch dir) -> UC volume upload -> CTAS Delta tables.
Idempotent: CREATE OR REPLACE TABLE each run (full refresh; data is small).

Run:  .venv/bin/python etl/replicate_to_databricks.py
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

WAREHOUSE = os.environ.get("DATABRICKS_WAREHOUSE_ID")
CATALOG = os.environ.get("DATABRICKS_CATALOG", "workspace")
SCHEMA = "ironman"
VOLUME = "staging"

if not WAREHOUSE:
    sys.exit("DATABRICKS_WAREHOUSE_ID not set (SQL warehouse id from your workspace).")

# (postgres object in ironman schema, target table name)
WHITELIST = [
    ("v_activities_export", "activities"),
    ("daily_load",          "daily_load"),
    ("daily_vitals",        "daily_vitals"),
    ("weekly_plan",         "weekly_plan"),
    ("v_weekly_compliance", "weekly_compliance"),
    ("v_monthly_volume",    "monthly_volume"),
    ("v_yearly_summary",    "yearly_summary"),
    ("v_dow_hour",          "dow_hour"),
    ("v_run_distance_dist", "run_distance_dist"),
    ("v_best_efforts",      "best_efforts"),
    ("v_records",           "records"),
    ("v_run_load_ratio",    "run_load_ratio"),
    ("v_efficiency",        "efficiency"),
]


def dbx(*args, json_in=None):
    cmd = ["databricks", *args]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       input=json.dumps(json_in) if json_in else None)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {r.stderr.strip()}")
    return r.stdout


def run_sql(sql):
    out = json.loads(dbx("api", "post", "/api/2.0/sql/statements",
                         "--json", json.dumps({
                             "warehouse_id": WAREHOUSE, "statement": sql,
                             "wait_timeout": "50s"})))
    state = out["status"]["state"]
    sid = out["statement_id"]
    while state in ("PENDING", "RUNNING"):
        time.sleep(3)
        out = json.loads(dbx("api", "get", f"/api/2.0/sql/statements/{sid}"))
        state = out["status"]["state"]
    if state != "SUCCEEDED":
        raise RuntimeError(f"SQL failed [{state}]: {sql[:80]} :: "
                           f"{out['status'].get('error', {}).get('message')}")
    return out


def main():
    con = connect()
    tmp = Path(tempfile.mkdtemp(prefix="ironman_dbx_"))
    uploaded = []

    print(f"Ensuring {CATALOG}.{SCHEMA} + volume...")
    run_sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    run_sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

    for src, tgt in WHITELIST:
        cur = con.execute(f"SELECT * FROM ironman.{src}")   # whitelist-only
        cols = [d.name for d in cur.description]
        path = tmp / f"{tgt}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in cur:
                w.writerow(row)
        vol = f"dbfs:/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{tgt}.csv"
        dbx("fs", "cp", "--overwrite", str(path), vol)
        run_sql(
            f"CREATE OR REPLACE TABLE {CATALOG}.{SCHEMA}.{tgt} AS "
            f"SELECT * FROM read_files('/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{tgt}.csv', "
            f"format => 'csv', header => true, inferSchema => true)")
        n = run_sql(f"SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.{tgt}")
        cnt = n["result"]["data_array"][0][0]
        uploaded.append((tgt, cnt))
        print(f"  {src:22s} -> {CATALOG}.{SCHEMA}.{tgt:20s} {cnt} rows")

    con.close()
    print("\nAudit — files uploaded to Databricks:")
    for t, c in uploaded:
        print(f"  {t}.csv ({c} rows)")
    print("Nothing outside ironman schema was read.")


if __name__ == "__main__":
    main()
