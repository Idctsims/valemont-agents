-- 043_feed_rules_cross_cap.sql — two drop rules and the cross-in cap on
-- feed_for_you (Chat 3, Phase 3; owner, 2026-10-13 16:19, 16:37 and 16:39).
--
-- What it holds:
--   feed_rules   url_regex: author pages (Axios Local, Complex); PGA Tour
--                player profile, bio and results pages.
--   feed_for_you gains p_cross_cap (6th argument, default NULL = the cross
--                cap, 5): the tier-1 sports stories allowed onto Home's page
--                one, FLOORED ONES FIRST, then by rank. 0 leaves every
--                cross-in out entirely, at any position: /news (any tab or
--                chip) is Home's pillar only. Home's short top 10 passes 2
--                (rank.HOME_SHORT_CROSS). With the default (5 > FLOOR_MAX 3)
--                every floored story still fits, so the order is db/042's.
--
-- 1. Author pages. The first categorize pass filed two bare names, "Chelsea
-- Brasted" and "Carly Mallenbaum", as stories. Every author/people/staff/
-- contributor/writer/profile path in content_items was listed on 2026-10-13
-- (db_inspect); the author pages among them are exactly two shapes, 13
-- items of 4,226:
--     https://www.axios.com/local/miami/authors/cbrasted          (10 like it)
--     https://www.complex.com/author/complexstaff3                 (3 like it)
-- The pattern ends at the author slug, so articles on the same sites
-- survive, including Complex's, which carry the author slug inside the path:
--     https://www.complex.com/pop-culture/a/complexstaff3/complexcon-bitcoin-cash-app-activation
--     https://www.axios.com/local/miami/2026/10/13/what-is-going-up-miami-development-real-estate
-- Not included: the one /people/ URL in the data is a real Sportico article
-- (https://www.sportico.com/personalities/people/2026/sportico-transactions-...),
-- no staff page occurs, and Boat International's /profiles/ pages are
-- already db/038's.
--
-- 2. PGA Tour player pages: profile, bio and results pages, 13 of the 158
-- pgatour.com items (db_inspect, 2026-10-13), in four shapes, with an
-- optional language and tour prefix:
--     https://www.pgatour.com/player/profiles/32781/results
--     https://www.pgatour.com/player/615/-/bio
--     https://www.pgatour.com/players/player.53465.justin-thomas.html
--     https://www.pgatour.com/korn-ferry-tour/player/45486/joaquin-niemann
--     https://www.pgatour.com/pt/pgatour-champions/player/23386/david-roesch/career
-- News lives under /article/news/ (with or without a tour prefix) and
-- clips under /video/; neither matches:
--     https://www.pgatour.com/article/news/latest/2026/10/13/jackson-koivun-brooks-koepka-...-skins-game-...
--     https://www.pgatour.com/korn-ferry-tour/article/news/latest/2026/10/11/korn-ferry-tour-championship-austin-hitt-...
-- tests/test_feed.py runs both patterns against their lists. The rules apply
-- to new stories; stories already in the feed are not refiled.
--
-- 3. The cross-in cap. The owner's screenshot review (16:39): /news showed
-- tier-1 sports cross-ins, and Home's short list may carry at most 2.
-- feed/rank.py order_for_you(cross_cap=...) is the twin (tests/ and
-- tests_live pin them); the signature changes, so db/042's 5-argument
-- function is dropped first. ctx_feed and ctx_sports_feed call
-- feed_for_you by name with 3 arguments and resolve to this one.
--
-- A self-test runs on paste (section 4), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/032, db/039, db/042.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Drop rules

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex', NULL, '^https?://(www\.)?(axios\.com/local/[^/]+/authors|complex\.com/author)/[^/?#]+/?([?#]|$)',
                  'junk: author pages, Axios Local and Complex (owner, 2026-10-13)'),
    ('url_regex', NULL, '^https?://(www\.)?pgatour\.com/([a-z]{2}/)?((korn-ferry-tour|pgatour-champions|americas)/)?players?/',
                  'junk: PGA Tour player profile and results pages (owner, 2026-10-13)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

-- ===================================================================== 2
-- feed_for_you: db/042's order, plus p_cross_cap.

DROP FUNCTION IF EXISTS public.feed_for_you(text, integer, integer, text, text);

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
    boost_hours real
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH k AS (
        SELECT 18.0::float8 AS half_life_h,   -- rank.HALF_LIFE_H
               72           AS window_h,      -- rank.WINDOW_H
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
               sc.score AS c_score, sc.follow_tier AS c_tier, sc.floor AS c_floor,
               sc.cross_pillar AS c_cross, sc.components AS c_components,
               NULLIF(r.reaction, 'undo') AS c_reaction,
               greatest(extract(epoch FROM (now() - v.lead_published_at)) / 3600.0, 0) AS age_h,
               coalesce((SELECT max(w.w_hours) FROM win w
                          WHERE w.w_sport = v.sport
                            AND ((w.w_entity IS NULL AND p_pillar = 'sports')
                                 OR (w.w_entity IS NOT NULL
                                     AND coalesce(sc.components -> 'follows', '[]'::jsonb) ? w.w_name))),
                        0)::real AS boost_h
          FROM public.v_feed_stories v
          JOIN public.v_story_scores_latest sc ON sc.story_id = v.id AND sc.pillar = p_pillar
          LEFT JOIN public.v_feed_reaction_latest r ON r.story_id = v.id AND r.pillar = p_pillar
         CROSS JOIN k
         WHERE v.first_seen_at > now() - make_interval(hours => k.window_h)
           -- A cap of 0 leaves cross-ins out entirely (/news: Home's pillar only).
           AND (p_pillar = ANY (v.pillars) OR (sc.cross_pillar AND coalesce(p_cross_cap, k.cross_cap) > 0))
           AND coalesce(r.reaction, '') NOT IN ('neutral', 'not_interested')
           AND (p_sport IS NULL OR v.sport = p_sport)
           AND (p_category IS NULL OR v.category = p_category)
    ),
    base AS (
        SELECT c.*,
               c.c_score + ln(2.0) * c.boost_h::float8 / k.half_life_h
                         - ln(2.0) * c.age_h / k.half_life_h AS r
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
                    -- Just above the reference, keeping the lifted stories' own order.
                    THEN (SELECT r FROM ref) + 1e-6 / (1.0 + ((SELECT r FROM ref) - b.r))
                    ELSE b.r END AS e
          FROM base b
    ),
    -- The entity cap (Sports only): each story's place among the stories of
    -- each tier-1 follow it matched, floored ones first.
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
    -- The cross-ins allowed on page one: floored first, then by rank, at most
    -- the cap (p_cross_cap, else cross_cap).
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
           o.boost_h
      FROM ordered o
     ORDER BY o.n
     LIMIT p_limit OFFSET p_offset;
$$;

-- ===================================================================== 3
-- Access (db/042's, for the new signature).

REVOKE ALL ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer)
    TO authenticated, valemont_readonly;

-- ===================================================================== 4
-- feed_for_you self-test, run on paste. Everything happens inside a
-- subtransaction rolled back at the end ('selftest-rollback'), so no row
-- survives; only sequence values are consumed.
--
-- Its stories are REAL (is_test = false, from a throwaway source) with Home
-- scores of 20 to 45, above any real score (about +-10): 40 Home stories,
-- 8 cross-ins and 3 floored cross-ins (floor: the best floored stories are
-- these, never a real Alabama story, whose scores are ~1).

DO $$
DECLARE
    v_src    bigint;
    v_story  bigint;
    v_item   bigint;
    v_home   bigint[] := '{}';
    v_cross  bigint[] := '{}';
    v_floor  bigint[] := '{}';
    v_n      bigint;
    v_ids    bigint[];
    i        int;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-043', 'Self-test 043', ARRAY['home', 'sports'], 'rss', 'https://selftest.invalid/043')
        RETURNING id INTO v_src;

        FOR i IN 1..51 LOOP
            INSERT INTO public.stories (pillars, title, sport)
            VALUES (CASE WHEN i <= 40 THEN ARRAY['home'] ELSE ARRAY['sports'] END,
                    'self-test 043 #' || i, CASE WHEN i <= 40 THEN NULL ELSE 'cfb' END)
            RETURNING id INTO v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest043:' || i, 'https://selftest.invalid/043/' || i,
                    'self-test 043 #' || i, now(), v_story) RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
            IF i <= 40 THEN       -- Home stories, rank ~30
                INSERT INTO public.story_scores (story_id, pillar, score, components)
                VALUES (v_story, 'home', 30 - i * 0.01, '{}'::jsonb);
                v_home := v_home || v_story;
            ELSIF i <= 48 THEN    -- cross-ins, rank ~45: above every Home story
                INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
                VALUES (v_story, 'home', 45 - i * 0.01, '{}'::jsonb, 1, true);
                v_cross := v_cross || v_story;
            ELSE                  -- floored cross-ins, rank ~20: lifted by the floor
                INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, floor, cross_pillar)
                VALUES (v_story, 'home', 20 - i * 0.01, '{}'::jsonb, 1, true, true);
                v_floor := v_floor || v_story;
            END IF;
        END LOOP;

        -- 1. The default: at most 5 cross-ins on page one, the 3 floored first.
        SELECT count(*) INTO v_n FROM public.feed_for_you('home', 30, 0) f WHERE f.cross_pillar;
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % cross-ins on Home page one by default (cap 5)', v_n;
        END IF;
        IF EXISTS (SELECT 1 FROM unnest(v_floor) x
                    WHERE x NOT IN (SELECT f.id FROM public.feed_for_you('home', 30, 0) f)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a floored cross-in left page one under the default cap';
        END IF;

        -- 2. Cap 2 (Home's short list): exactly the two best floored cross-ins.
        SELECT array_agg(f.id ORDER BY f.id) INTO v_ids
          FROM public.feed_for_you('home', 30, 0, NULL, NULL, 2) f WHERE f.cross_pillar;
        IF v_ids IS DISTINCT FROM v_floor[1:2] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: cap 2 allowed %, expected the floored %', v_ids, v_floor[1:2];
        END IF;
        SELECT count(*) INTO v_n FROM public.feed_for_you('home', 10, 0, NULL, NULL, 2) f WHERE f.cross_pillar;
        IF v_n > 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % cross-ins in Home''s top 10 (at most 2)', v_n;
        END IF;

        -- 3. Cap 0 (/news): no cross-in at any position, with or without a chip;
        --    every Home story still there.
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 100000, 0, NULL, NULL, 0) f
                    WHERE f.cross_pillar OR NOT ('home' = ANY (f.pillars))) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a cross-in reached /news (cap 0)';
        END IF;
        IF EXISTS (SELECT 1 FROM unnest(v_home) x
                    WHERE x NOT IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0, NULL, NULL, 0) f)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: cap 0 lost a Home story';
        END IF;
        UPDATE public.stories SET category = 'deals' WHERE id IN (v_home[1], v_cross[1]);
        SELECT array_agg(f.id) INTO v_ids
          FROM public.feed_for_you('home', 100000, 0, NULL, 'deals', 0) f WHERE f.id IN (v_home[1], v_cross[1]);
        IF v_ids IS DISTINCT FROM ARRAY[v_home[1]] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the deals chip at cap 0 returned % (expected only the Home story)', v_ids;
        END IF;

        -- 4. The callers by name still resolve (3 arguments), and Sports runs.
        IF jsonb_typeof(public.ctx_feed() -> 'top_stories') IS DISTINCT FROM 'array'
           OR jsonb_typeof(public.ctx_sports_feed() -> 'top_stories') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_feed or ctx_sports_feed broke';
        END IF;
        PERFORM 1 FROM public.feed_for_you('sports', 30, 0);

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '043 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('043_feed_rules_cross_cap')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the two rules, active; 52 active feed rules in all
--   (b) feed_for_you has 6 arguments; the 5-argument one is gone; the
--       readonly role executes it, anon does not
--   (c) the migration_log row (and the NOTICE "043 self-test passed")
-- ---------------------------------------------------------------------------

SELECT id, kind, pattern, active FROM feed_rules
 WHERE note IN ('junk: author pages, Axios Local and Complex (owner, 2026-10-13)',
                'junk: PGA Tour player profile and results pages (owner, 2026-10-13)')
 ORDER BY id;
SELECT count(*) AS active_rules FROM feed_rules WHERE active;                             -- (a)

SELECT to_regprocedure('public.feed_for_you(text, integer, integer, text, text, integer)') IS NOT NULL AS six_args,
       to_regprocedure('public.feed_for_you(text, integer, integer, text, text)') IS NULL AS five_args_gone,
       has_function_privilege('valemont_readonly', 'public.feed_for_you(text, integer, integer, text, text, integer)', 'EXECUTE') AS readonly_exec,
       has_function_privilege('anon', 'public.feed_for_you(text, integer, integer, text, text, integer)', 'EXECUTE') AS anon_exec; -- (b)

SELECT name, applied_at FROM migration_log WHERE name = '043_feed_rules_cross_cap';        -- (c)
