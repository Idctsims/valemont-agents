-- 049_repos.sql — GitHub repos in Tech Hub (Chat 4, Phase 2; owner, 2026-10-14).
--
-- A daily worker job (workers/feed/repos.py, 07:00 America/Chicago, and at
-- boot if today's run has not happened) discovers repos (github.com/trending
-- daily and weekly, plus one Search API query per interest), snapshots their
-- stars, and surfaces at most 8 a day as tech stories, category `repos`, on
-- the existing feed engine. Design: docs/tech_hub.md §7.
--
-- What it holds:
--   1. sources            kind `github` (the repos source; an https://github.com/
--                         URL). Its stories are not official and never push.
--   2. repos              one row per repo the job has looked at: what GitHub
--                         says about it, its latest stars and stars_7d (with
--                         where stars_7d came from), its latest why-it-matters
--                         line and latest story. App table: the worker updates it.
--   3. repo_star_snapshots  one row per repo per owner's day. APPEND-ONLY,
--                         TRUNCATE refused. snapped_on is the trigger's
--                         (owner_today()), never the writer's: stars_7d is
--                         today's snapshot minus the one 7 days ago, from our
--                         own rows, and a snapshot cannot be backdated. Not
--                         money, so no `mode` column.
--   4. repo_surfacings    one row per surfacing (a repo becoming a story).
--                         APPEND-ONLY, TRUNCATE refused. Its trigger is the
--                         rule, not the worker: at most 8 real surfacings per
--                         owner's day (repo_surface_room(day)), and a repo
--                         surfaces again only by repo_may_surface().
--                         surfaced_on is the trigger's.
--   5. repo_may_surface(repo, stars_7d, day)  THE RESURFACE RULE: a repo
--                         never surfaced may surface; one already surfaced
--                         only if its stars_7d is at least double its FIRST
--                         surfacing's stars_7d AND its last surfacing was at
--                         least 14 days before `day`. A first surfacing with
--                         no stars_7d (or 0) has no baseline to double: such a
--                         repo never resurfaces.
--   6. the repos test source `_e2e-repos` (is_test, never active) and
--      add_test_repo() (verified e2e marker): a test repo, story and
--      surfacing, invisible to every real selector.
--   7. ctx_tech           gains repos_this_week (surfaced in the last 7 days)
--                         and loved_repos (repo stories the owner loves).
--      ctx_tech_brief     gains top_repo ONLY when the owner loved a repo
--                         story in the last 14 days; otherwise repos stay out
--                         of the brief. The section is still NULL (silent)
--                         when there is nothing to say.
--
-- The `repos` category on stories already exists: db/045 reserved it in
-- stories_category_check and stories_category_pillar (tech stories only).
-- The section 8 self-test asserts it rather than re-creating it.
--
-- The worker's prune (core/ledger.py FEED_PRUNE_SQL) never deletes a story
-- named by repo_surfacings (its foreign key is ON DELETE RESTRICT).
--
-- A self-test runs on paste (section 8), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/015 (migration_log), db/020 (is_owner, owner_today,
-- reject_mutation, reject_truncate), db/027 (e2e_markers), db/029, db/032-048.
-- PASTE BEFORE THE MERGE: the worker reads these tables.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- sources: kind github

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_check;
ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ('rss', 'gnews', 'page', 'hn', 'github'));

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_url;
ALTER TABLE sources ADD CONSTRAINT sources_kind_url CHECK (
    (kind <> 'gnews' OR url ~ '^https://news\.google\.com/rss/search\?')
    AND (kind <> 'hn' OR url ~ '^https://hn\.algolia\.com/api/v1/')
    AND (kind <> 'github' OR url ~ '^https://github\.com/'));

-- A github source is tech only and never official (db/045's pillar CHECK
-- already makes a tech source tech only).
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_github_tech;
ALTER TABLE sources ADD CONSTRAINT sources_github_tech CHECK (
    kind <> 'github' OR (pillars = ARRAY['tech']::text[] AND NOT official));

INSERT INTO sources (slug, name, pillars, kind, url, priority, active, is_test, official, notes)
VALUES ('_e2e-repos', 'Test repos source', ARRAY['tech'], 'github', 'https://github.com/trending?e2e=1',
        1000, false, true, false, 'tests_live and add_test_repo() only')
ON CONFLICT (slug) DO NOTHING;

-- ===================================================================== 2
-- repos

CREATE TABLE IF NOT EXISTS repos (
    id               BIGSERIAL    PRIMARY KEY,
    full_name        TEXT         NOT NULL
                                  CHECK (full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' AND length(full_name) <= 200),
    html_url         TEXT         NOT NULL CHECK (html_url ~ '^https://' AND length(html_url) <= 300),
    description      TEXT         CHECK (length(description) <= 1000),
    language         TEXT         CHECK (length(language) <= 60),
    topics           TEXT[]       NOT NULL DEFAULT '{}' CHECK (cardinality(topics) <= 30),
    license          TEXT         CHECK (length(license) <= 100),
    archived         BOOLEAN      NOT NULL DEFAULT false,
    fork             BOOLEAN      NOT NULL DEFAULT false,
    gh_created_at    TIMESTAMPTZ,
    pushed_at        TIMESTAMPTZ,
    stars            INTEGER      CHECK (stars >= 0),
    -- Stars gained over 7 days, and where the number came from. 'measured':
    -- today's snapshot minus the one 7 days ago (ours). Anything else is an
    -- ESTIMATE and is labelled as one on screen: 'trending_weekly' /
    -- 'trending_daily' (github.com/trending's stars in its period),
    -- 'since_created' (a repo under 7 days old: every star it has),
    -- 'age_average' (stars / age in days × 7, for a repo 7-30 days old).
    stars_7d         INTEGER,
    stars_7d_source  TEXT         CHECK (stars_7d_source IN ('measured', 'trending_weekly', 'trending_daily',
                                                             'since_created', 'age_average')),
    why_it_matters   TEXT         CHECK (length(why_it_matters) <= 200),
    -- The latest surfacing's story (repo_surfacings has every one).
    story_id         BIGINT       REFERENCES stories (id) ON DELETE SET NULL,
    first_seen_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_checked_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    CONSTRAINT repos_stars_7d_sourced CHECK ((stars_7d IS NULL) = (stars_7d_source IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS repos_full_name ON repos (lower(full_name));
CREATE INDEX IF NOT EXISTS repos_last_checked ON repos (last_checked_at) WHERE NOT is_test;

-- ===================================================================== 3
-- repo_star_snapshots (append-only)

CREATE TABLE IF NOT EXISTS repo_star_snapshots (
    id          BIGSERIAL    PRIMARY KEY,
    repo_id     BIGINT       NOT NULL REFERENCES repos (id) ON DELETE RESTRICT,
    -- The owner's local date; set by the trigger, never by the writer.
    snapped_on  DATE         NOT NULL DEFAULT public.owner_today(),
    stars       INTEGER      NOT NULL CHECK (stars >= 0),
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT repo_star_snapshots_once UNIQUE (repo_id, snapped_on)
);

CREATE OR REPLACE FUNCTION public.repo_star_snapshot_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_test boolean;
BEGIN
    NEW.snapped_on := public.owner_today();
    NEW.created_at := now();
    SELECT r.is_test INTO v_test FROM public.repos r WHERE r.id = NEW.repo_id;
    -- An unknown repo is the foreign key's to refuse.
    IF v_test IS NOT NULL AND v_test <> NEW.is_test THEN
        RAISE EXCEPTION 'repo_star_snapshots: is_test % does not match repo % (is_test %)',
            NEW.is_test, NEW.repo_id, v_test;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS repo_star_snapshots_guard ON repo_star_snapshots;
CREATE TRIGGER repo_star_snapshots_guard
    BEFORE INSERT ON repo_star_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.repo_star_snapshot_guard();
DROP TRIGGER IF EXISTS repo_star_snapshots_immutable ON repo_star_snapshots;
CREATE TRIGGER repo_star_snapshots_immutable
    BEFORE UPDATE OR DELETE ON repo_star_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS repo_star_snapshots_no_truncate ON repo_star_snapshots;
CREATE TRIGGER repo_star_snapshots_no_truncate
    BEFORE TRUNCATE ON repo_star_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 4
-- repo_surfacings (append-only) and the resurface rule

CREATE TABLE IF NOT EXISTS repo_surfacings (
    id               BIGSERIAL    PRIMARY KEY,
    repo_id          BIGINT       NOT NULL REFERENCES repos (id) ON DELETE RESTRICT,
    story_id         BIGINT       NOT NULL UNIQUE REFERENCES stories (id) ON DELETE RESTRICT,
    -- The owner's local date; set by the trigger, never by the writer.
    surfaced_on      DATE         NOT NULL DEFAULT public.owner_today(),
    stars            INTEGER      CHECK (stars >= 0),
    stars_7d         INTEGER,
    stars_7d_source  TEXT         CHECK (stars_7d_source IN ('measured', 'trending_weekly', 'trending_daily',
                                                             'since_created', 'age_average')),
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT repo_surfacings_stars_7d_sourced CHECK ((stars_7d IS NULL) = (stars_7d_source IS NULL))
);

CREATE INDEX IF NOT EXISTS repo_surfacings_repo ON repo_surfacings (repo_id, surfaced_on);
CREATE INDEX IF NOT EXISTS repo_surfacings_day ON repo_surfacings (surfaced_on) WHERE NOT is_test;

-- THE RESURFACE RULE (owner, 2026-10-14). Real surfacings only. `p_day` is
-- an argument so the rule can be asked about any day (the self-test and
-- tests_live ask about day + 13 and day + 14); the trigger asks about today.
CREATE OR REPLACE FUNCTION public.repo_may_surface(p_repo bigint, p_stars_7d integer, p_day date)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH s AS (
        SELECT x.surfaced_on, x.stars_7d, x.id FROM public.repo_surfacings x
         WHERE x.repo_id = p_repo AND NOT x.is_test
    ),
    first_shown AS (SELECT s.stars_7d FROM s ORDER BY s.surfaced_on, s.id LIMIT 1)
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM s) THEN true
        ELSE p_stars_7d IS NOT NULL
             AND (SELECT max(s.surfaced_on) FROM s) <= p_day - 14
             AND coalesce((SELECT f.stars_7d FROM first_shown f), 0) > 0
             AND p_stars_7d >= 2 * (SELECT f.stars_7d FROM first_shown f)
    END;
$$;

-- THE CAP (owner, 2026-10-14): real surfacings left on `p_day`, 0 to 8.
CREATE OR REPLACE FUNCTION public.repo_surface_room(p_day date)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT greatest(8 - count(*), 0)::integer FROM public.repo_surfacings p
     WHERE p.surfaced_on = p_day AND NOT p.is_test;
$$;

CREATE OR REPLACE FUNCTION public.repo_surfacing_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_story_test boolean;
    v_repo_test  boolean;
BEGIN
    -- One writer at a time: the count, the rule and this insert are one decision.
    PERFORM pg_advisory_xact_lock(hashtext('repo_surfacings'));
    NEW.surfaced_on := public.owner_today();
    NEW.created_at := now();
    SELECT st.is_test INTO v_story_test FROM public.stories st
     WHERE st.id = NEW.story_id AND 'tech' = ANY (st.pillars) AND st.category = 'repos';
    IF v_story_test IS NULL THEN
        RAISE EXCEPTION 'repo_surfacings: story % is not a tech story in category repos', NEW.story_id;
    END IF;
    SELECT r.is_test INTO v_repo_test FROM public.repos r WHERE r.id = NEW.repo_id;
    IF v_repo_test IS NOT NULL AND (v_repo_test <> NEW.is_test OR v_story_test <> NEW.is_test) THEN
        RAISE EXCEPTION 'repo_surfacings: is_test % does not match the repo (%) or the story (%)',
            NEW.is_test, v_repo_test, v_story_test;
    END IF;
    IF NOT NEW.is_test THEN
        IF public.repo_surface_room(NEW.surfaced_on) <= 0 THEN
            RAISE EXCEPTION 'repo_surfacings: the cap of 8 a day is reached for %', NEW.surfaced_on;
        END IF;
        IF NOT public.repo_may_surface(NEW.repo_id, NEW.stars_7d, NEW.surfaced_on) THEN
            RAISE EXCEPTION 'repo_surfacings: repo % may not surface again on % (the resurface rule)',
                NEW.repo_id, NEW.surfaced_on;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS repo_surfacings_guard ON repo_surfacings;
CREATE TRIGGER repo_surfacings_guard
    BEFORE INSERT ON repo_surfacings
    FOR EACH ROW EXECUTE FUNCTION public.repo_surfacing_guard();
DROP TRIGGER IF EXISTS repo_surfacings_immutable ON repo_surfacings;
CREATE TRIGGER repo_surfacings_immutable
    BEFORE UPDATE OR DELETE ON repo_surfacings
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS repo_surfacings_no_truncate ON repo_surfacings;
CREATE TRIGGER repo_surfacings_no_truncate
    BEFORE TRUNCATE ON repo_surfacings
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 5
-- The e2e writer: a TEST repo, story and surfacing. Owner only, the verified
-- e2e marker, a .invalid URL. Invisible to every real selector (For you,
-- v_feed_stories, ctx_tech, the cap and the resurface rule).

CREATE OR REPLACE FUNCTION public.add_test_repo(p_marker text, p_full_name text, p_summary text,
                                                p_stars integer, p_stars_7d integer, p_stars_7d_source text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_hash    text;
    v_source  bigint;
    v_repo    bigint;
    v_story   bigint;
    v_item    bigint;
    v_url     text;
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'add_test_repo: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_repo: test marker refused';
    END IF;
    v_url := 'https://e2e.invalid/repos/' || p_full_name;
    SELECT id INTO v_source FROM public.sources WHERE slug = '_e2e-repos' AND is_test;
    INSERT INTO public.repos (full_name, html_url, description, language, topics, stars, stars_7d,
                              stars_7d_source, is_test)
    VALUES (p_full_name, v_url, 'Test repo', 'TypeScript', ARRAY['e2e-test', 'agents'], p_stars, p_stars_7d,
            CASE WHEN p_stars_7d IS NOT NULL THEN p_stars_7d_source END, true)
    RETURNING id INTO v_repo;
    INSERT INTO public.stories (pillars, title, summary, summarized_at, category, topics, is_test)
    VALUES (ARRAY['tech'], p_full_name, p_summary, CASE WHEN p_summary IS NOT NULL THEN now() END,
            'repos', ARRAY['e2e_test', 'agents'], true)
    RETURNING id INTO v_story;
    INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id, is_test,
                                      published_precision)
    VALUES (v_source, 'e2e:' || gen_random_uuid(), v_url, p_full_name, date_trunc('day', now()), v_story, true,
            'date')
    RETURNING id INTO v_item;
    UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
    INSERT INTO public.repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source, is_test)
    VALUES (v_repo, v_story, p_stars, p_stars_7d, CASE WHEN p_stars_7d IS NOT NULL THEN p_stars_7d_source END, true);
    UPDATE public.repos SET story_id = v_story WHERE id = v_repo;
    RETURN v_story;
END;
$$;

-- ===================================================================== 6
-- Context providers. ctx_tech keeps every key it had (db/045) and gains two.

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
    ),
    -- Repos surfaced in the last 7 days (real only), newest first.
    week_repos AS (
        SELECT r.full_name, r.html_url, r.language, r.topics, s.stars, s.stars_7d, s.stars_7d_source,
               s.surfaced_on, st.summary, st.why_it_matters, lr.reaction, s.id
          FROM public.repo_surfacings s
          JOIN public.repos r ON r.id = s.repo_id
          JOIN public.stories st ON st.id = s.story_id
          LEFT JOIN public.v_feed_reaction_latest lr ON lr.story_id = s.story_id AND lr.pillar = 'tech'
         WHERE NOT s.is_test AND s.surfaced_on > public.owner_today() - 7
         ORDER BY s.surfaced_on DESC, s.id
         LIMIT 56
    ),
    loved_repos AS (
        SELECT r.full_name, r.html_url, st.summary, s.surfaced_on, lr.reacted_at
          FROM public.v_feed_reaction_latest lr
          JOIN public.repo_surfacings s ON s.story_id = lr.story_id AND NOT s.is_test
          JOIN public.repos r ON r.id = s.repo_id
          JOIN public.stories st ON st.id = s.story_id
         WHERE lr.pillar = 'tech' AND lr.reaction = 'love' AND NOT lr.is_test
         ORDER BY lr.reacted_at DESC
         LIMIT 20
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Tech Hub: official AI and developer-stack releases (Anthropic, OpenAI, Claude Code, '
                 'Cursor, MCP, Next.js, Vercel, Supabase), Hacker News, and up to 8 GitHub repos a day. '
                 'official_7d is every official item published in the last 7 days, newest first. '
                 'repos_this_week: every repo surfaced in the last 7 days; stars_7d is MEASURED only when '
                 'stars_7d_source is "measured", otherwise it is an estimate and must be called one.',
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
        'repos_this_week', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'repo', w.full_name,
                       'url', w.html_url,
                       'surfaced', to_char(w.surfaced_on, 'YYYY-MM-DD'),
                       'summary', public.ctx_trim(w.summary, 260),
                       'language', w.language,
                       'topics', CASE WHEN cardinality(w.topics) > 0 THEN to_jsonb(w.topics[1:3]) END,
                       'stars', w.stars,
                       'stars_7d', w.stars_7d,
                       'stars_7d_source', w.stars_7d_source,
                       'why_it_matters', w.why_it_matters,
                       'reaction', CASE WHEN w.reaction IN ('love', 'neutral', 'not_interested') THEN w.reaction END))
                     ORDER BY w.surfaced_on DESC, w.id)
              FROM week_repos w), '[]'::jsonb),
        'loved_repos', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'repo', l.full_name,
                       'url', l.html_url,
                       'surfaced', to_char(l.surfaced_on, 'YYYY-MM-DD'),
                       'summary', public.ctx_trim(l.summary, 200)))
                     ORDER BY l.reacted_at DESC)
              FROM loved_repos l), '[]'::jsonb),
        'note', CASE WHEN NOT EXISTS (SELECT 1 FROM official) THEN 'No official items in the last 7 days.' END
    ));
$$;

-- The brief's Tech line (owner, 2026-10-14): it may name the top repo ONLY
-- if the owner loved a repo story in the last 14 days. The top repo is the
-- first of today's surfaced repos in Tech's For you (category repos).
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
    ),
    repo_love AS (
        SELECT 1 FROM public.v_feed_reaction_latest lr
          JOIN public.stories st ON st.id = lr.story_id
         WHERE lr.pillar = 'tech' AND lr.reaction = 'love' AND NOT lr.is_test AND st.category = 'repos'
           AND lr.reacted_at > now() - interval '14 days'
         LIMIT 1
    ),
    top_repo AS (
        SELECT r.full_name, f.summary
          FROM public.feed_for_you('tech', 30, 0, NULL, 'repos') f
          JOIN public.repo_surfacings s ON s.story_id = f.id AND NOT s.is_test
          JOIN public.repos r ON r.id = s.repo_id
         WHERE EXISTS (SELECT 1 FROM repo_love) AND s.surfaced_on = public.owner_today()
         ORDER BY f.pos
         LIMIT 1
    )
    SELECT CASE WHEN EXISTS (SELECT 1 FROM landed) OR EXISTS (SELECT 1 FROM top_repo) THEN jsonb_strip_nulls(jsonb_build_object(
        'about', 'Official tech items that landed in the last 24 hours, and (only when present) the top GitHub '
                 'repo surfaced today. Write exactly ONE line.',
        'official_24h', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                 'title', public.ctx_trim(l.title, 160),
                                 'source', l.source_name,
                                 'summary', public.ctx_trim(l.summary, 200)))
                               ORDER BY l.first_seen_at DESC, l.id DESC)
                           FROM landed l),
        'top_repo', (SELECT jsonb_strip_nulls(jsonb_build_object('repo', t.full_name,
                                                                 'summary', public.ctx_trim(t.summary, 200)))
                       FROM top_repo t))) END;
$$;

-- ===================================================================== 7
-- Access.

ALTER TABLE repos ENABLE ROW LEVEL SECURITY;
ALTER TABLE repo_star_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE repo_surfacings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON repos, repo_star_snapshots, repo_surfacings FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE repos_id_seq, repo_star_snapshots_id_seq, repo_surfacings_id_seq
    FROM anon, authenticated, PUBLIC;
GRANT SELECT ON repos, repo_star_snapshots, repo_surfacings TO authenticated;
DROP POLICY IF EXISTS repos_owner_read ON repos;
CREATE POLICY repos_owner_read ON repos
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS repo_star_snapshots_owner_read ON repo_star_snapshots;
CREATE POLICY repo_star_snapshots_owner_read ON repo_star_snapshots
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS repo_surfacings_owner_read ON repo_surfacings;
CREATE POLICY repo_surfacings_owner_read ON repo_surfacings
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
-- db/018's defaults cover new tables; restated so the file says what it expects.
GRANT SELECT ON repos, repo_star_snapshots, repo_surfacings TO valemont_readonly;

REVOKE ALL ON FUNCTION public.repo_star_snapshot_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.repo_surfacing_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.repo_may_surface(bigint, integer, date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.repo_surface_room(date) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.repo_surface_room(date) TO authenticated, valemont_readonly;
REVOKE ALL ON FUNCTION public.add_test_repo(text, text, text, integer, integer, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_tech() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_tech_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.repo_may_surface(bigint, integer, date) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_repo(text, text, text, integer, integer, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.ctx_tech() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_tech_brief() TO authenticated, valemont_readonly;

-- ===================================================================== 8
-- Self-test, run on paste. Everything happens inside a subtransaction rolled
-- back at the end ('selftest-rollback'), so no row survives; only sequence
-- values are consumed. A wrong result raises SELFTEST FAILED and nothing in
-- this file is applied.
--
-- Its repos, stories and surfacings are REAL (is_test = false, from a
-- throwaway github source) so the real rules apply. Each expected refusal
-- runs in its own nested block and is counted only when it fails with the
-- expected message or SQLSTATE, never on any error.

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_today   date := public.owner_today();
    v_src     bigint;
    v_repos   bigint[] := '{}';
    v_stories bigint[] := '{}';
    v_room    int;
    v_id      bigint;
    v_item    bigint;
    v_n       int;
    v_ok      boolean;
    v_ctx     jsonb;
    i         int;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the repos category exists (db/045)
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'public.stories'::regclass AND conname = 'stories_category_check'
                          AND pg_get_constraintdef(oid) LIKE '%''repos''%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: stories_category_check does not list repos';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['home'], 'x', 'repos');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a repos story on Home was accepted'; END IF;

        -- ---------------------------------------------------- 2. sources: kind github
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url)
            VALUES ('selftest-049-x', 'x', ARRAY['tech'], 'github', 'https://selftest.invalid/x');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a github source off github.com was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url, official)
            VALUES ('selftest-049-o', 'o', ARRAY['tech'], 'github', 'https://github.com/selftest', true);
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an official github source was accepted'; END IF;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-049', 'Self-test 049 repos', ARRAY['tech'], 'github', 'https://github.com/selftest-049')
        RETURNING id INTO v_src;

        -- ---------------------------------------------------- 3. repos and stories
        -- Enough to fill today's cap whatever real rows already exist, plus one.
        v_room := 8 - (SELECT count(*) FROM public.repo_surfacings WHERE surfaced_on = v_today AND NOT is_test);
        FOR i IN 1..(v_room + 2) LOOP
            INSERT INTO public.repos (full_name, html_url, topics, stars)
            VALUES ('selftest-049/repo-' || i, 'https://selftest.invalid/049/' || i, ARRAY['agents'], 100)
            RETURNING id INTO v_id;
            v_repos := v_repos || v_id;
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-049/repo-' || i, 'repos')
            RETURNING id INTO v_id;
            v_stories := v_stories || v_id;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id,
                                              published_precision)
            VALUES (v_src, 'selftest049:' || i, 'https://selftest.invalid/049/' || i, 'selftest-049/repo-' || i,
                    date_trunc('day', now()), v_id, 'date')
            RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_id;
        END LOOP;
        -- One more story, NOT category repos, for the guard's story check.
        INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-049 tooling', 'tooling')
        RETURNING id INTO v_id;
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source)
            VALUES (v_repos[1], v_id, 100, 10, 'measured');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%not a tech story in category repos%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a surfacing on a non-repos story was accepted'; END IF;

        -- ---------------------------------------------------- 4. the resurface rule (function)
        IF NOT public.repo_may_surface(v_repos[1], NULL, v_today) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a never-surfaced repo may not surface';
        END IF;
        -- First surfacing: stars_7d 10. surfaced_on is the trigger's, whatever the writer sends.
        INSERT INTO public.repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source)
        VALUES (v_repos[1], v_stories[1], DATE '2000-01-01', 100, 10, 'measured');
        IF (SELECT surfaced_on FROM public.repo_surfacings WHERE story_id = v_stories[1]) <> v_today THEN
            RAISE EXCEPTION 'SELFTEST FAILED: surfaced_on is not the owner''s today';
        END IF;
        IF public.repo_may_surface(v_repos[1], 1000, v_today + 13) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: resurfacing allowed 13 days later';
        END IF;
        IF NOT public.repo_may_surface(v_repos[1], 20, v_today + 14) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: double the first stars_7d, 14 days later, refused';
        END IF;
        IF public.repo_may_surface(v_repos[1], 19, v_today + 14) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: less than double allowed';
        END IF;
        IF public.repo_may_surface(v_repos[1], NULL, v_today + 30) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NULL stars_7d may resurface';
        END IF;
        -- The same repo again today: the trigger refuses (the rule).
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source)
            VALUES (v_repos[1], v_stories[2], 100, 1000, 'measured');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%the resurface rule%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a repo surfaced twice in a day'; END IF;
        -- No baseline (first stars_7d NULL): never resurfaces.
        INSERT INTO public.repo_surfacings (repo_id, story_id, stars) VALUES (v_repos[2], v_stories[2], 100);
        IF public.repo_may_surface(v_repos[2], 100000, v_today + 60) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a repo with no first stars_7d may resurface';
        END IF;

        -- ---------------------------------------------------- 5. the cap of 8 a day
        -- Two used above; fill the rest of today's room, then one more is refused.
        FOR i IN 3..v_room LOOP
            INSERT INTO public.repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source)
            VALUES (v_repos[i], v_stories[i], 100, 5, 'trending_weekly');
        END LOOP;
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source)
            VALUES (v_repos[v_room + 1], v_stories[v_room + 1], 100, 5, 'trending_daily');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%cap of 8 a day%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a 9th surfacing in a day was accepted'; END IF;
        IF (SELECT count(*) FROM public.repo_surfacings WHERE surfaced_on = v_today AND NOT is_test) <> 8
           OR public.repo_surface_room(v_today) <> 0 OR public.repo_surface_room(v_today + 1) <> 8 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: today has % real surfacings and room % (expected 8 and 0)',
                (SELECT count(*) FROM public.repo_surfacings WHERE surfaced_on = v_today AND NOT is_test),
                public.repo_surface_room(v_today);
        END IF;
        -- A test surfacing must match its repo and story (both real here): refused.
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_surfacings (repo_id, story_id, stars, is_test)
            VALUES (v_repos[v_room + 2], v_stories[v_room + 2], 100, true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%does not match%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a test surfacing of a real repo was accepted'; END IF;

        -- ---------------------------------------------------- 6. snapshots
        INSERT INTO public.repo_star_snapshots (repo_id, snapped_on, stars) VALUES (v_repos[1], DATE '2000-01-01', 100);
        IF (SELECT snapped_on FROM public.repo_star_snapshots WHERE repo_id = v_repos[1]) <> v_today THEN
            RAISE EXCEPTION 'SELFTEST FAILED: snapped_on is not the owner''s today';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_star_snapshots (repo_id, stars) VALUES (v_repos[1], 101);
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: two snapshots of one repo in a day'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.repo_star_snapshots (repo_id, stars, is_test) VALUES (v_repos[2], 1, true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%does not match%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a test snapshot of a real repo was accepted'; END IF;

        -- ---------------------------------------------------- 7. append-only
        -- Neither table is referenced by any other, so each TRUNCATE names
        -- its table alone (db/042's lesson: name every referencing table).
        v_n := 0;
        BEGIN
            UPDATE public.repo_surfacings SET stars = 1 WHERE story_id = v_stories[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.repo_surfacings WHERE story_id = v_stories[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.repo_surfacings;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.repo_star_snapshots SET stars = 1 WHERE repo_id = v_repos[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.repo_star_snapshots WHERE repo_id = v_repos[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.repo_star_snapshots;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 6 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 6 UPDATE/DELETE/TRUNCATE refused on the repo tables', v_n;
        END IF;
        -- The story a surfacing names cannot be deleted (the prune must skip it).
        v_ok := false;
        BEGIN
            DELETE FROM public.stories WHERE id = v_stories[1];
        EXCEPTION WHEN foreign_key_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a surfaced story could be deleted'; END IF;

        -- ---------------------------------------------------- 8. context
        v_ctx := public.ctx_tech();
        IF NOT (v_ctx -> 'repos_this_week') @> jsonb_build_array(jsonb_build_object('repo', 'selftest-049/repo-1',
                                                                                    'stars_7d_source', 'measured'))
           OR jsonb_typeof(v_ctx -> 'loved_repos') IS DISTINCT FROM 'array'
           OR jsonb_typeof(v_ctx -> 'official_7d') IS DISTINCT FROM 'array'
           OR jsonb_typeof(v_ctx -> 'top_stories') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech shape or repos_this_week: %', left(v_ctx::text, 400);
        END IF;
        -- No repo love in 14 days: no top_repo, whatever surfaced today.
        IF NOT EXISTS (SELECT 1 FROM public.v_feed_reaction_latest lr JOIN public.stories st ON st.id = lr.story_id
                        WHERE lr.pillar = 'tech' AND lr.reaction = 'love' AND NOT lr.is_test
                          AND st.category = 'repos' AND lr.reacted_at > now() - interval '14 days')
           AND public.ctx_tech_brief() ? 'top_repo' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the brief names a repo with no repo love in 14 days';
        END IF;
        -- A Love on a repo story (pillar tech): the brief may name today's top repo.
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        SELECT unnest(v_stories[1:v_room]), 'tech', 40, '{}';
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_stories[1], 'tech', 'love');
        v_ctx := public.ctx_tech_brief();
        IF v_ctx IS NULL OR NOT (v_ctx ? 'top_repo')
           OR NOT ((v_ctx -> 'top_repo' ->> 'repo') LIKE 'selftest-049/repo-%'
                   OR EXISTS (SELECT 1 FROM public.repo_surfacings WHERE surfaced_on = v_today AND NOT is_test
                                 AND story_id <> ALL (v_stories))) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech_brief top_repo after a repo Love: %',
                left(coalesce(v_ctx::text, 'NULL'), 400);
        END IF;
        IF NOT (public.ctx_tech() -> 'loved_repos') @> '[{"repo": "selftest-049/repo-1"}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_tech loved_repos';
        END IF;
        -- A home reaction on a repo story crosses the tech line (db/045's guard).
        v_ok := false;
        BEGIN
            INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_stories[1], 'home', 'love');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a home reaction on a repo story was accepted'; END IF;
        IF (public.context_snapshot() -> 'tech') ? 'error' OR (public.context_snapshot() -> 'tech_brief') ? 'error' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the context snapshot reports an error for tech';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '049 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('049_repos')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) sources_kind_check lists github; sources_kind_url and
--       sources_github_tech present (3 rows)
--   (b) the three tables: RLS on, 1 policy each, anon no privilege,
--       authenticated SELECT only, valemont_readonly SELECT; 0 rows each
--   (c) triggers: guard + immutable + no_truncate on both append-only tables (6)
--   (d) repo_may_surface, repo_surface_room, add_test_repo, ctx_tech,
--       ctx_tech_brief: readonly executes all but add_test_repo, anon none;
--       repo_surface_room(owner_today()) = 8
--   (e) `_e2e-repos` inactive, is_test, kind github
--   (f) ctx_tech() has repos_this_week [] and loved_repos []
--   (g) the log row
--
-- (a)
-- SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = 'public.sources'::regclass
--    AND conname IN ('sources_kind_check', 'sources_kind_url', 'sources_github_tech') ORDER BY 1;
-- (b)
-- SELECT c.relname, c.relrowsecurity,
--        (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
--        has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
--        has_table_privilege('authenticated', c.oid, 'SELECT') AS auth_select,
--        has_table_privilege('authenticated', c.oid, 'INSERT') AS auth_insert,
--        has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS ro_select
--   FROM pg_class c WHERE c.relname IN ('repos', 'repo_star_snapshots', 'repo_surfacings') ORDER BY 1;
-- SELECT (SELECT count(*) FROM repos) AS repos, (SELECT count(*) FROM repo_star_snapshots) AS snapshots,
--        (SELECT count(*) FROM repo_surfacings) AS surfacings;
-- (c)
-- SELECT tgrelid::regclass, tgname FROM pg_trigger
--  WHERE tgrelid IN ('repo_star_snapshots'::regclass, 'repo_surfacings'::regclass) AND NOT tgisinternal ORDER BY 1, 2;
-- (d)
-- SELECT p.proname, has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS ro,
--        has_function_privilege('anon', p.oid, 'EXECUTE') AS anon
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname = 'public'
--    AND p.proname IN ('repo_may_surface', 'repo_surface_room', 'add_test_repo', 'ctx_tech', 'ctx_tech_brief')
--  ORDER BY 1;
-- SELECT repo_surface_room(owner_today()) AS room_today;
-- (e)
-- SELECT slug, active, is_test, kind, official FROM sources WHERE slug = '_e2e-repos';
-- (f)
-- SELECT ctx_tech() -> 'repos_this_week' AS week, ctx_tech() -> 'loved_repos' AS loved;
-- (g)
-- SELECT name, applied_at FROM migration_log WHERE name = '049_repos';
