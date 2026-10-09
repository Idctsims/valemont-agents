-- 025_answered_attempts.sql — every answer is an attempt on record.
--
-- db/024 refuses a void resolution with no 'resolve' attempt on record, and a
-- 'missed' close snapshot with no 'capture' attempt. The rule stays strict
-- (no pnl IS NULL carve-out). What changes is that core now makes it hold by
-- construction: the attempt that produced an answer is written in the SAME
-- transaction as the answer, before it, so the trigger always sees it.
--
--   resolutions        resolve() answered (hit/miss/push/void)  -> 'answered', purpose 'resolve'
--   closing_snapshots  capture_close() returned a close         -> 'answered', purpose 'capture'
--                      capture_close() raised CloseUnavailable  -> 'error',    purpose 'capture'
--
-- So an adapter that returns a void on its FIRST look is accepted: the look
-- itself is on record. Core's own abandonment (budget expired) asks nothing
-- and records nothing new; its void stands on the attempts already there,
-- which is exactly what db/024 checks.
--
-- This file only widens the result CHECK. The budgets are unaffected:
-- an answered commitment leaves the due set in the same transaction, so its
-- 'answered' row is never counted against it. Anything that reads this table
-- as "failed attempts" (a void-rate or retry-rate view) must now filter
-- result IN ('deferred','error').
--
-- Prerequisites: db/003. Existing rows are all 'deferred' or 'error', so the
-- new CHECK validates against them as-is.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

ALTER TABLE resolution_attempts
    DROP CONSTRAINT IF EXISTS resolution_attempts_result_check;
ALTER TABLE resolution_attempts
    ADD CONSTRAINT resolution_attempts_result_check
    CHECK (result IN ('deferred', 'error', 'answered'));

COMMENT ON TABLE resolution_attempts IS
    'One row per resolution or capture attempt. deferred/error: no answer, '
    'counted against the bounded-defer budget (core/agent.py). answered: the '
    'attempt that produced the resolution or closing snapshot, written in the '
    'same transaction (db/025). Failure rates must filter out answered.';

INSERT INTO migration_log (name) VALUES ('025_answered_attempts')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect the three-value CHECK and the log row.
SELECT pg_get_constraintdef(oid) AS result_check
  FROM pg_constraint
 WHERE conname = 'resolution_attempts_result_check';
SELECT name, applied_at FROM migration_log WHERE name = '025_answered_attempts';
