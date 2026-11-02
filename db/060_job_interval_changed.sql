-- 060_job_interval_changed.sql: a job's staleness restarts when its expected
-- interval changes (owner, 2026-11-02).
--
-- Why: PR #64 changed arsenal_pool's interval from a week to 6 hours. At the
-- next boot job_register stored the new interval, and the health monitor
-- measured staleness from the job's last good run (Nov 1 09:30 UTC) against
-- 2 x 6 h, so it pushed a false "arsenal_pool needs attention" at 21:18 UTC
-- (notification 713), recovered at 21:24 when the boot run finished.
--
-- What it changes: job_health gains interval_changed_at (timestamptz, NULL
-- until an interval actually changes). The worker's job_register stamps it
-- only when the stored interval differs from the registered one (never on a
-- plain re-registration at boot); the worker's health monitor and the web's
-- /settings/health then count staleness from the LATER of the last good run
-- and that change. Nothing else reads it.
--
-- Grants: authenticated and valemont_readonly hold table-level SELECT on
-- job_health (read 2026-11-02: relacl authenticated=r, valemont_readonly=r),
-- which covers a new column; anon has nothing. No function or trigger is
-- created, so no self-test block (CLAUDE.md section 5).
--
-- ORDER: paste this BEFORE the worker PR that reads the column merges: the
-- worker's job_register and job_health_rows, and the web's health page,
-- name it.
--
-- Prerequisites: db/020 (job_health), db/015 (migration_log).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

ALTER TABLE public.job_health ADD COLUMN IF NOT EXISTS interval_changed_at timestamptz;

COMMENT ON COLUMN public.job_health.interval_changed_at IS
    'When job_register last CHANGED expected_interval_s (db/060). Staleness counts from the later of this and last_ok_at.';

INSERT INTO public.migration_log (name) VALUES ('060_job_interval_changed')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only):
--   SELECT name, applied_at FROM migration_log WHERE name = '060_job_interval_changed';
--   SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_name = 'job_health' AND column_name = 'interval_changed_at';
--   SELECT has_column_privilege('authenticated', 'public.job_health', 'interval_changed_at', 'SELECT'),
--          has_column_privilege('valemont_readonly', 'public.job_health', 'interval_changed_at', 'SELECT'),
--          has_column_privilege('anon', 'public.job_health', 'interval_changed_at', 'SELECT');
--   -- expected: true, true, false
