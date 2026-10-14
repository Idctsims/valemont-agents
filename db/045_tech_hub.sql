-- 045_tech_hub.sql — the Tech Hub pillar (Chat 4, Phase 1; owner, 2026-10-13).
--
-- A new feed pillar, `tech`, on the existing engine. Nothing here is a new
-- table for something that exists: tech stories are stories, tech items are
-- content_items, tech reactions are feed_reactions. Design and decisions:
-- docs/tech_hub.md.
--
-- What it holds:
--   1. sources       pillar `tech`; two new kinds: `page` (a listing-page
--                    watcher: the page's HTML, links/titles/dates pulled out
--                    by a per-source `config`) and `hn` (Hacker News via the
--                    Algolia API, thresholds in `config`); `official` (the
--                    "Official" tag; official items are summarized first).
--   2. stories       pillar `tech` is EXCLUSIVE: a story is tech and nothing
--                    else, or never tech (the no-cross-in rule, at the row).
--                    Tech categories (models_features, pricing_credits,
--                    tooling, agents, stack, community; `repos` reserved for
--                    Phase 2) live only on tech stories; Home's only on Home
--                    and Sports stories; `other` on either.
--                    claimable_offer: Haiku's answer to the free-credits
--                    question, kept for the record.
--   3. content_items backfill (an official source's first fetch keeps its
--                    newest few items whatever their age, so every source
--                    shows something; never counted in the latency report)
--                    and published_precision ('time' or 'date': a listing
--                    page that states only a date). fetched_at is the item's
--                    first_seen_at.
--   4. pillar CHECKs feed_rules, feed_reactions, interest_profiles,
--                    story_scores accept `tech`. follows does not (no tech
--                    follows this phase).
--   5. feed_pillar_guard  the no-cross-in rule at write time: a `tech` score
--                    or reaction only on a tech story, and a home or sports
--                    one never on a tech story.
--   6. the tech test source `_e2e_tech` (official, is_test, never active)
--                    and add_test_tech_story() (verified e2e marker).
--   7. seeds         seed_interests.feed.tech (owner's LOVE / NOT
--                    INTERESTED), set once, only while empty.
--   8. views         v_feed_stories gains source_official;
--                    v_feed_source_stats gains last_item_at.
--   9. feed_for_you  same 6 arguments, two more output columns (category,
--                    official). For `tech`: a 168-hour window, and an
--                    official story with no score yet is listed (score 0)
--                    instead of hidden until its summary lands. Cross-ins
--                    never involve tech, in either direction.
--  10. tech_credit_pushes  one row per free-credits push. Append-only. At
--                    most 3 real pushes per owner's day and never the same
--                    story twice, enforced by trigger; test pushes
--                    (is_test) are not counted.
--  11. ctx_tech (Wags: the last 7 days of official items and the top of the
--                    loved categories) and ctx_tech_brief (the Morning Brief:
--                    official items that landed in the last 24 h, or NULL,
--                    so the section is silent).
--
-- Sources themselves are NOT rows in this file: like every feed source, they
-- are listed in workers/feed/sources.toml and synced at boot.
--
-- A self-test runs on paste (section 13), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/019 (notifications), db/020, db/027, db/029, db/032-044.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- sources

ALTER TABLE sources ADD COLUMN IF NOT EXISTS official BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS config JSONB;

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_pillars_check;
ALTER TABLE sources ADD CONSTRAINT sources_pillars_check
    CHECK (cardinality(pillars) >= 1
           AND pillars <@ ARRAY['home', 'sports', 'tech']::text[]
           AND (NOT ('tech' = ANY (pillars)) OR pillars = ARRAY['tech']::text[]));

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_check;
ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ('rss', 'gnews', 'page', 'hn'));

-- db/032's unnamed "(kind = 'rss' OR url ~ google)" (sources_check1 on
-- production), found by its text: a Google News URL for gnews only now.
DO $$
DECLARE
    c text;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'public.sources'::regclass AND contype = 'c'
                AND pg_get_constraintdef(oid) LIKE '%news\\.google\\.com/rss/search%'
                AND conname <> 'sources_kind_url'
    LOOP
        EXECUTE format('ALTER TABLE public.sources DROP CONSTRAINT %I', c);
    END LOOP;
END
$$;
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_url;
ALTER TABLE sources ADD CONSTRAINT sources_kind_url CHECK (
    (kind <> 'gnews' OR url ~ '^https://news\.google\.com/rss/search\?')
    AND (kind <> 'hn' OR url ~ '^https://hn\.algolia\.com/api/v1/'));

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_config_shape;
ALTER TABLE sources ADD CONSTRAINT sources_config_shape CHECK (
    (config IS NULL OR jsonb_typeof(config) = 'object')
    AND (kind NOT IN ('page', 'hn') OR config IS NOT NULL));

-- ===================================================================== 2
-- stories

ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_pillars_check;
ALTER TABLE stories ADD CONSTRAINT stories_pillars_check
    CHECK (cardinality(pillars) >= 1
           AND pillars <@ ARRAY['home', 'sports', 'tech']::text[]
           AND (NOT ('tech' = ANY (pillars)) OR pillars = ARRAY['tech']::text[]));

ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_category_check;
ALTER TABLE stories ADD CONSTRAINT stories_category_check CHECK (category IN (
    'deals', 'hospitality', 'sports_business', 'film_music', 'luxury_marine', 'betting', 'other',
    'models_features', 'pricing_credits', 'tooling', 'agents', 'stack', 'community', 'repos'));

-- A tech category only on a tech story, a Home one only on a Home or Sports
-- story; `other` on either.
ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_category_pillar;
ALTER TABLE stories ADD CONSTRAINT stories_category_pillar CHECK (
    category IS NULL OR category = 'other'
    OR ('tech' = ANY (pillars)) = (category IN ('models_features', 'pricing_credits', 'tooling', 'agents',
                                                'stack', 'community', 'repos')));

ALTER TABLE stories ADD COLUMN IF NOT EXISTS claimable_offer BOOLEAN;

-- ===================================================================== 3
-- content_items (constant defaults: no rewrite, no UPDATE, so the freeze
-- trigger never sees these)

ALTER TABLE content_items ADD COLUMN IF NOT EXISTS backfill BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE content_items ADD COLUMN IF NOT EXISTS published_precision TEXT NOT NULL DEFAULT 'time';
ALTER TABLE content_items DROP CONSTRAINT IF EXISTS content_items_published_precision;
ALTER TABLE content_items ADD CONSTRAINT content_items_published_precision
    CHECK (published_precision IN ('time', 'date'));

-- ===================================================================== 4
-- Pillar CHECKs. feed_rules: NULL still means Home and Sports (the worker
-- applies a NULL-pillar rule to those two only); a tech rule names `tech`.

ALTER TABLE feed_rules DROP CONSTRAINT IF EXISTS feed_rules_pillar_check;
ALTER TABLE feed_rules ADD CONSTRAINT feed_rules_pillar_check CHECK (pillar IN ('home', 'sports', 'tech'));
ALTER TABLE feed_reactions DROP CONSTRAINT IF EXISTS feed_reactions_pillar_check;
ALTER TABLE feed_reactions ADD CONSTRAINT feed_reactions_pillar_check CHECK (pillar IN ('home', 'sports', 'tech'));
ALTER TABLE interest_profiles DROP CONSTRAINT IF EXISTS interest_profiles_pillar_check;
ALTER TABLE interest_profiles ADD CONSTRAINT interest_profiles_pillar_check CHECK (pillar IN ('home', 'sports', 'tech'));
ALTER TABLE story_scores DROP CONSTRAINT IF EXISTS story_scores_pillar_check;
ALTER TABLE story_scores ADD CONSTRAINT story_scores_pillar_check CHECK (pillar IN ('home', 'sports', 'tech'));

-- ===================================================================== 5
-- The no-cross-in rule at write time.

CREATE OR REPLACE FUNCTION public.feed_pillar_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_tech boolean;
BEGIN
    SELECT 'tech' = ANY (st.pillars) INTO v_tech FROM public.stories st WHERE st.id = NEW.story_id;
    -- An unknown story is the foreign key's to refuse.
    IF v_tech IS NOT NULL AND (NEW.pillar = 'tech') <> v_tech THEN
        RAISE EXCEPTION '%: a % row on story % crosses the tech line (tech stories live only in tech)',
            TG_TABLE_NAME, NEW.pillar, NEW.story_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS story_scores_pillar_guard ON story_scores;
CREATE TRIGGER story_scores_pillar_guard
    BEFORE INSERT ON story_scores
    FOR EACH ROW EXECUTE FUNCTION public.feed_pillar_guard();
DROP TRIGGER IF EXISTS feed_reactions_pillar_guard ON feed_reactions;
CREATE TRIGGER feed_reactions_pillar_guard
    BEFORE INSERT ON feed_reactions
    FOR EACH ROW EXECUTE FUNCTION public.feed_pillar_guard();

-- ===================================================================== 6
-- The tech test source and its e2e writer.

INSERT INTO sources (slug, name, pillars, kind, url, priority, active, is_test, official, notes)
VALUES ('_e2e_tech', 'Test official source', ARRAY['tech'], 'rss', 'https://e2e.invalid/tech-feed',
        1000, false, true, true, 'tests_live and add_test_tech_story() only')
ON CONFLICT (slug) DO NOTHING;

-- db/032's add_test_story, for a TECH story from the official test source,
-- with a category. Owner only, the verified e2e marker, a .invalid URL.
CREATE OR REPLACE FUNCTION public.add_test_tech_story(p_marker text, p_category text, p_title text,
                                                      p_summary text, p_url text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_hash    text;
    v_source  bigint;
    v_story   bigint;
    v_item    bigint;
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'add_test_tech_story: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_tech_story: test marker refused';
    END IF;
    IF p_url !~ '^https://[a-z0-9.-]+\.invalid/' THEN
        RAISE EXCEPTION 'add_test_tech_story: test URLs must be on a .invalid host';
    END IF;
    IF p_category IS NULL OR p_category NOT IN ('models_features', 'pricing_credits', 'tooling', 'agents',
                                                'stack', 'community', 'other') THEN
        RAISE EXCEPTION 'add_test_tech_story: unknown category %', p_category;
    END IF;
    SELECT id INTO v_source FROM public.sources WHERE slug = '_e2e_tech' AND is_test;
    INSERT INTO public.stories (pillars, title, summary, summarized_at, category, is_test)
    VALUES (ARRAY['tech'], p_title, p_summary, CASE WHEN p_summary IS NOT NULL THEN now() END,
            p_category, true)
    RETURNING id INTO v_story;
    INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id, is_test)
    VALUES (v_source, 'e2e:' || gen_random_uuid(), p_url, p_title, now(), v_story, true)
    RETURNING id INTO v_item;
    UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
    RETURN v_story;
END;
$$;

-- ===================================================================== 7
-- Tech seed phrases (owner, 2026-10-13), set once: only while both tech
-- arrays are empty, so a later edit by the owner is never overwritten.

UPDATE app_settings
   SET seed_interests = jsonb_set(seed_interests, '{feed,tech}', jsonb_build_object(
           'love', jsonb_build_array(
               'new Claude models and features from Anthropic',
               'new OpenAI models and ChatGPT features',
               'AI model and API pricing changes',
               'free API credits, free tier increases and promotions developers can claim',
               'Claude Code, Cursor and MCP developer tooling',
               'AI agent frameworks and agent SDKs',
               'Next.js, Supabase and Vercel updates',
               'useful GitHub repos and top trending repositories'),
           'not_interested', jsonb_build_array(
               'generic takes that AI will change everything',
               'AI doom and AI policy op-eds',
               'AI benchmark drama with no practical angle',
               'crypto price chatter'))),
       updated_at = now()
 WHERE seed_interests ? 'feed'
   AND coalesce(jsonb_array_length(seed_interests #> '{feed,tech,love}'), 0) = 0
   AND coalesce(jsonb_array_length(seed_interests #> '{feed,tech,not_interested}'), 0) = 0;

-- ===================================================================== 8
-- Views: appended columns only, so CREATE OR REPLACE keeps every column.

CREATE OR REPLACE VIEW v_feed_stories
WITH (security_invoker = true)
AS
SELECT st.id, st.pillars, st.sport, st.title, st.summary, st.entities, st.topics,
       st.item_count, st.first_seen_at, st.last_seen_at,
       i.id AS lead_item_id, i.url AS lead_url, i.published_at AS lead_published_at,
       s.slug AS source_slug, s.name AS source_name,
       st.why_it_matters,
       st.category,
       s.official AS source_official
  FROM public.stories st
  JOIN public.content_items i ON i.id = st.lead_item_id
  JOIN public.sources s ON s.id = i.source_id
 WHERE NOT st.is_test
   AND st.filtered_reason IS NULL
   AND i.url IS NOT NULL;

CREATE OR REPLACE VIEW v_feed_source_stats
WITH (security_invoker = true)
AS
SELECT s.id AS source_id,
       (SELECT count(*) FROM public.content_items i
         WHERE i.source_id = s.id AND NOT i.seeded
           AND i.fetched_at > now() - interval '24 hours') AS items_24h,
       (SELECT count(*) FROM public.content_items i
         WHERE i.source_id = s.id AND i.decode_failed
           AND i.fetched_at > now() - interval '24 hours') AS decode_failed_24h,
       (SELECT count(*) FROM public.v_feed_filtered f
         WHERE f.source_id = s.id
           AND f.first_seen_at > now() - interval '24 hours') AS filtered_24h,
       -- The newest item the source gave us (seen, not seeded), by first seen.
       (SELECT max(i.fetched_at) FROM public.content_items i
         WHERE i.source_id = s.id AND NOT i.seeded) AS last_item_at
  FROM public.sources s
 WHERE NOT s.is_test;

-- ===================================================================== 9
-- feed_for_you: db/043's order, plus the tech rules. Two output columns are
-- added (category, official), so the function is dropped and re-created
-- with the same 6 arguments; ctx_feed, ctx_sports_feed and ctx_tech call it
-- by name.

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

-- ===================================================================== 10
-- tech_credit_pushes: the free-credits push record (owner, 2026-10-13: at
-- most 3 per Chicago day, never the same story twice; a forced test push is
-- is_test and not counted). Append-only.

CREATE TABLE IF NOT EXISTS tech_credit_pushes (
    id               BIGSERIAL    PRIMARY KEY,
    story_id         BIGINT       NOT NULL REFERENCES stories (id) ON DELETE RESTRICT,
    notification_id  BIGINT       REFERENCES notifications (id) ON DELETE RESTRICT,
    -- The owner's local date of the push; set by the trigger, never by the writer.
    local_day        DATE         NOT NULL,
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Never the same story twice (real pushes; a forced test push may repeat).
CREATE UNIQUE INDEX IF NOT EXISTS tech_credit_pushes_once ON tech_credit_pushes (story_id) WHERE NOT is_test;
CREATE INDEX IF NOT EXISTS tech_credit_pushes_day ON tech_credit_pushes (local_day) WHERE NOT is_test;

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

DROP TRIGGER IF EXISTS tech_credit_pushes_guard ON tech_credit_pushes;
CREATE TRIGGER tech_credit_pushes_guard
    BEFORE INSERT ON tech_credit_pushes
    FOR EACH ROW EXECUTE FUNCTION public.tech_credit_push_guard();
DROP TRIGGER IF EXISTS tech_credit_pushes_immutable ON tech_credit_pushes;
CREATE TRIGGER tech_credit_pushes_immutable
    BEFORE UPDATE OR DELETE ON tech_credit_pushes
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS tech_credit_pushes_no_truncate ON tech_credit_pushes;
CREATE TRIGGER tech_credit_pushes_no_truncate
    BEFORE TRUNCATE ON tech_credit_pushes
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- Real pushes left today (0 to 3), and whether a story may be pushed now.
-- The worker asks first, under the same lock, then pushes and records.
CREATE OR REPLACE FUNCTION public.tech_credit_push_room()
RETURNS integer
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT greatest(3 - count(*), 0)::integer FROM public.tech_credit_pushes p
     WHERE p.local_day = public.owner_today() AND NOT p.is_test;
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
       AND NOT EXISTS (SELECT 1 FROM public.tech_credit_pushes p WHERE p.story_id = p_story AND NOT p.is_test);
$$;

-- ===================================================================== 11
-- Context providers.

-- ctx_tech (Wags; not a brief section): the official items published in the
-- last 7 days, the categories the owner loves most, and the top of For you
-- in those categories (or the top of For you, before any Love).
CREATE OR REPLACE FUNCTION public.ctx_tech()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH official AS (
        SELECT v.id, v.title, v.summary, v.source_name, v.category, v.lead_url, v.lead_published_at
          FROM public.v_feed_stories v
         WHERE 'tech' = ANY (v.pillars) AND v.source_official
           AND v.lead_published_at > now() - interval '7 days'
         ORDER BY v.lead_published_at DESC, v.id DESC
         LIMIT 30
    ),
    loves AS (
        SELECT st.category, count(*) AS n
          FROM public.v_feed_reaction_latest r
          JOIN public.stories st ON st.id = r.story_id
         WHERE r.pillar = 'tech' AND r.reaction = 'love' AND NOT r.is_test AND st.category IS NOT NULL
         GROUP BY st.category
         ORDER BY count(*) DESC, st.category
         LIMIT 3
    ),
    top AS (
        SELECT f.* FROM public.feed_for_you('tech', 90, 0) f
         WHERE NOT EXISTS (SELECT 1 FROM loves) OR f.category IN (SELECT l.category FROM loves l)
         ORDER BY f.pos
         LIMIT 5
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Tech Hub: official AI and developer-stack releases (Anthropic, OpenAI, Claude Code, '
                 'Cursor, MCP, Next.js, Vercel, Supabase) and Hacker News. official_7d is every official '
                 'item published in the last 7 days, newest first.',
        'official_7d', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(o.title, 160),
                       'source', o.source_name,
                       'published', to_char(o.lead_published_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
                       'category', o.category,
                       'summary', public.ctx_trim(o.summary, 260),
                       'url', o.lead_url))
                     ORDER BY o.lead_published_at DESC, o.id DESC)
              FROM official o), '[]'::jsonb),
        'loved_categories', coalesce((SELECT jsonb_agg(jsonb_build_object('category', l.category, 'loves', l.n)
                                                       ORDER BY l.n DESC, l.category)
                                        FROM loves l), '[]'::jsonb),
        'top_stories', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(t.title, 160),
                       'source', t.source_name,
                       'category', t.category,
                       'official', CASE WHEN t.official THEN true END,
                       'summary', public.ctx_trim(t.summary, 260),
                       'loved', CASE WHEN t.reaction = 'love' THEN true END))
                     ORDER BY t.pos)
              FROM top t), '[]'::jsonb),
        'note', CASE WHEN NOT EXISTS (SELECT 1 FROM official) THEN 'No official items in the last 7 days.' END
    ));
$$;

-- ctx_tech_brief (the Morning Brief's Tech section): official items that
-- LANDED (first seen, not a first-fetch backfill) in the last 24 hours, or
-- NULL. NULL makes the section silent: core/brief.py leaves out a section
-- whose provider returned nothing.
CREATE OR REPLACE FUNCTION public.ctx_tech_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH landed AS (
        SELECT v.id, v.title, v.summary, v.source_name, v.first_seen_at
          FROM public.v_feed_stories v
          JOIN public.content_items i ON i.id = v.lead_item_id
         WHERE 'tech' = ANY (v.pillars) AND v.source_official AND NOT i.backfill
           AND v.first_seen_at > now() - interval '24 hours'
         ORDER BY v.first_seen_at DESC, v.id DESC
         LIMIT 5
    )
    SELECT CASE WHEN EXISTS (SELECT 1 FROM landed) THEN jsonb_build_object(
        'about', 'Official tech items that landed in the last 24 hours. Write exactly ONE line.',
        'official_24h', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                 'title', public.ctx_trim(l.title, 160),
                                 'source', l.source_name,
                                 'summary', public.ctx_trim(l.summary, 200)))
                               ORDER BY l.first_seen_at DESC, l.id DESC)
                           FROM landed l)) END;
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('tech', 'Tech Hub', 'public.ctx_tech()'::regprocedure, false, 50),
       ('tech_brief', 'Tech', 'public.ctx_tech_brief()'::regprocedure, true, 50)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 12
-- Access.

ALTER TABLE tech_credit_pushes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON tech_credit_pushes FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE tech_credit_pushes_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON tech_credit_pushes TO authenticated;
DROP POLICY IF EXISTS tech_credit_pushes_owner_read ON tech_credit_pushes;
CREATE POLICY tech_credit_pushes_owner_read ON tech_credit_pushes
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
-- db/018's defaults cover the table; restated so the file says what it expects.
GRANT SELECT ON tech_credit_pushes TO valemont_readonly;

REVOKE ALL ON FUNCTION public.feed_pillar_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.tech_credit_push_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.tech_credit_push_room() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.tech_credit_push_allowed(bigint) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_tech_story(text, text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_tech() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_tech_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.tech_credit_push_room() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.tech_credit_push_allowed(bigint) TO valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_tech_story(text, text, text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.feed_for_you(text, integer, integer, text, text, integer)
    TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_tech() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_tech_brief() TO authenticated, valemont_readonly;
-- Views replaced above keep their grants; restated so the file says what it expects.
GRANT SELECT ON v_feed_stories, v_feed_source_stats TO authenticated, valemont_readonly;

-- ===================================================================== 13
-- Self-test, run on paste. Everything happens inside a subtransaction rolled
-- back at the end ('selftest-rollback'), so no row survives; only sequence
-- values are consumed. A wrong result raises SELFTEST FAILED and nothing in
-- this file is applied.
--
-- Its stories are REAL (is_test = false, from throwaway sources) so the real
-- selectors see them. Rule for refusals: each expected refusal runs in its
-- own nested block and is counted only when it fails with the expected
-- message (P0001 / check_violation / unique_violation), never on any error.

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_tsrc    bigint;   -- tech, official
    v_hsrc    bigint;   -- home
    v_t1      bigint;   -- tech, scored
    v_t2      bigint;   -- tech, official, unscored
    v_told    bigint;   -- tech, first seen 100 h ago
    v_h1      bigint;   -- home, scored
    v_hold    bigint;   -- home, first seen 100 h ago, scored
    v_s1      bigint;   -- sports, tier-1 cross-in to home
    v_tp      bigint[] := '{}';
    v_item    bigint;
    v_n       int;
    v_ok      boolean;
    v_ctx     jsonb;
    i         int;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. sources CHECKs
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url)
            VALUES ('selftest-045-x', 'x', ARRAY['tech', 'home'], 'rss', 'https://selftest.invalid/x');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a source in tech and home was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url)
            VALUES ('selftest-045-p', 'p', ARRAY['tech'], 'page', 'https://selftest.invalid/p');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a page source with no config was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url, config)
            VALUES ('selftest-045-h', 'h', ARRAY['tech'], 'hn', 'https://selftest.invalid/h', '{}');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an hn source off Algolia was accepted'; END IF;

        INSERT INTO public.sources (slug, name, pillars, kind, url, official, config)
        VALUES ('selftest-045-t', 'Self-test 045 tech', ARRAY['tech'], 'page', 'https://selftest.invalid/045t',
                true, '{"mode": "links"}')
        RETURNING id INTO v_tsrc;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-045-h2', 'Self-test 045 home', ARRAY['home', 'sports'], 'rss', 'https://selftest.invalid/045h')
        RETURNING id INTO v_hsrc;

        -- ---------------------------------------------------- 2. stories CHECKs
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title) VALUES (ARRAY['tech', 'sports'], 'x');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a story in tech and sports was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['home'], 'x', 'tooling');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tech category on a Home story was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'x', 'deals');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a Home category on a tech story was accepted'; END IF;

        -- ---------------------------------------------------- the stories
        -- (tech, title, category, src, first seen hours ago, scored?)
        FOR i IN 1..7 LOOP
            INSERT INTO public.stories (pillars, title, category, first_seen_at, last_seen_at)
            VALUES (CASE WHEN i <= 3 THEN ARRAY['tech'] WHEN i <= 5 THEN ARRAY['home'] ELSE ARRAY['sports'] END,
                    'self-test 045 #' || i,
                    CASE i WHEN 1 THEN 'tooling' WHEN 2 THEN 'pricing_credits' WHEN 3 THEN 'other'
                           WHEN 4 THEN 'deals' WHEN 5 THEN 'deals' END,
                    now() - CASE WHEN i IN (3, 5) THEN interval '100 hours' ELSE interval '0' END,
                    now() - CASE WHEN i IN (3, 5) THEN interval '100 hours' ELSE interval '0' END)
            RETURNING id INTO v_item;
            CASE i WHEN 1 THEN v_t1 := v_item; WHEN 2 THEN v_t2 := v_item; WHEN 3 THEN v_told := v_item;
                   WHEN 4 THEN v_h1 := v_item; WHEN 5 THEN v_hold := v_item; ELSE v_s1 := v_item; END CASE;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id,
                                              published_precision, fetched_at)
            VALUES (CASE WHEN i <= 3 THEN v_tsrc ELSE v_hsrc END, 'selftest045:' || i,
                    'https://selftest.invalid/045/' || i, 'self-test 045 #' || i,
                    now() - CASE WHEN i IN (3, 5) THEN interval '100 hours' ELSE interval '0' END,
                    v_item, CASE WHEN i = 2 THEN 'date' ELSE 'time' END,
                    now() - CASE WHEN i IN (3, 5) THEN interval '100 hours' ELSE interval '0' END)
            RETURNING id INTO v_n;
            UPDATE public.stories SET lead_item_id = v_n WHERE id = v_item;
        END LOOP;
        INSERT INTO public.story_scores (story_id, pillar, score, components) VALUES
            (v_t1, 'tech', 40, '{}'), (v_told, 'tech', 40, '{}'),
            (v_h1, 'home', 40, '{}'), (v_hold, 'home', 40, '{}');
        INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
        VALUES (v_s1, 'sports', 40, '{}', NULL, false), (v_s1, 'home', 45, '{}', 1, true);

        -- ---------------------------------------------------- 3. the pillar guard
        v_ok := false;
        BEGIN
            INSERT INTO public.story_scores (story_id, pillar, score, components) VALUES (v_t1, 'home', 1, '{}');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a home score on a tech story was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
            VALUES (v_h1, 'tech', 1, '{}', 1, true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tech score on a Home story was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_h1, 'tech', 'love');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tech reaction on a Home story was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_t1, 'home', 'love');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a home reaction on a tech story was accepted'; END IF;
        -- A tech reaction on a tech story is fine.
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_t1, 'tech', 'love');

        -- ---------------------------------------------------- 4. feed_for_you: no cross-in
        -- Tech: its scored, unscored and 100-hour-old stories; nothing else.
        IF EXISTS (SELECT 1 FROM unnest(ARRAY[v_t1, v_t2, v_told]) x
                    WHERE x NOT IN (SELECT f.id FROM public.feed_for_you('tech', 100000, 0) f)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a tech story (scored, unscored or 100 h old) is missing from tech';
        END IF;
        IF EXISTS (SELECT 1 FROM public.feed_for_you('tech', 100000, 0) f WHERE NOT ('tech' = ANY (f.pillars))) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a non-tech story reached tech For you';
        END IF;
        IF (SELECT f.official FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_t2) IS NOT TRUE
           OR (SELECT f.score FROM public.feed_for_you('tech', 100000, 0) f WHERE f.id = v_t2) <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the unscored official story is not official with score 0';
        END IF;
        IF (SELECT array_agg(f.id) FROM public.feed_for_you('tech', 100000, 0, NULL, 'pricing_credits') f
             WHERE f.id IN (v_t1, v_t2, v_told)) IS DISTINCT FROM ARRAY[v_t2] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the pricing_credits chip';
        END IF;
        -- Home (default cap, cap 2, cap 0) and Sports: never a tech story.
        FOR v_n IN SELECT unnest(ARRAY[-1, 2, 0]) LOOP
            IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 100000, 0, NULL, NULL,
                                                         CASE WHEN v_n < 0 THEN NULL ELSE v_n END) f
                        WHERE 'tech' = ANY (f.pillars)) THEN
                RAISE EXCEPTION 'SELFTEST FAILED: a tech story reached Home (cap %)', v_n;
            END IF;
        END LOOP;
        IF EXISTS (SELECT 1 FROM public.feed_for_you('sports', 100000, 0) f WHERE 'tech' = ANY (f.pillars)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a tech story reached Sports';
        END IF;
        -- Home keeps its own: the 72-hour window and the sports cross-in (default cap).
        IF v_h1 NOT IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0) f)
           OR v_hold IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0) f)
           OR v_s1 NOT IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0) f)
           OR v_s1 IN (SELECT f.id FROM public.feed_for_you('home', 100000, 0, NULL, NULL, 0) f) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Home''s window or cross-in rules changed';
        END IF;

        -- ---------------------------------------------------- 5. the push cap
        FOR i IN 1..4 LOOP
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'self-test 045 push #' || i, 'pricing_credits')
            RETURNING id INTO v_item;
            v_tp := v_tp || v_item;
        END LOOP;
        IF public.tech_credit_push_room() <> 3 OR NOT public.tech_credit_push_allowed(v_tp[1]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: room % before any push (expected 3)', public.tech_credit_push_room();
        END IF;
        FOR i IN 1..3 LOOP
            INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_tp[i]);
        END LOOP;
        IF public.tech_credit_push_room() <> 0 OR public.tech_credit_push_allowed(v_tp[4]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: room % after 3 pushes (expected 0)', public.tech_credit_push_room();
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_tp[4]);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%cap of 3 a day%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a 4th push in a day was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.tech_credit_pushes (story_id) VALUES (v_tp[1]);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%already pushed%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: the same story was pushed twice'; END IF;
        -- A test push is not counted and may repeat.
        INSERT INTO public.tech_credit_pushes (story_id, is_test) VALUES (v_tp[4], true), (v_tp[4], true);
        IF public.tech_credit_push_room() <> 0
           OR (SELECT count(*) FROM public.tech_credit_pushes WHERE story_id = ANY (v_tp) AND NOT is_test) <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: test pushes changed the count';
        END IF;
        -- local_day is the trigger's, whatever the writer sent.
        IF EXISTS (SELECT 1 FROM public.tech_credit_pushes WHERE story_id = ANY (v_tp)
                    AND local_day <> public.owner_today()) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: local_day is not the owner''s today';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.tech_credit_pushes (story_id, is_test) VALUES (v_h1, true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%not a tech story%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a push for a Home story was accepted'; END IF;
        -- Append-only. tech_credit_pushes is referenced by no table, so the
        -- TRUNCATE names it alone (db/042's lesson).
        v_n := 0;
        BEGIN
            UPDATE public.tech_credit_pushes SET is_test = true WHERE story_id = v_tp[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.tech_credit_pushes WHERE story_id = v_tp[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.tech_credit_pushes;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of UPDATE, DELETE, TRUNCATE refused on tech_credit_pushes (expected 3)', v_n;
        END IF;

        -- ---------------------------------------------------- 6. context
        v_ctx := public.ctx_tech();
        IF NOT (v_ctx -> 'official_7d') @> jsonb_build_array(jsonb_build_object('title', 'self-test 045 #2'))
           OR (v_ctx -> 'official_7d') @> jsonb_build_array(jsonb_build_object('title', 'self-test 045 #4'))
           OR jsonb_typeof(v_ctx -> 'top_stories') IS DISTINCT FROM 'array'
           OR NOT (v_ctx -> 'loved_categories') @> '[{"category": "tooling"}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech shape or content: %', left(v_ctx::text, 400);
        END IF;
        v_ctx := public.ctx_tech_brief();
        IF v_ctx IS NULL
           OR NOT (v_ctx -> 'official_24h') @> jsonb_build_array(jsonb_build_object('title', 'self-test 045 #2'))
           OR (v_ctx -> 'official_24h') @> jsonb_build_array(jsonb_build_object('title', 'self-test 045 #3')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech_brief: %', left(coalesce(v_ctx::text, 'NULL'), 400);
        END IF;
        IF (SELECT count(*) FROM public.context_providers
             WHERE (key = 'tech' AND NOT brief_section AND enabled)
                OR (key = 'tech_brief' AND brief_section AND enabled)) <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the tech providers are not registered as expected';
        END IF;
        -- The other callers by name still resolve.
        IF jsonb_typeof(public.ctx_feed() -> 'top_stories') IS DISTINCT FROM 'array'
           OR jsonb_typeof(public.ctx_sports_feed() -> 'top_stories') IS DISTINCT FROM 'array'
           OR (public.context_snapshot() -> 'tech') ? 'error' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_feed, ctx_sports_feed or the snapshot broke';
        END IF;

        -- ---------------------------------------------------- 7. views
        IF (SELECT v.source_official FROM public.v_feed_stories v WHERE v.id = v_t2) IS NOT TRUE
           OR (SELECT v.source_official FROM public.v_feed_stories v WHERE v.id = v_h1) IS NOT FALSE
           OR (SELECT s.last_item_at FROM public.v_feed_source_stats s WHERE s.source_id = v_tsrc) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_feed_stories.source_official or v_feed_source_stats.last_item_at';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '045 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('045_tech_hub')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the new CHECKs: sources pillars/kind/kind_url/config, stories
--       pillars/category/category_pillar, content_items published_precision,
--       and the four pillar CHECKs list tech (10 rows)
--   (b) tech_credit_pushes: RLS on, 1 policy, anon nothing, authenticated
--       SELECT only; 0 rows
--   (c) feed_for_you has 27 output columns ending category, official; the
--       readonly role executes it and ctx_tech / ctx_tech_brief / the room
--       function; anon executes none
--   (d) the providers: tech sort 50 brief_section false; tech_brief sort 50
--       brief_section true
--   (e) seed_interests.feed.tech: love 8, not interested 4; home 10 / 7 and
--       sports 8 / 2 unchanged
--   (f) _e2e_tech: inactive, is_test, official, pillars {tech}
--   (g) tech_credit_push_room() = 3; ctx_tech_brief() IS NULL (no tech
--       source has run yet)
--   (h) the migration_log row (and the NOTICE "045 self-test passed")
-- ---------------------------------------------------------------------------

SELECT conrelid::regclass AS tbl, conname
  FROM pg_constraint
 WHERE conname IN ('sources_pillars_check', 'sources_kind_check', 'sources_kind_url', 'sources_config_shape',
                   'stories_pillars_check', 'stories_category_check', 'stories_category_pillar',
                   'content_items_published_precision')
    OR (conname LIKE '%_pillar_check' AND pg_get_constraintdef(oid) LIKE '%tech%')
 ORDER BY 1, 2;                                                                           -- (a)

SELECT c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = 'tech_credit_pushes') AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any,
       has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE') AS auth_write,
       (SELECT count(*) FROM tech_credit_pushes) AS rows
  FROM pg_class c WHERE c.oid = 'public.tech_credit_pushes'::regclass;                    -- (b)

SELECT array_length(p.proargnames, 1) - p.pronargs AS out_columns,
       p.proargnames[array_length(p.proargnames, 1) - 1:] AS last_two,
       has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS readonly_exec,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_exec,
       has_function_privilege('valemont_readonly', 'public.ctx_tech()', 'EXECUTE') AS readonly_ctx_tech,
       has_function_privilege('valemont_readonly', 'public.ctx_tech_brief()', 'EXECUTE') AS readonly_ctx_brief,
       has_function_privilege('valemont_readonly', 'public.tech_credit_push_room()', 'EXECUTE') AS readonly_room,
       has_function_privilege('anon', 'public.ctx_tech()', 'EXECUTE') AS anon_ctx_tech
  FROM pg_proc p
 WHERE p.oid = 'public.feed_for_you(text, integer, integer, text, text, integer)'::regprocedure; -- (c)

SELECT key, label, sort, brief_section, enabled FROM context_providers
 WHERE key IN ('tech', 'tech_brief') ORDER BY key;                                        -- (d)

SELECT jsonb_array_length(seed_interests #> '{feed,tech,love}') AS tech_love,
       jsonb_array_length(seed_interests #> '{feed,tech,not_interested}') AS tech_ni,
       jsonb_array_length(seed_interests #> '{feed,home,love}') AS home_love,
       jsonb_array_length(seed_interests #> '{feed,home,not_interested}') AS home_ni,
       jsonb_array_length(seed_interests #> '{feed,sports,love}') AS sports_love,
       jsonb_array_length(seed_interests #> '{feed,sports,not_interested}') AS sports_ni
  FROM app_settings;                                                                      -- (e)

SELECT slug, active, is_test, official, pillars FROM sources WHERE slug = '_e2e_tech';    -- (f)

SELECT tech_credit_push_room() AS room, ctx_tech_brief() IS NULL AS brief_silent;         -- (g)

SELECT name, applied_at FROM migration_log WHERE name = '045_tech_hub';                   -- (h)
