-- 053_screening_recs.sql — Screening Room recommendations (Chat 4, Phase 4; owner, 2026-10-15).
--
-- Recommendations from the owner's taste, release alerts for the shelves,
-- a second trailer source (TMDB videos), and "In the news" on a title page.
-- Design: docs/screening.md §7. The worker's math: workers/screening/recs.py.
--
-- What it holds:
--   1. titles gains the recommendation inputs: keywords, vote_average,
--      vote_count, popularity, original_language, last_episode_date (TV),
--      tonight (the owner's services it streams on, written by whoever
--      writes providers, from the twin mappings), and the embedding
--      (gte-small, 384) with embedded_at. The worker embeds each title once.
--   2. screening_seeds   the owner's 15 seen-and-loved titles (verified on
--                        TMDB 2026-10-15), grouped tv / film / romcom; each
--                        also gets a seed reaction (love, seen) in (3). The
--                        taste notes go to app_settings.seed_interests
--                        feed.screening.love.
--   3. title_reactions   APPEND-ONLY: Love / Neutral / Not interested on a
--                        title (a For you card, a title page, the seen-it
--                        pass), and `seen`. The latest reaction counts;
--                        seen is sticky. created_at is the trigger's.
--   4. v_title_state     each title's record: latest reaction, seen, seed,
--                        seen-pass answer, shelf, ever finished, latest
--                        trailer reaction. The worker and every read path
--                        exclude from it.
--   5. screening_candidates  the pool (lanes, which loved titles named it,
--                        last seen); title_scores (one row per scored title,
--                        updated in place); screening_rec_runs (APPEND-ONLY:
--                        each run's profile and its top 12, the record of
--                        "the next refresh shifts toward the Loves").
--   6. screening_seen_pass   the one-time seen-it list (~30, written once).
--   7. screening_for_you(limit, with_tests): the For you row with the
--      exclusions applied LIVE (a tap removes a card before the next
--      re-rank), the quality floor (6.8 / 200, recs.QUALITY_*), and "movies
--      as much as TV" (the best 4 of each kind, then the best of the rest:
--      recs.balance). screening_trailers_for_you(limit, offset, with_tests):
--      trailers ranked by their title's score.
--   8. Re-rank on reaction: a trigger on title_reactions, watch_status and
--      screening feed_reactions queues ONE `screening_rerank` job (60 s
--      debounce: a queued one absorbs the rest). Test titles never queue.
--   9. Release alerts: title_provider_arrivals (APPEND-ONLY, the refresh's
--      "newly on your service" log); screening_events_on(day) (a movie out,
--      a new episode, an arrival, for a Want or Watching title);
--      release_pushes (APPEND-ONLY, at most ONE real per owner's day, by
--      trigger) and release_alerts (APPEND-ONLY, never twice for the same
--      title and event, by unique index; a real alert only for a real title
--      on Want or Watching). Test pushes are exempt (is_test).
--  10. entities kind `title` (entities.title_id): a Want or Watching title
--      gets one by trigger; title_news(title, limit, with_tests): up to 3
--      recent Home stories that mention it. Home's feed is unchanged: title
--      entities have no follows and every other entity reader filters by
--      kind.
--  11. content_items.youtube_key (generated from the URL) with a UNIQUE
--      index: a trailer from the YouTube RSS path and from the TMDB path
--      lands once, whichever comes first (the second insert is a no-op).
--      Source kind `tmdb` (the `tmdb-videos` source, screening only, fetched
--      by the recommendations job, never by the 30-minute ingest).
--  12. ctx_screening gains for_you_top5, today, recent_loves and each shelf
--      title's tonight services; ctx_screening_brief speaks (today's
--      releases on the owner's services, the top pick and its "because"),
--      silent (NULL) when there is nothing to say.
--  13. Test helpers (the verified e2e marker): add_test_rec,
--      add_test_seen_title.
--
-- A self-test runs on paste (section 15), rolled back. PASTE BEFORE THE
-- MERGE: the worker and the web read the new schema.
-- Prerequisites: db/020, db/027, db/029, db/032-052.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- titles: the recommendation inputs.

ALTER TABLE titles ADD COLUMN IF NOT EXISTS keywords TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE titles ADD COLUMN IF NOT EXISTS vote_average REAL;
ALTER TABLE titles ADD COLUMN IF NOT EXISTS vote_count INTEGER;
ALTER TABLE titles ADD COLUMN IF NOT EXISTS popularity REAL;
ALTER TABLE titles ADD COLUMN IF NOT EXISTS original_language TEXT;
ALTER TABLE titles ADD COLUMN IF NOT EXISTS last_episode_date DATE;
ALTER TABLE titles ADD COLUMN IF NOT EXISTS tonight TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE titles ADD COLUMN IF NOT EXISTS embedding extensions.halfvec(384);
ALTER TABLE titles ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ;

ALTER TABLE titles DROP CONSTRAINT IF EXISTS titles_recs_inputs;
ALTER TABLE titles ADD CONSTRAINT titles_recs_inputs CHECK (
    cardinality(keywords) <= 30
    AND (vote_average IS NULL OR vote_average BETWEEN 0 AND 10)
    AND (vote_count IS NULL OR vote_count >= 0)
    AND (popularity IS NULL OR popularity >= 0)
    AND (original_language IS NULL OR length(original_language) <= 8)
    AND (last_episode_date IS NULL OR kind = 'tv')
    AND cardinality(tonight) <= 5
    AND tonight <@ ARRAY['Netflix', 'HBO Max', 'Paramount+', 'Starz', 'Prime Video']::text[]
    AND ((embedding IS NULL) = (embedded_at IS NULL)));

-- ===================================================================== 2
-- The seeds (owner, 2026-10-15). TMDB ids verified the same day.

CREATE TABLE IF NOT EXISTS screening_seeds (
    title_id    BIGINT       PRIMARY KEY REFERENCES titles (id) ON DELETE RESTRICT,
    grp         TEXT         NOT NULL CHECK (grp IN ('tv', 'film', 'romcom')),
    sort        INTEGER      NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

INSERT INTO titles (tmdb_id, kind, name)
VALUES (76331, 'tv', 'Succession'), (62852, 'tv', 'Billions'), (69740, 'tv', 'Ozark'),
       (73586, 'tv', 'Yellowstone'), (236235, 'tv', 'The Gentlemen'), (153312, 'tv', 'Tulsa King'),
       (106646, 'movie', 'The Wolf of Wall Street'), (50839, 'movie', 'Margin Call'),
       (4566, 'movie', 'Michael Clayton'), (949, 'movie', 'Heat'), (238, 'movie', 'The Godfather'),
       (473033, 'movie', 'Uncut Gems'), (8488, 'movie', 'Hitch'), (455207, 'movie', 'Crazy Rich Asians'),
       (14736, 'movie', 'Love & Basketball')
ON CONFLICT (tmdb_id, kind) DO NOTHING;

INSERT INTO screening_seeds (title_id, grp, sort)
SELECT t.id, s.grp, s.sort
  FROM (VALUES (76331, 'tv', 'tv', 1), (62852, 'tv', 'tv', 2), (69740, 'tv', 'tv', 3), (73586, 'tv', 'tv', 4),
               (236235, 'tv', 'tv', 5), (153312, 'tv', 'tv', 6), (106646, 'movie', 'film', 7),
               (50839, 'movie', 'film', 8), (4566, 'movie', 'film', 9), (949, 'movie', 'film', 10),
               (238, 'movie', 'film', 11), (473033, 'movie', 'film', 12), (8488, 'movie', 'romcom', 13),
               (455207, 'movie', 'romcom', 14), (14736, 'movie', 'romcom', 15)) AS s(tmdb_id, kind, grp, sort)
  JOIN titles t ON t.tmdb_id = s.tmdb_id AND t.kind = s.kind AND NOT t.is_test
ON CONFLICT (title_id) DO NOTHING;

-- The taste notes: love phrases, the feed engine's seed format. The fourth
-- note ("movies as much as TV") is a rule, not a topic: section 7's balance.
UPDATE app_settings
   SET seed_interests = jsonb_set(
           seed_interests, '{feed,screening}',
           jsonb_build_object('love', jsonb_build_array(
               'power, money and dynasty dramas', 'crime and business thrillers', 'romantic comedies'),
               'not_interested', '[]'::jsonb), true)
 WHERE (seed_interests -> 'feed') IS NOT NULL
   AND NOT ((seed_interests -> 'feed') ? 'screening');

-- ===================================================================== 3
-- title_reactions: append-only.

CREATE TABLE IF NOT EXISTS title_reactions (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    title_id    BIGINT       NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    -- 'none' clears a reaction (an un-tap; the seen-it pass's "Haven't seen").
    reaction    TEXT         NOT NULL CHECK (reaction IN ('love', 'neutral', 'not_interested', 'none')),
    seen        BOOLEAN      NOT NULL DEFAULT false,
    origin      TEXT         NOT NULL CHECK (origin IN ('seed', 'card', 'title', 'seen_pass')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT title_reactions_seed CHECK (origin <> 'seed' OR (reaction = 'love' AND seen)),
    -- The seen-it pass's three answers, exactly.
    CONSTRAINT title_reactions_pass CHECK (origin <> 'seen_pass'
        OR (reaction = 'love' AND seen) OR (reaction = 'not_interested' AND seen) OR (reaction = 'none' AND NOT seen)),
    -- A card or a title page never claims "seen" (the pass and the seeds do).
    CONSTRAINT title_reactions_seen CHECK (origin NOT IN ('card', 'title') OR NOT seen)
);
CREATE INDEX IF NOT EXISTS title_reactions_title ON title_reactions (title_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.title_reactions_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS title_reactions_stamp ON title_reactions;
CREATE TRIGGER title_reactions_stamp
    BEFORE INSERT ON title_reactions
    FOR EACH ROW EXECUTE FUNCTION public.title_reactions_stamp();
DROP TRIGGER IF EXISTS title_reactions_immutable ON title_reactions;
CREATE TRIGGER title_reactions_immutable
    BEFORE UPDATE OR DELETE ON title_reactions
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS title_reactions_no_truncate ON title_reactions;
CREATE TRIGGER title_reactions_no_truncate
    BEFORE TRUNCATE ON title_reactions
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The seeds: seen and loved, once each (as the owner).
INSERT INTO title_reactions (owner_id, title_id, reaction, seen, origin)
SELECT (SELECT owner_id FROM app_settings LIMIT 1), s.title_id, 'love', true, 'seed'
  FROM screening_seeds s
 WHERE NOT EXISTS (SELECT 1 FROM title_reactions r WHERE r.title_id = s.title_id AND r.origin = 'seed')
 ORDER BY s.sort;

-- ===================================================================== 4
-- v_title_state: each title's record, for the worker and every read path.

CREATE OR REPLACE VIEW v_title_state
WITH (security_invoker = true)
AS
SELECT t.id AS title_id, t.is_test,
       nullif(r.reaction, 'none') AS reaction, r.reacted_at, coalesce(r.seen, false) AS seen, r.seen_pass,
       (sd.title_id IS NOT NULL) AS seed, sd.grp AS seed_group,
       w.status AS shelf, coalesce(w.finished, false) AS finished, tr.reaction AS trailer_reaction
  FROM public.titles t
  LEFT JOIN LATERAL (
      SELECT (array_agg(x.reaction ORDER BY x.created_at DESC, x.id DESC))[1] AS reaction,
             max(x.created_at) AS reacted_at, bool_or(x.seen) AS seen,
             (array_agg(CASE WHEN x.reaction = 'love' THEN 'seen_loved'
                             WHEN x.reaction = 'not_interested' THEN 'seen_not_for_me'
                             ELSE 'not_seen' END ORDER BY x.created_at DESC, x.id DESC)
                  FILTER (WHERE x.origin = 'seen_pass'))[1] AS seen_pass
        FROM public.title_reactions x WHERE x.title_id = t.id) r ON true
  LEFT JOIN public.screening_seeds sd ON sd.title_id = t.id
  LEFT JOIN LATERAL (
      SELECT (array_agg(ws.status ORDER BY ws.created_at DESC, ws.id DESC))[1] AS status,
             bool_or(ws.status = 'finished') AS finished
        FROM public.watch_status ws WHERE ws.title_id = t.id) w ON true
  LEFT JOIN LATERAL (
      SELECT (array_agg(l.reaction ORDER BY l.reacted_at DESC))[1] AS reaction
        FROM public.title_trailers tt
        JOIN public.stories s ON s.lead_item_id = tt.item_id
        JOIN public.v_feed_reaction_latest l ON l.story_id = s.id AND l.pillar = 'screening'
       WHERE tt.title_id = t.id AND l.reaction IN ('love', 'neutral', 'not_interested')) tr ON true
 WHERE r.reacted_at IS NOT NULL OR sd.title_id IS NOT NULL OR w.status IS NOT NULL OR tr.reaction IS NOT NULL;

-- ===================================================================== 5
-- The pool, the scores and the runs.

CREATE TABLE IF NOT EXISTS screening_candidates (
    title_id       BIGINT       PRIMARY KEY REFERENCES titles (id) ON DELETE RESTRICT,
    lanes          TEXT[]       NOT NULL CHECK (cardinality(lanes) >= 1
                                    AND lanes <@ ARRAY['recommendations', 'similar', 'discover', 'trending']::text[]),
    -- The loved titles (titles.id) whose recommendations or similar lists named it.
    via_title_ids  BIGINT[]     NOT NULL DEFAULT '{}' CHECK (cardinality(via_title_ids) <= 60),
    first_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS screening_candidates_seen ON screening_candidates (last_seen_at);

CREATE TABLE IF NOT EXISTS screening_rec_runs (
    id              BIGSERIAL    PRIMARY KEY,
    -- full: gather + embed + rank (nightly, boot); rerank: the stored pool
    -- after a reaction; manual: scripts/screening_recs.py.
    reason          TEXT         NOT NULL CHECK (reason IN ('full', 'rerank', 'manual')),
    mean_vec        extensions.halfvec(384),
    love_vec        extensions.halfvec(384),
    ni_vec          extensions.halfvec(384),
    love_count      INTEGER      NOT NULL CHECK (love_count >= 0),
    ni_count        INTEGER      NOT NULL CHECK (ni_count >= 0),
    neutral_count   INTEGER      NOT NULL CHECK (neutral_count >= 0),
    seed_count      INTEGER      NOT NULL CHECK (seed_count >= 0),
    notes_weight    REAL         NOT NULL CHECK (notes_weight BETWEEN 0 AND 1),
    pool_n          INTEGER      NOT NULL CHECK (pool_n >= 0),
    eligible_n      INTEGER      NOT NULL CHECK (eligible_n >= 0),
    scored_n        INTEGER      NOT NULL CHECK (scored_n >= 0),
    tmdb_calls      INTEGER      NOT NULL DEFAULT 0 CHECK (tmdb_calls >= 0),
    -- The run's For you, as it ranked it: [{title_id, name, kind, score, because}].
    top             JSONB        NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(top) = 'array'),
    is_test         BOOLEAN      NOT NULL DEFAULT false,
    built_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS screening_rec_runs_built ON screening_rec_runs (built_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.screening_rec_runs_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.built_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS screening_rec_runs_stamp ON screening_rec_runs;
CREATE TRIGGER screening_rec_runs_stamp
    BEFORE INSERT ON screening_rec_runs
    FOR EACH ROW EXECUTE FUNCTION public.screening_rec_runs_stamp();
DROP TRIGGER IF EXISTS screening_rec_runs_immutable ON screening_rec_runs;
CREATE TRIGGER screening_rec_runs_immutable
    BEFORE UPDATE OR DELETE ON screening_rec_runs
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS screening_rec_runs_no_truncate ON screening_rec_runs;
CREATE TRIGGER screening_rec_runs_no_truncate
    BEFORE TRUNCATE ON screening_rec_runs
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- One row per scored title, updated in place by each run (the latest
-- profile's view). For you and Trailers For you read it.
CREATE TABLE IF NOT EXISTS title_scores (
    title_id          BIGINT       PRIMARY KEY REFERENCES titles (id) ON DELETE RESTRICT,
    score             REAL         NOT NULL CHECK (score BETWEEN -50 AND 50),
    because_title_id  BIGINT       REFERENCES titles (id) ON DELETE RESTRICT,
    components        JSONB        NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(components) = 'object'),
    -- NULL for a test row (add_test_rec).
    run_id            BIGINT       REFERENCES screening_rec_runs (id) ON DELETE RESTRICT,
    scored_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT title_scores_not_self CHECK (because_title_id IS DISTINCT FROM title_id)
);
CREATE INDEX IF NOT EXISTS title_scores_score ON title_scores (score DESC, title_id);

-- ===================================================================== 6
-- The seen-it pass: written once by the worker (about 30 titles).

CREATE TABLE IF NOT EXISTS screening_seen_pass (
    title_id    BIGINT       PRIMARY KEY REFERENCES titles (id) ON DELETE RESTRICT,
    grp         TEXT         NOT NULL CHECK (grp IN ('tv', 'film', 'romcom')),
    pos         INTEGER      NOT NULL CHECK (pos >= 0),
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ===================================================================== 7
-- The For you row and Trailers For you.

CREATE OR REPLACE FUNCTION public.screening_excluded(p_title_id bigint)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    -- recs.excluded: seen, a seed, finished, on any shelf, or reacted to.
    SELECT EXISTS (
        SELECT 1 FROM public.v_title_state s
         WHERE s.title_id = p_title_id
           AND (s.seen OR s.seed OR s.finished OR s.shelf IS NOT NULL
                OR s.reaction IN ('love', 'neutral', 'not_interested')));
$$;

CREATE OR REPLACE FUNCTION public.screening_for_you(p_limit integer DEFAULT 12, p_with_tests boolean DEFAULT false)
RETURNS TABLE (
    pos integer, title_id bigint, tmdb_id integer, kind text, name text, release_date date, poster_path text,
    score real, because_title_id bigint, because_name text, tonight text[], is_test boolean
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH e AS (
        SELECT t.id, t.tmdb_id, t.kind, t.name, t.release_date, t.poster_path, s.score, s.because_title_id,
               t.tonight, t.is_test
          FROM public.title_scores s
          JOIN public.titles t ON t.id = s.title_id
          JOIN public.screening_candidates c ON c.title_id = t.id
         WHERE (CASE WHEN t.is_test THEN p_with_tests
                     -- The pool of the last 3 days, at the quality floor
                     -- (recs.QUALITY_MIN_AVG, recs.QUALITY_MIN_COUNT).
                     ELSE c.last_seen_at > now() - interval '3 days'
                          AND t.vote_average >= 6.8 AND t.vote_count >= 200 END)
           AND NOT public.screening_excluded(t.id)
    ),
    r AS (
        -- recs.balance: the best 4 of each kind are guaranteed a place.
        SELECT e.*, (row_number() OVER (PARTITION BY e.kind ORDER BY e.score DESC, e.id) <= 4) AS guaranteed
          FROM e
    ),
    chosen AS (
        SELECT * FROM r ORDER BY r.guaranteed DESC, r.score DESC, r.id LIMIT greatest(p_limit, 0)
    )
    SELECT (row_number() OVER (ORDER BY c.score DESC, c.id))::integer, c.id, c.tmdb_id, c.kind, c.name,
           c.release_date, c.poster_path, c.score, c.because_title_id, b.name, c.tonight, c.is_test
      FROM chosen c
      LEFT JOIN public.titles b ON b.id = c.because_title_id
     ORDER BY c.score DESC, c.id;
$$;

-- Trailers For you: the screening pillar's kept trailers of the last 60
-- days, minus the ones reacted Neutral or Not interested; those matched to a
-- scored title by its score, then everything else newest first.
CREATE OR REPLACE FUNCTION public.screening_trailers_for_you(p_limit integer DEFAULT 30, p_offset integer DEFAULT 0,
                                                             p_with_tests boolean DEFAULT false)
RETURNS TABLE (pos integer, story_id bigint, title_id bigint, score real)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH tr AS (
        SELECT st.id AS story_id, tt.title_id, ts.score, st.first_seen_at
          FROM public.stories st
          JOIN public.content_items i ON i.id = st.lead_item_id AND i.url IS NOT NULL
          LEFT JOIN public.title_trailers tt ON tt.item_id = i.id AND tt.status = 'matched'
          LEFT JOIN public.title_scores ts ON ts.title_id = tt.title_id
         WHERE 'screening' = ANY (st.pillars) AND st.filtered_reason IS NULL
           AND (NOT st.is_test OR p_with_tests)
           AND st.first_seen_at > now() - interval '60 days'
           AND NOT EXISTS (SELECT 1 FROM public.v_feed_reaction_latest l
                            WHERE l.story_id = st.id AND l.pillar = 'screening'
                              AND l.reaction IN ('neutral', 'not_interested'))
    )
    SELECT (row_number() OVER (ORDER BY (tr.score IS NULL), tr.score DESC, tr.first_seen_at DESC, tr.story_id DESC))::integer,
           tr.story_id, tr.title_id, tr.score
      FROM tr
     ORDER BY (tr.score IS NULL), tr.score DESC, tr.first_seen_at DESC, tr.story_id DESC
     LIMIT greatest(p_limit, 0) OFFSET greatest(p_offset, 0);
$$;

-- ===================================================================== 8
-- Re-rank on reaction: one queued `screening_rerank` job, debounced.

CREATE OR REPLACE FUNCTION public.screening_request_rerank()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_test boolean;
BEGIN
    IF TG_TABLE_NAME = 'feed_reactions' THEN
        IF NEW.pillar <> 'screening' OR NEW.reaction = 'open' THEN
            RETURN NULL;
        END IF;
        SELECT st.is_test INTO v_test FROM public.stories st WHERE st.id = NEW.story_id;
    ELSE
        SELECT t.is_test INTO v_test FROM public.titles t WHERE t.id = NEW.title_id;
    END IF;
    IF coalesce(v_test, true) THEN
        RETURN NULL;  -- a test row never moves the real recommendations
    END IF;
    -- One writer at a time, so two reactions cannot both see "none queued".
    PERFORM pg_advisory_xact_lock(hashtext('screening_rerank'));
    IF NOT EXISTS (SELECT 1 FROM public.job_queue q WHERE q.kind = 'screening_rerank' AND q.status = 'queued') THEN
        INSERT INTO public.job_queue (kind, payload, run_after)
        VALUES ('screening_rerank', '{}'::jsonb, now() + interval '60 seconds');
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS title_reactions_rerank ON title_reactions;
CREATE TRIGGER title_reactions_rerank
    AFTER INSERT ON title_reactions
    FOR EACH ROW EXECUTE FUNCTION public.screening_request_rerank();
DROP TRIGGER IF EXISTS watch_status_rerank ON watch_status;
CREATE TRIGGER watch_status_rerank
    AFTER INSERT ON watch_status
    FOR EACH ROW EXECUTE FUNCTION public.screening_request_rerank();
DROP TRIGGER IF EXISTS feed_reactions_screening_rerank ON feed_reactions;
CREATE TRIGGER feed_reactions_screening_rerank
    AFTER INSERT ON feed_reactions
    FOR EACH ROW EXECUTE FUNCTION public.screening_request_rerank();

-- ===================================================================== 9
-- Release alerts.

-- The refresh's log of a Want or Watching title newly streaming on one of
-- the owner's services (it was not there at the previous refresh).
CREATE TABLE IF NOT EXISTS title_provider_arrivals (
    id           BIGSERIAL    PRIMARY KEY,
    title_id     BIGINT       NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    service      TEXT         NOT NULL CHECK (service IN ('Netflix', 'HBO Max', 'Paramount+', 'Starz', 'Prime Video')),
    -- The owner's date it was seen; set by the trigger.
    detected_on  DATE         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT title_provider_arrivals_once UNIQUE (title_id, service, detected_on)
);

CREATE OR REPLACE FUNCTION public.title_provider_arrivals_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.detected_on := public.owner_today();
    NEW.created_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS title_provider_arrivals_stamp ON title_provider_arrivals;
CREATE TRIGGER title_provider_arrivals_stamp
    BEFORE INSERT ON title_provider_arrivals
    FOR EACH ROW EXECUTE FUNCTION public.title_provider_arrivals_stamp();
DROP TRIGGER IF EXISTS title_provider_arrivals_immutable ON title_provider_arrivals;
CREATE TRIGGER title_provider_arrivals_immutable
    BEFORE UPDATE OR DELETE ON title_provider_arrivals
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS title_provider_arrivals_no_truncate ON title_provider_arrivals;
CREATE TRIGGER title_provider_arrivals_no_truncate
    BEFORE TRUNCATE ON title_provider_arrivals
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- One push a day at most (real); a forced test push is exempt.
CREATE TABLE IF NOT EXISTS release_pushes (
    id               BIGSERIAL    PRIMARY KEY,
    notification_id  BIGINT       REFERENCES notifications (id) ON DELETE RESTRICT,
    -- The owner's date; set by the trigger, never by the writer.
    local_day        DATE         NOT NULL,
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS release_pushes_one_a_day ON release_pushes (local_day) WHERE NOT is_test;

CREATE OR REPLACE FUNCTION public.release_pushes_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.local_day := public.owner_today();
    NEW.created_at := now();
    IF NOT NEW.is_test AND EXISTS (SELECT 1 FROM public.release_pushes p
                                    WHERE p.local_day = NEW.local_day AND NOT p.is_test) THEN
        RAISE EXCEPTION 'release_pushes: one release push a day (% already has one)', NEW.local_day;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS release_pushes_guard ON release_pushes;
CREATE TRIGGER release_pushes_guard
    BEFORE INSERT ON release_pushes
    FOR EACH ROW EXECUTE FUNCTION public.release_pushes_guard();
DROP TRIGGER IF EXISTS release_pushes_immutable ON release_pushes;
CREATE TRIGGER release_pushes_immutable
    BEFORE UPDATE OR DELETE ON release_pushes
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS release_pushes_no_truncate ON release_pushes;
CREATE TRIGGER release_pushes_no_truncate
    BEFORE TRUNCATE ON release_pushes
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- Each event a push announced: never twice for the same title and event.
CREATE TABLE IF NOT EXISTS release_alerts (
    id          BIGSERIAL    PRIMARY KEY,
    push_id     BIGINT       NOT NULL REFERENCES release_pushes (id) ON DELETE RESTRICT,
    title_id    BIGINT       NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    -- release:YYYY-MM-DD, episode:YYYY-MM-DD, arrival:<service>
    event_key   TEXT         NOT NULL CHECK (event_key ~ '^(release|episode):\d{4}-\d{2}-\d{2}$'
                                             OR event_key ~ '^arrival:(Netflix|HBO Max|Paramount\+|Starz|Prime Video)$'),
    service     TEXT         CHECK (service IN ('Netflix', 'HBO Max', 'Paramount+', 'Starz', 'Prime Video')),
    -- The push's, by trigger.
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS release_alerts_once ON release_alerts (title_id, event_key) WHERE NOT is_test;

CREATE OR REPLACE FUNCTION public.release_alerts_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    SELECT p.is_test INTO NEW.is_test FROM public.release_pushes p WHERE p.id = NEW.push_id;
    NEW.created_at := now();
    IF NEW.is_test IS NULL THEN
        RETURN NEW;  -- the foreign key refuses an unknown push
    END IF;
    IF NOT NEW.is_test AND NOT EXISTS (
            SELECT 1 FROM public.v_watch_current c
             WHERE c.title_id = NEW.title_id AND NOT c.is_test AND c.status IN ('want', 'watching')) THEN
        RAISE EXCEPTION 'release_alerts: title % is not a real Want or Watching title', NEW.title_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS release_alerts_guard ON release_alerts;
CREATE TRIGGER release_alerts_guard
    BEFORE INSERT ON release_alerts
    FOR EACH ROW EXECUTE FUNCTION public.release_alerts_guard();
DROP TRIGGER IF EXISTS release_alerts_immutable ON release_alerts;
CREATE TRIGGER release_alerts_immutable
    BEFORE UPDATE OR DELETE ON release_alerts
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS release_alerts_no_truncate ON release_alerts;
CREATE TRIGGER release_alerts_no_truncate
    BEFORE TRUNCATE ON release_alerts
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The day's events for the owner's Want and Watching titles (real only):
-- a movie's US release, a series' episode (the next or the latest airing
-- that day), and an arrival on one of the owner's services. `alerted`: a
-- real push already announced it. `service`: where it streams tonight (the
-- arrival's service, else the title's first tonight service), NULL when it
-- streams on none of them (a theatrical release).
CREATE OR REPLACE FUNCTION public.screening_events_on(p_day date DEFAULT NULL)
RETURNS TABLE (title_id bigint, tmdb_id integer, kind text, name text, what text, event_key text,
               service text, alerted boolean)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (SELECT coalesce(p_day, public.owner_today()) AS day),
    shelf AS (
        SELECT t.* FROM public.v_watch_current c JOIN public.titles t ON t.id = c.title_id
         WHERE NOT c.is_test AND c.status IN ('want', 'watching')
    ),
    ev AS (
        SELECT s.id, s.tmdb_id, s.kind, s.name, 'release' AS what, 'release:' || d.day::text AS event_key,
               s.tonight[1] AS service
          FROM shelf s, d WHERE s.kind = 'movie' AND s.release_date = d.day
        UNION ALL
        SELECT s.id, s.tmdb_id, s.kind, s.name, 'episode', 'episode:' || d.day::text, s.tonight[1]
          FROM shelf s, d WHERE s.kind = 'tv' AND (s.next_episode_date = d.day OR s.last_episode_date = d.day)
        UNION ALL
        SELECT s.id, s.tmdb_id, s.kind, s.name, 'arrival', 'arrival:' || a.service, a.service
          FROM shelf s JOIN public.title_provider_arrivals a ON a.title_id = s.id, d
         WHERE a.detected_on = d.day
    )
    SELECT ev.id, ev.tmdb_id, ev.kind, ev.name, ev.what, ev.event_key, ev.service,
           EXISTS (SELECT 1 FROM public.release_alerts ra
                    WHERE ra.title_id = ev.id AND ra.event_key = ev.event_key AND NOT ra.is_test)
      FROM ev
     ORDER BY (ev.service IS NULL), ev.name, ev.event_key;
$$;

-- ===================================================================== 10
-- Title entities and "In the news".

ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_kind_check;
ALTER TABLE entities ADD CONSTRAINT entities_kind_check
    CHECK (kind IN ('person', 'team', 'program', 'league', 'tour', 'company', 'broadcaster', 'title'));
ALTER TABLE entities ADD COLUMN IF NOT EXISTS title_id BIGINT UNIQUE REFERENCES titles (id) ON DELETE RESTRICT;
ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_title_kind;
ALTER TABLE entities ADD CONSTRAINT entities_title_kind CHECK ((kind = 'title') = (title_id IS NOT NULL));

-- A title's entity, once (its name unique among entities: on a clash the
-- kind, then the id, is appended). A test title's is "Test title: ...".
CREATE OR REPLACE FUNCTION public.title_entity_ensure(p_title_id bigint)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_t    public.titles%ROWTYPE;
    v_id   bigint;
    v_name text;
BEGIN
    SELECT e.id INTO v_id FROM public.entities e WHERE e.title_id = p_title_id;
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;
    SELECT * INTO v_t FROM public.titles WHERE id = p_title_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    v_name := CASE WHEN v_t.is_test THEN left('Test title: ' || v_t.name, 120) ELSE left(v_t.name, 120) END;
    IF EXISTS (SELECT 1 FROM public.entities e WHERE e.name = v_name) THEN
        v_name := left(v_name, 100) || ' (' || v_t.kind || ')';
    END IF;
    IF EXISTS (SELECT 1 FROM public.entities e WHERE e.name = v_name) THEN
        v_name := left(v_name, 100) || ' #' || v_t.id;
    END IF;
    INSERT INTO public.entities (kind, name, aliases, title_id, is_test)
    VALUES ('title', v_name, ARRAY[lower(left(v_t.name, 120))], v_t.id, v_t.is_test)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.watch_status_title_entity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NEW.status IN ('want', 'watching') THEN
        PERFORM public.title_entity_ensure(NEW.title_id);
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS watch_status_title_entity ON watch_status;
CREATE TRIGGER watch_status_title_entity
    AFTER INSERT ON watch_status
    FOR EACH ROW EXECUTE FUNCTION public.watch_status_title_entity();

-- Every title on a shelf now gets its entity.
SELECT public.title_entity_ensure(c.title_id) FROM v_watch_current c WHERE c.status IN ('want', 'watching');

-- Up to p_limit recent Home stories (30 days, kept, linkable) that mention a
-- title on Want or Watching, newest first. A one-word title matches only a
-- story entity equal to it ("Heat" in a weather headline never counts); a
-- longer one also matches the headline or summary as whole words. A test
-- title reads only test stories, a real one only real stories.
CREATE OR REPLACE FUNCTION public.title_news(p_title_id bigint, p_limit integer DEFAULT 3,
                                             p_with_tests boolean DEFAULT false)
RETURNS TABLE (story_id bigint, title text, summary text, url text, source_name text, first_seen_at timestamptz)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT st.id, st.title, st.summary, i.url, so.name, st.first_seen_at
      FROM public.titles t
      JOIN public.entities e ON e.title_id = t.id
      JOIN public.v_watch_current c ON c.title_id = t.id AND c.status IN ('want', 'watching')
      JOIN public.stories st ON 'home' = ANY (st.pillars) AND st.is_test = t.is_test
      JOIN public.content_items i ON i.id = st.lead_item_id AND i.url IS NOT NULL
      JOIN public.sources so ON so.id = i.source_id
     WHERE t.id = p_title_id AND (NOT t.is_test OR p_with_tests)
       AND st.filtered_reason IS NULL AND st.first_seen_at > now() - interval '30 days'
       AND EXISTS (
           SELECT 1 FROM unnest(e.aliases) AS a
            WHERE EXISTS (SELECT 1 FROM unnest(coalesce(st.entities, '{}'::text[])) AS x WHERE lower(x) = a)
               OR (a ~ ' ' AND lower(st.title || ' ' || coalesce(st.summary, ''))
                       ~ ('(^|[^a-z0-9])' || regexp_replace(a, '[.^$|?*+(){}\\\[\]]', '\\\&', 'g') || '([^a-z0-9]|$)')))
     ORDER BY st.first_seen_at DESC, st.id DESC
     LIMIT greatest(p_limit, 0);
$$;

-- ===================================================================== 11
-- One trailer, whichever source brings it first; the TMDB source.

ALTER TABLE content_items ADD COLUMN IF NOT EXISTS youtube_key TEXT
    GENERATED ALWAYS AS (substring(url FROM '^https://(?:www\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})')) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS content_items_youtube_key ON content_items (youtube_key)
    WHERE youtube_key IS NOT NULL AND NOT is_test;

ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_check;
ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ('rss', 'gnews', 'page', 'hn', 'github', 'tmdb'));
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_kind_url;
ALTER TABLE sources ADD CONSTRAINT sources_kind_url CHECK (
    (kind <> 'gnews' OR url ~ '^https://news\.google\.com/rss/search\?')
    AND (kind <> 'hn' OR url ~ '^https://hn\.algolia\.com/api/v1/')
    AND (kind <> 'github' OR url ~ '^https://github\.com/')
    AND (kind <> 'tmdb' OR url ~ '^https://api\.themoviedb\.org/'));
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_tmdb_screening;
ALTER TABLE sources ADD CONSTRAINT sources_tmdb_screening CHECK (
    kind <> 'tmdb' OR (pillars = ARRAY['screening']::text[] AND NOT official));

-- ===================================================================== 12
-- Context: Wags and the brief.

CREATE OR REPLACE FUNCTION public.ctx_screening()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH cur AS (
        SELECT c.title_id, c.status, c.finished_at, c.moved_at, t.name, t.kind, t.release_date, t.next_episode_date,
               t.tmdb_id, t.tonight
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
    ),
    loves AS (
        SELECT t.name, t.kind, s.reacted_at, s.finished
          FROM public.v_title_state s JOIN public.titles t ON t.id = s.title_id
         WHERE NOT s.is_test AND s.reaction = 'love' AND NOT s.seed
           AND s.reacted_at > now() - interval '30 days'
         ORDER BY s.reacted_at DESC
         LIMIT 10
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Screening Room: the owner''s shelves (watching, want, finished), what is coming up in the next 30 '
                 'days on them, the newest trailers matched to a title, the For you picks (each with the loved '
                 'title it is like), today''s releases and recent loves. "tonight" lists the owner''s services a '
                 'title streams on in the US (Netflix, HBO Max, Paramount+ with Showtime, Starz, Prime Video). For '
                 '"what should I watch tonight", suggest titles with a tonight service first: a Watching title, '
                 'then a For you pick.',
        'watching', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                         'title', c.name, 'kind', c.kind, 'next_episode', c.next_episode_date,
                         'tonight', CASE WHEN cardinality(c.tonight) > 0 THEN to_jsonb(c.tonight) END))
                       ORDER BY c.moved_at DESC) FROM cur c WHERE c.status = 'watching'), '[]'::jsonb),
        'want', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                     'title', c.name, 'kind', c.kind, 'release_date', c.release_date, 'next_episode', c.next_episode_date,
                     'tonight', CASE WHEN cardinality(c.tonight) > 0 THEN to_jsonb(c.tonight) END))
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
                                    FROM trailers t), '[]'::jsonb),
        'for_you_top5', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                      'title', f.name, 'kind', f.kind, 'because_you_loved', f.because_name,
                                      'tonight', CASE WHEN cardinality(f.tonight) > 0 THEN to_jsonb(f.tonight) END))
                                    ORDER BY f.pos)
                                    FROM public.screening_for_you(5, false) f), '[]'::jsonb),
        'today', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                               'title', e.name, 'kind', e.kind, 'what', e.what, 'on', e.service)))
                             FROM public.screening_events_on(NULL) e), '[]'::jsonb),
        'recent_loves', coalesce((SELECT jsonb_agg(jsonb_build_object('title', l.name, 'kind', l.kind,
                                                                      'finished', l.finished)
                                                   ORDER BY l.reacted_at DESC) FROM loves l), '[]'::jsonb)
    ));
$$;

-- The brief's section: today's releases on the owner's shelves (where they
-- stream), and the top For you pick with its "because". NULL (silent) when
-- there is neither.
CREATE OR REPLACE FUNCTION public.ctx_screening_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH rel AS (
        SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'title', e.name, 'kind', e.kind, 'what', e.what, 'on', e.service))), '[]'::jsonb) AS j
          FROM public.screening_events_on(NULL) e
    ),
    pick AS (
        SELECT jsonb_strip_nulls(jsonb_build_object(
                   'title', f.name, 'kind', f.kind, 'because_you_loved', f.because_name,
                   'tonight', CASE WHEN cardinality(f.tonight) > 0 THEN to_jsonb(f.tonight) END)) AS j
          FROM public.screening_for_you(1, false) f
    )
    SELECT CASE WHEN jsonb_array_length(rel.j) = 0 AND (SELECT j FROM pick) IS NULL THEN NULL
                ELSE jsonb_strip_nulls(jsonb_build_object('releases_today', rel.j, 'top_pick', (SELECT j FROM pick)))
           END
      FROM rel;
$$;

-- ===================================================================== 13
-- Test rows (e2e). Owner only, the verified e2e marker (db/051 e2e_marker_ok).

-- A TEST recommendation: a test title, scored at the top, "because" another
-- test title named p_because. Returns the recommended title's id.
CREATE OR REPLACE FUNCTION public.add_test_rec(p_marker text, p_kind text, p_name text, p_because text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_rec bigint;
    v_because bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_rec: the owner and a verified test marker only';
    END IF;
    v_rec := public.add_test_title(p_marker, p_kind, p_name);
    v_because := public.add_test_title(p_marker, p_kind, p_because);
    INSERT INTO public.screening_candidates (title_id, lanes) VALUES (v_rec, ARRAY['recommendations']);
    INSERT INTO public.title_scores (title_id, score, because_title_id, components)
    VALUES (v_rec, 49, v_because, '{"test": true}'::jsonb);
    RETURN v_rec;
END;
$$;

-- A TEST seen-it pass title, first in the pass and recommended until the
-- owner answers it. Returns its id.
CREATE OR REPLACE FUNCTION public.add_test_seen_title(p_marker text, p_kind text, p_name text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_seen_title: the owner and a verified test marker only';
    END IF;
    v_id := public.add_test_rec(p_marker, p_kind, p_name, p_name || ' (loved)');
    INSERT INTO public.screening_seen_pass (title_id, grp, pos, is_test)
    VALUES (v_id, CASE WHEN p_kind = 'tv' THEN 'tv' ELSE 'film' END, 0, true);
    RETURN v_id;
END;
$$;

-- ===================================================================== 14
-- Access.

ALTER TABLE screening_seeds ENABLE ROW LEVEL SECURITY;
ALTER TABLE title_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE screening_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE screening_rec_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE title_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE screening_seen_pass ENABLE ROW LEVEL SECURITY;
ALTER TABLE title_provider_arrivals ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_pushes ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_alerts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON screening_seeds, title_reactions, screening_candidates, screening_rec_runs, title_scores,
              screening_seen_pass, title_provider_arrivals, release_pushes, release_alerts, v_title_state
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE title_reactions_id_seq, screening_rec_runs_id_seq, title_provider_arrivals_id_seq,
                       release_pushes_id_seq, release_alerts_id_seq FROM anon, authenticated, PUBLIC;

-- The owner reads everything here; writes only her own reactions (card,
-- title page, seen-it pass: never a seed). The worker writes the rest.
GRANT SELECT ON screening_seeds, title_reactions, screening_candidates, screening_rec_runs, title_scores,
                screening_seen_pass, title_provider_arrivals, release_pushes, release_alerts, v_title_state
    TO authenticated;
GRANT INSERT ON title_reactions TO authenticated;
GRANT USAGE ON SEQUENCE title_reactions_id_seq TO authenticated;
DO $$
DECLARE
    v_t text;
BEGIN
    FOREACH v_t IN ARRAY ARRAY['screening_seeds', 'title_reactions', 'screening_candidates', 'screening_rec_runs',
                               'title_scores', 'screening_seen_pass', 'title_provider_arrivals', 'release_pushes',
                               'release_alerts'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', v_t || '_owner_read', v_t);
        EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING ((SELECT public.is_owner()))',
                       v_t || '_owner_read', v_t);
    END LOOP;
END
$$;
DROP POLICY IF EXISTS title_reactions_owner_insert ON title_reactions;
CREATE POLICY title_reactions_owner_insert ON title_reactions FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()) AND origin IN ('card', 'title', 'seen_pass'));

GRANT SELECT ON screening_seeds, title_reactions, screening_candidates, screening_rec_runs, title_scores,
                screening_seen_pass, title_provider_arrivals, release_pushes, release_alerts, v_title_state
    TO valemont_readonly;

REVOKE ALL ON FUNCTION public.title_reactions_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.screening_rec_runs_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.screening_request_rerank() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.title_provider_arrivals_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_pushes_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_alerts_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.title_entity_ensure(bigint) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.watch_status_title_entity() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.screening_excluded(bigint) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.screening_for_you(integer, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.screening_trailers_for_you(integer, integer, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.screening_events_on(date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.title_news(bigint, integer, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_rec(text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_seen_title(text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.screening_excluded(bigint) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.screening_for_you(integer, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.screening_trailers_for_you(integer, integer, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.screening_events_on(date) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.title_news(bigint, integer, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_rec(text, text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_seen_title(text, text, text) TO authenticated;
-- ctx_screening and ctx_screening_brief keep db/051's grants (replaced in place).

-- ===================================================================== 15
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with the expected
-- message or SQLSTATE.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_vec    text := '[' || array_to_string(array_fill(0.05::real, ARRAY[384]), ',') || ']';
    v_ids    bigint[] := '{}';
    v_id     bigint;
    v_a      bigint;
    v_b      bigint;
    v_c      bigint;
    v_src    bigint;
    v_story  bigint;
    v_item   bigint;
    v_push   bigint;
    v_run    bigint;
    v_ok     boolean;
    v_n      int;
    v_ctx    jsonb;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the seeds
        IF (SELECT count(*) FROM public.screening_seeds) <> 15
           OR (SELECT count(*) FROM public.screening_seeds WHERE grp = 'romcom') <> 3
           OR (SELECT count(*) FROM public.title_reactions WHERE origin = 'seed' AND reaction = 'love' AND seen) <> 15
           OR NOT EXISTS (SELECT 1 FROM public.v_title_state s JOIN public.titles t ON t.id = s.title_id
                           WHERE t.tmdb_id = 949 AND t.kind = 'movie' AND s.seed AND s.seen AND s.reaction = 'love') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the 15 seeds and their seen-and-loved reactions';
        END IF;

        -- ---------------------------------------------------- 2. title_reactions rules
        INSERT INTO public.titles (tmdb_id, kind, name, vote_average, vote_count, tonight, embedding, embedded_at)
        VALUES (999999531, 'movie', 'selftest 053 alpha', 7.5, 900, ARRAY['Netflix'], v_vec::extensions.halfvec, now())
        RETURNING id INTO v_a;
        INSERT INTO public.titles (tmdb_id, kind, name, vote_average, vote_count)
        VALUES (999999532, 'movie', 'selftest 053 bravo', 7.0, 500) RETURNING id INTO v_b;
        INSERT INTO public.titles (tmdb_id, kind, name, vote_average, vote_count)
        VALUES (999999533, 'tv', 'selftest 053 charlie show', 8.0, 300) RETURNING id INTO v_c;
        v_ok := false;
        BEGIN
            INSERT INTO public.titles (tmdb_id, kind, name, embedding) VALUES (999999534, 'movie', 'x', v_vec::extensions.halfvec);
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an embedding without embedded_at was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.titles (tmdb_id, kind, name, tonight) VALUES (999999535, 'movie', 'x', ARRAY['Hulu']);
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tonight service that is not the owner''s was accepted'; END IF;
        FOR v_n IN 1..3 LOOP
            v_ok := false;
            BEGIN
                INSERT INTO public.title_reactions (owner_id, title_id, reaction, seen, origin)
                VALUES (v_owner, v_a, CASE v_n WHEN 1 THEN 'neutral' ELSE 'love' END, v_n <> 3,
                        CASE v_n WHEN 1 THEN 'seen_pass' WHEN 2 THEN 'card' ELSE 'seed' END);
            EXCEPTION WHEN check_violation THEN v_ok := true;
            END;
            IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: title_reactions rule % accepted a bad row', v_n; END IF;
        END LOOP;

        -- ---------------------------------------------------- 3. the rerank queue (debounced)
        DELETE FROM public.job_queue WHERE kind = 'screening_rerank' AND status = 'queued';
        INSERT INTO public.title_reactions (owner_id, title_id, reaction, origin) VALUES (v_owner, v_b, 'love', 'card');
        INSERT INTO public.title_reactions (owner_id, title_id, reaction, origin) VALUES (v_owner, v_b, 'none', 'title');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_c, 'want');
        IF (SELECT count(*) FROM public.job_queue WHERE kind = 'screening_rerank' AND status = 'queued'
                AND run_after > now() + interval '50 seconds') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: three reactions should queue exactly one debounced rerank';
        END IF;
        -- created_at is the trigger's; append-only; TRUNCATE refused (no table references it).
        v_n := 0;
        BEGIN
            UPDATE public.title_reactions SET reaction = 'love' WHERE title_id = v_b;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.title_reactions WHERE title_id = v_b;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.title_reactions;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 mutations refused on title_reactions', v_n; END IF;

        -- ---------------------------------------------------- 4. v_title_state
        IF (SELECT reaction FROM public.v_title_state WHERE title_id = v_b) IS NOT NULL
           OR (SELECT shelf FROM public.v_title_state WHERE title_id = v_c) <> 'want'
           OR EXISTS (SELECT 1 FROM public.v_title_state WHERE title_id = v_a) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_title_state (a cleared love, a shelf, no record)';
        END IF;
        -- The seen-it pass's "Seen, not for me": seen and not interested.
        INSERT INTO public.title_reactions (owner_id, title_id, reaction, seen, origin)
        VALUES (v_owner, v_b, 'not_interested', true, 'seen_pass');
        IF NOT (SELECT seen AND reaction = 'not_interested' AND seen_pass = 'seen_not_for_me'
                  FROM public.v_title_state WHERE title_id = v_b) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the seen-it pass answer in v_title_state';
        END IF;

        -- ---------------------------------------------------- 5. For you: exclusions, floor, balance
        INSERT INTO public.screening_rec_runs (reason, love_count, ni_count, neutral_count, seed_count, notes_weight,
                                               pool_n, eligible_n, scored_n)
        VALUES ('manual', 15, 0, 0, 15, 0.3, 1, 1, 1) RETURNING id INTO v_run;
        v_n := 0;
        BEGIN
            UPDATE public.screening_rec_runs SET pool_n = 2 WHERE id = v_run;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            -- title_scores references the runs: named together (CLAUDE.md §5, db/042).
            TRUNCATE public.screening_rec_runs, public.title_scores;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: screening_rec_runs is not append-only'; END IF;
        -- Twelve movies (40..29) outscore five series (20..16); an 18th movie,
        -- below the quality floor, would top them all (45).
        FOR v_n IN 1..18 LOOP
            INSERT INTO public.titles (tmdb_id, kind, name, vote_average, vote_count)
            VALUES (999999540 + v_n, CASE WHEN v_n BETWEEN 13 AND 17 THEN 'tv' ELSE 'movie' END,
                    'selftest 053 pick ' || v_n, CASE WHEN v_n = 18 THEN 6.0 ELSE 7.2 END, 400)
            RETURNING id INTO v_id;
            v_ids := v_ids || v_id;
            INSERT INTO public.screening_candidates (title_id, lanes) VALUES (v_id, ARRAY['recommendations']);
            INSERT INTO public.title_scores (title_id, score, because_title_id, run_id)
            VALUES (v_id, CASE WHEN v_n = 18 THEN 45 WHEN v_n <= 12 THEN 41 - v_n ELSE 33 - v_n END, v_a, v_run);
        END LOOP;
        -- (At first paste no real score exists; the checks read only these rows.)
        IF EXISTS (SELECT 1 FROM public.screening_for_you(100, false) f WHERE f.title_id = v_ids[18]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a title below the quality floor was recommended';
        END IF;
        -- Excluded live: a Not interested, a seen-it "Seen, loved", a shelf.
        INSERT INTO public.title_reactions (owner_id, title_id, reaction, origin) VALUES (v_owner, v_ids[1], 'not_interested', 'card');
        INSERT INTO public.title_reactions (owner_id, title_id, reaction, seen, origin) VALUES (v_owner, v_ids[2], 'love', true, 'seen_pass');
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_ids[13], 'want');
        IF EXISTS (SELECT 1 FROM public.screening_for_you(100, false) f WHERE f.title_id IN (v_ids[1], v_ids[2], v_ids[13]))
           OR NOT public.screening_excluded(v_ids[1]) OR public.screening_excluded(v_ids[3]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: For you shows an excluded title';
        END IF;
        -- Balance: 10 movies (3..12) and 4 series (14..17) are left. By score
        -- alone the 12 would be 10 movies and 2 series; the rule keeps 4 series
        -- and drops the two lowest movies (11, 12).
        SELECT count(*) FILTER (WHERE f.kind = 'tv'), count(*) FILTER (WHERE f.title_id IN (v_ids[11], v_ids[12]))
          INTO v_n, v_id
          FROM public.screening_for_you(12, false) f WHERE f.title_id = ANY (v_ids);
        IF v_n <> 4 OR v_id <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: For you balance (% series, % of the two lowest movies)', v_n, v_id;
        END IF;
        IF (SELECT f.because_name FROM public.screening_for_you(100, false) f WHERE f.title_id = v_ids[3])
           <> 'selftest 053 alpha' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: For you''s because name';
        END IF;

        -- ---------------------------------------------------- 6. release alerts
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_a, 'watching');
        UPDATE public.titles SET release_date = public.owner_today() WHERE id = v_a;
        INSERT INTO public.title_provider_arrivals (title_id, service, detected_on) VALUES (v_a, 'Netflix', '2000-01-01');
        IF (SELECT detected_on FROM public.title_provider_arrivals WHERE title_id = v_a) <> public.owner_today() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: detected_on is not the trigger''s';
        END IF;
        IF (SELECT count(*) FROM public.screening_events_on(NULL) e WHERE e.title_id = v_a AND NOT e.alerted) <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a release and an arrival today (expected 2 events)';
        END IF;
        INSERT INTO public.release_pushes (local_day) VALUES ('2000-01-01') RETURNING id INTO v_push;
        IF (SELECT local_day FROM public.release_pushes WHERE id = v_push) <> public.owner_today() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: local_day is not the trigger''s';
        END IF;
        INSERT INTO public.release_alerts (push_id, title_id, event_key, service)
        VALUES (v_push, v_a, 'release:' || public.owner_today()::text, 'Netflix');
        IF (SELECT count(*) FROM public.screening_events_on(NULL) e WHERE e.title_id = v_a AND e.alerted) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the alerted event is not marked';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.release_pushes (is_test) VALUES (false);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%one release push a day%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a second real release push in a day was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.release_alerts (push_id, title_id, event_key)
            VALUES (v_push, v_a, 'release:' || public.owner_today()::text);
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: the same title and event alerted twice'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.release_alerts (push_id, title_id, event_key)
            VALUES (v_push, v_ids[3], 'episode:' || public.owner_today()::text);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%not a real Want or Watching title%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an alert for a title off the shelves was accepted'; END IF;
        -- A test push is exempt from both: a second today, the same event again.
        INSERT INTO public.release_pushes (is_test) VALUES (true) RETURNING id INTO v_id;
        INSERT INTO public.release_alerts (push_id, title_id, event_key)
        VALUES (v_id, v_a, 'release:' || public.owner_today()::text);
        IF NOT (SELECT is_test FROM public.release_alerts WHERE push_id = v_id) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test push''s alert is not a test alert';
        END IF;
        -- Append-only, all three. release_alerts references release_pushes: named together.
        v_n := 0;
        BEGIN
            UPDATE public.release_alerts SET service = NULL WHERE push_id = v_push;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.release_pushes WHERE id = v_push;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.release_pushes, public.release_alerts;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.title_provider_arrivals WHERE title_id = v_a;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.title_provider_arrivals;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 5 mutations refused on the release tables', v_n; END IF;

        -- ---------------------------------------------------- 7. title entities and title_news
        IF NOT EXISTS (SELECT 1 FROM public.entities e WHERE e.title_id = v_c AND e.kind = 'title'
                         AND e.aliases = ARRAY['selftest 053 charlie show'])
           OR NOT EXISTS (SELECT 1 FROM public.entities e WHERE e.title_id = v_a) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a Want or Watching title has no entity';
        END IF;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-053-h', 'Self-test 053 home', ARRAY['home'], 'rss', 'https://selftest.invalid/053h')
        RETURNING id INTO v_src;
        FOR v_n IN 1..3 LOOP
            INSERT INTO public.stories (pillars, title, entities)
            VALUES (ARRAY['home'],
                    CASE v_n WHEN 1 THEN 'Inside the selftest 053 charlie show finale'
                             WHEN 2 THEN 'A story with no mention'
                             ELSE 'Selftest 053 charlie showrunner speaks' END,
                    CASE v_n WHEN 2 THEN ARRAY['Selftest 053 Alpha'] ELSE NULL END)
            RETURNING id INTO v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest053:' || v_n, 'https://selftest.invalid/053/' || v_n, 'x', now(), v_story)
            RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
        END LOOP;
        -- Multi-word: the whole-word headline match only ("showrunner" is not "show").
        IF (SELECT count(*) FROM public.title_news(v_c, 3, false)) <> 1
           OR (SELECT title FROM public.title_news(v_c, 3, false)) <> 'Inside the selftest 053 charlie show finale' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: title_news (multi-word headline match)';
        END IF;
        -- The entity path: a story entity equal to the alias (the only path for a one-word title).
        IF (SELECT count(*) FROM public.title_news(v_a, 3, false) n WHERE n.title = 'A story with no mention') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: title_news (story entity match)';
        END IF;
        -- Off the shelves (finished): no news.
        INSERT INTO public.watch_status (owner_id, title_id, status) VALUES (v_owner, v_c, 'finished');
        IF EXISTS (SELECT 1 FROM public.title_news(v_c, 3, false)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: title_news for a finished title';
        END IF;

        -- ---------------------------------------------------- 8. one trailer from either source
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-053-s', 'Self-test 053 trailers', ARRAY['screening'], 'rss', 'https://selftest.invalid/053s')
        RETURNING id INTO v_src;
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-053-tmdb', 'Self-test 053 TMDB', ARRAY['screening'], 'tmdb', 'https://api.themoviedb.org/3')
        RETURNING id INTO v_id;
        INSERT INTO public.stories (pillars, title) VALUES (ARRAY['screening'], 'self-test 053 trailer') RETURNING id INTO v_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'yt:video:SelfTest053', 'https://www.youtube.com/watch?v=SelfTest053', 'x', now(), v_story)
        RETURNING id INTO v_item;
        IF (SELECT youtube_key FROM public.content_items WHERE id = v_item) <> 'SelfTest053' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: youtube_key from the watch URL';
        END IF;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_id, 'yt:video:SelfTest053', 'https://www.youtube.com/watch?feature=x&v=SelfTest053', 'x', now(), v_story)
        ON CONFLICT DO NOTHING;
        IF (SELECT count(*) FROM public.content_items WHERE youtube_key = 'SelfTest053') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the same YouTube video landed twice';
        END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.sources (slug, name, pillars, kind, url)
            VALUES ('selftest-053-bad', 'x', ARRAY['home'], 'tmdb', 'https://api.themoviedb.org/3');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a tmdb source outside screening was accepted'; END IF;
        -- Trailers For you: the matched, scored trailer first.
        INSERT INTO public.title_trailers (item_id, title_id, status) VALUES (v_item, v_ids[3], 'matched');
        IF (SELECT f.title_id FROM public.screening_trailers_for_you(1000, 0, false) f WHERE f.story_id = v_story)
           IS DISTINCT FROM v_ids[3] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Trailers For you did not rank the matched trailer';
        END IF;

        -- ---------------------------------------------------- 9. context
        v_ctx := public.ctx_screening();
        IF NOT (v_ctx ? 'for_you_top5' AND v_ctx ? 'today' AND v_ctx ? 'recent_loves')
           OR NOT (v_ctx -> 'today') @> '[{"title": "selftest 053 alpha"}]'::jsonb
           OR NOT (v_ctx -> 'watching') @> '[{"title": "selftest 053 alpha", "tonight": ["Netflix"]}]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening keys: %', left(v_ctx::text, 400);
        END IF;
        IF NOT (public.ctx_screening_brief() -> 'releases_today') @> '[{"title": "selftest 053 alpha", "on": "Netflix"}]'::jsonb
           OR (public.context_snapshot() -> 'screening_brief') ? 'error'
           OR (public.context_snapshot() -> 'screening') ? 'error' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_screening_brief: %', left(public.ctx_screening_brief()::text, 400);
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '053 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('053_screening_recs')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) 15 seeds (6 tv, 6 film, 3 romcom), 15 seed reactions, the notes
--   (b) the 9 new tables RLS on, 1 policy each (title_reactions 2); anon
--       nothing; authenticated SELECT (title_reactions also INSERT, no
--       UPDATE/DELETE); readonly reads all and v_title_state
--   (c) the triggers: title_reactions 4, screening_rec_runs 3, release_pushes 3,
--       release_alerts 3, title_provider_arrivals 3, watch_status +2 (rerank,
--       title entity), feed_reactions +1
--   (d) readonly executes screening_for_you, screening_trailers_for_you,
--       screening_events_on, title_news; anon none
--   (e) content_items.youtube_key and its unique index; the tmdb source rules
--   (f) entities: kind title allowed; every Want or Watching title has one
--   (g) ctx_screening has for_you_top5, today, recent_loves; the log row
--
-- (a)
-- SELECT grp, count(*) FROM screening_seeds GROUP BY grp ORDER BY grp;
-- SELECT count(*) FROM title_reactions WHERE origin = 'seed';
-- SELECT seed_interests -> 'feed' -> 'screening' FROM app_settings;
-- (b)
-- SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
--        has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon,
--        has_table_privilege('authenticated', c.oid, 'SELECT') AS auth_s,
--        has_table_privilege('authenticated', c.oid, 'INSERT') AS auth_i,
--        has_table_privilege('authenticated', c.oid, 'UPDATE') AS auth_u,
--        has_table_privilege('authenticated', c.oid, 'DELETE') AS auth_d,
--        has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS ro
--   FROM pg_class c
--  WHERE c.relname IN ('screening_seeds', 'title_reactions', 'screening_candidates', 'screening_rec_runs',
--                      'title_scores', 'screening_seen_pass', 'title_provider_arrivals', 'release_pushes',
--                      'release_alerts', 'v_title_state')
--  ORDER BY 1;
-- (c)
-- SELECT tgrelid::regclass, count(*) FROM pg_trigger
--  WHERE NOT tgisinternal AND tgrelid::regclass::text IN ('title_reactions', 'screening_rec_runs', 'release_pushes',
--        'release_alerts', 'title_provider_arrivals', 'watch_status', 'feed_reactions')
--  GROUP BY 1 ORDER BY 1;
-- (d)
-- SELECT p.proname, has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS ro,
--        has_function_privilege('anon', p.oid, 'EXECUTE') AS anon
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace AND n.nspname = 'public'
--  WHERE p.proname IN ('screening_for_you', 'screening_trailers_for_you', 'screening_events_on', 'title_news',
--                      'add_test_rec', 'add_test_seen_title')
--  ORDER BY 1;
-- (e)
-- SELECT attname, attgenerated FROM pg_attribute WHERE attrelid = 'content_items'::regclass AND attname = 'youtube_key';
-- SELECT indexname FROM pg_indexes WHERE indexname = 'content_items_youtube_key';
-- SELECT conname FROM pg_constraint WHERE conname IN ('sources_kind_check', 'sources_kind_url', 'sources_tmdb_screening');
-- (f)
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'entities_kind_check';
-- SELECT count(*) AS shelf, count(e.id) AS with_entity FROM v_watch_current c
--   LEFT JOIN entities e ON e.title_id = c.title_id WHERE c.status IN ('want', 'watching');
-- (g)
-- SELECT jsonb_object_keys(ctx_screening()) ORDER BY 1;
-- SELECT name, applied_at FROM migration_log WHERE name = '053_screening_recs';
