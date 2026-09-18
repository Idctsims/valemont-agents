-- 002_test_flag_and_roster.sql — quarantine test data, extend the roster
--
-- Two changes that have to land together:
--
-- 1. `agents.is_test`. Commitments and events are append-only by trigger, so
--    a throwaway agent's rows can never be deleted. Without a flag, the first
--    end-to-end test permanently contaminates the track record. The flag is
--    the quarantine: rows still exist and still prove the pipeline works, but
--    anything that computes a score filters them out.
--
-- 2. Two new agents. `_fake` is the harness for the core loop (is_test).
--    `kalshi` is a real fourth agent — CFTC-regulated event contracts,
--    paper/shadow tracking only, no orders, same as everything else here.
--    It is seeded DISABLED because its adapter does not exist yet.
--
-- Re-running this file is safe: the ALTER is IF NOT EXISTS and the INSERT is
-- ON CONFLICT DO NOTHING.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. The editor
-- supplies its own transaction, so there is no BEGIN/COMMIT here.

-- ---------------------------------------------------------------------------
-- 1. The test flag
-- ---------------------------------------------------------------------------

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN agents.is_test IS
    'Rows from this agent are excluded from every track-record calculation. '
    'Set for harness agents only. The append-only triggers mean test rows '
    'cannot be deleted, so they are quarantined instead.';

-- The three seeded agents are real. Explicit rather than relying on the
-- DEFAULT, so re-running this file after someone flips a flag by hand
-- restores the intended state.
UPDATE agents SET is_test = false
 WHERE slug IN ('crypto', 'equities', 'prizepicks');

-- ---------------------------------------------------------------------------
-- 2. Roster additions
-- ---------------------------------------------------------------------------

INSERT INTO agents (slug, display_name, domain, enabled, is_test) VALUES
    -- Harness for the core loop. Invents commitments that resolve 60s out so
    -- observe -> commit -> wait -> resolve -> score can be watched end to end
    -- against the real schema. Delete the adapter when it stops earning its
    -- keep; the row stays, because its rows can't be removed anyway.
    ('_fake',  'Fake Agent',     'test',   TRUE,  TRUE),

    -- Fourth real agent. Event contracts have an entry price like a paper
    -- position AND a binary settlement like a prop, which is why they were
    -- the design test for core/. Disabled until adapters/kalshi.py exists.
    ('kalshi', 'Event Contracts', 'events', FALSE, FALSE)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — the output of this SELECT is what you should see after Run.
-- Expect 5 rows: three real and enabled, kalshi real and disabled,
-- _fake enabled and flagged test.
-- ---------------------------------------------------------------------------

SELECT id, slug, display_name, domain, enabled, is_test
  FROM agents
 ORDER BY is_test, id;
