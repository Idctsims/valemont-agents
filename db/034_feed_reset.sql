-- 034_feed_reset.sql — reset the first ingest's mis-clustered stories, add
-- title embeddings for clustering, and new drop rules (Chat 3, Phase 1;
-- owner decisions 2026-10-11).
--
-- WHY THE RESET. The first ingest (run by hand 2026-10-12 00:31:52 UTC)
-- clustered with "cosine >= 0.86 to the story centroid OR fuzz >= 90". On
-- real data that over-merged: 214 stories from 853 items, one of 230 unrelated
-- college-football items. content_items are frozen (db/032), so items cannot
-- be re-assigned; the only clean fix is to delete those stories (their items
-- and similarity-log rows go with them, ON DELETE CASCADE) and the 10 seeded
-- rows, and re-ingest with the approved rule:
--   max title-only cosine to any member >= 0.94, or fuzz >= 90 AND cosine
--   >= 0.90; candidates only within 48 h of a story's first_seen_at; at most
--   25 items per story.
-- No summary had been written (no AI spend) and no reaction exists yet.
--
-- WHAT IS DELETED: only real (is_test = false) rows written before CUTOFF.
-- The counts are checked first; any mismatch raises and NOTHING in this file
-- is applied. All 50 real sources are kept; their fetch state is reset so the
-- re-ingest is a true first fetch (72-hour window, seed-and-skip).
--
-- ALSO:
--   * content_items.title_embedding halfvec(384): what clustering compares.
--     `embedding` (title + teaser) stays for Phase 2. The freeze allows
--     clearing either; the nightly prune clears both after 72 h.
--   * feed_similarity_log.kind gains 'cap_overflow' (an item that matched a
--     story already at 25 items and started a new one).
--   * Drop rules: sportsbook and Kalshi promo/referral posts, yacht-for-sale
--     listings and PGA Tour shot clips. Regulation news is untouched.
--
-- Prerequisites: db/032, db/033.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Counts first: exactly the first ingest, or nothing happens.

DO $$
DECLARE
    cutoff    constant timestamptz := '2026-10-12 01:00:00+00';
    v_stories bigint;
    v_items   bigint;
    v_seeded  bigint;
    v_sources bigint;
    v_after   bigint;
BEGIN
    SELECT count(*) INTO v_stories FROM public.stories WHERE NOT is_test AND first_seen_at < cutoff;
    SELECT count(*) INTO v_items FROM public.content_items WHERE NOT is_test AND NOT seeded AND fetched_at < cutoff;
    SELECT count(*) INTO v_seeded FROM public.content_items WHERE NOT is_test AND seeded AND fetched_at < cutoff;
    SELECT count(*) INTO v_sources FROM public.sources WHERE NOT is_test;
    -- Nothing may have been written after the cutoff either: the worker has
    -- not run the feed yet, so anything newer is unexpected.
    SELECT count(*) INTO v_after FROM public.content_items WHERE NOT is_test AND fetched_at >= cutoff;
    IF (v_stories, v_items, v_seeded, v_sources, v_after) IS DISTINCT FROM (214::bigint, 853::bigint, 10::bigint, 50::bigint, 0::bigint) THEN
        RAISE EXCEPTION 'db/034 refused: expected 214 stories, 853 items, 10 seeded, 50 sources, 0 newer items; found % stories, % items, % seeded, % sources, % newer. Nothing was applied.',
            v_stories, v_items, v_seeded, v_sources, v_after;
    END IF;

    DELETE FROM public.stories WHERE NOT is_test AND first_seen_at < cutoff;
    DELETE FROM public.content_items WHERE NOT is_test AND seeded AND fetched_at < cutoff;

    IF EXISTS (SELECT 1 FROM public.content_items WHERE NOT is_test)
       OR EXISTS (SELECT 1 FROM public.stories WHERE NOT is_test) THEN
        RAISE EXCEPTION 'db/034: real feed rows remain after the delete. Nothing was applied.';
    END IF;

    UPDATE public.sources
       SET etag = NULL, last_modified = NULL, last_fetched_at = NULL, last_ok_at = NULL,
           last_status = NULL, last_error = NULL, consecutive_failures = 0, alerted_at = NULL
     WHERE NOT is_test;

    RAISE NOTICE 'db/034: deleted % stories, % items, % seeded rows; reset % sources',
        v_stories, v_items, v_seeded, v_sources;
END
$$;

-- ===================================================================== 2
-- Title embeddings for clustering.

ALTER TABLE content_items ADD COLUMN IF NOT EXISTS title_embedding extensions.halfvec(384);

CREATE INDEX IF NOT EXISTS content_items_title_embedded ON content_items (fetched_at)
    WHERE title_embedding IS NOT NULL;

-- The freeze (db/032), widened: clearing either embedding is the only change.
CREATE OR REPLACE FUNCTION public.content_items_frozen()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF (NEW.embedding IS NOT NULL AND NEW.embedding IS DISTINCT FROM OLD.embedding)
       OR (NEW.title_embedding IS NOT NULL AND NEW.title_embedding IS DISTINCT FROM OLD.title_embedding)
       OR (to_jsonb(NEW) - 'embedding' - 'title_embedding')
          IS DISTINCT FROM (to_jsonb(OLD) - 'embedding' - 'title_embedding') THEN
        RAISE EXCEPTION 'content_items % is frozen: only clearing its embeddings is allowed', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 3
-- The similarity log learns the cap.

ALTER TABLE feed_similarity_log DROP CONSTRAINT IF EXISTS feed_similarity_log_kind_check;
ALTER TABLE feed_similarity_log ADD CONSTRAINT feed_similarity_log_kind_check
    CHECK (kind IN ('join', 'near_miss', 'cap_overflow'));

-- ===================================================================== 4
-- Drop rules. Promo/referral posts are sportsbook and prediction-market
-- marketing; 'promos' alone would also catch regulation headlines ("Ohio
-- bans sportsbook promos"), so the rules name the marketing shapes.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('title_regex', NULL, 'betting promos',                 'promo (owner, 2026-10-11)'),
    ('title_regex', NULL, 'promos (for|:)',                  'promo (owner, 2026-10-11)'),
    ('title_regex', NULL, 'bonus bets',                      'promo (owner, 2026-10-11)'),
    ('title_regex', NULL, 'welcome offers?',                 'promo (owner, 2026-10-11)'),
    ('title_regex', NULL, 'kalshi (promo|referral|bonus)',   'Kalshi promo/referral (owner, 2026-10-11)'),
    ('title_regex', NULL, 'referral code',                   'promo/referral (owner, 2026-10-11)'),
    -- Boat International's Google News results include yacht-for-sale pages:
    -- "Sunshot yacht (Sunreef Yachts, 23.95m, 2025)". Listings, not news.
    ('title_regex', NULL, ' yacht \([^)]*, [0-9.]+m, [0-9]{4}\)$', 'listing'),
    -- PGA Tour shot clips: "Rod Pampling makes birdie on No. 10 at ...".
    ('title_regex', NULL, 'makes (birdie|eagle|bogey|par) on no\.', 'shot clip')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('034_feed_reset')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) 0 real stories, 0 real items; 50 real sources, every one with
--       last_ok_at, etag and last_modified NULL and 0 failures
--   (b) content_items.title_embedding exists (halfvec)
--   (c) the 8 new rules
--   (d) the migration_log row
-- (The deleted counts, 214 / 853 / 10, appear in the NOTICE when it runs.)
-- ---------------------------------------------------------------------------

SELECT (SELECT count(*) FROM stories WHERE NOT is_test) AS real_stories,
       (SELECT count(*) FROM content_items WHERE NOT is_test) AS real_items,
       (SELECT count(*) FROM sources WHERE NOT is_test) AS real_sources,
       (SELECT count(*) FROM sources WHERE NOT is_test
          AND last_ok_at IS NULL AND etag IS NULL AND last_modified IS NULL
          AND consecutive_failures = 0) AS sources_reset;                                 -- (a)

SELECT column_name, udt_name FROM information_schema.columns
 WHERE table_name = 'content_items' AND column_name = 'title_embedding';                 -- (b)

SELECT id, pattern, note FROM feed_rules
 WHERE note LIKE 'promo%' OR note LIKE 'Kalshi promo%' OR note IN ('listing', 'shot clip')
 ORDER BY id;                                                                             -- (c) 8 rows

SELECT name, applied_at FROM migration_log WHERE name = '034_feed_reset';                 -- (d)
