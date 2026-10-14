-- 046_tech_official_decay.sql — official tech items decay last (Chat 4
-- Phase 1; owner, 2026-10-13: official items "should be the last thing to
-- decay, not the first").
--
-- What it changes: feed_for_you's recency term for one case only. An
-- OFFICIAL story on Tech decays with a 72-hour half-life instead of 18, so a
-- busy Hacker News day cannot push a day-old release below page one:
--
--     rank = score + ln 2 * boost_h / 18 - ln 2 * age_h / H
--     H = 72 for an official story when p_pillar = 'tech', else 18
--
-- At 48 hours an official item has lost 0.46 (it lost 1.85 under db/045);
-- a 12-hour-old HN story with the same score has lost 0.46 too. Home,
-- Sports and every boost are unchanged. workers/feed/rank.py
-- TECH_OFFICIAL_HALF_LIFE_H and order_for_you are the twin (tests/ and
-- tests_live pin them).
--
-- Not a guarantee of page one: 30 community stories that outrank an official
-- one still fill the page first. The owner's reading of "seen" (an open or a
-- reaction) is not tracked as an impression; see docs/tech_hub.md.
--
-- Same 6 arguments and the same 27 output columns as db/045, so CREATE OR
-- REPLACE keeps the function's grants.
--
-- Section 1b, push eligibility (owner, 2026-10-14, after notification 323
-- pushed a first-fetch backfill item): a REAL free-credits push needs the
-- story's lead item to be push-eligible:
--   * first seen on a cycle after its source's first successful fetch (not
--     a first-fetch backfill: content_items.backfill is false), AND
--   * published within 24 hours of first seen (fetched_at - published_at
--     <= 24 h).
-- tech_credit_push_guard refuses anything else, and tech_credit_push_allowed
-- answers false for it, so the worker never sends it. A forced test push
-- (is_test) is not held to this. workers/feed/tech.py push_eligible is the
-- twin. Notification 323 (tech_credit_pushes row 20) stays as history and
-- counts toward 2026-10-13's cap.
--
-- A self-test runs on paste (section 2), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/045.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- feed_for_you: db/045's, with the official half-life.

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
    boost_hours real, category text, official boolean
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
         WHERE v.first_seen_at > now() - make_interval(
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
           o.boost_h, o.c_category, o.c_official
      FROM ordered o
     ORDER BY o.n
     LIMIT p_limit OFFSET p_offset;
$$;

-- ===================================================================== 1b
-- Push eligibility: only a fresh item, never a first-fetch backfill.

-- True when story p_story's lead item may trigger a real push.
CREATE OR REPLACE FUNCTION public.tech_push_eligible(p_story bigint)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT coalesce((
        SELECT NOT i.backfill AND i.fetched_at - i.published_at <= interval '24 hours'
          FROM public.stories st
          JOIN public.content_items i ON i.id = st.lead_item_id
         WHERE st.id = p_story), false);
$$;

CREATE OR REPLACE FUNCTION public.tech_credit_push_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    -- One writer at a time: the count below and this insert are one decision.
    PERFORM pg_advisory_xact_lock(hashtext('tech_credit_pushes'));
    NEW.local_day := public.owner_today();
    NEW.created_at := now();
    IF NOT EXISTS (SELECT 1 FROM public.stories st WHERE st.id = NEW.story_id AND 'tech' = ANY (st.pillars)) THEN
        RAISE EXCEPTION 'tech_credit_pushes: story % is not a tech story', NEW.story_id;
    END IF;
    IF NOT NEW.is_test THEN
        IF EXISTS (SELECT 1 FROM public.stories st WHERE st.id = NEW.story_id AND st.is_test) THEN
            RAISE EXCEPTION 'tech_credit_pushes: a real push for test story %', NEW.story_id;
        END IF;
        IF NOT public.tech_push_eligible(NEW.story_id) THEN
            RAISE EXCEPTION 'tech_credit_pushes: story % is not push-eligible (a first-fetch backfill, or published more than 24 h before first seen)', NEW.story_id;
        END IF;
        IF EXISTS (SELECT 1 FROM public.tech_credit_pushes p WHERE p.story_id = NEW.story_id AND NOT p.is_test) THEN
            RAISE EXCEPTION 'tech_credit_pushes: story % was already pushed', NEW.story_id;
        END IF;
        IF (SELECT count(*) FROM public.tech_credit_pushes p
             WHERE p.local_day = NEW.local_day AND NOT p.is_test) >= 3 THEN
            RAISE EXCEPTION 'tech_credit_pushes: the cap of 3 a day is reached for %', NEW.local_day;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.tech_credit_push_allowed(p_story bigint)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT public.tech_credit_push_room() > 0
       AND EXISTS (SELECT 1 FROM public.stories st
                    WHERE st.id = p_story AND 'tech' = ANY (st.pillars) AND NOT st.is_test)
       AND public.tech_push_eligible(p_story)
       AND NOT EXISTS (SELECT 1 FROM public.tech_credit_pushes p WHERE p.story_id = p_story AND NOT p.is_test);
$$;

REVOKE ALL ON FUNCTION public.tech_push_eligible(bigint) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.tech_push_eligible(bigint) TO authenticated, valemont_readonly;

-- ===================================================================== 2
-- Self-test, run on paste, rolled back ('selftest-rollback').
--
-- Two tech stories with the same score: an OFFICIAL one published 48 h ago
-- and a community one published 20 h ago. Under db/045 (18 h for both) the
-- community story ranks first (-0.77 against -1.85); now the official one
-- does (-0.46 against -0.77). A Home story's rank is still score - ln 2 *
-- age / 18 to the micro-unit, and an official story on Home (none exists,
-- but the rule is per pillar) would not get the slow half-life.

DO $$
DECLARE
    v_off   bigint;   -- tech, official source, 48 h old
    v_com   bigint;   -- tech, community source, 20 h old
    v_home  bigint;   -- home, 30 h old
    v_src   bigint;
    v_item  bigint;
    v_rank  float8;
    i       int;
BEGIN
    BEGIN
        FOR i IN 1..3 LOOP
            INSERT INTO public.sources (slug, name, pillars, kind, url, official)
            VALUES ('selftest-046-' || i, 'Self-test 046 #' || i,
                    CASE WHEN i = 3 THEN ARRAY['home'] ELSE ARRAY['tech'] END, 'rss',
                    'https://selftest.invalid/046/' || i, i = 1)
            RETURNING id INTO v_src;
            INSERT INTO public.stories (pillars, title, category)
            VALUES (CASE WHEN i = 3 THEN ARRAY['home'] ELSE ARRAY['tech'] END, 'self-test 046 #' || i,
                    CASE WHEN i = 3 THEN 'deals' ELSE 'tooling' END)
            RETURNING id INTO v_item;
            CASE i WHEN 1 THEN v_off := v_item; WHEN 2 THEN v_com := v_item; ELSE v_home := v_item; END CASE;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest046:' || i, 'https://selftest.invalid/046/item/' || i, 'self-test 046 #' || i,
                    now() - CASE i WHEN 1 THEN interval '48 hours' WHEN 2 THEN interval '20 hours'
                                   ELSE interval '30 hours' END, v_item)
            RETURNING id INTO v_src;
            UPDATE public.stories SET lead_item_id = v_src WHERE id = v_item;
            INSERT INTO public.story_scores (story_id, pillar, score, components)
            VALUES (v_item, CASE WHEN i = 3 THEN 'home' ELSE 'tech' END, 40, '{}');
        END LOOP;

        -- 1. The official story outranks the fresher community one.
        IF (SELECT f.pos FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_off)
           > (SELECT f.pos FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_com) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the 48 h official story ranks below the 20 h community one';
        END IF;
        -- 2. Its rank uses 72 h: 40 - ln 2 * 48 / 72.
        SELECT f.rank INTO v_rank FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_off;
        IF abs(v_rank - (40 - ln(2.0) * 48 / 72)) > 0.001 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: official rank % (expected %)', v_rank, 40 - ln(2.0) * 48 / 72;
        END IF;
        -- 3. The community story and Home keep 18 h.
        SELECT f.rank INTO v_rank FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_com;
        IF abs(v_rank - (40 - ln(2.0) * 20 / 18)) > 0.001 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: community rank % (expected %)', v_rank, 40 - ln(2.0) * 20 / 18;
        END IF;
        SELECT f.rank INTO v_rank FROM public.feed_for_you('home', 100000, 0) f WHERE f.id = v_home;
        IF abs(v_rank - (40 - ln(2.0) * 30 / 18)) > 0.001 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Home rank % changed (expected %)', v_rank, 40 - ln(2.0) * 30 / 18;
        END IF;
        -- 5. Push eligibility. Three pricing stories from the official
        --    source: a backfill item, one published 30 h before first seen,
        --    and a fresh one (published 1 h before first seen).
        DECLARE
            v_bf    bigint;
            v_old   bigint;
            v_fresh bigint;
            v_ok    boolean;
            v_osrc  bigint := (SELECT id FROM public.sources WHERE slug = 'selftest-046-1');
            j       int;
        BEGIN
            FOR j IN 1..3 LOOP
                INSERT INTO public.stories (pillars, title, category)
                VALUES (ARRAY['tech'], 'self-test 046 push #' || j, 'pricing_credits') RETURNING id INTO v_item;
                INSERT INTO public.content_items (source_id, external_id, url, title, published_at, fetched_at,
                                                  story_id, backfill)
                VALUES (v_osrc, 'selftest046push:' || j, 'https://selftest.invalid/046/push/' || j,
                        'self-test 046 push #' || j,
                        now() - CASE j WHEN 2 THEN interval '30 hours' ELSE interval '1 hour' END, now(),
                        v_item, j = 1)
                RETURNING id INTO v_src;
                UPDATE public.stories SET lead_item_id = v_src WHERE id = v_item;
                CASE j WHEN 1 THEN v_bf := v_item; WHEN 2 THEN v_old := v_item; ELSE v_fresh := v_item; END CASE;
            END LOOP;
            IF public.tech_push_eligible(v_bf) OR public.tech_push_eligible(v_old)
               OR NOT public.tech_push_eligible(v_fresh) THEN
                RAISE EXCEPTION 'SELFTEST FAILED: tech_push_eligible (backfill %, 30 h %, fresh %)',
                    public.tech_push_eligible(v_bf), public.tech_push_eligible(v_old),
                    public.tech_push_eligible(v_fresh);
            END IF;
            -- The worker's check says no for both (whatever today's room).
            IF public.tech_credit_push_allowed(v_bf) OR public.tech_credit_push_allowed(v_old) THEN
                RAISE EXCEPTION 'SELFTEST FAILED: tech_credit_push_allowed said yes to an ineligible story';
            END IF;
            -- The trigger refuses both as real pushes, with the eligibility reason.
            FOREACH v_item IN ARRAY ARRAY[v_bf, v_old] LOOP
                v_ok := false;
                BEGIN
                    INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_item);
                EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%not push-eligible%';
                END;
                IF NOT v_ok THEN
                    RAISE EXCEPTION 'SELFTEST FAILED: an ineligible story (%) was pushed', v_item;
                END IF;
            END LOOP;
            -- A test push is not held to it; the fresh story passes eligibility
            -- (the cap may still refuse it on a day with 3 real pushes).
            INSERT INTO public.tech_credit_pushes (story_id, is_test) VALUES (v_bf, true);
            IF public.tech_credit_push_room() > 0 THEN
                INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_fresh);
            END IF;
        END;

        -- 4. The callers by name still resolve.
        IF jsonb_typeof(public.ctx_tech() -> 'top_stories') IS DISTINCT FROM 'array'
           OR jsonb_typeof(public.ctx_feed() -> 'top_stories') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech or ctx_feed broke';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '046 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('046_tech_official_decay')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) feed_for_you's body has the 72-hour half-life; 27 output columns;
--       readonly executes it, anon does not
--   (b) the migration_log row (and the NOTICE "046 self-test passed")
--   (c) push eligibility: the guard and the check call tech_push_eligible;
--       readonly executes it, anon does not; story 8359 (notification 323,
--       the Vercel backfill item) is NOT eligible; its push row 20 stays
-- ---------------------------------------------------------------------------

SELECT pg_get_functiondef(p.oid) LIKE '%tech_official_half_life_h%' AS slow_official,
       array_length(p.proargnames, 1) - p.pronargs AS out_columns,
       has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS readonly_exec,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_exec
  FROM pg_proc p
 WHERE p.oid = 'public.feed_for_you(text, integer, integer, text, text, integer)'::regprocedure; -- (a)

SELECT name, applied_at FROM migration_log WHERE name = '046_tech_official_decay';        -- (b)

SELECT pg_get_functiondef('public.tech_credit_push_guard()'::regprocedure) LIKE '%tech_push_eligible%' AS guard_checks,
       pg_get_functiondef('public.tech_credit_push_allowed(bigint)'::regprocedure) LIKE '%tech_push_eligible%' AS allowed_checks,
       has_function_privilege('valemont_readonly', 'public.tech_push_eligible(bigint)', 'EXECUTE') AS readonly_exec,
       has_function_privilege('anon', 'public.tech_push_eligible(bigint)', 'EXECUTE') AS anon_exec,
       tech_push_eligible(8359) AS story_8359_eligible,
       (SELECT count(*) FROM tech_credit_pushes WHERE id = 20) AS row_20_kept;               -- (c)
