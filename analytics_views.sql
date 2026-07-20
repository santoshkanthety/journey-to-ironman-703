-- Analytics marts for dashboards (Databricks + local). All athlete-aware.
-- Apply: psql "$IRONMAN_DB_URL" -f analytics_views.sql
SET search_path TO ironman;

-- Monthly volume by sport
CREATE OR REPLACE VIEW v_monthly_volume AS
SELECT
    athlete_id,
    date_trunc('month', start_time)::date AS month,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 1)    AS hours,
    ROUND(SUM(distance_m) / 1609.344, 1)  AS mi,
    ROUND(SUM(elevation_gain_m) * 3.28084, 0) AS elev_ft,
    ROUND(AVG(avg_hr), 0)                 AS avg_hr,
    ROUND(SUM(COALESCE(tss, 0)), 0)       AS tss
FROM activities
GROUP BY 1, 2, 3;

-- Yearly summary by sport
CREATE OR REPLACE VIEW v_yearly_summary AS
SELECT
    athlete_id,
    EXTRACT(YEAR FROM start_time)::int    AS year,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 0)    AS hours,
    ROUND(SUM(distance_m) / 1609.344, 0)  AS mi,
    ROUND(SUM(elevation_gain_m) * 3.28084 / 1000, 1) AS elev_kft,
    ROUND(MAX(distance_m) / 1609.344, 1)  AS longest_mi,
    ROUND(AVG(distance_m) / 1609.344, 1)  AS avg_mi
FROM activities
GROUP BY 1, 2, 3;

-- Training-time heatmap: day-of-week x hour-of-day
CREATE OR REPLACE VIEW v_dow_hour AS
SELECT
    athlete_id,
    EXTRACT(ISODOW FROM start_time)::int  AS dow,
    to_char(start_time, 'Dy')             AS dow_name,
    EXTRACT(HOUR FROM start_time)::int    AS hour,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 1)    AS hours
FROM activities
GROUP BY 1, 2, 3, 4, 5;

-- Run distance distribution (1 mile buckets to 16, then wide)
CREATE OR REPLACE VIEW v_run_distance_dist AS
SELECT
    athlete_id,
    CASE WHEN distance_m < 25750 THEN floor(distance_m / 1609.344)::int
         ELSE 16 END                      AS mi_bucket,
    COUNT(*)                              AS runs
FROM activities
WHERE sport = 'run' AND distance_m > 400
GROUP BY 1, 2;

-- Best efforts (proxy): fastest avg pace among runs >= band distance.
CREATE OR REPLACE VIEW v_best_efforts AS
WITH bands AS (
    SELECT * FROM (VALUES
        ('5k', 5000), ('10k', 10000), ('15k', 15000),
        ('Half marathon', 21097), ('30k', 30000), ('Marathon', 42195)
    ) AS b(band, meters)
),
ranked AS (
    SELECT
        a.athlete_id, b.band, b.meters,
        EXTRACT(YEAR FROM a.start_time)::int AS year,
        a.start_time::date AS date, a.title,
        a.distance_m, a.moving_s,
        a.moving_s * b.meters / a.distance_m AS est_time_s,
        a.moving_s / (a.distance_m / 1000.0) AS pace_s_km,
        ROW_NUMBER() OVER (PARTITION BY a.athlete_id, b.band
                           ORDER BY a.moving_s / a.distance_m) AS rk_all,
        ROW_NUMBER() OVER (PARTITION BY a.athlete_id, b.band,
                                        EXTRACT(YEAR FROM a.start_time)
                           ORDER BY a.moving_s / a.distance_m) AS rk_year
    FROM activities a
    JOIN bands b ON a.distance_m >= b.meters * 0.98
    WHERE a.sport = 'run' AND a.moving_s > 0 AND a.distance_m > 0
      -- plausible-pace guard: 3:15–12:00 /km; faster = mistagged ride / GPS error
      AND a.moving_s / (a.distance_m / 1000.0) BETWEEN 195 AND 720
)
SELECT athlete_id, band, meters, year, date, title,
       ROUND(distance_m / 1609.344, 2) AS run_mi,
       ROUND(est_time_s)              AS est_time_s,
       to_char((ROUND(est_time_s) || ' seconds')::interval, 'HH24:MI:SS') AS est_time,
       ROUND(pace_s_km)               AS pace_s_km,
       to_char((ROUND(pace_s_km) || ' seconds')::interval, 'MI:SS')       AS pace_min_km,
       ROUND(pace_s_km * 1.609344)    AS pace_s_mi,
       to_char((ROUND(pace_s_km * 1.609344) || ' seconds')::interval, 'MI:SS') AS pace_min_mi,
       (rk_all = 1)                   AS is_all_time_pr,
       rk_year
FROM ranked
WHERE rk_year = 1
ORDER BY athlete_id, meters, year;

-- Personal records, per athlete
CREATE OR REPLACE VIEW v_records AS
SELECT athlete_id, 'Longest run (mi)' AS record,
       ROUND(distance_m / 1609.344, 1)::text AS value, start_time::date::text AS date
FROM (SELECT DISTINCT ON (athlete_id) athlete_id, distance_m, start_time
      FROM activities WHERE sport = 'run'
      ORDER BY athlete_id, distance_m DESC NULLS LAST) r
UNION ALL
SELECT athlete_id, 'Longest ride (mi)',
       ROUND(distance_m / 1609.344, 1)::text, start_time::date::text
FROM (SELECT DISTINCT ON (athlete_id) athlete_id, distance_m, start_time
      FROM activities WHERE sport = 'bike'
      ORDER BY athlete_id, distance_m DESC NULLS LAST) r
UNION ALL
SELECT athlete_id, 'Most climbing in a ride (ft)',
       ROUND(elevation_gain_m * 3.28084, 0)::text, start_time::date::text
FROM (SELECT DISTINCT ON (athlete_id) athlete_id, elevation_gain_m, start_time
      FROM activities WHERE sport = 'bike'
      ORDER BY athlete_id, elevation_gain_m DESC NULLS LAST) r
UNION ALL
SELECT athlete_id, 'Biggest week (hours)', ROUND(h, 1)::text, wk::text
FROM (SELECT DISTINCT ON (athlete_id) athlete_id,
             date_trunc('week', start_time)::date AS wk,
             SUM(duration_s) / 3600.0 AS h
      FROM activities GROUP BY athlete_id, 2
      ORDER BY athlete_id, h DESC) w
UNION ALL
SELECT athlete_id, 'Longest run streak (days)', MAX(len)::text, NULL
FROM (
    SELECT athlete_id, COUNT(*) AS len
    FROM (SELECT athlete_id, d,
                 d - (ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY d))::int
                   * INTERVAL '1 day' AS grp
          FROM (SELECT DISTINCT athlete_id, start_time::date AS d
                FROM activities WHERE sport = 'run') x) y
    GROUP BY athlete_id, grp) z
GROUP BY athlete_id;

-- Rolling 7d / 28d run load (acute:chronic ratio — injury guard)
CREATE OR REPLACE VIEW v_run_load_ratio AS
WITH daily AS (
    SELECT athlete_id, start_time::date AS d, SUM(distance_m) / 1609.344 AS mi
    FROM activities WHERE sport = 'run' GROUP BY 1, 2
),
bounds AS (
    SELECT athlete_id, MIN(d) AS lo FROM daily GROUP BY 1
),
cal AS (
    SELECT b.athlete_id, gs::date AS d
    FROM bounds b
    CROSS JOIN LATERAL generate_series(b.lo, CURRENT_DATE, '1 day') gs
)
SELECT
    cal.athlete_id, cal.d AS date,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w7, 1)       AS mi_7d,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w28 / 4, 1)  AS mi_28d_weekly_avg,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w7
        / NULLIF(SUM(COALESCE(daily.mi,0)) OVER w28 / 4, 0), 2) AS acwr
FROM cal LEFT JOIN daily USING (athlete_id, d)
WINDOW w7  AS (PARTITION BY cal.athlete_id ORDER BY cal.d
               ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
       w28 AS (PARTITION BY cal.athlete_id ORDER BY cal.d
               ROWS BETWEEN 27 PRECEDING AND CURRENT ROW);

-- Flat activity extract for replication (avoids raw_json bloat)
CREATE OR REPLACE VIEW v_activities_export AS
SELECT id, athlete_id, source, sport, start_time, start_time::date AS date,
       EXTRACT(YEAR FROM start_time)::int AS year,
       to_char(start_time, 'Dy') AS dow_name,
       EXTRACT(ISODOW FROM start_time)::int AS dow,
       duration_s, moving_s, ROUND(duration_s/3600.0, 2) AS hours,
       distance_m, ROUND(distance_m/1609.344, 2) AS mi,
       elevation_gain_m, avg_hr, max_hr, avg_power_w, norm_power_w,
       avg_cadence, avg_pace_s_km, avg_pace_s_100m,
       intensity_factor, tss, calories, is_race, is_brick, title
FROM activities;

-- Monthly vitals rollup (Apple Health) — RHR/HRV/sleep/VO2max/weight trends
CREATE OR REPLACE VIEW v_vitals_monthly AS
SELECT
    athlete_id,
    date_trunc('month', date)::date AS month,
    ROUND(AVG(resting_hr), 1)  AS rhr_avg,
    MIN(resting_hr)            AS rhr_min,
    MAX(resting_hr)            AS rhr_max,
    ROUND(AVG(hrv_ms), 1)      AS hrv_avg,
    ROUND(AVG(sleep_hours), 2) AS sleep_avg,
    ROUND(AVG(weight_kg) * 2.20462, 1) AS weight_lb_avg,
    ROUND(AVG(vo2max_est), 1)  AS vo2_avg,
    COUNT(resting_hr)          AS rhr_days
FROM daily_vitals
GROUP BY 1, 2;

-- Monthly workout heart rate by sport (cardiac drift / fitness signal)
CREATE OR REPLACE VIEW v_workout_hr_monthly AS
SELECT
    athlete_id,
    date_trunc('month', start_time)::date AS month,
    sport,
    ROUND(AVG(avg_hr), 0) AS avg_hr,
    ROUND(MAX(max_hr), 0) AS max_hr,
    COUNT(*)              AS sessions
FROM activities
WHERE avg_hr IS NOT NULL AND sport IN ('run', 'bike')
GROUP BY 1, 2, 3;
