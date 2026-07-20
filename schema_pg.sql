-- Ironman 70.3 Training — Postgres schema (multi-athlete)
-- Apply: psql "$IRONMAN_DB_URL" -f schema_pg.sql

CREATE SCHEMA IF NOT EXISTS ironman;
SET search_path TO ironman;

-- ---------------------------------------------------------------
-- Athletes + per-athlete configurable targets (SaaS model: every
-- indicator reads these instead of hardcoded values).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS athletes (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    tagline    TEXT DEFAULT 'Journey to Ironman 70.3',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS athlete_settings (
    athlete_id          INT PRIMARY KEY REFERENCES athletes (id),
    race_date           DATE,                    -- kickoff anchor; week 16 contains it
    plan_weeks          INT NOT NULL DEFAULT 16,
    ctl_target          INT NOT NULL DEFAULT 65,
    compliance_goal_pct INT NOT NULL DEFAULT 85,
    ramp_limit_pct      INT NOT NULL DEFAULT 30,
    long_run_cap_min    INT NOT NULL DEFAULT 150,
    tsb_race_lo         INT NOT NULL DEFAULT 5,
    tsb_race_hi         INT NOT NULL DEFAULT 15,
    units               TEXT NOT NULL DEFAULT 'imperial'
                        CHECK (units IN ('imperial', 'metric'))
);

-- ---------------------------------------------------------------
-- Threshold benchmarks (one row per test date, per athlete).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS athlete_profile (
    id                      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    athlete_id              INT NOT NULL DEFAULT 1 REFERENCES athletes (id),
    test_date               DATE NOT NULL,
    weight_kg               NUMERIC(5,1),
    ftp_watts               INT,
    run_threshold_pace_s_km INT,
    css_pace_s_100m         INT,
    lthr_bike               INT,
    lthr_run                INT,
    max_hr                  INT,
    resting_hr_baseline     INT,
    hrv_baseline_ms         NUMERIC(5,1),
    notes                   TEXT
);

-- ---------------------------------------------------------------
-- Activities: one row per workout (Strava / Apple Health).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    athlete_id       INT NOT NULL DEFAULT 1 REFERENCES athletes (id),
    source           TEXT NOT NULL CHECK (source IN ('strava','apple','manual')),
    external_id      TEXT,
    sport            TEXT NOT NULL CHECK (sport IN ('swim','bike','run','strength','other')),
    start_time       TIMESTAMPTZ NOT NULL,
    duration_s       INT NOT NULL,
    moving_s         INT,
    distance_m       NUMERIC(9,1),
    elevation_gain_m NUMERIC(7,1),
    avg_hr           INT,
    max_hr           INT,
    avg_power_w      INT,
    norm_power_w     INT,
    avg_cadence      NUMERIC(5,1),
    avg_pace_s_km    NUMERIC(6,1),
    avg_pace_s_100m  NUMERIC(6,1),
    intensity_factor NUMERIC(4,2),
    tss              NUMERIC(6,1),
    calories         NUMERIC(7,1),
    is_race          BOOLEAN DEFAULT FALSE,
    is_brick         BOOLEAN DEFAULT FALSE,
    rpe              INT CHECK (rpe BETWEEN 1 AND 10),
    title            TEXT,
    raw_json         JSONB
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_athlete_src
  ON activities (athlete_id, source, external_id);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities (start_time);
CREATE INDEX IF NOT EXISTS idx_activities_sport ON activities (sport, start_time);
CREATE INDEX IF NOT EXISTS idx_activities_athlete_start
  ON activities (athlete_id, start_time);

-- ---------------------------------------------------------------
-- Daily vitals (Apple Health + manual), per athlete.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_vitals (
    athlete_id         INT NOT NULL DEFAULT 1 REFERENCES athletes (id),
    date               DATE NOT NULL,
    resting_hr         INT,
    hrv_ms             NUMERIC(5,1),
    sleep_hours        NUMERIC(4,2),
    sleep_deep_hours   NUMERIC(4,2),
    weight_kg          NUMERIC(5,1),
    body_fat_pct       NUMERIC(4,1),
    spo2_pct           NUMERIC(4,1),
    vo2max_est         NUMERIC(4,1),
    steps              INT,
    active_kcal        NUMERIC(7,1),
    subjective_fatigue INT CHECK (subjective_fatigue BETWEEN 1 AND 10),
    soreness           INT CHECK (soreness BETWEEN 1 AND 10),
    mood               INT CHECK (mood BETWEEN 1 AND 10),
    notes              TEXT,
    PRIMARY KEY (athlete_id, date)
);

-- ---------------------------------------------------------------
-- Daily training load: CTL/ATL/TSB (compute_metrics.py fills).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_load (
    athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id),
    date       DATE NOT NULL,
    tss_total  NUMERIC(6,1) NOT NULL DEFAULT 0,
    ctl        NUMERIC(6,1),
    atl        NUMERIC(6,1),
    tsb        NUMERIC(6,1),
    PRIMARY KEY (athlete_id, date)
);

-- ---------------------------------------------------------------
-- 16-week plan per athlete. start_date set by the kickoff feature
-- (race_date in athlete_settings anchors week 16).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_plan (
    athlete_id        INT NOT NULL DEFAULT 1 REFERENCES athletes (id),
    week_num          INT NOT NULL CHECK (week_num BETWEEN 1 AND 16),
    phase             TEXT NOT NULL CHECK (phase IN ('base','build','peak','taper','race')),
    start_date        DATE,
    planned_hours     NUMERIC(4,1) NOT NULL,
    planned_tss       INT NOT NULL,
    swim_m            INT NOT NULL,
    bike_km           NUMERIC(6,1) NOT NULL,
    run_km            NUMERIC(5,1) NOT NULL,
    swim_sessions     INT NOT NULL,
    bike_sessions     INT NOT NULL,
    run_sessions      INT NOT NULL,
    strength_sessions INT NOT NULL DEFAULT 2,
    long_ride_min     INT,
    long_run_min      INT,
    is_recovery       BOOLEAN DEFAULT FALSE,
    focus             TEXT,
    key_workouts      TEXT,
    PRIMARY KEY (athlete_id, week_num)
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    athlete_id   INT NOT NULL DEFAULT 1,
    week_num     INT NOT NULL,
    day_of_week  INT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    sport        TEXT NOT NULL CHECK (sport IN ('swim','bike','run','strength','brick','rest')),
    title        TEXT NOT NULL,
    description  TEXT,
    duration_min INT,
    intensity    TEXT CHECK (intensity IN ('recovery','endurance','tempo','threshold','vo2','race_pace')),
    tss_est      INT,
    completed_activity_id BIGINT REFERENCES activities (id),
    FOREIGN KEY (athlete_id, week_num) REFERENCES weekly_plan (athlete_id, week_num)
);
CREATE INDEX IF NOT EXISTS idx_planned_week ON planned_workouts (week_num, day_of_week);

-- ---------------------------------------------------------------
-- Views (KPI sources) — all athlete-aware
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW v_weekly_actuals AS
SELECT
    athlete_id,
    date_trunc('week', start_time)::date                       AS week_start,
    ROUND(SUM(duration_s) / 3600.0, 2)                         AS hours,
    ROUND(SUM(COALESCE(tss, 0)), 0)                            AS tss,
    ROUND(SUM(distance_m) FILTER (WHERE sport = 'swim'), 0)            AS swim_m,
    ROUND(SUM(distance_m) FILTER (WHERE sport = 'bike') / 1000.0, 1)   AS bike_km,
    ROUND(SUM(distance_m) FILTER (WHERE sport = 'run')  / 1000.0, 1)   AS run_km,
    COUNT(*) FILTER (WHERE sport = 'swim')                     AS swim_sessions,
    COUNT(*) FILTER (WHERE sport = 'bike')                     AS bike_sessions,
    COUNT(*) FILTER (WHERE sport = 'run')                      AS run_sessions,
    COUNT(*) FILTER (WHERE sport = 'strength')                 AS strength_sessions,
    COUNT(*) FILTER (WHERE is_brick)                           AS bricks
FROM activities
GROUP BY 1, 2;

CREATE OR REPLACE VIEW v_weekly_compliance AS
SELECT
    p.athlete_id, p.week_num, p.phase, p.start_date, p.is_recovery,
    p.planned_hours, a.hours  AS actual_hours,
    ROUND(100 * a.hours  / NULLIF(p.planned_hours, 0), 0) AS hours_pct,
    p.planned_tss,   a.tss    AS actual_tss,
    ROUND(100 * a.tss    / NULLIF(p.planned_tss, 0), 0)   AS tss_pct,
    p.swim_m,  a.swim_m  AS actual_swim_m,
    ROUND(100 * a.swim_m  / NULLIF(p.swim_m, 0), 0)       AS swim_pct,
    p.bike_km, a.bike_km AS actual_bike_km,
    ROUND(100 * a.bike_km / NULLIF(p.bike_km, 0), 0)      AS bike_pct,
    p.run_km,  a.run_km  AS actual_run_km,
    ROUND(100 * a.run_km  / NULLIF(p.run_km, 0), 0)       AS run_pct
FROM weekly_plan p
LEFT JOIN v_weekly_actuals a
  ON a.athlete_id = p.athlete_id AND a.week_start = p.start_date;

CREATE OR REPLACE VIEW v_efficiency AS
SELECT
    athlete_id, start_time::date AS date, sport,
    CASE
      WHEN sport = 'bike' AND avg_hr > 0 AND norm_power_w IS NOT NULL
        THEN ROUND(norm_power_w::numeric / avg_hr, 3)
      WHEN sport = 'run' AND avg_hr > 0 AND avg_pace_s_km > 0
        THEN ROUND((60000.0 / avg_pace_s_km) / avg_hr, 3)
    END AS efficiency_factor,
    avg_hr, duration_s
FROM activities
WHERE sport IN ('bike','run')
  AND avg_hr > 0
  AND duration_s >= 2400
  AND COALESCE(intensity_factor, 0) < 0.85;

CREATE OR REPLACE VIEW v_readiness AS
SELECT
    v.athlete_id, v.date,
    v.resting_hr, v.hrv_ms, v.sleep_hours, v.subjective_fatigue,
    l.ctl, l.atl, l.tsb,
    (v.resting_hr - p.resting_hr_baseline)        AS rhr_delta,
    ROUND(100 * (v.hrv_ms - p.hrv_baseline_ms) / NULLIF(p.hrv_baseline_ms, 0), 0) AS hrv_delta_pct,
    CASE
      WHEN l.tsb < -30 OR (v.resting_hr - p.resting_hr_baseline) >= 7
        OR (v.hrv_ms < p.hrv_baseline_ms * 0.85) OR v.sleep_hours < 5
        THEN 'red'
      WHEN l.tsb < -20 OR (v.resting_hr - p.resting_hr_baseline) >= 4
        OR (v.hrv_ms < p.hrv_baseline_ms * 0.93) OR v.sleep_hours < 6.5
        THEN 'yellow'
      ELSE 'green'
    END AS readiness
FROM daily_vitals v
LEFT JOIN daily_load l USING (athlete_id, date)
LEFT JOIN LATERAL (
    SELECT resting_hr_baseline, hrv_baseline_ms
    FROM athlete_profile
    WHERE athlete_id = v.athlete_id
    ORDER BY test_date DESC LIMIT 1
) p ON TRUE;
