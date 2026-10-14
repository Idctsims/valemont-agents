-- 048_feed_dates.sql — undated items, date-only cards, and the backfill
-- window (Chat 4 Phase 1; owner, 2026-10-14).
--
-- Three owner decisions:
--   1. An UNDATED item (the source gave no date) is stored with
--      published_precision 'none' by the worker (feed/ingest.py), its
--      published_at its first-seen time as before. It still shows on Home
--      and Sports as today, but it NEVER pushes (tech_push_eligible requires
--      a stated date) and never counts as a latency sample
--      (scripts/tech_latency.py). Before this, an RSS item that dropped its
--      date on a later fetch looked fresh to every rule (0 such rows on
--      2026-10-14; the gap was in the code).
--   2. A date-only card prints its stated UTC date: v_feed_stories and
--      feed_for_you carry the lead item's published_precision for the web.
--   3. The For you window counts from first seen, except for a first-fetch
--      BACKFILL item, which counts from when it was published (a date-only
--      one from the end of its stated day, the same one-day grace as the
--      72 h rule). Counted on 2026-10-14 ~06:10 UTC: see the verify block.
--
-- feed_for_you gains one output column (published_precision, 28 in all), so
-- it is dropped and re-created with the same 6 arguments; ctx_feed,
-- ctx_sports_feed and ctx_tech call it by name. Everything else in it is
-- db/046's, unchanged (the official 72 h half-life included).
--
-- A self-test runs on paste (section 5), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/045, db/046, db/047.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- published_precision gains 'none'.

ALTER TABLE content_items DROP CONSTRAINT IF EXISTS content_items_published_precision;
ALTER TABLE content_items ADD CONSTRAINT content_items_published_precision
    CHECK (published_precision IN ('time', 'date', 'none'));

-- ===================================================================== 2
-- An undated item never pushes (db/046's rule, plus a stated date).

CREATE OR REPLACE FUNCTION public.tech_push_eligible(p_story bigint)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT coalesce((
        SELECT NOT i.backfill AND i.published_precision <> 'none'
               AND i.fetched_at - i.published_at <= interval '24 hours'
          FROM public.stories st
          JOIN public.content_items i ON i.id = st.lead_item_id
         WHERE st.id = p_story), false);
$$;

-- ===================================================================== 3
-- v_feed_stories gains the lead item's precision and backfill flag
-- (appended, so CREATE OR REPLACE keeps every column).

CREATE OR REPLACE VIEW v_feed_stories
WITH (security_invoker = true)
AS
SELECT st.id, st.pillars, st.sport, st.title, st.summary, st.entities, st.topics,
       st.item_count, st.first_seen_at, st.last_seen_at,
       i.id AS lead_item_id, i.url AS lead_url, i.published_at AS lead_published_at,
       s.slug AS source_slug, s.name AS source_name,
       st.why_it_matters,
       st.category,
       s.official AS source_official,
       i.published_precision AS lead_published_precision,
       i.backfill AS lead_backfill
  FROM public.stories st
  JOIN public.content_items i ON i.id = st.lead_item_id
  JOIN public.sources s ON s.id = i.source_id
 WHERE NOT st.is_test
   AND st.filtered_reason IS NULL
   AND i.url IS NOT NULL;

-- ===================================================================== 4
-- feed_for_you: db/046's, plus published_precision out and the backfill
-- window.

DROP FUNCTION IF EXISTS public.feed_for_you(text, integer, integer, text, text, integer);

CREATE OR REPLACE FUNCTION public.feed_for_you(p_pillar text, p_limit integer DEFAULT 30,
                                               p_offset integer DEFAULT 0, p_sport text DEFAULT NULL,
                                               p_category text DEFAULT NULL,
                                               p_cross_cap integer DEFAULT NULL)
RETURNS TABLE (
    id bigint, pos bigint, rank double precision, score real, follow_tier smallint,
    floored boolean, cross_pillar boolean, components jsonb, reaction text,
    pillars text[], sport text, title text, summary text, why_it_matters text,
    entities text[], topics text[], item_count integer, first_seen_at timestamptz,
    last_seen_at timestamptz, lead_item_id bigint, lead_url text,
    lead_published_at timestamptz, source_slug text, source_name text,
    boost_hours real, category text, official boolean, published_precision text
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH k AS (
        SELECT 18.0::float8 AS half_life_h,   -- rank.HALF_LIFE_H
               72           AS window_h,      -- rank.WINDOW_H
               168          AS tech_window_h, -- rank.TECH_WINDOW_H
               72.0::float8 AS tech_official_half_life_h, -- rank.TECH_OFFICIAL_HALF_LIFE_H
               3            AS floor_max,     -- rank.FLOOR_MAX
               6            AS floor_position,-- rank.FLOOR_POSITION
               48.0::float8 AS floor_max_age_h, -- rank.FLOOR_MAX_AGE_H
               5            AS cross_cap,     -- rank.CROSS_CAP
               30           AS page_size,     -- rank.PAGE_SIZE
               6            AS entity_cap,    -- rank.ENTITY_CAP
               9            AS entity_cap_boosted -- rank.ENTITY_CAP_BOOSTED
    ),
    win AS (
        SELECT w.sport AS w_sport, w.entity_id AS w_entity, e.name AS w_name, w.boost_hours AS w_hours
          FROM public.boost_windows w
          LEFT JOIN public.entities e ON e.id = w.entity_id
         WHERE w.starts_at <= now() AND now() < w.ends_at
    ),
    t1 AS (
        SELECT DISTINCT e.name AS t_name
          FROM public.follows f
          JOIN public.entities e ON e.id = f.entity_id
         WHERE f.tier = 1 AND (f.pillar IS NULL OR f.pillar = p_pillar)
    ),
    cand AS (
        SELECT v.id AS sid, v.pillars AS c_pillars, v.sport AS c_sport, v.title AS c_title,
               v.summary AS c_summary, v.why_it_matters AS c_why, v.entities AS c_entities,
               v.topics AS c_topics, v.item_count AS c_item_count, v.first_seen_at AS c_first,
               v.last_seen_at AS c_last, v.lead_item_id AS c_lead, v.lead_url AS c_url,
               v.lead_published_at AS c_pub, v.source_slug AS c_slug, v.source_name AS c_source,
               v.category AS c_category, v.source_official AS c_official,
               v.lead_published_precision AS c_precision,
               -- A tech story with no score yet ranks at 0 (listed, not hidden).
               coalesce(sc.score, 0)::real AS c_score, sc.follow_tier AS c_tier,
               coalesce(sc.floor, false) AS c_floor,
               coalesce(sc.cross_pillar, false) AS c_cross,
               coalesce(sc.components, '{}'::jsonb) AS c_components,
               NULLIF(r.reaction, 'undo') AS c_reaction,
               greatest(extract(epoch FROM (now() - v.lead_published_at)) / 3600.0, 0) AS age_h,
               coalesce((SELECT max(w.w_hours) FROM win w
                          WHERE w.w_sport = v.sport
                            AND ((w.w_entity IS NULL AND p_pillar = 'sports')
                                 OR (w.w_entity IS NOT NULL
                                     AND coalesce(sc.components -> 'follows', '[]'::jsonb) ? w.w_name))),
                        0)::real AS boost_h
          FROM public.v_feed_stories v
          LEFT JOIN public.v_story_scores_latest sc ON sc.story_id = v.id AND sc.pillar = p_pillar
          LEFT JOIN public.v_feed_reaction_latest r ON r.story_id = v.id AND r.pillar = p_pillar
         CROSS JOIN k
         -- The window counts from first seen; a first-fetch BACKFILL item counts
         -- from when it was published (db/048; a date-only one from the end of
         -- its stated day), so a months-old release backfilled at deploy does
         -- not sit on page one for a week.
         WHERE (CASE WHEN v.lead_backfill
                     THEN v.lead_published_at + CASE WHEN v.lead_published_precision = 'date'
                                                     THEN interval '24 hours' ELSE interval '0' END
                     ELSE v.first_seen_at END)
               > now() - make_interval(
                   hours => CASE WHEN p_pillar = 'tech' THEN k.tech_window_h ELSE k.window_h END)
           -- Home and Sports list scored stories only; Tech also its unscored ones.
           AND (sc.story_id IS NOT NULL OR (p_pillar = 'tech' AND 'tech' = ANY (v.pillars)))
           -- The pillar's own stories, plus tier-1 cross-ins between Home and
           -- Sports only: never into Tech, never out of it. A cap of 0 leaves
           -- cross-ins out entirely (/news).
           AND (p_pillar = ANY (v.pillars)
                OR (sc.cross_pillar AND coalesce(p_cross_cap, k.cross_cap) > 0
                    AND p_pillar <> 'tech' AND NOT ('tech' = ANY (v.pillars))))
           AND coalesce(r.reaction, '') NOT IN ('neutral', 'not_interested')
           AND (p_sport IS NULL OR v.sport = p_sport)
           AND (p_category IS NULL OR v.category = p_category)
    ),
    base AS (
        SELECT c.*,
               -- An official tech story decays at its own, slower half-life
               -- (db/046); every other story, and every boost, at 18 h.
               c.c_score + ln(2.0) * c.boost_h::float8 / k.half_life_h
                         - ln(2.0) * c.age_h / CASE WHEN p_pillar = 'tech' AND c.c_official
                                                    THEN k.tech_official_half_life_h
                                                    ELSE k.half_life_h END AS r
          FROM cand c CROSS JOIN k
    ),
    f AS (
        SELECT b.sid FROM base b CROSS JOIN k
         WHERE b.c_floor AND b.age_h < k.floor_max_age_h
         ORDER BY b.r DESC, b.sid DESC
         LIMIT (SELECT floor_max FROM k)
    ),
    ref AS (
        SELECT b.r FROM base b
         WHERE NOT b.c_floor
         ORDER BY b.r DESC, b.sid DESC
         OFFSET (SELECT floor_position - 1 FROM k) LIMIT 1
    ),
    eff AS (
        SELECT b.*,
               (b.sid IN (SELECT sid FROM f)) AS in_f,
               (b.sid IN (SELECT sid FROM f) AND b.r < (SELECT r FROM ref)) AS lifted,
               CASE WHEN b.sid IN (SELECT sid FROM f) AND b.r < (SELECT r FROM ref)
                    THEN (SELECT r FROM ref) + 1e-6 / (1.0 + ((SELECT r FROM ref) - b.r))
                    ELSE b.r END AS e
          FROM base b
    ),
    ent AS (
        SELECT x.sid, t.t_name,
               row_number() OVER (PARTITION BY t.t_name ORDER BY x.in_f DESC, x.e DESC, x.sid DESC) AS n
          FROM eff x
          JOIN t1 t ON coalesce(x.c_components -> 'follows', '[]'::jsonb) ? t.t_name
         WHERE p_pillar = 'sports'
    ),
    over_cap AS (
        SELECT DISTINCT en.sid
          FROM ent en CROSS JOIN k
         WHERE en.n > CASE WHEN EXISTS (SELECT 1 FROM win w WHERE w.w_name = en.t_name)
                           THEN k.entity_cap_boosted ELSE k.entity_cap END
    ),
    allowed AS (
        SELECT e.sid FROM eff e
         WHERE e.c_cross
         ORDER BY e.in_f DESC, e.e DESC, e.sid DESC
         LIMIT (SELECT coalesce(p_cross_cap, cross_cap) FROM k)
    ),
    page1 AS (
        SELECT e.sid FROM eff e
         WHERE (NOT e.c_cross OR e.sid IN (SELECT sid FROM allowed))
           AND e.sid NOT IN (SELECT sid FROM over_cap)
         ORDER BY e.e DESC, e.sid DESC
         LIMIT (SELECT page_size FROM k)
    ),
    ordered AS (
        SELECT e.*, row_number() OVER (ORDER BY (e.sid IN (SELECT sid FROM page1)) DESC,
                                                e.e DESC, e.sid DESC) AS n
          FROM eff e
    )
    SELECT o.sid, o.n, o.e, o.c_score, o.c_tier, o.lifted, o.c_cross, o.c_components, o.c_reaction,
           o.c_pillars, o.c_sport, o.c_title, o.c_summary, o.c_why, o.c_entities, o.c_topics,
           o.c_item_count, o.c_first, o.c_last, o.c_lead, o.c_url, o.c_pub, o.c_slug, o.c_source,
           o.boost_h, o.c_category, o.c_official, o.c_precision
      FROM ordered o
     ORDER BY o.n
     LIMIT p_limit OFFSET p_offset;
$$;

REVOKE ALL ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer)
    TO authenticated, valemont_readonly;
-- Replaced above, grants kept; restated so the file says what it expects.
GRANT SELECT ON v_feed_stories TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.tech_push_eligible(bigint) TO authenticated, valemont_readonly;

-- ===================================================================== 5
-- Self-test, rolled back ('selftest-rollback'). Tech stories from a
-- throwaway official source, each with one item, all first seen NOW:
--   A  backfill, published 10 days ago (time)            -> out of For you
--   B  backfill, published 2 days ago (time)             -> in
--   C  backfill, date-only, stated day 7 days ago        -> in (the day's grace)
--   D  backfill, date-only, stated day 8 days ago        -> out
--   E  not backfill, published 1 h ago                   -> in, push-eligible
--   F  not backfill, UNDATED ('none'), published = now   -> in, NOT push-eligible
-- and a Home story whose undated item still shows on Home.

DO $$
DECLARE
    v_src   bigint;
    v_hsrc  bigint;
    v_story bigint;
    v_item  bigint;
    v_ids   bigint[] := '{}';
    v_home  bigint;
    v_in    bigint[];
    v_ok    boolean;
    i       int;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url, official)
        VALUES ('selftest-048', 'Self-test 048', ARRAY['tech'], 'rss', 'https://selftest.invalid/048', true)
        RETURNING id INTO v_src;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-048-h', 'Self-test 048 home', ARRAY['home'], 'rss', 'https://selftest.invalid/048h')
        RETURNING id INTO v_hsrc;
        FOR i IN 1..6 LOOP
            INSERT INTO public.stories (pillars, title, category)
            VALUES (ARRAY['tech'], 'self-test 048 #' || i, 'tooling') RETURNING id INTO v_story;
            v_ids := v_ids || v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, fetched_at,
                                              story_id, backfill, published_precision)
            VALUES (v_src, 'selftest048:' || i, 'https://selftest.invalid/048/' || i, 'self-test 048 #' || i,
                    CASE i WHEN 1 THEN now() - interval '10 days'
                           WHEN 2 THEN now() - interval '2 days'
                           WHEN 3 THEN date_trunc('day', now() - interval '7 days')
                           WHEN 4 THEN date_trunc('day', now() - interval '8 days')
                           WHEN 5 THEN now() - interval '1 hour'
                           ELSE now() END,
                    now(), v_story, i <= 4,
                    CASE WHEN i IN (3, 4) THEN 'date' WHEN i = 6 THEN 'none' ELSE 'time' END)
            RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
        END LOOP;
        INSERT INTO public.stories (pillars, title) VALUES (ARRAY['home'], 'self-test 048 home') RETURNING id INTO v_home;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, fetched_at,
                                          story_id, published_precision)
        VALUES (v_hsrc, 'selftest048:h', 'https://selftest.invalid/048/h', 'self-test 048 home', now(), now(),
                v_home, 'none')
        RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_home;
        INSERT INTO public.story_scores (story_id, pillar, score, components) VALUES (v_home, 'home', 40, '{}');

        -- 3. The backfill window.
        SELECT array_agg(f.id ORDER BY f.id) INTO v_in
          FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = ANY (v_ids);
        IF v_in IS DISTINCT FROM ARRAY[v_ids[2], v_ids[3], v_ids[5], v_ids[6]] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: tech For you has % of %, expected B C E F', v_in, v_ids;
        END IF;
        -- 1. Undated: shows, never pushes; still on Home.
        IF public.tech_push_eligible(v_ids[6]) OR NOT public.tech_push_eligible(v_ids[5]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: tech_push_eligible (undated %, fresh %)',
                public.tech_push_eligible(v_ids[6]), public.tech_push_eligible(v_ids[5]);
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_ids[6]);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%not push-eligible%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an undated story was pushed'; END IF;
        IF v_home NOT IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0) f) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an undated Home story left Home';
        END IF;
        -- 2. The precision reaches the web.
        IF (SELECT f.published_precision FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_ids[3]) <> 'date'
           OR (SELECT v.lead_published_precision FROM public.v_feed_stories v WHERE v.id = v_ids[6]) <> 'none'
           OR (SELECT v.lead_backfill FROM public.v_feed_stories v WHERE v.id = v_ids[1]) IS NOT TRUE THEN
            RAISE EXCEPTION 'SELFTEST FAILED: published_precision or lead_backfill not carried';
        END IF;
        -- An unknown precision is still refused.
        v_ok := false;
        BEGIN
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id,
                                              published_precision)
            VALUES (v_src, 'selftest048:x', 'https://selftest.invalid/048/x', 'x', now(), v_ids[1], 'week');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: published_precision ''week'' was accepted'; END IF;
        -- The callers by name still resolve.
        IF jsonb_typeof(public.ctx_tech() -> 'top_stories') IS DISTINCT FROM 'array'
           OR jsonb_typeof(public.ctx_feed() -> 'top_stories') IS DISTINCT FROM 'array'
           OR jsonb_typeof(public.ctx_sports_feed() -> 'top_stories') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech, ctx_feed or ctx_sports_feed broke';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '048 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('048_feed_dates')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the precision CHECK lists time, date, none; tech_push_eligible checks
--       the precision; feed_for_you has 28 output columns ending
--       published_precision; readonly executes it, anon does not
--   (b) v_feed_stories carries lead_published_precision and lead_backfill
--   (c) Tech For you: backfill leads still listed, and 0 listed backfill
--       leads past the published-date window
--   (d) the migration_log row (and the NOTICE "048 self-test passed")
-- ---------------------------------------------------------------------------

SELECT pg_get_constraintdef(c.oid) AS precision_check FROM pg_constraint c
 WHERE c.conname = 'content_items_published_precision';
SELECT pg_get_functiondef('public.tech_push_eligible(bigint)'::regprocedure) LIKE '%published_precision <> ''none''%' AS push_needs_a_date,
       array_length(p.proargnames, 1) - p.pronargs AS out_columns,
       p.proargnames[array_length(p.proargnames, 1)] AS last_column,
       has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS readonly_exec,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_exec
  FROM pg_proc p
 WHERE p.oid = 'public.feed_for_you(text, integer, integer, text, text, integer)'::regprocedure; -- (a)

SELECT count(*) FILTER (WHERE column_name = 'lead_published_precision') AS has_precision,
       count(*) FILTER (WHERE column_name = 'lead_backfill') AS has_backfill
  FROM information_schema.columns WHERE table_name = 'v_feed_stories';                   -- (b)

SELECT count(*) AS tech_for_you,
       count(*) FILTER (WHERE v.lead_backfill) AS backfill_listed,
       count(*) FILTER (WHERE v.lead_backfill
                          AND v.lead_published_at + CASE WHEN v.lead_published_precision = 'date'
                                                         THEN interval '24 hours' ELSE interval '0' END
                              <= now() - interval '168 hours') AS backfill_past_window
  FROM feed_for_you('tech', 100000, 0) f JOIN v_feed_stories v ON v.id = f.id;          -- (c)

SELECT name, applied_at FROM migration_log WHERE name = '048_feed_dates';                -- (d)
