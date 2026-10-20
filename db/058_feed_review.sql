-- 058_feed_review.sql — the Oct 19 feed review's two schema changes (owner,
-- 2026-10-20; docs/drafts/feed-review-2026-10.md; branch feed-review-fixes).
--
-- What it changes:
--   1. summary_rejections (APPEND-ONLY, TRUNCATE-guarded): one row for every
--      summary the validator refuses and every story an answer leaves out
--      (feed/summarize.py results_for): the story, the attempt (1 or 2), the
--      reason ("copy: …", "invalid: …", "missing: …"), created_at (stamped).
--      Why: Variety, Deadline and THR ran out of attempts 4-5x as often as
--      other Home sources (Oct 12-19), and the reasons lived only in
--      Railway's log lines, unreadable once the worker was removed.
--      story_id has NO foreign key on purpose: the nightly prune deletes old
--      stories, and an append-only row must not block it or be cascaded away.
--   2. feed_similarity_log.kind gains 'would_join' (review item 3a, in
--      shadow): a near miss with cosine >= 0.92 and two shared proper names
--      (feed/cluster.py would_join) is also logged as would_join. It joins
--      nothing; joined_by stays NULL (the existing CHECK holds).
--
-- A self-test runs on paste (section 4), rolled back.
-- Prerequisites: db/032 (feed), db/054 (arsenal_stamp_created).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- PASTE BEFORE THE MERGE: the summarizer writes summary_rejections and
-- ingest writes 'would_join'.

-- ===================================================================== 1
-- Summary rejections, append-only.

CREATE TABLE IF NOT EXISTS summary_rejections (
    id          BIGSERIAL    PRIMARY KEY,
    story_id    BIGINT       NOT NULL,
    attempt     SMALLINT     NOT NULL CHECK (attempt BETWEEN 1 AND 9),
    reason      TEXT         NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 500),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS summary_rejections_story ON summary_rejections (story_id);
CREATE INDEX IF NOT EXISTS summary_rejections_created ON summary_rejections (created_at);

DROP TRIGGER IF EXISTS summary_rejections_stamp ON summary_rejections;
CREATE TRIGGER summary_rejections_stamp
    BEFORE INSERT ON summary_rejections
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS summary_rejections_immutable ON summary_rejections;
CREATE TRIGGER summary_rejections_immutable
    BEFORE UPDATE OR DELETE ON summary_rejections
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS summary_rejections_no_truncate ON summary_rejections;
CREATE TRIGGER summary_rejections_no_truncate
    BEFORE TRUNCATE ON summary_rejections
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 2
-- The shadow join's kind.

ALTER TABLE feed_similarity_log DROP CONSTRAINT IF EXISTS feed_similarity_log_kind_check;
ALTER TABLE feed_similarity_log ADD CONSTRAINT feed_similarity_log_kind_check
    CHECK (kind IN ('join', 'near_miss', 'cap_overflow', 'would_join'));

-- ===================================================================== 3
-- Access: the owner reads; the worker writes (DATABASE_URL); readonly reads.

ALTER TABLE summary_rejections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON summary_rejections FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE summary_rejections_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON summary_rejections TO authenticated;
DROP POLICY IF EXISTS summary_rejections_owner_read ON summary_rejections;
CREATE POLICY summary_rejections_owner_read ON summary_rejections FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON summary_rejections TO valemont_readonly;

-- ===================================================================== 4
-- Self-test (rolled back).

DO $$
DECLARE
    v_src    bigint;
    v_story  bigint;
    v_item   bigint;
    v_r      bigint;
    v_n      int := 0;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. rejections: stamped, append-only
        INSERT INTO public.summary_rejections (story_id, attempt, reason)
        VALUES (-58, 1, 'copy: copies 8 words from the teaser: ''selftest''')
        RETURNING id INTO v_r;
        IF (SELECT created_at FROM public.summary_rejections WHERE id = v_r) IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: created_at is not the database''s';
        END IF;
        BEGIN
            UPDATE public.summary_rejections SET reason = 'rewritten' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.summary_rejections WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.summary_rejections;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN  -- attempt out of range
            INSERT INTO public.summary_rejections (story_id, attempt, reason) VALUES (-58, 0, 'x');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- a blank reason
            INSERT INTO public.summary_rejections (story_id, attempt, reason) VALUES (-58, 1, '   ');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: summary_rejections refusals (% of 5)', v_n;
        END IF;

        -- ---------------------------------------------------- 2. would_join: logged, never a join
        SELECT id INTO v_src FROM public.sources WHERE slug = '_e2e' AND is_test;
        INSERT INTO public.stories (pillars, title, is_test) VALUES (ARRAY['home'], 'self-test 058', true)
        RETURNING id INTO v_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id, is_test)
        VALUES (v_src, 'selftest:058', 'https://selftest.invalid/058', 'self-test item', now(), v_story, true)
        RETURNING id INTO v_item;
        INSERT INTO public.feed_similarity_log (item_id, story_id, kind, cosine, fuzz, joined_by)
        VALUES (v_item, v_story, 'would_join', 0.93, 64, NULL);
        v_n := 0;
        BEGIN  -- a would_join is never a join
            INSERT INTO public.feed_similarity_log (item_id, story_id, kind, cosine, fuzz, joined_by)
            VALUES (v_item, v_story, 'would_join', 0.93, 64, 'cosine');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- the other kinds are unchanged
            INSERT INTO public.feed_similarity_log (item_id, story_id, kind, cosine, fuzz, joined_by)
            VALUES (v_item, v_story, 'maybe', 0.93, 64, NULL);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: feed_similarity_log kind checks (% of 2)', v_n;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '058 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('058_feed_review')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '058_feed_review';
-- SELECT relrowsecurity FROM pg_class WHERE relname = 'summary_rejections';                         -- true
-- SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.summary_rejections'::regclass AND NOT tgisinternal;
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'feed_similarity_log_kind_check';  -- would_join
-- SELECT count(*) FROM summary_rejections;                                                           -- 0 until the worker writes
