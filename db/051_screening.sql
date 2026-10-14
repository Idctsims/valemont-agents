-- 051_screening.sql — the Screening Room core (Chat 4, Phase 3; owner, 2026-10-14).
--
-- TMDB titles, the owner's shelves (want / watching / finished), and a new
-- feed pillar `screening` for trailers from YouTube channel feeds, on the
-- existing feed engine. Design: docs/screening.md.
--
-- What it holds:
--   1. pillar `screening` EXCLUSIVE, like tech (db/045): a source or story in
--      screening is in nothing else; feed_rules, feed_reactions,
--      interest_profiles and story_scores accept it.
--   2. feed_pillar_guard, generalized: tech and screening are each a world
--      of their own. A score or reaction whose pillar's world differs from
--      the story's is refused (the message still says "crosses the tech
--      line", which the web and the tests match on).
--   3. titles           one row per TMDB title (tmdb_id + kind): name, the
--                       US release date, poster, overview, genres, US
--                       providers (jsonb: flatrate / ads / free / rent /
--                       buy, each [{id, name, logo}], plus link), the
--                       trailer key, the next episode date (TV), TMDB's
--                       status. App table: the web (owner session) and the
--                       worker write it.
--   4. title_trailers   one row per screening content item: the TMDB title
--                       it was matched to, or `unmatched` (still shown,
--                       flagged). The worker writes it.
--   5. watch_status     the shelves' HISTORY, APPEND-ONLY (UPDATE, DELETE
--                       and TRUNCATE refused): each move is a new row; a
--                       re-watch is a new row. finished_at is the
--                       trigger's: now() on every move to finished, NULL
--                       otherwise. v_watch_current is the latest row per
--                       title (what the shelves show). Chat 5's Arsenal hook
--                       reads finished rows.
--   6. the test source `_e2e-screening`, add_test_title() and
--      add_test_trailer() (verified e2e marker): invisible to real selectors.
--   7. ctx_screening (Wags: shelves, upcoming 30 days, newest matched
--      trailers) and ctx_screening_brief (a brief section, registered, NULL
--      until Phase 4: silent).
--
-- Sources are NOT rows here: they are in workers/feed/sources.toml.
-- A self-test runs on paste (section 9), rolled back. PASTE BEFORE THE MERGE.
-- Prerequisites: db/020, db/027, db/029, db/032-050.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The pillar.

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_pillars_check;
ALTER TABLE sources ADD CONSTRAINT sources_pillars_check
    CHECK (cardinality(pillars) >= 1
           AND pillars <@ ARRAY['home', 'sports', 'tech', 'screening']::text[]
           AND (NOT ('tech' = ANY (pillars)) OR pillars = ARRAY['tech']::text[])
           AND (NOT ('screening' = ANY (pillars)) OR pillars = ARRAY['screening']::text[]));

ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_pillars_check;
ALTER TABLE stories ADD CONSTRAINT stories_pillars_check
    CHECK (cardinality(pillars) >= 1
           AND pillars <@ ARRAY['home', 'sports', 'tech', 'screening']::text[]
           AND (NOT ('tech' = ANY (pillars)) OR pillars = ARRAY['tech']::text[])
           AND (NOT ('screening' = ANY (pillars)) OR pillars = ARRAY['screening']::text[]));

-- A screening story carries no category (or `other`): the tech categories
-- stay tech's, Home's stay Home's and Sports'.
ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_category_screening;
ALTER TABLE stories ADD CONSTRAINT stories_category_screening
    CHECK (NOT ('screening' = ANY (pillars)) OR category IS NULL OR category = 'other');

ALTER TABLE feed_rules DROP CONSTRAINT IF EXISTS feed_rules_pillar_check;
ALTER TABLE feed_rules ADD CONSTRAINT feed_rules_pillar_check CHECK (pillar IN ('home', 'sports', 'tech', 'screening'));
ALTER TABLE feed_reactions DROP CONSTRAINT IF EXISTS feed_reactions_pillar_check;
ALTER TABLE feed_reactions ADD CONSTRAINT feed_reactions_pillar_check CHECK (pillar IN ('home', 'sports', 'tech', 'screening'));
ALTER TABLE interest_profiles DROP CONSTRAINT IF EXISTS interest_profiles_pillar_check;
ALTER TABLE interest_profiles ADD CONSTRAINT interest_profiles_pillar_check CHECK (pillar IN ('home', 'sports', 'tech', 'screening'));
ALTER TABLE story_scores DROP CONSTRAINT IF EXISTS story_scores_pillar_check;
ALTER TABLE story_scores ADD CONSTRAINT story_scores_pillar_check CHECK (pillar IN ('home', 'sports', 'tech', 'screening'));

-- ===================================================================== 2
-- The no-cross-in guard: two exclusive worlds now.

CREATE OR REPLACE FUNCTION public.feed_pillar_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_story_world text;
    v_row_world   text;
BEGIN
    SELECT CASE WHEN 'tech' = ANY (st.pillars) THEN 'tech'
                WHEN 'screening' = ANY (st.pillars) THEN 'screening'
                ELSE 'main' END
      INTO v_story_world FROM public.stories st WHERE st.id = NEW.story_id;
    v_row_world := CASE WHEN NEW.pillar IN ('tech', 'screening') THEN NEW.pillar ELSE 'main' END;
    -- An unknown story is the foreign key's to refuse.
    IF v_story_world IS NOT NULL AND v_story_world <> v_row_world THEN
        RAISE EXCEPTION '%: a % row on story % crosses the tech line (tech and screening stories live only in their own pillar)',
            TG_TABLE_NAME, NEW.pillar, NEW.story_id;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 3
-- titles

CREATE TABLE IF NOT EXISTS titles (
    id                      BIGSERIAL    PRIMARY KEY,
    tmdb_id                 INTEGER      NOT NULL CHECK (tmdb_id > 0),
    kind                    TEXT         NOT NULL CHECK (kind IN ('movie', 'tv')),
    name                    TEXT         NOT NULL CHECK (length(name) BETWEEN 1 AND 300),
    release_date            DATE,
    poster_path             TEXT         CHECK (poster_path ~ '^/[A-Za-z0-9_.-]+$'),
    overview                TEXT         CHECK (length(overview) <= 4000),
    genres                  TEXT[]       NOT NULL DEFAULT '{}' CHECK (cardinality(genres) <= 20),
    providers               JSONB        CHECK (providers IS NULL OR jsonb_typeof(providers) = 'object'),
    providers_refreshed_at  TIMESTAMPTZ,
    trailer_key             TEXT         CHECK (trailer_key ~ '^[A-Za-z0-9_-]{6,20}$'),
    next_episode_date       DATE,
    status                  TEXT         CHECK (length(status) <= 60),
    refreshed_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    is_test                 BOOLEAN      NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT titles_tmdb UNIQUE (tmdb_id, kind),
    CONSTRAINT titles_next_episode_tv CHECK (next_episode_date IS NULL OR kind = 'tv')
);

-- ===================================================================== 4
-- title_trailers: which TMDB title a trailer item was matched to.

CREATE TABLE IF NOT EXISTS title_trailers (
    id          BIGSERIAL    PRIMARY KEY,
    item_id     BIGINT       NOT NULL UNIQUE REFERENCES content_items (id) ON DELETE CASCADE,
    title_id    BIGINT       REFERENCES titles (id) ON DELETE RESTRICT,
    status      TEXT         NOT NULL CHECK (status IN ('matched', 'unmatched')),
    -- What was searched (the cleaned headline), for the record.
    query       TEXT         CHECK (length(query) <= 300),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT title_trailers_matched CHECK ((status = 'matched') = (title_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS title_trailers_title ON title_trailers (title_id);

-- ===================================================================== 5
-- watch_status: the shelves' history, append-only.

CREATE TABLE IF NOT EXISTS watch_status (
    id           BIGSERIAL    PRIMARY KEY,
    owner_id     UUID         NOT NULL DEFAULT auth.uid(),
    title_id     BIGINT       NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    status       TEXT         NOT NULL CHECK (status IN ('want', 'watching', 'finished')),
    -- The trigger's: now() on a move to finished, else NULL.
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT watch_status_finished CHECK ((status = 'finished') = (finished_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS watch_status_title ON watch_status (title_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.watch_status_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    NEW.finished_at := CASE WHEN NEW.status = 'finished' THEN now() END;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS watch_status_stamp ON watch_status;
CREATE TRIGGER watch_status_stamp
    BEFORE INSERT ON watch_status
    FOR EACH ROW EXECUTE FUNCTION public.watch_status_stamp();
DROP TRIGGER IF EXISTS watch_status_immutable ON watch_status;
CREATE TRIGGER watch_status_immutable
    BEFORE UPDATE OR DELETE ON watch_status
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS watch_status_no_truncate ON watch_status;
CREATE TRIGGER watch_status_no_truncate
    BEFORE TRUNCATE ON watch_status
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The shelves: the latest move per title.
CREATE OR REPLACE VIEW v_watch_current
WITH (security_invoker = true)
AS
SELECT DISTINCT ON (w.title_id)
       w.title_id, w.status, w.finished_at, w.created_at AS moved_at, t.is_test
  FROM watch_status w
  JOIN titles t ON t.id = w.title_id
 ORDER BY w.title_id, w.created_at DESC, w.id DESC;

-- ===================================================================== 6
-- Test rows (e2e). Owner only, the verified e2e marker.

INSERT INTO sources (slug, name, pillars, kind, url, priority, active, is_test, notes)
VALUES ('_e2e-screening', 'Test trailers source', ARRAY['screening'], 'rss', 'https://e2e.invalid/screening-feed',
        1000, false, true, 'tests_live and add_test_trailer() only')
ON CONFLICT (slug) DO NOTHING;

CREATE OR REPLACE FUNCTION public.e2e_marker_ok(p_marker text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT p_marker IS NOT NULL AND auth.uid() IS NOT NULL AND public.is_owner()
       AND encode(sha256(convert_to(p_marker, 'UTF8')), 'hex')
           = (SELECT m.marker_sha256 FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1);
$$;

-- A TEST title (tmdb_id from 900000000 up, so it never collides with a real
-- one), with no shelf; the spec moves it.
CREATE OR REPLACE FUNCTION public.add_test_title(p_marker text, p_kind text, p_name text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_title: the owner and a verified test marker only';
    END IF;
    INSERT INTO public.titles (tmdb_id, kind, name, overview, is_test, release_date)
    VALUES (900000000 + (floor(random() * 99999999))::int, p_kind, p_name, 'A test title.', true,
            (now() AT TIME ZONE 'America/Chicago')::date + 10)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- A TEST trailer story (pillar screening) from the test source, optionally
-- matched to a test title.
CREATE OR REPLACE FUNCTION public.add_test_trailer(p_marker text, p_title text, p_url text, p_title_id bigint)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_source bigint;
    v_story  bigint;
    v_item   bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_trailer: the owner and a verified test marker only';
    END IF;
    IF p_url !~ '^https://[a-z0-9.-]+\.invalid/' THEN
        RAISE EXCEPTION 'add_test_trailer: test URLs must be on a .invalid host';
    END IF;
    IF p_title_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.titles WHERE id = p_title_id AND is_test) THEN
        RAISE EXCEPTION 'add_test_trailer: a test trailer matches only a test title';
    END IF;
    SELECT id INTO v_source FROM public.sources WHERE slug = '_e2e-screening' AND is_test;
    INSERT INTO public.stories (pillars, title, is_test) VALUES (ARRAY['screening'], p_title, true)
    RETURNING id INTO v_story;
    INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id, is_test)
    VALUES (v_source, 'e2e:' || gen_random_uuid(), p_url, p_title, now(), v_story, true)
    RETURNING id INTO v_item;
    UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
    INSERT INTO public.title_trailers (item_id, title_id, status, query)
    VALUES (v_item, p_title_id, CASE WHEN p_title_id IS NULL THEN 'unmatched' ELSE 'matched' END, p_title);
    RETURN v_story;
END;
$$;

-- ===================================================================== 7
-- Context providers.

CREATE OR REPLACE FUNCTION public.ctx_screening()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH cur AS (
        SELECT c.title_id, c.status, c.finished_at, c.moved_at, t.name, t.kind, t.release_date, t.next_episode_date,
               t.tmdb_id, t.providers
          FROM public.v_watch_current c
          JOIN public.titles t ON t.id = c.title_id
         WHERE NOT c.is_test
    ),
    soon AS (
        SELECT c.*, coalesce(
                   CASE WHEN c.next_episode_date >= public.owner_today() THEN c.next_episode_date END,
                   CASE WHEN c.release_date >= public.owner_today() THEN c.release_date END) AS on_date
          FROM cur c
         WHERE c.status IN ('want', 'watching')
    ),
    trailers AS (
        SELECT v.id, v.title, v.lead_url, v.first_seen_at, t.name AS matched, t.kind, t.tmdb_id
          FROM public.v_feed_stories v
          JOIN public.title_trailers tt ON tt.item_id = v.lead_item_id AND tt.status = 'matched'
          JOIN public.titles t ON t.id = tt.title_id
         WHERE 'screening' = ANY (v.pillars)
         ORDER BY v.first_seen_at DESC, v.id DESC
         LIMIT 10
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Screening Room: the owner''s shelves (watching, want, finished), what is coming up in the next 30 '
                 'days on them, and the newest trailers matched to a title. "on" lists where a title streams in '
                 'the US on the owner''s services (Netflix, HBO Max, Paramount+ with Showtime, Starz, Prime Video).',
        'watching', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                         'title', c.name, 'kind', c.kind, 'next_episode', c.next_episode_date))
                       ORDER BY c.moved_at DESC) FROM cur c WHERE c.status = 'watching'), '[]'::jsonb),
        'want', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                     'title', c.name, 'kind', c.kind, 'release_date', c.release_date, 'next_episode', c.next_episode_date))
                   ORDER BY c.moved_at DESC) FROM cur c WHERE c.status = 'want'), '[]'::jsonb),
        'finished_recent', coalesce((SELECT jsonb_agg(jsonb_build_object('title', f.name, 'kind', f.kind,
                                                                         'finished', f.finished_at::date)
                                                      ORDER BY f.finished_at DESC)
                                       FROM (SELECT * FROM cur WHERE status = 'finished'
                                              ORDER BY finished_at DESC LIMIT 20) f), '[]'::jsonb),
        'upcoming_30d', coalesce((SELECT jsonb_agg(jsonb_build_object('title', s.name, 'kind', s.kind,
                                                                      'on', s.on_date, 'shelf', s.status)
                                                   ORDER BY s.on_date)
                                    FROM soon s WHERE s.on_date <= public.owner_today() + 30), '[]'::jsonb),
        'new_trailers', coalesce((SELECT jsonb_agg(jsonb_build_object('trailer', public.ctx_trim(t.title, 160),
                                                                      'title', t.matched, 'kind', t.kind)
                                                   ORDER BY t.first_seen_at DESC, t.id DESC)
                                    FROM trailers t), '[]'::jsonb)
    ));
$$;

-- The brief's section: registered now, silent (NULL) until Phase 4.
CREATE OR REPLACE FUNCTION public.ctx_screening_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT NULL::jsonb;
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('screening', 'Screening Room', 'public.ctx_screening()'::regprocedure, false, 55),
       ('screening_brief', 'Screening', 'public.ctx_screening_brief()'::regprocedure, true, 55)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 8
-- Access.

ALTER TABLE titles ENABLE ROW LEVEL SECURITY;
ALTER TABLE title_trailers ENABLE ROW LEVEL SECURITY;
ALTER TABLE watch_status ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON titles, title_trailers, watch_status FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE titles_id_seq, title_trailers_id_seq, watch_status_id_seq FROM anon, authenticated, PUBLIC;
-- titles: the owner reads, adds and refreshes (the web adds a title when it
-- goes on a shelf); never deletes.
GRANT SELECT, INSERT, UPDATE ON titles TO authenticated;
GRANT USAGE ON SEQUENCE titles_id_seq TO authenticated;
DROP POLICY IF EXISTS titles_owner_read ON titles;
CREATE POLICY titles_owner_read ON titles FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS titles_owner_insert ON titles;
CREATE POLICY titles_owner_insert ON titles FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND NOT is_test);
DROP POLICY IF EXISTS titles_owner_update ON titles;
CREATE POLICY titles_owner_update ON titles FOR UPDATE TO authenticated
    USING ((SELECT public.is_owner())) WITH CHECK ((SELECT public.is_owner()));
-- title_trailers: read only (the worker writes it).
GRANT SELECT ON title_trailers TO authenticated;
DROP POLICY IF EXISTS title_trailers_owner_read ON title_trailers;
CREATE POLICY title_trailers_owner_read ON title_trailers FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
-- watch_status: the owner reads and appends, as herself.
GRANT SELECT, INSERT ON watch_status TO authenticated;
GRANT USAGE ON SEQUENCE watch_status_id_seq TO authenticated;
DROP POLICY IF EXISTS watch_status_owner_read ON watch_status;
CREATE POLICY watch_status_owner_read ON watch_status FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS watch_status_owner_insert ON watch_status;
CREATE POLICY watch_status_owner_insert ON watch_status FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
GRANT SELECT ON titles, title_trailers, watch_status, v_watch_current TO valemont_readonly;
GRANT SELECT ON v_watch_current TO authenticated;

REVOKE ALL ON FUNCTION public.watch_status_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.e2e_marker_ok(text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_test_title(text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_trailer(text, text, text, bigint) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_screening() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_screening_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_title(text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_trailer(text, text, text, bigint) TO authenticated;
GRANT EXECUTE ON FUNCTION public.ctx_screening() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_screening_brief() TO authenticated, valemont_readonly;

-- ===================================================================== 9
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with the expected
-- message or SQLSTATE.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_ssrc   bigint;
    v_hsrc   bigint;
    v_tsrc   bigint;
    v_sstory bigint;
    v_hstory bigint;
    v_tstory bigint;
    v_item   bigint;
    v_title  bigint;
    v_ok     boolean;
    v_n      int;
    v_ctx    jsonb;
    v_row    record;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the pillar CHECKs
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url)
            VALUES ('selftest-051-x', 'x', ARRAY['screening', 'home'], 'rss', 'https://selftest.invalid/x');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a source in screening and home was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title) VALUES (ARRAY['screening', 'tech'], 'x');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a story in screening and tech was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['screening'], 'x', 'tooling');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tech category on a screening story was accepted'; END IF;

        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-051-s', 'Self-test 051 trailers', ARRAY['screening'], 'rss', 'https://selftest.invalid/051s')
        RETURNING id INTO v_ssrc;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-051-h', 'Self-test 051 home', ARRAY['home'], 'rss', 'https://selftest.invalid/051h')
        RETURNING id INTO v_hsrc;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-051-t', 'Self-test 051 tech', ARRAY['tech'], 'rss', 'https://selftest.invalid/051t')
        RETURNING id INTO v_tsrc;
        FOR v_n IN 1..3 LOOP
            INSERT INTO public.stories (pillars, title)
            VALUES (CASE v_n WHEN 1 THEN ARRAY['screening'] WHEN 2 THEN ARRAY['home'] ELSE ARRAY['tech'] END,
                    'self-test 051 #' || v_n)
            RETURNING id INTO v_item;
            CASE v_n WHEN 1 THEN v_sstory := v_item; WHEN 2 THEN v_hstory := v_item; ELSE v_tstory := v_item; END CASE;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (CASE v_n WHEN 1 THEN v_ssrc WHEN 2 THEN v_hsrc ELSE v_tsrc END, 'selftest051:' || v_n,
                    'https://selftest.invalid/051/' || v_n, 'self-test 051 #' || v_n, now(), v_item)
            RETURNING id INTO v_title;  -- (the item id, reused below)
            UPDATE public.stories SET lead_item_id = v_title WHERE id = v_item;
        END LOOP;

        -- ---------------------------------------------------- 2. the guard, both worlds
        FOR v_row IN SELECT * FROM (VALUES (v_sstory, 'home'), (v_sstory, 'tech'), (v_sstory, 'sports'),
                                           (v_hstory, 'screening'), (v_tstory, 'screening')) AS x(story, pillar) LOOP
            v_ok := false;
            BEGIN
                INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction)
                VALUES (v_owner, v_row.story, v_row.pillar, 'love');
            EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
            END;
            IF NOT v_ok THEN
                RAISE EXCEPTION 'SELFTEST FAILED: a % reaction on story % was accepted', v_row.pillar, v_row.story;
            END IF;
            v_ok := false;
            BEGIN
                INSERT INTO public.story_scores (story_id, pillar, score, components)
                VALUES (v_row.story, v_row.pillar, 1, '{}');
            EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
            END;
            IF NOT v_ok THEN
                RAISE EXCEPTION 'SELFTEST FAILED: a % score on story % was accepted', v_row.pillar, v_row.story;
            END IF;
        END LOOP;
        -- Within its own world: fine. Tech and Home keep their rules.
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_sstory, 'screening', 'love');
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_hstory, 'home', 'love');
        INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_tstory, 'tech', 'love');
        v_ok := false;
        BEGIN
            INSERT INTO public.feed_reactions (owner_id, story_id, pillar, reaction) VALUES (v_owner, v_tstory, 'home', 'love');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%crosses the tech line%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: the tech guard changed (a home reaction on tech)'; END IF;

        -- ---------------------------------------------------- 3. no screening story in Home, Sports or Tech For you
        INSERT INTO public.story_scores (story_id, pillar, score, components) VALUES (v_sstory, 'screening', 49, '{}');
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 100000, 0) f WHERE 'screening' = ANY (f.pillars))
           OR EXISTS (SELECT 1 FROM public.feed_for_you('sports', 100000, 0) f WHERE 'screening' = ANY (f.pillars))
           OR EXISTS (SELECT 1 FROM public.feed_for_you('tech', 100000, 0) f WHERE 'screening' = ANY (f.pillars))
           OR EXISTS (SELECT 1 FROM public.feed_for_you('home', 100000, 0, NULL, NULL, 0) f
                       WHERE 'screening' = ANY (f.pillars)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a screening story reached Home, Sports or Tech';
        END IF;

        -- ---------------------------------------------------- 4. titles and watch_status
        INSERT INTO public.titles (tmdb_id, kind, name, release_date, next_episode_date)
        VALUES (999999901, 'tv', 'self-test 051 show', NULL, public.owner_today() + 5)
        RETURNING id INTO v_title;
        v_ok := false;
        BEGIN
            INSERT INTO public.titles (tmdb_id, kind, name) VALUES (999999901, 'tv', 'dup');
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a duplicate tmdb_id + kind was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.titles (tmdb_id, kind, name, next_episode_date) VALUES (999999902, 'movie', 'm', now()::date);
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a movie with a next episode was accepted'; END IF;

        -- Want -> watching -> finished -> want (a re-watch): four rows; finished_at the trigger's.
        INSERT INTO public.watch_status (owner_id, title_id, status, finished_at)
        VALUES (v_owner, v_title, 'want', '2000-01-01');
        IF (SELECT finished_at FROM public.watch_status WHERE title_id = v_title) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a want row kept the writer''s finished_at';
        END IF;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_title, 'watching');
        IF (SELECT status FROM public.v_watch_current WHERE title_id = v_title) <> 'watching' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_watch_current is not the latest move';
        END IF;
        v_ctx := public.ctx_screening();
        IF NOT (v_ctx -> 'watching') @> '[{"title": "self-test 051 show"}]'::jsonb
           OR NOT (v_ctx -> 'upcoming_30d') @> '[{"title": "self-test 051 show", "shelf": "watching"}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening watching or upcoming: %', left(v_ctx::text, 400);
        END IF;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_title, 'finished');
        IF (SELECT finished_at FROM public.v_watch_current WHERE title_id = v_title) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a move to finished has no finished_at';
        END IF;
        IF NOT (public.ctx_screening() -> 'finished_recent') @> '[{"title": "self-test 051 show"}]'::jsonb
           OR (public.ctx_screening() -> 'upcoming_30d') @> '[{"title": "self-test 051 show"}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening after finished';
        END IF;
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_title, 'want');
        IF (SELECT count(*) FROM public.watch_status WHERE title_id = v_title) <> 4
           OR (SELECT count(*) FROM public.watch_status WHERE title_id = v_title AND status = 'finished'
                 AND finished_at IS NOT NULL) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the history is not four rows with one finished';
        END IF;
        -- Append-only. watch_status is referenced by no table: its TRUNCATE names it alone.
        v_n := 0;
        BEGIN
            UPDATE public.watch_status SET status = 'want' WHERE title_id = v_title;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.watch_status WHERE title_id = v_title;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.watch_status;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of UPDATE, DELETE, TRUNCATE refused on watch_status (expected 3)', v_n;
        END IF;

        -- ---------------------------------------------------- 5. title_trailers
        INSERT INTO public.title_trailers (item_id, title_id, status, query)
        VALUES ((SELECT lead_item_id FROM public.stories WHERE id = v_sstory), v_title, 'matched', 'self-test 051 show');
        v_ok := false;
        BEGIN
            INSERT INTO public.title_trailers (item_id, title_id, status)
            VALUES ((SELECT lead_item_id FROM public.stories WHERE id = v_hstory), NULL, 'matched');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a matched trailer with no title was accepted'; END IF;
        IF NOT (public.ctx_screening() -> 'new_trailers') @> '[{"title": "self-test 051 show"}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening new_trailers';
        END IF;

        -- ---------------------------------------------------- 6. context registry
        IF public.ctx_screening_brief() IS NOT NULL
           OR (SELECT count(*) FROM public.context_providers
                WHERE (key = 'screening' AND NOT brief_section AND enabled)
                   OR (key = 'screening_brief' AND brief_section AND enabled)) <> 2
           OR (public.context_snapshot() -> 'screening') ? 'error' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the screening providers';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '051 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('051_screening')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the pillar CHECKs list screening (6) and stories_category_screening exists
--   (b) titles: RLS on, 3 policies; title_trailers 1; watch_status 2; anon nothing;
--       authenticated: titles S/I/U, title_trailers S, watch_status S/I (no U/D);
--       readonly reads all three and v_watch_current; 0 rows each
--   (c) 3 watch_status triggers (stamp, immutable, no_truncate)
--   (d) providers: screening (not a brief section) and screening_brief (a brief
--       section), both enabled; ctx_screening_brief() IS NULL
--   (e) `_e2e-screening` inactive, is_test, pillar screening
--   (f) the log row
--
-- (a)
-- SELECT conrelid::regclass, conname FROM pg_constraint
--  WHERE contype = 'c' AND pg_get_constraintdef(oid) LIKE '%screening%' ORDER BY 1, 2;
-- (b)
-- SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
--        has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon,
--        has_table_privilege('authenticated', c.oid, 'SELECT') AS auth_s,
--        has_table_privilege('authenticated', c.oid, 'INSERT') AS auth_i,
--        has_table_privilege('authenticated', c.oid, 'UPDATE') AS auth_u,
--        has_table_privilege('authenticated', c.oid, 'DELETE') AS auth_d,
--        has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS ro
--   FROM pg_class c WHERE c.relname IN ('titles', 'title_trailers', 'watch_status', 'v_watch_current') ORDER BY 1;
-- SELECT (SELECT count(*) FROM titles) AS titles, (SELECT count(*) FROM title_trailers) AS trailers,
--        (SELECT count(*) FROM watch_status) AS moves;
-- (c)
-- SELECT tgname FROM pg_trigger WHERE tgrelid = 'watch_status'::regclass AND NOT tgisinternal ORDER BY 1;
-- (d)
-- SELECT key, brief_section, enabled, sort FROM context_providers WHERE key LIKE 'screening%' ORDER BY key;
-- SELECT ctx_screening_brief() IS NULL AS silent, jsonb_object_keys(ctx_screening()) AS keys;
-- (e)
-- SELECT slug, active, is_test, pillars FROM sources WHERE slug = '_e2e-screening';
-- (f)
-- SELECT name, applied_at FROM migration_log WHERE name = '051_screening';
