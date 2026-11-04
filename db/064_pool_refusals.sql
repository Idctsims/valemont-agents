-- 064_pool_refusals.sql — why a pool run refused what it refused (owner,
-- 2026-11-04; docs/arsenal.md §11.8).
--
-- Nov 4's fresh batch (run 120) inserted 15 of 36 references: 6 were
-- near-duplicates and 20 "invalid", and the database could not say which
-- invalid: the reasons were only logged. The cause was the never-repeat cap
-- (3f), which the owner reverted for the pool the same day.
--
-- What it changes:
--   arsenal_pool_runs.refusals: a 'collected' run's refusals, by category and
--   reason: {"finance_business": {"duplicate_key": 4, "near_dup": 1},
--   "words": {"duplicate_key": 1}, "unknown": {"invalid_field": 2}}.
--   Reasons: duplicate_key (the key or lemma exists: a repeat, or the UNIQUE
--   conflict at insert), near_dup (the embedding check), invalid_field (a bad
--   key, category, room, kind or length), other. Categories are the seven,
--   "words", or "unknown" (no valid category). NULL on every other row, and
--   on runs before this file. arsenal_pool_runs stays append-only.
--
-- It creates no function or trigger; a rolled-back self-test still checks the
-- CHECK both ways (db/061's pattern), so a wrong CHECK fails the paste.
-- Prerequisites: db/054, db/061.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- PASTE BEFORE THE MERGE: the worker writes the column on its next collect.

ALTER TABLE arsenal_pool_runs ADD COLUMN IF NOT EXISTS refusals JSONB;
ALTER TABLE arsenal_pool_runs DROP CONSTRAINT IF EXISTS arsenal_pool_runs_refusals;
ALTER TABLE arsenal_pool_runs ADD CONSTRAINT arsenal_pool_runs_refusals CHECK (
    refusals IS NULL
    OR (status = 'collected'
        AND jsonb_typeof(refusals) = 'object'
        -- every key is a category, "words" or "unknown"; every value an object
        AND NOT jsonb_path_exists(refusals, '$.keyvalue() ? (@.value.type() != "object")')
        AND NOT jsonb_path_exists(refusals, '$.keyvalue() ? (!(@.key like_regex "^(finance_business|history_military|film_tv|sports|music_culture|philosophy|literature_myth|words|unknown)$"))')
        -- every reason is one of the four, every count a number >= 0
        AND NOT jsonb_path_exists(refusals, '$.*.keyvalue() ? (!(@.key like_regex "^(duplicate_key|near_dup|invalid_field|other)$"))')
        AND NOT jsonb_path_exists(refusals, '$.*.* ? (@.type() != "number" || @ < 0)')));
COMMENT ON COLUMN arsenal_pool_runs.refusals IS
    'db/064: a collected run''s refusals by category ("words", "unknown") and reason (duplicate_key, near_dup, invalid_field, other); NULL otherwise and before 2026-11-04.';

INSERT INTO migration_log (name) VALUES ('064_pool_refusals')
ON CONFLICT (name) DO NOTHING;

-- Self-test, rolled back ('selftest-rollback').
DO $$
DECLARE
    v_s  bigint;
    v_c  bigint;
    v_n  int := 0;
BEGIN
    BEGIN
        INSERT INTO public.arsenal_pool_runs (kind, status, model, refs_requested, words_requested, is_test)
        VALUES ('topup', 'submitted', 'claude-sonnet-5-5', 6, 2, true)
        RETURNING id INTO v_s;
        INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
        VALUES ('topup', 'collected', v_s,
                '{"finance_business": {"duplicate_key": 4, "near_dup": 1}, "words": {"duplicate_key": 1}, "unknown": {"invalid_field": 2}}'::jsonb,
                true)
        RETURNING id INTO v_c;
        -- Read back in its own statement (db/062's rule).
        IF (SELECT refusals -> 'finance_business' ->> 'duplicate_key' FROM public.arsenal_pool_runs WHERE id = v_c)
           IS DISTINCT FROM '4' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: refusals not stored';
        END IF;
        BEGIN  -- on a submitted row
            INSERT INTO public.arsenal_pool_runs (kind, status, model, refusals, is_test)
            VALUES ('topup', 'submitted', 'claude-sonnet-5-5', '{"sports": {"other": 1}}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- an unknown category
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
            VALUES ('topup', 'collected', v_s, '{"cooking": {"other": 1}}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- an unknown reason
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
            VALUES ('topup', 'collected', v_s, '{"sports": {"bored": 1}}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a negative count
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
            VALUES ('topup', 'collected', v_s, '{"sports": {"near_dup": -1}}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a count that is not a number
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
            VALUES ('topup', 'collected', v_s, '{"sports": {"near_dup": "two"}}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a category whose value is not an object
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, refusals, is_test)
            VALUES ('topup', 'collected', v_s, '{"sports": 3}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- still append-only
            UPDATE public.arsenal_pool_runs SET refusals = NULL WHERE id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 7 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 7 refusals', v_n;
        END IF;
        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '064 self-test passed (rolled back)';
END
$$;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '064_pool_refusals';
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'arsenal_pool_runs_refusals';
-- SELECT has_column_privilege('valemont_readonly', 'public.arsenal_pool_runs', 'refusals', 'SELECT');   -- t
-- SELECT count(*) FROM arsenal_pool_runs WHERE refusals IS NOT NULL;                                    -- 0 until a collect
