-- Ironman 70.3 Training — Postgres schema
-- Apply: psql "$IRONMAN_DB_URL" -f schema_pg.sql

CREATE SCHEMA IF NOT EXISTS ironman;
SET search_path TO ironman;

-- ---------------------------------------------------------------
-- Athlete profile: threshold benchmarks driving zones + TSS.
-- One row per test date; latest row = current zones.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS athlete_profile (
    id                      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    test_date               DATE NOT NULL,
    weight_kg               NUMERIC(5,1),
    ftp_watts               INT,        -- bike Functional Threshold Power
    run_threshold_pace_s_km INT,        -- sec per km at threshold
    css_pace_s_100m         INT,        -- swim Critical Swim Speed, sec/100m
    lthr_bike               INT,
    lthr_run                INT,
    max_hr                  INT,
    resting_hr_baseline     INT,
    hrv_baseline_ms         NUMERIC(5,1),
    notes                   TEXT
);

-- ---------------------------------------------------------------
-- Activities: one row per workout from Strava / Apple Health.
-- (source, external_id) unique -> idempotent re-sync, dedupe.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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
    avg_pace_s_km    NUMERIC(6,1),      -- run
    avg_pace_s_100m  NUMERIC(6,1),      -- swim
    intensity_factor NUMERIC(4,2),
    tss              NUMERIC(6,1),      -- computed by etl if source lacks it
    calories         NUMERIC(7,1),
    is_race          BOOLEAN DEFAULT FALSE,
    is_brick         BOOLEAN DEFAULT FALSE,
    rpe              INT CHECK (rpe BETWEEN 1 AND 10),
    title            TEXT,
    raw_json         JSONB,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities (start_time);
CREATE INDEX IF NOT EXISTS idx_activities_sport ON activities (sport, start_time);

-- ---------------------------------------------------------------
-- Daily vitals: Apple Health (watch) + manual subjective scores.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_vitals (
    date               DATE PRIMARY KEY,
    resting_hr         INT,
    hrv_ms             NUMERIC(5,1),    -- Apple SDNN
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
    notes              TEXT
);

-- ---------------------------------------------------------------
-- Daily training load: CTL/ATL/TSB (compute_metrics.py fills).
-- CTL = 42d EMA of daily TSS (fitness), ATL = 7d EMA (fatigue),
-- TSB = prior-day CTL - prior-day ATL (form).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_load (
    date      DATE PRIMARY KEY,
    tss_total NUMERIC(6,1) NOT NULL DEFAULT 0,
    ctl       NUMERIC(6,1),
    atl       NUMERIC(6,1),
    tsb       NUMERIC(6,1)
);

-- ---------------------------------------------------------------
-- 16-week plan. week 16 = race week. start_date = Monday.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weekly_plan (
    week_num          INT PRIMARY KEY CHECK (week_num BETWEEN 1 AND 16),
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
    key_workouts      TEXT
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    week_num     INT NOT NULL REFERENCES weekly_plan (week_num),
    day_of_week  INT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),  -- 1=Mon
    sport        TEXT NOT NULL CHECK (sport IN ('swim','bike','run','strength','brick','rest')),
    title        TEXT NOT NULL,
    description  TEXT,
    duration_min INT,
    intensity    TEXT CHECK (intensity IN ('recovery','endurance','tempo','threshold','vo2','race_pace')),
    tss_est      INT,
    completed_activity_id BIGINT REFERENCES activities (id)
);
CREATE INDEX IF NOT EXISTS idx_planned_week ON planned_workouts (week_num, day_of_week);

-- ---------------------------------------------------------------
-- Views (KPI sources)
-- ---------------------------------------------------------------
CREATE OR REPLACE VIEW v_weekly_actuals AS
SELECT
    date_trunc('week', start_time)::date                       AS week_start,  -- Monday
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
GROUP BY 1;

CREATE OR REPLACE VIEW v_weekly_compliance AS
SELECT
    p.week_num, p.phase, p.start_date, p.is_recovery,
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
LEFT JOIN v_weekly_actuals a ON a.week_start = p.start_date;

-- Efficiency factor: bike NP/HR, run speed(m/min)/HR on steady sessions.
-- Rising EF at same HR = aerobic fitness gain.
CREATE OR REPLACE VIEW v_efficiency AS
SELECT
    start_time::date AS date, sport,
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

-- Readiness flags: red = back off today.
CREATE OR REPLACE VIEW v_readiness AS
SELECT
    v.date,
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
LEFT JOIN daily_load l USING (date)
CROSS JOIN LATERAL (
    SELECT resting_hr_baseline, hrv_baseline_ms
    FROM athlete_profile ORDER BY test_date DESC LIMIT 1
) p;
