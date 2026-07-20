-- Multi-athlete foundation: athletes + per-athlete settings, athlete_id on
-- every fact table. Existing data backfills to athlete 1.
-- Apply: psql "$IRONMAN_DB_URL" -f migrations/001_athletes.sql
SET search_path TO ironman;

CREATE TABLE IF NOT EXISTS athletes (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    tagline    TEXT DEFAULT 'Journey to Ironman 70.3',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Per-athlete configurable targets that drive every indicator.
CREATE TABLE IF NOT EXISTS athlete_settings (
    athlete_id          INT PRIMARY KEY REFERENCES athletes (id),
    race_date           DATE,                    -- kickoff anchor; week 16 contains it
    plan_weeks          INT NOT NULL DEFAULT 16,
    ctl_target          INT NOT NULL DEFAULT 65, -- fitness goal by race day
    compliance_goal_pct INT NOT NULL DEFAULT 85,
    ramp_limit_pct      INT NOT NULL DEFAULT 30, -- weekly hours ramp warning
    long_run_cap_min    INT NOT NULL DEFAULT 150,
    tsb_race_lo         INT NOT NULL DEFAULT 5,
    tsb_race_hi         INT NOT NULL DEFAULT 15,
    units               TEXT NOT NULL DEFAULT 'imperial'
                        CHECK (units IN ('imperial', 'metric'))
);

-- Seed athlete 1 from existing single-user data
INSERT INTO athletes (name)
SELECT 'Santosh Kanthety'
WHERE NOT EXISTS (SELECT 1 FROM athletes);
INSERT INTO athlete_settings (athlete_id)
SELECT id FROM athletes
ON CONFLICT (athlete_id) DO NOTHING;

-- athlete_id everywhere, default 1 for existing rows
ALTER TABLE activities       ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);
ALTER TABLE daily_vitals     ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);
ALTER TABLE daily_load       ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);
ALTER TABLE athlete_profile  ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);
ALTER TABLE weekly_plan      ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);
ALTER TABLE planned_workouts ADD COLUMN IF NOT EXISTS athlete_id INT NOT NULL DEFAULT 1 REFERENCES athletes (id);

-- Composite keys where the old PK was single-athlete
DO $$
BEGIN
  IF (SELECT COUNT(*) FROM information_schema.key_column_usage
      WHERE table_schema='ironman' AND table_name='daily_load'
        AND constraint_name='daily_load_pkey') = 1 THEN
    ALTER TABLE daily_load DROP CONSTRAINT daily_load_pkey;
    ALTER TABLE daily_load ADD PRIMARY KEY (athlete_id, date);
  END IF;
  IF (SELECT COUNT(*) FROM information_schema.key_column_usage
      WHERE table_schema='ironman' AND table_name='daily_vitals'
        AND constraint_name='daily_vitals_pkey') = 1 THEN
    ALTER TABLE daily_vitals DROP CONSTRAINT daily_vitals_pkey;
    ALTER TABLE daily_vitals ADD PRIMARY KEY (athlete_id, date);
  END IF;
  IF (SELECT COUNT(*) FROM information_schema.key_column_usage
      WHERE table_schema='ironman' AND table_name='weekly_plan'
        AND constraint_name='weekly_plan_pkey') = 1 THEN
    ALTER TABLE planned_workouts DROP CONSTRAINT planned_workouts_week_num_fkey;
    ALTER TABLE weekly_plan DROP CONSTRAINT weekly_plan_pkey;
    ALTER TABLE weekly_plan ADD PRIMARY KEY (athlete_id, week_num);
    ALTER TABLE planned_workouts
      ADD CONSTRAINT planned_workouts_plan_fkey
      FOREIGN KEY (athlete_id, week_num)
      REFERENCES weekly_plan (athlete_id, week_num);
  END IF;
END $$;

-- Activity uniqueness is per athlete now
ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_source_external_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_athlete_src
  ON activities (athlete_id, source, external_id);
CREATE INDEX IF NOT EXISTS idx_activities_athlete_start
  ON activities (athlete_id, start_time);
