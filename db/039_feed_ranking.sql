-- 039_feed_ranking.sql — reactions, interest profiles, follows, scores, the
-- For you read path and the feed context provider (Chat 3, Phase 2).
--
-- Design: docs/feed_ranking.md (approved by the owner 2026-10-11, with the
-- changes recorded in its §0). Every number below that also lives in
-- workers/feed/rank.py is pinned to it by a test (tests/test_feed_rank.py).
--
-- What it holds:
--   feed_reactions        the owner's taps: love | neutral | not_interested |
--                         open | undo. APPEND-ONLY (trigger refuses UPDATE
--                         and DELETE; TRUNCATE refused). Owner INSERT and
--                         SELECT only.
--   v_feed_reaction_latest
--                         one row per (story, pillar): the latest reaction
--                         that is not 'open' ('undo' = no reaction), plus
--                         whether and when the story was opened.
--   interest_profiles     one row per rebuild per pillar (worker-written,
--                         owner-read): the 7-day mean vector, the love and
--                         not-interested profiles, learned weights, counts.
--   entities, entity_links, follows
--                         who and what the owner follows, by tier. Seeded.
--   story_scores          the worker's time-free score per (story, pillar);
--                         the latest row wins (v_story_scores_latest).
--                         Recency is applied at read time, never stored.
--   stories.why_it_matters (+ why_generated_at, why_ai_usage_id)
--   feed_for_you(pillar, limit, offset)
--                         the For you order: score minus recency, the capped
--                         Alabama floor, and at most 5 cross-pillar (tier-1
--                         sports) stories on page one.
--   ctx_feed()            the context provider: For you's top 3, in the
--                         Morning Brief (brief_section = true) and Wags.
--   app_settings.seed_interests -> 'feed'
--                         the owner's seed phrases (owner, 2026-10-11).
--
-- Never pruned: a story with any reaction. feed_reactions.story_id references
-- stories ON DELETE RESTRICT, so the database refuses to delete it whatever
-- the prune query says.
--
-- Test rows: a reaction is a test reaction exactly when its story is a test
-- story (stories.is_test); no column of its own. v_feed_stories never shows
-- a test story, so feed_for_you and ctx_feed never do either.
--
-- Access: RLS on every table; anon nothing; the owner reads everything and
-- inserts reactions; only the worker (DATABASE_URL) writes the rest.
-- valemont_readonly reads every table (db/018 defaults) and is granted the
-- views and the two readers explicitly (db/028's rule).
--
-- A self-test runs on paste (section 9), rolled back: no row survives. A wrong
-- result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/020 (app_settings, is_owner, reject_mutation,
-- reject_truncate), db/029 (context_providers, ctx_trim), db/032-035 (feed).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run (IF NOT EXISTS; seeds ON CONFLICT DO NOTHING).

-- ===================================================================== 1
-- feed_reactions

CREATE TABLE IF NOT EXISTS feed_reactions (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    UUID         NOT NULL DEFAULT auth.uid()
                             REFERENCES auth.users (id) ON DELETE RESTRICT,
    -- RESTRICT: nothing may delete a story the owner reacted to.
    story_id    BIGINT       NOT NULL REFERENCES stories (id) ON DELETE RESTRICT,
    -- The feed the tap came from (a tier-1 sports story tapped on Home is 'home').
    pillar      TEXT         NOT NULL CHECK (pillar IN ('home', 'sports')),
    reaction    TEXT         NOT NULL
                             CHECK (reaction IN ('love', 'neutral', 'not_interested', 'open', 'undo')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feed_reactions_story ON feed_reactions (story_id, pillar, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS feed_reactions_created ON feed_reactions (created_at);

DROP TRIGGER IF EXISTS feed_reactions_immutable ON feed_reactions;
CREATE TRIGGER feed_reactions_immutable
    BEFORE UPDATE OR DELETE ON feed_reactions
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS feed_reactions_no_truncate ON feed_reactions;
CREATE TRIGGER feed_reactions_no_truncate
    BEFORE TRUNCATE ON feed_reactions
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- One row per (story, pillar). `reaction` is the latest non-open reaction
-- (NULL when there is only opens; 'undo' means the owner took it back, so
-- the card shows again). Ties in created_at (one transaction) break by id.
CREATE OR REPLACE VIEW v_feed_reaction_latest
WITH (security_invoker = true)
AS
SELECT r.story_id, r.pillar,
       (array_agg(r.reaction ORDER BY r.created_at DESC, r.id DESC)
            FILTER (WHERE r.reaction <> 'open'))[1] AS reaction,
       max(r.created_at) FILTER (WHERE r.reaction <> 'open') AS reacted_at,
       bool_or(r.reaction = 'open') AS opened,
       max(r.created_at) FILTER (WHERE r.reaction = 'open') AS last_opened_at,
       st.is_test
  FROM public.feed_reactions r
  JOIN public.stories st ON st.id = r.story_id
 GROUP BY r.story_id, r.pillar, st.is_test;

-- ===================================================================== 2
-- interest_profiles (one row per rebuild per pillar; the latest wins)

CREATE TABLE IF NOT EXISTS interest_profiles (
    id                BIGSERIAL    PRIMARY KEY,
    pillar            TEXT         NOT NULL CHECK (pillar IN ('home', 'sports')),
    -- Mean of the pillar's candidate story vectors over 7 days: subtracted
    -- before every cosine (gte-small is anisotropic).
    mean_vec          extensions.halfvec(384) NOT NULL,
    -- NULL when the profile has no signal (no seeds and no reactions).
    love_vec          extensions.halfvec(384),
    ni_vec            extensions.halfvec(384),
    -- {name: weight}, bounded by the worker (rank.py B_*), only names with evidence.
    topic_weights     JSONB        NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(topic_weights) = 'object'),
    source_weights    JSONB        NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_weights) = 'object'),
    entity_weights    JSONB        NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(entity_weights) = 'object'),
    -- Stories with a love or not_interested signal.
    reaction_count    INTEGER      NOT NULL DEFAULT 0 CHECK (reaction_count >= 0),
    love_count        INTEGER      NOT NULL DEFAULT 0 CHECK (love_count >= 0),
    ni_count          INTEGER      NOT NULL DEFAULT 0 CHECK (ni_count >= 0),
    open_count        INTEGER      NOT NULL DEFAULT 0 CHECK (open_count >= 0),
    neutral_count     INTEGER      NOT NULL DEFAULT 0 CHECK (neutral_count >= 0),
    -- k / (k + n) at build time, per profile.
    seed_weight_love  REAL         NOT NULL CHECK (seed_weight_love BETWEEN 0 AND 1),
    seed_weight_ni    REAL         NOT NULL CHECK (seed_weight_ni BETWEEN 0 AND 1),
    -- Candidate stories the mean was taken over.
    mean_n            INTEGER      NOT NULL CHECK (mean_n >= 0),
    built_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (reaction_count = love_count + ni_count)
);

CREATE INDEX IF NOT EXISTS interest_profiles_latest ON interest_profiles (pillar, built_at DESC, id DESC);

-- ===================================================================== 3
-- entities, entity_links, follows

CREATE TABLE IF NOT EXISTS entities (
    id          BIGSERIAL    PRIMARY KEY,
    kind        TEXT         NOT NULL CHECK (kind IN ('person', 'team', 'program', 'league', 'tour', 'company')),
    name        TEXT         NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 120),
    -- Lowercase, matched as whole words against the title and Haiku's entities.
    aliases     TEXT[]       NOT NULL DEFAULT '{}' CHECK (lower(aliases::text) = aliases::text),
    -- Set: an alias counts only when the story's sport equals it ("alabama"
    -- in state news, "hamilton" the musical, "georgia" the state never match).
    sport       TEXT         CHECK (sport IN ('cfb', 'nfl', 'nba', 'f1', 'golf')),
    -- Matches every story of its sport, with no alias (NFL, NBA, F1, golf).
    sport_wide  BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (NOT sport_wide OR sport IS NOT NULL),
    CHECK (sport_wide OR cardinality(aliases) >= 1)
);

-- A trade or a move is a NEW row with a later valid_from; rows are not edited.
CREATE TABLE IF NOT EXISTS entity_links (
    id                  BIGSERIAL    PRIMARY KEY,
    from_entity_id      BIGINT       NOT NULL REFERENCES entities (id) ON DELETE RESTRICT,
    to_entity_id        BIGINT       NOT NULL REFERENCES entities (id) ON DELETE RESTRICT,
    kind                TEXT         NOT NULL CHECK (kind IN ('plays_for', 'drives_for', 'member_of')),
    valid_from          DATE         NOT NULL,
    confirmed_by_owner  BOOLEAN      NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (from_entity_id <> to_entity_id),
    CONSTRAINT entity_links_once UNIQUE (from_entity_id, to_entity_id, kind, valid_from)
);

CREATE TABLE IF NOT EXISTS follows (
    id          BIGSERIAL    PRIMARY KEY,
    entity_id   BIGINT       NOT NULL REFERENCES entities (id) ON DELETE RESTRICT,
    tier        SMALLINT     NOT NULL CHECK (tier BETWEEN 1 AND 3),
    -- NULL = both pillars.
    pillar      TEXT         CHECK (pillar IN ('home', 'sports')),
    -- The Alabama floor (docs/feed_ranking.md §2.7). Tier 1 only.
    floor       BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (NOT floor OR tier = 1),
    CONSTRAINT follows_once UNIQUE NULLS NOT DISTINCT (entity_id, pillar)
);

-- ---- seeds (owner, 2026-10-11)

INSERT INTO entities (kind, name, aliases, sport, sport_wide) VALUES
    ('program', 'Alabama Crimson Tide', ARRAY['alabama', 'crimson tide', 'bama', 'alabama football'], 'cfb', false),
    ('person',  'LeBron James',         ARRAY['lebron', 'lebron james'],                             'nba', false),
    ('team',    'Philadelphia 76ers',   ARRAY['76ers', 'sixers', 'philadelphia 76ers'],              'nba', false),
    ('person',  'Lewis Hamilton',       ARRAY['lewis hamilton', 'hamilton'],                         'f1',  false),
    ('person',  'Charles Leclerc',      ARRAY['leclerc', 'charles leclerc'],                         'f1',  false),
    ('team',    'Ferrari',              ARRAY['ferrari', 'scuderia ferrari'],                        'f1',  false),
    ('person',  'Scottie Scheffler',    ARRAY['scheffler', 'scottie scheffler'],                     'golf', false),
    ('person',  'Brooks Koepka',        ARRAY['koepka', 'brooks koepka'],                            'golf', false),
    ('company', 'Kalshi',               ARRAY['kalshi'],                                             NULL,  false),
    -- Tier 3 by league. A sport-wide league matches every story of its sport.
    ('league',  'NFL',                  '{}', 'nfl',  true),
    ('league',  'NBA',                  '{}', 'nba',  true),
    ('league',  'Formula 1',            '{}', 'f1',   true),
    ('tour',    'Golf',                 '{}', 'golf', true),
    -- Power 4 conferences (2026 football membership), matched by program
    -- name. Alabama is left out: its own row is tier 1.
    ('league',  'SEC', ARRAY['sec', 'arkansas', 'auburn', 'florida gators', 'georgia', 'kentucky', 'lsu',
                             'mississippi state', 'missouri', 'oklahoma', 'ole miss', 'south carolina',
                             'tennessee', 'texas', 'texas a&m', 'vanderbilt'], 'cfb', false),
    ('league',  'Big Ten', ARRAY['big ten', 'illinois', 'indiana', 'iowa', 'maryland', 'michigan',
                                 'michigan state', 'minnesota', 'nebraska', 'northwestern', 'ohio state',
                                 'oregon', 'penn state', 'purdue', 'rutgers', 'ucla', 'usc', 'washington',
                                 'wisconsin'], 'cfb', false),
    ('league',  'Big 12', ARRAY['big 12', 'arizona', 'arizona state', 'baylor', 'byu', 'cincinnati',
                                'colorado', 'houston', 'iowa state', 'kansas', 'kansas state',
                                'oklahoma state', 'tcu', 'texas tech', 'ucf', 'utah', 'west virginia'], 'cfb', false),
    ('league',  'ACC', ARRAY['acc', 'boston college', 'cal', 'clemson', 'duke', 'florida state',
                             'georgia tech', 'louisville', 'miami hurricanes', 'nc state', 'north carolina',
                             'pitt', 'smu', 'stanford', 'syracuse', 'virginia', 'virginia tech',
                             'wake forest'], 'cfb', false),
    ('program', 'Notre Dame', ARRAY['notre dame'], 'cfb', false)
ON CONFLICT (name) DO NOTHING;

INSERT INTO follows (entity_id, tier, pillar, floor)
SELECT e.id, f.tier, f.pillar, f.floor
  FROM (VALUES
        ('Alabama Crimson Tide', 1, NULL::text, true),
        ('LeBron James',         1, NULL,       false),
        ('Philadelphia 76ers',   2, 'sports',   false),
        ('Lewis Hamilton',       1, NULL,       false),
        ('Charles Leclerc',      1, NULL,       false),
        ('Ferrari',              2, 'sports',   false),
        ('Scottie Scheffler',    1, NULL,       false),
        ('Brooks Koepka',        1, NULL,       false),
        ('Kalshi',               1, 'home',     false),
        ('NFL',                  3, 'sports',   false),
        ('NBA',                  3, 'sports',   false),
        ('Formula 1',            3, 'sports',   false),
        ('Golf',                 3, 'sports',   false),
        ('SEC',                  3, 'sports',   false),
        ('Big Ten',              3, 'sports',   false),
        ('Big 12',               3, 'sports',   false),
        ('ACC',                  3, 'sports',   false),
        ('Notre Dame',           3, 'sports',   false)
       ) AS f (name, tier, pillar, floor)
  JOIN entities e ON e.name = f.name
ON CONFLICT ON CONSTRAINT follows_once DO NOTHING;

INSERT INTO entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
SELECT a.id, b.id, l.kind, l.valid_from::date, true
  FROM (VALUES
        ('LeBron James',    'Philadelphia 76ers', 'plays_for',  '2026-07-01'),
        ('Lewis Hamilton',  'Ferrari',            'drives_for', '2026-01-01'),
        ('Charles Leclerc', 'Ferrari',            'drives_for', '2026-01-01')
       ) AS l (from_name, to_name, kind, valid_from)
  JOIN entities a ON a.name = l.from_name
  JOIN entities b ON b.name = l.to_name
ON CONFLICT ON CONSTRAINT entity_links_once DO NOTHING;

-- ===================================================================== 4
-- stories: why it matters

ALTER TABLE stories
    ADD COLUMN IF NOT EXISTS why_it_matters TEXT
        CONSTRAINT stories_why_length CHECK (length(why_it_matters) <= 200),
    -- Set whenever the story was asked, even when the answer was NULL, so it
    -- is asked once.
    ADD COLUMN IF NOT EXISTS why_generated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS why_ai_usage_id BIGINT REFERENCES ai_usage (id) ON DELETE RESTRICT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stories_why_asked') THEN
        ALTER TABLE stories ADD CONSTRAINT stories_why_asked
            CHECK (why_it_matters IS NULL OR why_generated_at IS NOT NULL);
    END IF;
    -- A real line names the call that wrote it (as summary does, db/032).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stories_why_costed') THEN
        ALTER TABLE stories ADD CONSTRAINT stories_why_costed
            CHECK (why_it_matters IS NULL OR is_test OR why_ai_usage_id IS NOT NULL);
    END IF;
END
$$;

-- v_feed_stories (db/032) gains why_it_matters, appended so CREATE OR REPLACE
-- keeps every existing column in place.
CREATE OR REPLACE VIEW v_feed_stories
WITH (security_invoker = true)
AS
SELECT st.id, st.pillars, st.sport, st.title, st.summary, st.entities, st.topics,
       st.item_count, st.first_seen_at, st.last_seen_at,
       i.id AS lead_item_id, i.url AS lead_url, i.published_at AS lead_published_at,
       s.slug AS source_slug, s.name AS source_name,
       st.why_it_matters
  FROM public.stories st
  JOIN public.content_items i ON i.id = st.lead_item_id
  JOIN public.sources s ON s.id = i.source_id
 WHERE NOT st.is_test
   AND st.filtered_reason IS NULL
   AND i.url IS NOT NULL;

-- ===================================================================== 5
-- story_scores

CREATE TABLE IF NOT EXISTS story_scores (
    id            BIGSERIAL    PRIMARY KEY,
    story_id      BIGINT       NOT NULL REFERENCES stories (id) ON DELETE CASCADE,
    pillar        TEXT         NOT NULL CHECK (pillar IN ('home', 'sports')),
    -- The time-free base (rank.py): W_AFF*affinity + follow + source + topic
    -- + entity. Recency is applied by feed_for_you at read time.
    score         REAL         NOT NULL CHECK (score BETWEEN -50 AND 50),
    -- Every term, for /settings/feed and the audit.
    components    JSONB        NOT NULL CHECK (jsonb_typeof(components) = 'object'),
    -- Best matched follow tier in this pillar; NULL = none.
    follow_tier   SMALLINT     CHECK (follow_tier BETWEEN 1 AND 3),
    -- Matched a floor follow (Alabama).
    floor         BOOLEAN      NOT NULL DEFAULT false,
    -- Not in this pillar; scored here only because it matched a tier-1
    -- follow (decision 1: tier-1 sports stories on Home).
    cross_pillar  BOOLEAN      NOT NULL DEFAULT false,
    profile_id    BIGINT       REFERENCES interest_profiles (id) ON DELETE SET NULL,
    scored_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (NOT floor OR follow_tier = 1),
    CHECK (NOT cross_pillar OR follow_tier = 1)
);

CREATE INDEX IF NOT EXISTS story_scores_latest ON story_scores (story_id, pillar, scored_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS story_scores_scored ON story_scores (scored_at);

CREATE OR REPLACE VIEW v_story_scores_latest
WITH (security_invoker = true)
AS
SELECT DISTINCT ON (s.story_id, s.pillar)
       s.id, s.story_id, s.pillar, s.score, s.components, s.follow_tier, s.floor,
       s.cross_pillar, s.profile_id, s.scored_at
  FROM public.story_scores s
 ORDER BY s.story_id, s.pillar, s.scored_at DESC, s.id DESC;

-- ===================================================================== 6
-- feed_for_you: the For you order.
--
--   rank = score - ln 2 * age_h / HALF_LIFE_H      (score is the stored base)
--
-- Candidates: real, kept, linked stories (v_feed_stories) first seen in the
-- last WINDOW_H hours, with a score for this pillar, that are in the pillar
-- or scored here as cross-pillar; minus stories whose latest reaction here is
-- neutral or not_interested.
--
-- Page one (PAGE_SIZE stories):
--   * the floor: the best FLOOR_MAX floor stories under FLOOR_MAX_AGE_H rank
--     at least as high as the FLOOR_POSITION-th best story without the floor
--     flag (they keep their own order);
--   * the cross cap: at most CROSS_CAP cross-pillar stories; floored ones
--     always keep their place, the rest are the best by rank. Cross-pillar
--     stories over the cap move below page one, in rank order.
-- Later pages: everything else by rank.
--
-- The constants are literals here and in workers/feed/rank.py; a test pins
-- one to the other.

CREATE OR REPLACE FUNCTION public.feed_for_you(p_pillar text, p_limit integer DEFAULT 30,
                                               p_offset integer DEFAULT 0)
RETURNS TABLE (
    id bigint, pos bigint, rank double precision, score real, follow_tier smallint,
    floored boolean, cross_pillar boolean, components jsonb, reaction text,
    pillars text[], sport text, title text, summary text, why_it_matters text,
    entities text[], topics text[], item_count integer, first_seen_at timestamptz,
    last_seen_at timestamptz, lead_item_id bigint, lead_url text,
    lead_published_at timestamptz, source_slug text, source_name text
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
               30           AS page_size      -- rank.PAGE_SIZE
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
               greatest(extract(epoch FROM (now() - v.lead_published_at)) / 3600.0, 0) AS age_h
          FROM public.v_feed_stories v
          JOIN public.v_story_scores_latest sc ON sc.story_id = v.id AND sc.pillar = p_pillar
          LEFT JOIN public.v_feed_reaction_latest r ON r.story_id = v.id AND r.pillar = p_pillar
         CROSS JOIN k
         WHERE v.first_seen_at > now() - make_interval(hours => k.window_h)
           AND (p_pillar = ANY (v.pillars) OR sc.cross_pillar)
           AND coalesce(r.reaction, '') NOT IN ('neutral', 'not_interested')
    ),
    base AS (
        SELECT c.*, c.c_score - ln(2.0) * c.age_h / k.half_life_h AS r
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
    allowed AS (
        SELECT e.sid FROM eff e WHERE e.c_cross AND e.in_f
        UNION ALL
        (SELECT e.sid FROM eff e
          WHERE e.c_cross AND NOT e.in_f
          ORDER BY e.e DESC, e.sid DESC
          LIMIT greatest((SELECT cross_cap FROM k)
                         - (SELECT count(*) FROM eff x WHERE x.c_cross AND x.in_f), 0))
    ),
    page1 AS (
        SELECT e.sid FROM eff e
         WHERE NOT e.c_cross OR e.sid IN (SELECT sid FROM allowed)
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
           o.c_item_count, o.c_first, o.c_last, o.c_lead, o.c_url, o.c_pub, o.c_slug, o.c_source
      FROM ordered o
     ORDER BY o.n
     LIMIT p_limit OFFSET p_offset;
$$;

-- ===================================================================== 7
-- ctx_feed: For you's top 3 (Home), for the Morning Brief and Wags.

CREATE OR REPLACE FUNCTION public.ctx_feed()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH top AS (
        SELECT * FROM public.feed_for_you('home', 3, 0)
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Top of the For you feed on Home, ranked by the owner''s learned interest profile.',
        'top_stories', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(t.title, 160),
                       'source', t.source_name,
                       'summary', public.ctx_trim(t.summary, 260),
                       'why_it_matters', t.why_it_matters,
                       'loved', CASE WHEN t.reaction = 'love' THEN true END))
                     ORDER BY t.pos)
              FROM top t), '[]'::jsonb),
        'note', CASE WHEN NOT EXISTS (SELECT 1 FROM top) THEN 'No ranked stories yet.' END
    ));
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('feed', 'Feed', 'public.ctx_feed()'::regprocedure, true, 40)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 8
-- Seed phrases (owner, 2026-10-11), set once: a later edit by the owner is
-- never overwritten by a re-run. Sports seeds are left empty for Phase 3
-- (Sports News); the sports profile learns from reactions and follows.

UPDATE app_settings
   SET seed_interests = seed_interests || jsonb_build_object('feed', jsonb_build_object(
           'home', jsonb_build_object(
               'love', jsonb_build_array(
                   'experiential hospitality: beach clubs, members'' clubs, waterfront venues, hotels',
                   'yachts and marine: builds, sales, launches, marinas',
                   'Miami and Biscayne Bay development; Florida coastal permitting',
                   'venture capital, family offices, fund raises, notable deals',
                   'sports business: team ownership, stakes, valuations, media rights, athlete businesses',
                   'music business, artist management, creator economy deals',
                   'film and TV dealmaking and talent deals',
                   'luxury and lifestyle brands',
                   'prediction markets and sports-betting regulation, Kalshi'),
               'not_interested', jsonb_build_array(
                   'celebrity gossip and relationships',
                   'SEO listicles and filler',
                   'partisan horse-race and culture-war politics (policy only when it moves money)',
                   'generic "AI will change everything" takes',
                   'crypto price chatter',
                   'plain box-score recaps with no storyline')),
           'sports', jsonb_build_object('love', '[]'::jsonb, 'not_interested', '[]'::jsonb))),
       updated_at = now()
 WHERE NOT (seed_interests ? 'feed');

-- ===================================================================== 9
-- Access.

ALTER TABLE feed_reactions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE interest_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities          ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_links      ENABLE ROW LEVEL SECURITY;
ALTER TABLE follows           ENABLE ROW LEVEL SECURITY;
ALTER TABLE story_scores      ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON feed_reactions, interest_profiles, entities, entity_links, follows, story_scores,
              v_feed_reaction_latest, v_story_scores_latest
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE feed_reactions_id_seq, interest_profiles_id_seq, entities_id_seq,
                       entity_links_id_seq, follows_id_seq, story_scores_id_seq
    FROM anon, authenticated, PUBLIC;

-- The owner reads all six; only feed_reactions takes an insert, as the owner,
-- for the owner. No UPDATE or DELETE grant or policy anywhere here.
GRANT SELECT ON feed_reactions, interest_profiles, entities, entity_links, follows, story_scores
    TO authenticated;
GRANT INSERT ON feed_reactions TO authenticated;
GRANT USAGE ON SEQUENCE feed_reactions_id_seq TO authenticated;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['feed_reactions', 'interest_profiles', 'entities', 'entity_links',
                             'follows', 'story_scores'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_owner_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING ((SELECT public.is_owner()))',
            t || '_owner_read', t);
    END LOOP;
END
$$;

DROP POLICY IF EXISTS feed_reactions_owner_insert ON feed_reactions;
CREATE POLICY feed_reactions_owner_insert ON feed_reactions
    FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));

GRANT SELECT ON v_feed_reaction_latest, v_story_scores_latest TO authenticated, valemont_readonly;
-- v_feed_stories was replaced above; its grants survive CREATE OR REPLACE,
-- restated here so the file says what it expects.
GRANT SELECT ON v_feed_stories TO authenticated, valemont_readonly;

REVOKE ALL ON FUNCTION public.feed_for_you(text, integer, integer) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_feed() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.feed_for_you(text, integer, integer) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_feed() TO authenticated, valemont_readonly;

-- ===================================================================== 10
-- Self-test, run on paste. Everything happens inside a subtransaction rolled
-- back at the end ('selftest-rollback'), so no row survives; only sequence
-- values are consumed. A wrong result raises SELFTEST FAILED and nothing in
-- this file is applied.
--
-- Its stories are REAL (is_test = false, from a throwaway real source) so
-- they reach v_feed_stories, with scores of 20 to 48: a real score stays within
-- about +-10 (docs/feed_ranking.md 2.5), so real scored stories cannot change
-- the positions it checks.

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_src     bigint;
    v_test_src bigint;
    v_story   bigint;
    v_item    bigint;
    v_ids     bigint[] := '{}';
    v_floor   bigint[] := '{}';
    v_cross   bigint[] := '{}';
    v_target  bigint;
    v_test_story bigint;
    v_refused int := 0;
    v_n       bigint;
    v_r       record;
    v_ctx     jsonb;
    i         int;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-039', 'Self-test 039', ARRAY['home', 'sports'], 'rss', 'https://selftest.invalid/039')
        RETURNING id INTO v_src;
        SELECT id INTO v_test_src FROM public.sources WHERE slug = '_e2e' AND is_test;

        -- 35 stories: 22 plain home (scores ~40 down), 5 home floor stories
        -- (score ~18), 8 sports-only cross-pillar stories (score ~42).
        FOR i IN 1..35 LOOP
            INSERT INTO public.stories (pillars, title)
            VALUES (CASE WHEN i > 27 THEN ARRAY['sports'] ELSE ARRAY['home'] END, 'self-test 039 #' || i)
            RETURNING id INTO v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest039:' || i, 'https://selftest.invalid/039/' || i,
                    'self-test 039 #' || i, now(), v_story)
            RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
            v_ids := v_ids || v_story;
            IF i <= 22 THEN
                INSERT INTO public.story_scores (story_id, pillar, score, components)
                VALUES (v_story, 'home', 40 - i * 0.1, '{}'::jsonb);
            ELSIF i <= 27 THEN
                INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, floor)
                VALUES (v_story, 'home', 20 - i * 0.1, '{}'::jsonb, 1, true);
                v_floor := v_floor || v_story;
            ELSE
                INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
                VALUES (v_story, 'home', 45 - i * 0.1, '{}'::jsonb, 1, true);
                v_cross := v_cross || v_story;
            END IF;
        END LOOP;

        -- 1. The latest score row wins.
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_ids[1], 'home', 39.95, '{"rescored": true}'::jsonb);
        IF (SELECT score FROM public.v_story_scores_latest WHERE story_id = v_ids[1] AND pillar = 'home')
           IS DISTINCT FROM 39.95::real THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_story_scores_latest did not return the newest row';
        END IF;

        -- 2. The cross cap: exactly 5 cross-pillar stories on page one, the
        --    best five; the other three below every page-one story.
        SELECT count(*) INTO v_n FROM public.feed_for_you('home', 30, 0) f
         WHERE f.id = ANY (v_cross);
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % cross-pillar stories on page one (cap 5)', v_n;
        END IF;
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 30, 0) f
                    WHERE f.id = ANY (v_cross[6:8])) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a cross-pillar story over the cap is on page one';
        END IF;

        -- 3. The floor: exactly the best 3 floor stories are lifted, all into
        --    the top 9 (6th-best other story + 3); the other 2 are not.
        SELECT count(*) INTO v_n FROM public.feed_for_you('home', 1000, 0) f
         WHERE f.floored;
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stories floored (cap 3)', v_n;
        END IF;
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f
                    WHERE f.id = ANY (v_floor[1:3]) AND (f.pos > 9 OR NOT f.floored))
           OR EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f
                       WHERE f.id = ANY (v_floor[4:5]) AND f.floored) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the floor lifted the wrong stories or not into the top 9';
        END IF;
        IF (SELECT array_agg(f.id ORDER BY f.pos) FROM public.feed_for_you('home', 1000, 0) f
             WHERE f.id = ANY (v_floor[1:3])) IS DISTINCT FROM v_floor[1:3] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: floored stories lost their own order';
        END IF;

        -- 4. Recency: a story published 36 h ago (two half-lives) with score
        --    41 must rank at 41 - 2 ln 2 = 39.614.
        INSERT INTO public.stories (pillars, title) VALUES (ARRAY['home'], 'self-test 039 old')
        RETURNING id INTO v_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest039:old', 'https://selftest.invalid/039/old', 'self-test 039 old',
                now() - interval '36 hours', v_story)
        RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_story, 'home', 41, '{}'::jsonb);
        SELECT f.rank INTO v_r FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_story;
        IF abs(v_r.rank - (41 - 2 * ln(2.0))) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: rank % for score 41 at 36 h; expected %', v_r.rank, 41 - 2 * ln(2.0);
        END IF;

        -- 5. Reactions: not_interested and neutral remove a story; undo
        --    brings it back; an open never overrides; love marks it.
        v_target := v_ids[5];
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_target, 'home', 'not_interested');
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_target) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a not_interested story is still in For you';
        END IF;
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_target, 'home', 'open');
        IF (SELECT reaction FROM public.v_feed_reaction_latest WHERE story_id = v_target AND pillar = 'home')
           IS DISTINCT FROM 'not_interested'
           OR NOT (SELECT opened FROM public.v_feed_reaction_latest WHERE story_id = v_target AND pillar = 'home') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an open replaced the latest reaction, or was not recorded';
        END IF;
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_target, 'home', 'undo');
        IF NOT EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f
                        WHERE f.id = v_target AND f.reaction IS NULL) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: undo did not bring the story back unmarked';
        END IF;
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_ids[6], 'home', 'neutral');
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_ids[6]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a neutral story is still in For you';
        END IF;
        -- A reaction in the other pillar does not touch this one.
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_ids[7], 'sports', 'not_interested');
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
        VALUES (v_owner, v_ids[8], 'home', 'love');
        IF NOT EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_ids[7])
           OR NOT EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f
                           WHERE f.id = v_ids[8] AND f.reaction = 'love') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: pillar isolation or the love mark';
        END IF;

        -- 6. Append-only: UPDATE, DELETE and TRUNCATE are refused.
        BEGIN
            UPDATE public.feed_reactions SET reaction = 'love' WHERE story_id = v_target;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            DELETE FROM public.feed_reactions WHERE story_id = v_target;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            TRUNCATE public.feed_reactions;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 3 mutations of feed_reactions were refused', v_refused;
        END IF;

        -- 7. A reacted story cannot be deleted (the 90-day prune's backstop);
        --    an unreacted one can.
        v_refused := 0;
        BEGIN
            DELETE FROM public.stories WHERE id = v_target;
        EXCEPTION WHEN foreign_key_violation THEN v_refused := 1;
        END;
        IF v_refused <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a reacted story was deleted';
        END IF;
        DELETE FROM public.stories WHERE id = v_ids[22];
        IF EXISTS (SELECT 1 FROM public.story_scores WHERE story_id = v_ids[22]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: story_scores did not cascade with its story';
        END IF;

        -- 8. Test stories are invisible: a test story with the top score
        --    never reaches feed_for_you or ctx_feed.
        INSERT INTO public.stories (pillars, title, is_test) VALUES (ARRAY['home'], 'self-test 039 test', true)
        RETURNING id INTO v_test_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id, is_test)
        VALUES (v_test_src, 'selftest039:test', 'https://selftest.invalid/039/test', 'self-test 039 test',
                now(), v_test_story, true)
        RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_test_story;
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_test_story, 'home', 48, '{}'::jsonb);
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_test_story) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test story reached feed_for_you';
        END IF;

        -- 9. ctx_feed: three stories, the top three of For you, in order,
        --    no test story; registered as a brief section.
        v_ctx := public.ctx_feed();
        IF jsonb_array_length(v_ctx -> 'top_stories') <> 3
           OR (v_ctx -> 'top_stories' -> 0 ->> 'title')
              IS DISTINCT FROM (SELECT f.title FROM public.feed_for_you('home', 1, 0) f)
           OR v_ctx::text LIKE '%self-test 039 test%' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_feed shape or content: %', left(v_ctx::text, 300);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers
                        WHERE key = 'feed' AND brief_section AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the feed provider is not a brief section';
        END IF;

        -- 10. CHECKs: cross-pillar and floor need tier 1.
        v_refused := 0;
        BEGIN
            INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier, cross_pillar)
            VALUES (v_ids[2], 'home', 1, '{}'::jsonb, 2, true);
        EXCEPTION WHEN check_violation THEN v_refused := v_refused + 1;
        END;
        BEGIN
            INSERT INTO public.follows (entity_id, tier, pillar, floor)
            SELECT id, 2, 'sports', true FROM public.entities WHERE name = 'Kalshi';
        EXCEPTION WHEN check_violation THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 2 tier CHECKs refused', v_refused;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '039 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('039_feed_ranking')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) six tables: RLS on; one owner read policy each, plus the insert
--       policy on feed_reactions (2); anon nothing; authenticated writes only
--       feed_reactions (INSERT)
--   (b) valemont_readonly reads the six tables and three views, executes the
--       two functions
--   (c) follows: 18 rows (tier 1: 7, tier 2: 2, tier 3: 9), one floor
--       (Alabama Crimson Tide); entity_links: 3
--   (d) the feed provider: sort 40, brief_section true
--   (e) seed_interests.feed: home 9 loves, 6 not interested; sports empty
--   (f) the migration_log row (and the NOTICE "039 self-test passed")
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any,
       has_table_privilege('authenticated', c.oid, 'INSERT') AS auth_insert,
       has_table_privilege('authenticated', c.oid, 'UPDATE,DELETE') AS auth_update_delete
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('feed_reactions', 'interest_profiles', 'entities', 'entity_links', 'follows', 'story_scores')
 ORDER BY c.relname;                                                                      -- (a)

SELECT c.relname, has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('feed_reactions', 'interest_profiles', 'entities', 'entity_links', 'follows',
                     'story_scores', 'v_feed_reaction_latest', 'v_story_scores_latest', 'v_feed_stories')
 ORDER BY c.relname;
SELECT has_function_privilege('valemont_readonly', 'public.feed_for_you(text, integer, integer)', 'EXECUTE') AS readonly_for_you,
       has_function_privilege('valemont_readonly', 'public.ctx_feed()', 'EXECUTE') AS readonly_ctx_feed,
       has_function_privilege('anon', 'public.feed_for_you(text, integer, integer)', 'EXECUTE') AS anon_for_you; -- (b)

SELECT f.tier, count(*), string_agg(e.name, ', ' ORDER BY e.name) FILTER (WHERE f.floor) AS floor
  FROM follows f JOIN entities e ON e.id = f.entity_id
 GROUP BY f.tier ORDER BY f.tier;
SELECT count(*) AS entity_links FROM entity_links;                                        -- (c)

SELECT key, sort, brief_section, enabled FROM context_providers ORDER BY sort;           -- (d)

SELECT jsonb_array_length(seed_interests -> 'feed' -> 'home' -> 'love') AS home_love,
       jsonb_array_length(seed_interests -> 'feed' -> 'home' -> 'not_interested') AS home_ni,
       jsonb_array_length(seed_interests -> 'feed' -> 'sports' -> 'love') AS sports_love
  FROM app_settings;                                                                      -- (e)

SELECT name, applied_at FROM migration_log WHERE name = '039_feed_ranking';               -- (f)
