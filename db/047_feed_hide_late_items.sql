-- 047_feed_hide_late_items.sql — hide the stories stored from months-old
-- items (owner, 2026-10-14).
--
-- The bug (fixed in the worker the same day, feed/ingest.py): a source's
-- first fetch skipped items older than 72 hours WITHOUT recording them, so
-- its next fetch stored them as new. 667 items, from Chat 3 on (341 Home and
-- Sports, 23 sources) and tonight's tech deploy (326, 13 sources; months-old
-- releases on /tech with the Official tag). The worker now records such an
-- item as seen and never shows it, on every fetch.
--
-- This file hides what was already stored. Nothing is deleted:
--   * a LATE item: not seeded, not a first-fetch backfill, and stored more
--     than 72 hours after it was published (96 hours for a date-only item,
--     whose time is 00:00 UTC of its day): the worker's new rule exactly;
--   * a story is hidden when EVERY one of its items is late, it is kept
--     (filtered_reason IS NULL) and real, and nothing anchors it: no
--     reaction of any kind (the owner's rule: keep anything reacted to), no
--     free-credits push, no move proposal;
--   * hidden = filtered_reason 'stale:first_seen_after_72h'. v_feed_stories,
--     feed_for_you and the summarizer's queue skip filtered stories; the row,
--     its items and its scores stay. /settings/sources lists it under
--     "filtered".
-- A story mixing late and fresh items stays visible.
--
-- Note: like every filtered story, a hidden story becomes eligible for the
-- nightly prune 30 days after it was last seen (ledger.FEED_PRUNE_SQL
-- "filtered_stories_deleted"), instead of 90 days as a kept story. Retention
-- is not changed here (owner, 2026-10-14).
--
-- Counted on 2026-10-14 ~04:40 UTC (db_inspect): 579 stories to hide (318
-- Home/Sports, 261 tech), 0 of them reacted to, 20 mixed stories kept.
--
-- This migration depends on production rows: its effect is a data change,
-- and the verify block reports the count. The self-test (section 2) runs on
-- its own rows, rolled back.
--
-- Prerequisites: db/032, db/039, db/042, db/045.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The rule, as a function (kept for the record and for the self-test).

CREATE OR REPLACE FUNCTION public.feed_hide_late_stories()
RETURNS integer
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_n integer;
BEGIN
    UPDATE public.stories st
       SET filtered_reason = 'stale:first_seen_after_72h'
     WHERE NOT st.is_test
       AND st.filtered_reason IS NULL
       AND EXISTS (SELECT 1 FROM public.content_items i WHERE i.story_id = st.id)
       AND NOT EXISTS (
           SELECT 1 FROM public.content_items i
            WHERE i.story_id = st.id
              AND NOT (NOT i.seeded AND NOT i.backfill
                       AND i.fetched_at - i.published_at >
                           CASE WHEN i.published_precision = 'date' THEN interval '96 hours'
                                ELSE interval '72 hours' END))
       AND NOT EXISTS (SELECT 1 FROM public.feed_reactions r WHERE r.story_id = st.id)
       AND NOT EXISTS (SELECT 1 FROM public.tech_credit_pushes p WHERE p.story_id = st.id)
       AND NOT EXISTS (SELECT 1 FROM public.entity_link_proposals p WHERE p.story_id = st.id);
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END;
$$;

REVOKE ALL ON FUNCTION public.feed_hide_late_stories() FROM PUBLIC, anon, authenticated;

-- ===================================================================== 2
-- Self-test, rolled back ('selftest-rollback'). Seven stories from a
-- throwaway source, each with one or two items:
--   A  late (published 100 h before stored)                -> hidden
--   B  fresh (1 h)                                          -> kept
--   C  late, but the owner reacted (an open)                -> kept
--   D  one late item and one fresh                          -> kept
--   E  date-only, 90 h (inside the 96 h date grace)         -> kept
--   F  a first-fetch backfill, 100 h                        -> kept
--   G  late, already filtered by a rule                     -> its reason unchanged

DO $$
DECLARE
    v_owner uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_src   bigint;
    v_story bigint;
    v_item  bigint;
    v_ids   bigint[] := '{}';
    v_n     integer;
    i       int;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-047', 'Self-test 047', ARRAY['home'], 'rss', 'https://selftest.invalid/047')
        RETURNING id INTO v_src;
        FOR i IN 1..7 LOOP
            INSERT INTO public.stories (pillars, title, filtered_reason)
            VALUES (ARRAY['home'], 'self-test 047 #' || i, CASE WHEN i = 7 THEN 'title_regex:1' END)
            RETURNING id INTO v_story;
            v_ids := v_ids || v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, fetched_at,
                                              story_id, backfill, published_precision)
            VALUES (v_src, 'selftest047:' || i, 'https://selftest.invalid/047/' || i, 'self-test 047 #' || i,
                    now() - CASE i WHEN 2 THEN interval '1 hour' WHEN 5 THEN interval '90 hours'
                                   ELSE interval '100 hours' END,
                    now(), v_story, i = 6, CASE WHEN i = 5 THEN 'date' ELSE 'time' END)
            RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
        END LOOP;
        -- D's second, fresh item.
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, fetched_at, story_id)
        VALUES (v_src, 'selftest047:4b', 'https://selftest.invalid/047/4b', 'self-test 047 #4b',
                now() - interval '2 hours', now(), v_ids[4]);
        -- C's reaction.
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_ids[3], 'home', 'open');

        v_n := public.feed_hide_late_stories();
        IF (SELECT array_agg(id ORDER BY id) FROM public.stories
             WHERE id = ANY (v_ids) AND filtered_reason = 'stale:first_seen_after_72h') IS DISTINCT FROM ARRAY[v_ids[1]] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: hidden %, expected only A (%)',
                (SELECT array_agg(id ORDER BY id) FROM public.stories
                  WHERE id = ANY (v_ids) AND filtered_reason = 'stale:first_seen_after_72h'), v_ids[1];
        END IF;
        IF (SELECT filtered_reason FROM public.stories WHERE id = v_ids[7]) <> 'title_regex:1' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an already filtered story changed reason';
        END IF;
        IF EXISTS (SELECT 1 FROM public.v_feed_stories WHERE id = v_ids[1])
           OR NOT EXISTS (SELECT 1 FROM public.v_feed_stories WHERE id = v_ids[2]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_feed_stories still shows A or lost B';
        END IF;
        -- Nothing is deleted.
        IF (SELECT count(*) FROM public.content_items WHERE story_id = ANY (v_ids)) <> 8 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an item was deleted';
        END IF;
        -- Idempotent: a second call hides nothing more among these.
        PERFORM public.feed_hide_late_stories();
        IF (SELECT count(*) FROM public.stories WHERE id = ANY (v_ids)
             AND filtered_reason = 'stale:first_seen_after_72h') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the second call changed the result';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '047 self-test passed (rolled back)';
END
$$;

-- ===================================================================== 3
-- The cleanup itself, once, on production rows.

DO $$
DECLARE
    v_n integer;
BEGIN
    v_n := public.feed_hide_late_stories();
    RAISE NOTICE '047 hid % stories (all items late; none reacted to, pushed or proposed)', v_n;
END
$$;

INSERT INTO migration_log (name) VALUES ('047_feed_hide_late_items')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) hidden stories by group: about 318 Home/Sports and 261 tech (the
--       2026-10-14 ~04:40 UTC count; a few more if late items arrived from
--       the old worker before the fix deployed), 0 reacted to
--   (b) late stories still kept: only the mixed ones (about 20) and any
--       reacted, pushed or proposed ones
--   (c) nothing on /tech For you is a late-only story
--   (d) the migration_log row (and the NOTICEs "047 self-test passed" and
--       "047 hid N stories")
-- ---------------------------------------------------------------------------

SELECT CASE WHEN 'tech' = ANY (st.pillars) THEN 'tech' ELSE 'home/sports' END AS grp, count(*) AS hidden,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM feed_reactions r WHERE r.story_id = st.id)) AS reacted
  FROM stories st WHERE st.filtered_reason = 'stale:first_seen_after_72h' GROUP BY 1 ORDER BY 1;   -- (a)

SELECT count(*) AS late_stories_still_kept
  FROM stories st
 WHERE NOT st.is_test AND st.filtered_reason IS NULL
   AND EXISTS (SELECT 1 FROM content_items i WHERE i.story_id = st.id AND NOT i.seeded AND NOT i.backfill
                 AND i.fetched_at - i.published_at > CASE WHEN i.published_precision = 'date'
                                                          THEN interval '96 hours' ELSE interval '72 hours' END); -- (b)

SELECT count(*) AS late_only_on_tech_for_you
  FROM feed_for_you('tech', 100000, 0) f
 WHERE NOT EXISTS (SELECT 1 FROM content_items i WHERE i.story_id = f.id
                     AND NOT (NOT i.seeded AND NOT i.backfill
                              AND i.fetched_at - i.published_at > CASE WHEN i.published_precision = 'date'
                                                                       THEN interval '96 hours' ELSE interval '72 hours' END)); -- (c)

SELECT name, applied_at FROM migration_log WHERE name = '047_feed_hide_late_items';       -- (d)
