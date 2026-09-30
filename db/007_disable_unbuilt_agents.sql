-- 007_disable_unbuilt_agents.sql — make agents.enabled mean something
--
-- db/002 seeded crypto, equities and prizepicks with enabled = TRUE. Until now
-- nothing read the column, so that was harmless and misleading: the roster
-- said three agents were live while none was. The orchestrator now requires
-- BOTH a Registration in code AND agents.enabled = true before it schedules an
-- agent, so this flag becomes a real gate and has to tell the truth.
--
-- No real agent is enabled yet. Turn one on with its own numbered migration
-- when it is meant to run, not by hand in the Table Editor (CLAUDE.md §5).
--
-- Unaffected: kalshi (already FALSE), _fake (the canary — must stay TRUE or the
-- Railway worker refuses to start), _test (FALSE, db/006).
--
-- Read at worker boot and cached, so a running worker picks this up on its
-- next restart.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

UPDATE agents
   SET enabled = FALSE
 WHERE slug IN ('crypto', 'equities', 'prizepicks');

-- ---------------------------------------------------------------------------
-- Verify — expect every real agent disabled, _fake enabled, _test disabled.
-- ---------------------------------------------------------------------------

SELECT slug, enabled, is_test
  FROM agents
 ORDER BY is_test, slug;
