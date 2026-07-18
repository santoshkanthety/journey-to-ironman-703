#!/usr/bin/env python3
"""Seed the 16-week Ironman 70.3 plan into the ironman schema.

Usage:
    .venv/bin/python seed_plan.py                # week numbers only
    .venv/bin/python seed_plan.py 2026-11-08     # race date (Sunday) -> start_date per week
"""
import sys
from datetime import date, timedelta

from db import connect

# week, phase, hours, tss, swim_m, bike_km, run_km,
# swim/bike/run sessions, strength, long_ride_min, long_run_min, recovery, focus, key_workouts
PLAN = [
    (1,  "base",  6.0, 300, 3000,  80, 20, 2, 3, 3, 2,  90,  50, 0,
     "Establish routine, all easy aerobic (Z2). Baseline tests.",
     "FTP test 20min | CSS swim test 400/200 | Run threshold test 30min"),
    (2,  "base",  6.5, 330, 3500,  90, 22, 2, 3, 3, 2, 105,  55, 0,
     "Aerobic volume, technique focus in swim.",
     "Swim drills + endurance | Long ride Z2 | Long run Z2"),
    (3,  "base",  7.5, 380, 4000, 100, 25, 3, 3, 3, 2, 120,  60, 0,
     "Biggest base week. Keep everything conversational.",
     "Long ride 2h Z2 | Long run 60min | 3rd swim added"),
    (4,  "base",  5.0, 240, 2500,  60, 15, 2, 2, 2, 1,  75,  40, 1,
     "RECOVERY. Absorb the work. Sleep priority.",
     "All sessions short + easy | Optional mobility"),
    (5,  "build", 7.5, 420, 4000, 105, 26, 3, 3, 3, 2, 135,  65, 0,
     "Intensity begins: bike tempo, run strides.",
     "Bike 2x15min tempo | Run strides 6x20s | Swim CSS intervals"),
    (6,  "build", 8.0, 460, 4500, 115, 28, 3, 3, 3, 2, 150,  70, 0,
     "First brick. Threshold touches on bike.",
     "BRICK: 90min bike + 20min run | Bike 3x12min sweet spot | Swim 10x100 @CSS"),
    (7,  "build", 8.5, 500, 4500, 125, 30, 3, 3, 4, 2, 165,  75, 0,
     "Volume + intensity peak of block. 4th run is short recovery.",
     "Long ride 2:45 w/ 3x20min tempo | Long run 75min w/ last 15 @HM effort"),
    (8,  "build", 5.5, 280, 3000,  70, 18, 2, 2, 3, 1,  90,  45, 1,
     "RECOVERY. Re-test FTP + CSS end of week.",
     "FTP re-test | CSS re-test | Everything else easy"),
    (9,  "build", 9.0, 540, 5000, 135, 32, 3, 3, 4, 2, 180,  85, 0,
     "Race-specific block starts. Ride at target 70.3 watts.",
     "BRICK: 2h bike w/ 60min @race power + 30min run @race pace | Open water swim if possible"),
    (10, "build", 9.5, 570, 5000, 145, 34, 3, 4, 4, 2, 195,  90, 0,
     "Biggest bike week. Nutrition rehearsal on long ride.",
     "Long ride 3:15 fueled exactly like race day | Run 90min w/ 30min @race pace"),
    (11, "peak", 10.0, 600, 5500, 150, 36, 4, 4, 4, 2, 210, 100, 0,
     "Peak volume week. Full race-sim brick.",
     "RACE SIM: 1500m swim + 2.5h bike @race watts + 40min run @race pace | Longest run 100min"),
    (12, "peak",  6.0, 300, 3000,  75, 20, 2, 3, 3, 1, 100,  50, 1,
     "RECOVERY. Absorb peak block. Re-test if feeling fresh.",
     "Optional FTP confirm | Race-pace touches only"),
    (13, "peak",  9.5, 560, 5000, 140, 33, 3, 4, 4, 2, 180,  90, 0,
     "Final hard block. Everything race-specific now.",
     "BRICK: 2h bike race watts + 45min run race pace | Open water swim w/ race starts"),
    (14, "peak",  8.5, 500, 4500, 120, 28, 3, 3, 3, 2, 150,  75, 0,
     "Last big week. Final long ride + long run.",
     "Last long ride 2:30 | Last long run 75min | Swim race-distance straight"),
    (15, "taper", 5.5, 300, 3500,  70, 18, 3, 3, 3, 1,  90,  45, 0,
     "TAPER: volume -40%, keep intensity touches. Extra sleep.",
     "Short race-pace intervals all 3 sports | Gear check | Course review"),
    (16, "race",  4.0, 350, 2500,  50, 12, 2, 2, 2, 0,  45,  30, 0,
     "RACE WEEK. Short openers, big rest. Trust the work.",
     "Tue swim openers | Wed 30min bike w/ 3x1min race watts | Thu 20min run w/ strides | SUNDAY: RACE 70.3"),
]


def week_days(wk, phase, recovery, st, long_ride, long_run):
    """Expand standard week shape into day-level sessions.

    Mon strength, Tue swim+run, Wed bike quality, Thu swim(+strength),
    Fri run quality, Sat long ride (brick on brick weeks), Sun long run.
    """
    if wk == 16:
        return [
            (1, "rest", "Full rest", None, 0),
            (2, "swim", "Openers: 4x50 build", "race_pace", 30),
            (3, "bike", "30min + 3x1min race watts", "race_pace", 30),
            (4, "run", "20min + 4 strides", "race_pace", 20),
            (5, "rest", "Rest + travel/check-in", None, 0),
            (6, "swim", "10min loosen (optional)", "recovery", 15),
            (7, "brick", "RACE: Ironman 70.3", "race_pace", 330),
        ]
    brick = wk in (6, 9, 11, 13)
    quality = phase in ("build", "peak") and not recovery
    days = [
        (1, "strength", "Strength + mobility", "recovery", 45),
        (2, "swim",
         "Swim: drills + endurance" if phase == "base" else "Swim: CSS intervals",
         "endurance" if phase == "base" else "threshold", 45),
        (2, "run", "Run: easy Z2 + strides", "endurance", 40),
        (3, "bike",
         "Bike: tempo/sweet spot" if quality else "Bike: Z2 endurance",
         "tempo" if quality else "endurance", 70),
        (4, "swim", "Swim: endurance + technique", "endurance", 45),
        (5, "run",
         "Run: threshold intervals" if quality else "Run: easy Z2",
         "threshold" if quality else "endurance", 50),
        (6, "brick" if brick else "bike",
         "BRICK: long ride + transition run" if brick else "Long ride Z2",
         "race_pace" if brick else "endurance",
         long_ride + (25 if brick else 0)),
        (7, "run",
         "Long run Z2" + (" w/ race-pace finish" if quality else ""),
         "endurance", long_run),
    ]
    if st >= 2:
        days.insert(5, (4, "strength", "Strength: heavy lower + core", "endurance", 40))
    return days


def seed(race_date):
    con = connect()
    cur = con.cursor()
    cur.execute("DELETE FROM planned_workouts")
    cur.execute("DELETE FROM weekly_plan")

    for row in PLAN:
        (wk, phase, hours, tss, swim_m, bike_km, run_km,
         s, b, r, st, long_ride, long_run, rec, focus, key) = row
        start = None
        if race_date:
            monday_w16 = race_date - timedelta(days=race_date.weekday())
            start = monday_w16 - timedelta(weeks=16 - wk)
        cur.execute(
            """INSERT INTO weekly_plan
               (week_num, phase, start_date, planned_hours, planned_tss,
                swim_m, bike_km, run_km, swim_sessions, bike_sessions,
                run_sessions, strength_sessions, long_ride_min, long_run_min,
                is_recovery, focus, key_workouts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (wk, phase, start, hours, tss, swim_m, bike_km, run_km,
             s, b, r, st, long_ride, long_run, bool(rec), focus, key),
        )
        for dow, sport, title, intensity, mins in week_days(wk, phase, rec, st, long_ride, long_run):
            cur.execute(
                """INSERT INTO planned_workouts
                   (week_num, day_of_week, sport, title, intensity, duration_min)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (wk, dow, sport, title, intensity, mins),
            )

    con.commit()
    cur.execute("SELECT COUNT(*) FROM planned_workouts")
    print(f"Seeded 16 weeks, {cur.fetchone()[0]} planned workouts")
    con.close()


if __name__ == "__main__":
    rd = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    seed(rd)
