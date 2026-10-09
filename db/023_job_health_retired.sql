-- 023_job_health_retired.sql — retire a job_health row instead of leaving
-- it on the board.
--
-- job_health keeps one row per job forever (db/020). A job that is no longer
-- scheduled, such as health_drill after Drill 1, stayed on /settings/health
-- with its old failures, read as a live problem. Deleting the row would throw
-- away its history, so it is retired instead:
--
--   retired_at   set when a job is retired; NULL for every live job.
--
-- Retired rows drop out of /settings/health and out of the worker's health
-- monitor (core/ledger.py job_health_rows). If the worker schedules the job
-- again (HEALTH_DRILL=true), job_register clears retired_at, so it comes back
-- on the board by itself.
--
-- Retired here: health_drill (Drill 1, 2026-10-09; not scheduled since).
--
-- Prerequisites: db/020 (job_health).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

ALTER TABLE job_health ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;

UPDATE job_health
   SET retired_at = now(), updated_at = now()
 WHERE job = 'health_drill' AND retired_at IS NULL;

INSERT INTO migration_log (name) VALUES ('023_job_health_retired')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect health_drill retired, every other job not.
SELECT job, consecutive_failures, alert_state, retired_at FROM job_health ORDER BY job;
SELECT name, applied_at FROM migration_log WHERE name = '023_job_health_retired';
