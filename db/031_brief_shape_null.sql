-- 031_brief_shape_null.sql — db/030's morning-shape CHECK let a brief with
-- no one_move (or no headline) through.
--
-- Found by tests_live/test_brief_sql.py on 2026-10-10: for a body without
-- one_move, jsonb_typeof(body -> 'one_move') is NULL, so `= 'string'` is
-- NULL, the AND chain is NULL, and a CHECK that evaluates to NULL PASSES.
-- Wrapping the morning condition in coalesce(..., false) makes a missing
-- field a refusal. No row is affected: every brief written so far has the
-- full shape (the verify below counts any that would not).
--
-- Prerequisites: db/030.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

ALTER TABLE briefs DROP CONSTRAINT IF EXISTS briefs_morning_shape;
ALTER TABLE briefs ADD CONSTRAINT briefs_morning_shape CHECK (
    kind <> 'morning' OR coalesce(
        brief_date IS NOT NULL
        AND jsonb_typeof(body -> 'headline') = 'string'
        AND jsonb_typeof(body -> 'sections') = 'array'
        AND jsonb_typeof(body -> 'one_move') = 'string'
        AND word_count IS NOT NULL
        AND (fallback OR (model IS NOT NULL AND ai_usage_id IS NOT NULL)),
        false));

INSERT INTO migration_log (name) VALUES ('031_brief_shape_null')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the constraint text contains COALESCE
--   (b) 0 morning briefs that would fail it (the ALTER would have refused
--       otherwise; this states it)
--   (c) the migration_log row
-- ---------------------------------------------------------------------------

SELECT pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid = 'public.briefs'::regclass AND conname = 'briefs_morning_shape';         -- (a)

SELECT count(*) AS bad_rows FROM briefs
 WHERE kind = 'morning' AND NOT coalesce(jsonb_typeof(body -> 'one_move') = 'string', false); -- (b)

SELECT name, applied_at FROM migration_log WHERE name = '031_brief_shape_null';         -- (c)
