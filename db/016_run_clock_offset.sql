-- 016_run_clock_offset.sql — the worker-to-database clock offset, per run
--
-- Each tick, core times a `SELECT clock_timestamp()` round trip and stores:
--
--   clock_offset_ms  database time − worker time at the round trip's midpoint.
--                    Positive = database ahead.
--   clock_rtt_ms     the round trip. The offset is good to ± rtt / 2.
--
-- Commitment payloads carry `quote_provenance`: the quote's fetch time on the
-- WORKER's clock, and separately a database-clock estimate (worker + offset).
-- Worker time is never stored as, or labelled as, database time. Comparing a
-- worker fetch time with `committed_at` (database clock) without this offset
-- compares two different clocks.
--
-- Written at INSERT by start_run, so db/015's runs_close_once guard (UPDATE
-- only) does not touch it.
--
-- Prerequisites: db/015 (migration_log).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS clock_offset_ms NUMERIC;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS clock_rtt_ms    NUMERIC CHECK (clock_rtt_ms IS NULL OR clock_rtt_ms >= 0);

INSERT INTO migration_log (name) VALUES ('016_run_clock_offset')
ON CONFLICT (name) DO NOTHING;

SELECT name, applied_at FROM migration_log ORDER BY id;
