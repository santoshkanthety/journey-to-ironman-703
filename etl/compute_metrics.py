#!/usr/bin/env python3
"""Compute daily training load: CTL / ATL / TSB from activity TSS.

CTL (fitness) = 42-day exponentially weighted avg of daily TSS
ATL (fatigue) =  7-day exponentially weighted avg of daily TSS
TSB (form)    = yesterday's CTL - yesterday's ATL

Run after every sync:  .venv/bin/python etl/compute_metrics.py
Rebuilds daily_load from the first activity through today.
"""
import math
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import connect

K_CTL = 1 - math.exp(-1 / 42)
K_ATL = 1 - math.exp(-1 / 7)


def main():
    con = connect()
    rows = con.execute(
        """SELECT start_time::date, SUM(COALESCE(tss, 0))
           FROM activities GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    if not rows:
        print("No activities yet.")
        return
    tss_by_day = dict(rows)
    d, end = rows[0][0], date.today()
    con.execute("DELETE FROM daily_load")
    ctl = atl = 0.0
    prev_ctl = prev_atl = 0.0
    while d <= end:
        tss = float(tss_by_day.get(d, 0))
        tsb = prev_ctl - prev_atl
        ctl = ctl + K_CTL * (tss - ctl)
        atl = atl + K_ATL * (tss - atl)
        con.execute(
            """INSERT INTO daily_load (date, tss_total, ctl, atl, tsb)
               VALUES (%s,%s,%s,%s,%s)""",
            (d, tss, round(ctl, 1), round(atl, 1), round(tsb, 1)))
        prev_ctl, prev_atl = ctl, atl
        d += timedelta(days=1)
    con.commit()
    print(f"daily_load rebuilt {rows[0][0]} -> {end}. "
          f"Today: CTL {ctl:.0f}, ATL {atl:.0f}, TSB {prev_ctl - prev_atl:+.0f}")
    con.close()


if __name__ == "__main__":
    main()
