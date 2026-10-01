-- 013_enable_production_roster.sql — turn on the approved Railway roster
--
-- PASTE ONLY WITH THE OWNER'S GO, together with deploying the prod-roster
-- branch and setting ROSTER=production (and removing CANARY) on Railway.
--
-- Prerequisites: db/009 (nfl_ml row) and db/012 (_kalshi_probe row) pasted.
--
--   _kalshi_probe  is_test = true; one real contract per NFL week, purely to
--                  exercise settlement and close capture.
--   nfl_ml         forward live-pipeline test; expected to commit ~never (A3).
--
-- No props strategy is enabled here: none has passed its holdout. Each is
-- enabled by its own migration if and when it does.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

UPDATE agents SET enabled = TRUE WHERE slug IN ('_kalshi_probe', 'nfl_ml');

SELECT slug, enabled, is_test FROM agents ORDER BY is_test, slug;
