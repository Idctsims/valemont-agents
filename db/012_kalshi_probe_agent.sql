-- 012_kalshi_probe_agent.sql — a test agent that exercises Kalshi plumbing
--
-- The nfl_ml holdout passed with zero commitments, so settlement and close
-- capture have never run against a real Kalshi market. `_kalshi_probe`
-- commits one contract per NFL week, regardless of edge, purely to make them
-- run (adapters/_kalshi_probe.py).
--
-- is_test = TRUE: TRACK_RECORD_FILTER excludes every row it writes from every
-- track-record calculation, exactly like _fake and _test. It must never appear
-- in a performance view.
--
-- enabled = FALSE: it is registered in code but scheduled only once this flag
-- is turned on, by a later migration, deliberately.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

INSERT INTO agents (slug, display_name, domain, enabled, is_test) VALUES
    ('_kalshi_probe', 'Kalshi Plumbing Probe', 'kalshi', FALSE, TRUE)
ON CONFLICT (slug) DO NOTHING;

-- Re-assert the quarantine even if a row with this slug already existed.
UPDATE agents SET is_test = TRUE WHERE slug = '_kalshi_probe';

-- ---------------------------------------------------------------------------
-- Verify — expect _kalshi_probe present, disabled, is_test.
-- ---------------------------------------------------------------------------

SELECT id, slug, enabled, is_test FROM agents ORDER BY is_test, id;
