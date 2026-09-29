-- 006_live_test_agent.sql — an agent for the live-database test suite
--
-- Some invariants this project depends on cannot be tested without a database:
-- the two attempt-budget queries, every CHECK constraint, every append-only
-- trigger, `committed_at` being set by the server, `capture_lag_seconds` being
-- computed by one. The stub suite in `tests/` refuses the database on purpose
-- and so is structurally blind to all of it. `tests_live/` covers that gap, and
-- it needs somewhere to write.
--
-- WHY NOT `_fake`. It would work — it is already `is_test` — but `_fake`'s row
-- counts are a documented, predictable property of `scripts/run_fake.py`, and
-- mixing automated test rows into them destroys that. Two harness agents keeps
-- "the demo" and "the test suite" separable in the one table nobody can delete
-- from.
--
-- These rows are PERMANENT, like every other row here. That is the cost, and it
-- is the right cost: `is_test = true` keeps them out of every track-record
-- calculation (TRACK_RECORD_FILTER, CLAUDE.md §5), and a test agent whose rows
-- could be deleted would be testing a different database from the real one.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

INSERT INTO agents (slug, display_name, domain, enabled, is_test) VALUES
    ('_test', 'Live Test Suite', 'test', FALSE, TRUE)
ON CONFLICT (slug) DO NOTHING;

-- enabled = FALSE: the orchestrator must never schedule this one. It exists to
-- be written to by tests, never to wake up on a timer.
UPDATE agents SET enabled = FALSE, is_test = TRUE WHERE slug = '_test';

COMMENT ON TABLE agents IS
    'Agent registry. is_test quarantines harness rows from every track-record '
    'calculation: _fake drives scripts/run_fake.py, _test is written to by '
    'tests_live/. Neither is ever scheduled in production.';

-- ---------------------------------------------------------------------------
-- Verify — expect _test present, disabled, flagged.
-- ---------------------------------------------------------------------------

SELECT id, slug, display_name, domain, enabled, is_test
  FROM agents
 ORDER BY is_test, id;
