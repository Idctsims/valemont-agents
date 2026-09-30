-- 009_kalshi_nfl_agents.sql — roster rows for the Kalshi NFL pillar
--
-- CLAUDE.md §8 (owner decision, 2026-09-30): the Kalshi pillar commits into
-- moneylines, spreads and player props as SEPARATE agents with separate track
-- records. Separate records need separate `agents` rows; one shared row would
-- blend them in every query (§9.2).
--
-- All start DISABLED. The orchestrator schedules an agent only with BOTH a
-- Registration in code AND agents.enabled = true (core/orchestrator.py). Each
-- is enabled by its own later migration, once its pre-registered holdout
-- (docs/preregistration_nfl.md §5) has passed.
--
-- `kalshi_collector` is the candlestick archive job. It commits nothing and is
-- never scheduled by the orchestrator; the row exists so its runs and events
-- (health, §8) are attributed to something.
--
-- The original `kalshi` row from db/002 is left alone: rows are permanent, and
-- nothing has ever been written as it. It is superseded, not deleted.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

INSERT INTO agents (slug, display_name, domain, enabled, is_test) VALUES
    ('nfl_ml',           'NFL Moneyline (Kalshi)',     'kalshi', FALSE, FALSE),
    ('nfl_spread',       'NFL Spread (Kalshi)',        'kalshi', FALSE, FALSE),
    ('nfl_props',        'NFL Player Props (Kalshi)',  'kalshi', FALSE, FALSE),
    ('kalshi_collector', 'Kalshi Candle Collector',    'kalshi', FALSE, FALSE)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect the four new rows, all disabled, none is_test.
-- ---------------------------------------------------------------------------

SELECT id, slug, display_name, domain, enabled, is_test
  FROM agents
 ORDER BY id;
