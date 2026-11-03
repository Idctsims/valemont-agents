-- 061_prune_runs_pool_quotas.sql — two records the owner asked for on
-- 2026-11-03, after the morning readings could not answer them from the
-- database (branch worker-credit-prune-quotas).
--
-- What it changes:
--   1. feed_prune_runs (APPEND-ONLY, TRUNCATE-guarded): one row per nightly
--      feed_prune (core/ledger.py feed_prune), written in the prune's own
--      transaction:
--        deleted             table -> rows deleted, cascades included
--                            (stories, content_items, story_scores,
--                            feed_similarity_log, interest_profiles);
--        steps               step -> {table: rows}, as each step counted it;
--        held                story rule -> the protected stories its age test
--                            took and the prune kept: {"stories": distinct,
--                            "reactions", "move_proposals", "credit_pushes",
--                            "repo_surfacings", "trailers"};
--        embeddings_cleared  items whose embeddings were cleared (not deleted).
--      Why: Nov 3's first 21-day prune could be read only from a Railway log
--      line, and the CLI was logged out.
--   2. arsenal_pool_runs.quotas: a 'submitted' run's target references per
--      category ({"music_culture": 8, ...}; arsenal/pool.py submit and
--      topup). NULL on every other row, and on runs before this file.
--      Why: Nov 3's first automatic refill recorded what was inserted, not
--      what it asked for.
--
-- No new function. The triggers reuse arsenal_stamp_created (db/054),
-- reject_mutation and reject_truncate. A self-test runs on paste (section 4),
-- rolled back.
-- Prerequisites: db/032 (feed), db/054 (arsenal_pool_runs, arsenal_stamp_created).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- PASTE BEFORE THE MERGE: the worker writes both from its first prune / batch.

-- ===================================================================== 1
-- The prune's record, append-only.

CREATE TABLE IF NOT EXISTS feed_prune_runs (
    id                  BIGSERIAL    PRIMARY KEY,
    deleted             JSONB        NOT NULL CHECK (
                            jsonb_typeof(deleted) = 'object'
                            AND NOT jsonb_path_exists(deleted, '$.* ? (@.type() != "number" || @ < 0)')),
    steps               JSONB        NOT NULL CHECK (jsonb_typeof(steps) = 'object'),
    held                JSONB        NOT NULL CHECK (jsonb_typeof(held) = 'object'),
    embeddings_cleared  INTEGER      NOT NULL DEFAULT 0 CHECK (embeddings_cleared >= 0),
    is_test             BOOLEAN      NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feed_prune_runs_created ON feed_prune_runs (created_at);

DROP TRIGGER IF EXISTS feed_prune_runs_stamp ON feed_prune_runs;
CREATE TRIGGER feed_prune_runs_stamp
    BEFORE INSERT ON feed_prune_runs
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS feed_prune_runs_immutable ON feed_prune_runs;
CREATE TRIGGER feed_prune_runs_immutable
    BEFORE UPDATE OR DELETE ON feed_prune_runs
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS feed_prune_runs_no_truncate ON feed_prune_runs;
CREATE TRIGGER feed_prune_runs_no_truncate
    BEFORE TRUNCATE ON feed_prune_runs
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 2
-- The pool run's target quotas.

ALTER TABLE arsenal_pool_runs ADD COLUMN IF NOT EXISTS quotas JSONB;
ALTER TABLE arsenal_pool_runs DROP CONSTRAINT IF EXISTS arsenal_pool_runs_quotas;
ALTER TABLE arsenal_pool_runs ADD CONSTRAINT arsenal_pool_runs_quotas CHECK (
    quotas IS NULL
    OR (status = 'submitted'
        AND jsonb_typeof(quotas) = 'object'
        AND NOT jsonb_path_exists(quotas, '$.* ? (@.type() != "number" || @ < 0)')));
COMMENT ON COLUMN arsenal_pool_runs.quotas IS
    'db/061: a submitted run''s target references per category; NULL otherwise and before 2026-11-03.';

-- ===================================================================== 3
-- Access: the owner reads; the worker writes (DATABASE_URL); readonly reads.
-- arsenal_pool_runs keeps db/054's grants (its new column with them).

ALTER TABLE feed_prune_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON feed_prune_runs FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE feed_prune_runs_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON feed_prune_runs TO authenticated;
DROP POLICY IF EXISTS feed_prune_runs_owner_read ON feed_prune_runs;
CREATE POLICY feed_prune_runs_owner_read ON feed_prune_runs FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON feed_prune_runs TO valemont_readonly;

-- ===================================================================== 4
-- Self-test (rolled back).

DO $$
DECLARE
    v_r   bigint;
    v_q   bigint;
    v_n   int := 0;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. prune runs: stamped, append-only
        INSERT INTO public.feed_prune_runs (deleted, steps, held, embeddings_cleared, is_test)
        VALUES ('{"stories": 2, "content_items": 3}'::jsonb,
                '{"unreacted_stories_deleted": {"stories": 2, "content_items": 3}}'::jsonb,
                '{"unreacted_stories_deleted": {"stories": 1, "reactions": 1}}'::jsonb, 4, true)
        RETURNING id INTO v_r;
        IF (SELECT created_at FROM public.feed_prune_runs WHERE id = v_r) IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: created_at is not the database''s';
        END IF;
        BEGIN
            UPDATE public.feed_prune_runs SET embeddings_cleared = 0 WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.feed_prune_runs WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.feed_prune_runs;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN  -- a negative count
            INSERT INTO public.feed_prune_runs (deleted, steps, held, is_test)
            VALUES ('{"stories": -1}'::jsonb, '{}'::jsonb, '{}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- not an object
            INSERT INTO public.feed_prune_runs (deleted, steps, held, is_test)
            VALUES ('[1]'::jsonb, '{}'::jsonb, '{}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: feed_prune_runs refusals (% of 5)', v_n;
        END IF;

        -- ---------------------------------------------------- 2. quotas: on 'submitted' only
        INSERT INTO public.arsenal_pool_runs (kind, status, model, refs_requested, words_requested, quotas, is_test)
        VALUES ('topup', 'submitted', 'claude-sonnet-5-5', 6, 2, '{"sports": 2, "philosophy": 4}'::jsonb, true)
        RETURNING id INTO v_q;
        IF (SELECT quotas ->> 'philosophy' FROM public.arsenal_pool_runs WHERE id = v_q) IS DISTINCT FROM '4' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: quotas not stored';
        END IF;
        v_n := 0;
        BEGIN  -- a quota on a later row
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, quotas, is_test)
            VALUES ('topup', 'collected', v_q, '{"sports": 2}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a negative quota
            INSERT INTO public.arsenal_pool_runs (kind, status, model, quotas, is_test)
            VALUES ('topup', 'submitted', 'claude-sonnet-5-5', '{"sports": -2}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a quota that is not a number
            INSERT INTO public.arsenal_pool_runs (kind, status, model, quotas, is_test)
            VALUES ('topup', 'submitted', 'claude-sonnet-5-5', '{"sports": "two"}'::jsonb, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- still append-only
            UPDATE public.arsenal_pool_runs SET quotas = NULL WHERE id = v_q;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: arsenal_pool_runs quota refusals (% of 4)', v_n;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '061 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('061_prune_runs_pool_quotas')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '061_prune_runs_pool_quotas';
-- SELECT relrowsecurity FROM pg_class WHERE relname = 'feed_prune_runs';                            -- true
-- SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.feed_prune_runs'::regclass AND NOT tgisinternal;  -- 3
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'arsenal_pool_runs_quotas';
-- SELECT count(*) FROM feed_prune_runs;                                    -- 0 until the 03:15 Chicago prune
