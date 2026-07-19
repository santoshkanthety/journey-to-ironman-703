-- Analytics marts for Databricks dashboard (and local use).
-- Apply: psql "$IRONMAN_DB_URL" -f analytics_views.sql
SET search_path TO ironman;

-- Monthly volume by sport
CREATE OR REPLACE VIEW v_monthly_volume AS
SELECT
    date_trunc('month', start_time)::date AS month,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 1)    AS hours,
    ROUND(SUM(distance_m) / 1609.344, 1)  AS mi,
    ROUND(SUM(elevation_gain_m) * 3.28084, 0) AS elev_ft,
    ROUND(AVG(avg_hr), 0)                 AS avg_hr,
    ROUND(SUM(COALESCE(tss, 0)), 0)       AS tss
FROM activities
GROUP BY 1, 2;

-- Yearly summary by sport
CREATE OR REPLACE VIEW v_yearly_summary AS
SELECT
    EXTRACT(YEAR FROM start_time)::int    AS year,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 0)    AS hours,
    ROUND(SUM(distance_m) / 1609.344, 0)  AS mi,
    ROUND(SUM(elevation_gain_m) * 3.28084 / 1000, 1) AS elev_kft,
    ROUND(MAX(distance_m) / 1609.344, 1)  AS longest_mi,
    ROUND(AVG(distance_m) / 1609.344, 1)  AS avg_mi
FROM activities
GROUP BY 1, 2;

-- Training-time heatmap: day-of-week x hour-of-day
CREATE OR REPLACE VIEW v_dow_hour AS
SELECT
    EXTRACT(ISODOW FROM start_time)::int  AS dow,          -- 1=Mon
    to_char(start_time, 'Dy')             AS dow_name,
    EXTRACT(HOUR FROM start_time)::int    AS hour,
    sport,
    COUNT(*)                              AS sessions,
    ROUND(SUM(duration_s) / 3600.0, 1)    AS hours
FROM activities
GROUP BY 1, 2, 3, 4;

-- Run distance distribution (1 mile buckets to 16, then wide)
CREATE OR REPLACE VIEW v_run_distance_dist AS
SELECT
    CASE WHEN distance_m < 25750 THEN floor(distance_m / 1609.344)::int
         ELSE 16 END                      AS mi_bucket,
    COUNT(*)                              AS runs
FROM activities
WHERE sport = 'run' AND distance_m > 400
GROUP BY 1;

-- Best efforts (proxy): fastest avg pace among runs >= band distance.
-- True Strava best-effort splits need per-activity API pulls; this uses
-- whole-run averages, so times slightly conservative.
CREATE OR REPLACE VIEW v_best_efforts AS
WITH bands AS (
    SELECT * FROM (VALUES
        ('5k', 5000), ('10k', 10000), ('15k', 15000),
        ('Half marathon', 21097), ('30k', 30000), ('Marathon', 42195)
    ) AS b(band, meters)
),
ranked AS (
    SELECT
        b.band, b.meters,
        EXTRACT(YEAR FROM a.start_time)::int AS year,
        a.start_time::date AS date, a.title,
        a.distance_m, a.moving_s,
        a.moving_s * b.meters / a.distance_m AS est_time_s,   -- time at band distance at run's avg pace
        a.moving_s / (a.distance_m / 1000.0) AS pace_s_km,
        ROW_NUMBER() OVER (PARTITION BY b.band ORDER BY a.moving_s / a.distance_m) AS rk_all,
        ROW_NUMBER() OVER (PARTITION BY b.band, EXTRACT(YEAR FROM a.start_time)
                           ORDER BY a.moving_s / a.distance_m) AS rk_year
    FROM activities a
    JOIN bands b ON a.distance_m >= b.meters * 0.98
    WHERE a.sport = 'run' AND a.moving_s > 0 AND a.distance_m > 0
      -- plausible-pace guard: 3:15–12:00 /km; faster = mistagged ride / GPS error
      AND a.moving_s / (a.distance_m / 1000.0) BETWEEN 195 AND 720
)
SELECT band, meters, year, date, title,
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
ORDER BY meters, year;

-- Personal records, one row per metric
CREATE OR REPLACE VIEW v_records AS
SELECT 'Longest run (mi)' AS record, ROUND(MAX(distance_m)/1609.344,1)::text AS value,
       (SELECT start_time::date::text FROM activities WHERE sport='run'
        ORDER BY distance_m DESC NULLS LAST LIMIT 1) AS date
FROM activities WHERE sport='run'
UNION ALL
SELECT 'Longest ride (mi)', ROUND(MAX(distance_m)/1609.344,1)::text,
       (SELECT start_time::date::text FROM activities WHERE sport='bike'
        ORDER BY distance_m DESC NULLS LAST LIMIT 1)
FROM activities WHERE sport='bike'
UNION ALL
SELECT 'Most climbing in a ride (ft)', ROUND(MAX(elevation_gain_m)*3.28084,0)::text,
       (SELECT start_time::date::text FROM activities WHERE sport='bike'
        ORDER BY elevation_gain_m DESC NULLS LAST LIMIT 1)
FROM activities WHERE sport='bike'
UNION ALL
SELECT 'Biggest week (hours)', ROUND(MAX(w.h),1)::text, MAX(w.wk)::text
FROM (SELECT date_trunc('week', start_time)::date wk, SUM(duration_s)/3600.0 h
      FROM activities GROUP BY 1 ORDER BY h DESC LIMIT 1) w
UNION ALL
SELECT 'Longest run streak (days)', MAX(len)::text, NULL
FROM (
    SELECT COUNT(*) AS len
    FROM (SELECT d, d - (ROW_NUMBER() OVER (ORDER BY d))::int * INTERVAL '1 day' AS grp
          FROM (SELECT DISTINCT start_time::date AS d FROM activities WHERE sport='run') x) y
    GROUP BY grp) z;

-- Rolling 7d / 28d run load (acute:chronic ratio — injury guard)
CREATE OR REPLACE VIEW v_run_load_ratio AS
WITH daily AS (
    SELECT start_time::date AS d, SUM(distance_m) / 1609.344 AS mi
    FROM activities WHERE sport = 'run' GROUP BY 1
),
cal AS (
    SELECT generate_series(
        (SELECT MIN(d) FROM daily), CURRENT_DATE, '1 day')::date AS d
)
SELECT
    cal.d AS date,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w7, 1)       AS mi_7d,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w28 / 4, 1)  AS mi_28d_weekly_avg,
    ROUND(SUM(COALESCE(daily.mi,0)) OVER w7
        / NULLIF(SUM(COALESCE(daily.mi,0)) OVER w28 / 4, 0), 2) AS acwr
FROM cal LEFT JOIN daily USING (d)
WINDOW w7  AS (ORDER BY cal.d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
       w28 AS (ORDER BY cal.d ROWS BETWEEN 27 PRECEDING AND CURRENT ROW);

-- Flat activity extract for replication (avoids raw_json bloat)
CREATE OR REPLACE VIEW v_activities_export AS
SELECT id, source, sport, start_time, start_time::date AS date,
       EXTRACT(YEAR FROM start_time)::int AS year,
       to_char(start_time, 'Dy') AS dow_name,
       EXTRACT(ISODOW FROM start_time)::int AS dow,
       duration_s, moving_s, ROUND(duration_s/3600.0, 2) AS hours,
       distance_m, ROUND(distance_m/1609.344, 2) AS mi,
       elevation_gain_m, avg_hr, max_hr, avg_power_w, norm_power_w,
       avg_cadence, avg_pace_s_km, avg_pace_s_100m,
       intensity_factor, tss, calories, is_race, is_brick, title
FROM activities;
