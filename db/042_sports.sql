-- 042_sports.sql — Sports News: event boosts, move proposals, the sports
-- seeds and entities, a per-entity cap and sport chips in For you, and the
-- sports context provider (Chat 3, Phase 3).
--
-- Design: docs/sports_news.md (approved by the owner 2026-10-12 17:27
-- Chicago, with the decisions recorded at its top). Dates and names:
-- docs/sports_calendar_2026.md (fetched 2026-10-12 from the official
-- sources named on each row). Numbers that also live in workers/feed/rank.py
-- (ENTITY_CAP, ENTITY_CAP_BOOSTED and the db/039 ones) are pinned to it by a
-- test.
--
-- What it holds:
--   entities.is_test       test entities ("Test ..."), written only by
--                          add_test_move_proposal() (verified e2e marker) or
--                          the worker. Never followed, never in a window,
--                          never linked to or proposed with a real entity.
--   add_test_sports_story  db/032's add_test_story plus a sport, for the
--                          chips' e2e spec (owner only, verified marker).
--   stories.category       the /news chips (scope addition, owner 2026-10-12
--                          21:56): deals | hospitality | sports_business |
--                          film_music | luxury_marine | betting | other, NULL
--                          until categorized; in v_feed_stories; a
--                          feed_for_you filter (p_category); test writer
--                          add_test_news_story.
--   entity_links           now APPEND-ONLY (UPDATE, DELETE and TRUNCATE
--                          refused), with proposal_id and at most one current
--                          link per (person, kind).
--   entity_link_ends       APPEND-ONLY: a link ended by a confirmed move.
--   v_entity_links_current links with no end row.
--   entity_link_proposals  moves found by the summarizer, for the owner to
--                          decide. The decision is written ONCE (trigger);
--                          DELETE refused. At most one open per person.
--   decide_entity_link_proposal(id, decision)
--                          SECURITY INVOKER, the owner's session: writes the
--                          decision and, on confirm, ends the old link and
--                          adds the new one, in one transaction.
--   boost_windows          event windows (Alabama game weeks, F1 race
--                          weekends), seeded from the calendar doc §5.
--   feed_for_you(pillar, limit, offset, sport)
--                          db/039's order plus: the active-window boost (log
--                          hours, read time), the per-entity cap on Sports
--                          page one (6, or 9 while that entity has a window)
--                          and the sport chip, applied before ranking.
--   ctx_sports_feed()      provider 'sports_feed' (brief_section = false):
--                          Sports' top 5, the boosts now, open moves.
--   seeds                  the 14 OWGR top-15 people not yet entities (tier 3,
--                          sports), 30 NBA teams less the 76ers already
--                          present, 11 F1 teams less Ferrari, 3 tours (no
--                          follow); the 76ers and Ferrari tier-2 follows are
--                          deleted (owner D3: the worker derives them from
--                          the current link); the sports seed phrases; a
--                          url_regex for Golf Digest's course-directory
--                          pages (Phase 1 hotfix, owner 2026-10-12).
--
-- Access: RLS on every new table; anon nothing. The owner reads everything,
-- decides proposals (UPDATE of `decision` only, write-once by trigger) and
-- inserts links and link ends only as part of an open proposal (policies).
-- The worker (DATABASE_URL) writes the rest. valemont_readonly reads every
-- table (db/018 defaults) and is granted the view and readers explicitly.
--
-- Between this paste and the Step 2 worker deploy:
--   * stories matching the 76ers or Ferrari, and no tier-1 follow, lose their
--     tier-2 boost (+0.50 -> the sport-wide +0.15) until the worker derives it;
--   * tests_live ForYou.test_sql_order_equals_the_python_twin compares this
--     SQL with rank.order_for_you over every real Home candidate, so it is
--     expected to go red while an Alabama window is active (the Tennessee
--     one is, until Oct 19) and a boosted Alabama story is on Home, until
--     Step 2 gives the twin the boost and the cap;
--   * the feed prune must skip stories a proposal names (story_id is ON
--     DELETE RESTRICT); no proposal exists until Step 2 writes one.
--
-- A self-test runs on paste (section 11), rolled back: no row survives. A
-- wrong result raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/020 (app_settings, is_owner, reject_mutation,
-- reject_truncate), db/027 (e2e_markers), db/029 (context_providers,
-- ctx_trim), db/032-041 (feed).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run (IF NOT EXISTS; seeds ON CONFLICT DO NOTHING; the seed
-- phrases are set only while both sports arrays are empty).

-- ===================================================================== 1
-- entities.is_test, and test isolation

ALTER TABLE entities ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entities_test_named') THEN
        ALTER TABLE entities ADD CONSTRAINT entities_test_named
            CHECK (NOT is_test OR name LIKE 'Test %');
    END IF;
END
$$;

-- An entity cannot change sides.
CREATE OR REPLACE FUNCTION public.entities_is_test_fixed()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.is_test IS DISTINCT FROM OLD.is_test THEN
        RAISE EXCEPTION 'entities.is_test cannot change (entity %)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entities_is_test_fixed ON entities;
CREATE TRIGGER entities_is_test_fixed
    BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION public.entities_is_test_fixed();

-- follows and boost_windows never name a test entity.
CREATE OR REPLACE FUNCTION public.no_test_entity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.entity_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.entities e WHERE e.id = NEW.entity_id AND e.is_test) THEN
        RAISE EXCEPTION '%: a test entity cannot be used here', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS follows_no_test_entity ON follows;
CREATE TRIGGER follows_no_test_entity
    BEFORE INSERT OR UPDATE ON follows
    FOR EACH ROW EXECUTE FUNCTION public.no_test_entity();

-- ===================================================================== 2
-- entity_links: append-only, one current link per (person, kind)

ALTER TABLE entity_links ADD COLUMN IF NOT EXISTS proposal_id BIGINT;

CREATE TABLE IF NOT EXISTS entity_link_ends (
    id           BIGSERIAL    PRIMARY KEY,
    link_id      BIGINT       NOT NULL UNIQUE REFERENCES entity_links (id) ON DELETE RESTRICT,
    ended_on     DATE         NOT NULL,
    proposal_id  BIGINT       NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW v_entity_links_current
WITH (security_invoker = true)
AS
SELECT l.id, l.from_entity_id, l.to_entity_id, l.kind, l.valid_from, l.confirmed_by_owner,
       l.proposal_id, l.created_at
  FROM public.entity_links l
 WHERE NOT EXISTS (SELECT 1 FROM public.entity_link_ends x WHERE x.link_id = l.id);

CREATE OR REPLACE FUNCTION public.entity_links_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF (SELECT a.is_test FROM public.entities a WHERE a.id = NEW.from_entity_id)
       IS DISTINCT FROM (SELECT b.is_test FROM public.entities b WHERE b.id = NEW.to_entity_id) THEN
        RAISE EXCEPTION 'entity_links: a test entity links only to a test entity';
    END IF;
    -- A re-run of a seed (ON CONFLICT DO NOTHING) passes; a second current
    -- link does not: the old one is ended first (decide_entity_link_proposal).
    IF EXISTS (SELECT 1 FROM public.entity_links l
                WHERE l.from_entity_id = NEW.from_entity_id AND l.to_entity_id = NEW.to_entity_id
                  AND l.kind = NEW.kind AND l.valid_from = NEW.valid_from) THEN
        RETURN NEW;
    END IF;
    IF EXISTS (SELECT 1 FROM public.v_entity_links_current c
                WHERE c.from_entity_id = NEW.from_entity_id AND c.kind = NEW.kind) THEN
        RAISE EXCEPTION 'entity_links: entity % already has a current % link; end it first',
            NEW.from_entity_id, NEW.kind;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entity_links_guard ON entity_links;
CREATE TRIGGER entity_links_guard
    BEFORE INSERT ON entity_links
    FOR EACH ROW EXECUTE FUNCTION public.entity_links_guard();
DROP TRIGGER IF EXISTS entity_links_immutable ON entity_links;
CREATE TRIGGER entity_links_immutable
    BEFORE UPDATE OR DELETE ON entity_links
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS entity_links_no_truncate ON entity_links;
CREATE TRIGGER entity_links_no_truncate
    BEFORE TRUNCATE ON entity_links
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

DROP TRIGGER IF EXISTS entity_link_ends_immutable ON entity_link_ends;
CREATE TRIGGER entity_link_ends_immutable
    BEFORE UPDATE OR DELETE ON entity_link_ends
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS entity_link_ends_no_truncate ON entity_link_ends;
CREATE TRIGGER entity_link_ends_no_truncate
    BEFORE TRUNCATE ON entity_link_ends
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 3
-- entity_link_proposals

CREATE TABLE IF NOT EXISTS entity_link_proposals (
    id                BIGSERIAL    PRIMARY KEY,
    person_entity_id  BIGINT       NOT NULL REFERENCES entities (id) ON DELETE RESTRICT,
    kind              TEXT         NOT NULL CHECK (kind IN ('trade', 'signing', 'release', 'retirement')),
    -- The link a confirm ends and the kind of the link it adds.
    link_kind         TEXT         NOT NULL CHECK (link_kind IN ('plays_for', 'drives_for', 'member_of')),
    -- The person's current link target when proposed (NULL: none).
    from_entity_id    BIGINT       REFERENCES entities (id) ON DELETE RESTRICT,
    -- NULL for a release or a retirement.
    to_entity_id      BIGINT       REFERENCES entities (id) ON DELETE RESTRICT,
    -- The story that reported the move. RESTRICT: the prune keeps it.
    story_id          BIGINT       REFERENCES stories (id) ON DELETE RESTRICT,
    is_test           BOOLEAN      NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- Written once, by the owner (trigger). decided_at is the database's.
    decided_at        TIMESTAMPTZ,
    decision          TEXT         CHECK (decision IN ('confirmed', 'dismissed')),
    CHECK ((decided_at IS NULL) = (decision IS NULL)),
    CHECK ((kind IN ('trade', 'signing')) = (to_entity_id IS NOT NULL)),
    CHECK (to_entity_id IS DISTINCT FROM from_entity_id),
    CHECK (is_test OR story_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS entity_link_proposals_one_open
    ON entity_link_proposals (person_entity_id) WHERE decision IS NULL;
CREATE INDEX IF NOT EXISTS entity_link_proposals_created ON entity_link_proposals (created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entity_links_proposal_fk') THEN
        ALTER TABLE entity_links ADD CONSTRAINT entity_links_proposal_fk
            FOREIGN KEY (proposal_id) REFERENCES entity_link_proposals (id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entity_link_ends_proposal_fk') THEN
        ALTER TABLE entity_link_ends ADD CONSTRAINT entity_link_ends_proposal_fk
            FOREIGN KEY (proposal_id) REFERENCES entity_link_proposals (id) ON DELETE RESTRICT;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.entity_link_proposals_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_person   public.entities%ROWTYPE;
    v_to       public.entities%ROWTYPE;
    v_current  bigint[];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'entity_link_proposals: a proposal is never deleted (id %)', OLD.id;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.decision IS NOT NULL OR NEW.decided_at IS NOT NULL THEN
            RAISE EXCEPTION 'entity_link_proposals: a proposal is created open';
        END IF;
        SELECT * INTO v_person FROM public.entities WHERE id = NEW.person_entity_id;
        IF v_person.kind IS DISTINCT FROM 'person' THEN
            RAISE EXCEPTION 'entity_link_proposals: entity % is not a person', NEW.person_entity_id;
        END IF;
        IF NEW.is_test IS DISTINCT FROM v_person.is_test
           OR EXISTS (SELECT 1 FROM public.entities e
                       WHERE e.id IN (NEW.from_entity_id, NEW.to_entity_id) AND e.is_test <> v_person.is_test) THEN
            RAISE EXCEPTION 'entity_link_proposals: test and real entities never mix';
        END IF;
        IF NEW.to_entity_id IS NOT NULL THEN
            SELECT * INTO v_to FROM public.entities WHERE id = NEW.to_entity_id;
            IF v_to.kind NOT IN ('team', 'tour') THEN
                RAISE EXCEPTION 'entity_link_proposals: a move goes to a team or a tour';
            END IF;
            IF v_person.sport IS NOT NULL AND v_to.sport IS DISTINCT FROM v_person.sport THEN
                RAISE EXCEPTION 'entity_link_proposals: % is %, the destination is %',
                    v_person.name, v_person.sport, coalesce(v_to.sport, 'no sport');
            END IF;
        END IF;
        -- `from` is the world as it is now: the person's current link target.
        SELECT array_agg(c.to_entity_id) INTO v_current
          FROM public.v_entity_links_current c
         WHERE c.from_entity_id = NEW.person_entity_id AND c.kind = NEW.link_kind;
        IF NEW.from_entity_id IS DISTINCT FROM v_current[1] OR cardinality(coalesce(v_current, '{}')) > 1 THEN
            RAISE EXCEPTION 'entity_link_proposals: from % is not the current % link of entity % (%)',
                NEW.from_entity_id, NEW.link_kind, NEW.person_entity_id, coalesce(v_current::text, 'none');
        END IF;
        IF NEW.kind IN ('release', 'retirement') AND NEW.from_entity_id IS NULL THEN
            RAISE EXCEPTION 'entity_link_proposals: a % ends a current link', NEW.kind;
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: the decision, once, and nothing else.
    IF OLD.decision IS NOT NULL THEN
        RAISE EXCEPTION 'entity_link_proposals: proposal % is already %', OLD.id, OLD.decision;
    END IF;
    IF NEW.decision IS NULL THEN
        RAISE EXCEPTION 'entity_link_proposals: an update must decide';
    END IF;
    IF (NEW.id, NEW.person_entity_id, NEW.kind, NEW.link_kind, NEW.from_entity_id, NEW.to_entity_id,
        NEW.story_id, NEW.is_test, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.person_entity_id, OLD.kind, OLD.link_kind, OLD.from_entity_id, OLD.to_entity_id,
        OLD.story_id, OLD.is_test, OLD.created_at) THEN
        RAISE EXCEPTION 'entity_link_proposals: only the decision may be written';
    END IF;
    NEW.decided_at := now();
    -- A confirm is real only with its effect on record (written first by
    -- decide_entity_link_proposal, in the same transaction).
    IF NEW.decision = 'confirmed' THEN
        IF NEW.to_entity_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM public.entity_links l WHERE l.proposal_id = NEW.id) THEN
            RAISE EXCEPTION 'entity_link_proposals: confirm proposal % through decide_entity_link_proposal()', NEW.id;
        END IF;
        IF NEW.to_entity_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM public.entity_link_ends x WHERE x.proposal_id = NEW.id) THEN
            RAISE EXCEPTION 'entity_link_proposals: confirm proposal % through decide_entity_link_proposal()', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entity_link_proposals_guard ON entity_link_proposals;
CREATE TRIGGER entity_link_proposals_guard
    BEFORE INSERT OR UPDATE OR DELETE ON entity_link_proposals
    FOR EACH ROW EXECUTE FUNCTION public.entity_link_proposals_guard();
DROP TRIGGER IF EXISTS entity_link_proposals_no_truncate ON entity_link_proposals;
CREATE TRIGGER entity_link_proposals_no_truncate
    BEFORE TRUNCATE ON entity_link_proposals
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- The owner's calendar date (America/Chicago unless app_settings says
-- otherwise): a confirmed link's valid_from and a link end's ended_on.
CREATE OR REPLACE FUNCTION public.owner_today()
RETURNS date
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT (now() AT TIME ZONE coalesce((SELECT s.timezone FROM public.app_settings s LIMIT 1),
                                        'America/Chicago'))::date;
$$;

CREATE OR REPLACE FUNCTION public.decide_entity_link_proposal(p_id bigint, p_decision text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_p       public.entity_link_proposals%ROWTYPE;
    v_today   date := public.owner_today();
    v_cur     bigint[];
    v_cur_to  bigint[];
    v_new     bigint;
    v_ended   integer := 0;
BEGIN
    IF p_decision IS NULL OR p_decision NOT IN ('confirmed', 'dismissed') THEN
        RAISE EXCEPTION 'decide_entity_link_proposal: decision must be confirmed or dismissed';
    END IF;
    SELECT * INTO v_p FROM public.entity_link_proposals WHERE id = p_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'decide_entity_link_proposal: no proposal %', p_id;
    END IF;
    IF v_p.decision IS NOT NULL THEN
        RAISE EXCEPTION 'decide_entity_link_proposal: proposal % is already %', p_id, v_p.decision;
    END IF;

    IF p_decision = 'confirmed' THEN
        SELECT array_agg(c.id), array_agg(c.to_entity_id) INTO v_cur, v_cur_to
          FROM public.v_entity_links_current c
         WHERE c.from_entity_id = v_p.person_entity_id AND c.kind = v_p.link_kind;
        -- The world must still be the one the proposal saw.
        IF v_p.from_entity_id IS DISTINCT FROM v_cur_to[1] OR cardinality(coalesce(v_cur, '{}')) > 1 THEN
            RAISE EXCEPTION 'decide_entity_link_proposal: proposal % is stale (the current link changed)', p_id;
        END IF;
        IF v_cur IS NOT NULL THEN
            INSERT INTO public.entity_link_ends (link_id, ended_on, proposal_id)
            SELECT u, v_today, v_p.id FROM unnest(v_cur) AS u;
            GET DIAGNOSTICS v_ended = ROW_COUNT;
        END IF;
        IF v_p.to_entity_id IS NOT NULL THEN
            INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from,
                                             confirmed_by_owner, proposal_id)
            VALUES (v_p.person_entity_id, v_p.to_entity_id, v_p.link_kind, v_today, true, v_p.id)
            RETURNING id INTO v_new;
        END IF;
    END IF;

    UPDATE public.entity_link_proposals SET decision = p_decision WHERE id = p_id;
    RETURN jsonb_build_object('id', p_id, 'decision', p_decision, 'ended', v_ended, 'new_link', v_new);
END;
$$;

-- The e2e writer of a test move: three test entities ("Test Player <s>",
-- "Test Team A <s>", "Test Team B <s>"), the player's current link to A, and
-- an open trade to B. Owner only, verified marker (db/027's rule).
CREATE OR REPLACE FUNCTION public.add_test_move_proposal(p_marker text, p_suffix text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_hash    text;
    v_s       text := lower(p_suffix);
    v_person  bigint;
    v_a       bigint;
    v_b       bigint;
    v_id      bigint;
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'add_test_move_proposal: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_move_proposal: test marker refused';
    END IF;
    IF p_suffix IS NULL OR p_suffix !~ '^[A-Za-z0-9-]{1,40}$' THEN
        RAISE EXCEPTION 'add_test_move_proposal: suffix must be 1-40 letters, digits or dashes';
    END IF;
    INSERT INTO public.entities (kind, name, aliases, sport, is_test)
    VALUES ('person', 'Test Player ' || v_s, ARRAY['test player ' || v_s], 'nba', true) RETURNING id INTO v_person;
    INSERT INTO public.entities (kind, name, aliases, sport, is_test)
    VALUES ('team', 'Test Team A ' || v_s, ARRAY['test team a ' || v_s], 'nba', true) RETURNING id INTO v_a;
    INSERT INTO public.entities (kind, name, aliases, sport, is_test)
    VALUES ('team', 'Test Team B ' || v_s, ARRAY['test team b ' || v_s], 'nba', true) RETURNING id INTO v_b;
    INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
    VALUES (v_person, v_a, 'plays_for', public.owner_today() - 1, true);
    INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, is_test)
    VALUES (v_person, 'trade', 'plays_for', v_a, v_b, true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- The e2e writer of a test SPORTS story with a sport (the chips' spec):
-- db/032's add_test_story (owner only, verified marker, a .invalid URL),
-- then the story's sport. Still is_test: no real view or reader sees it.
CREATE OR REPLACE FUNCTION public.add_test_sports_story(p_marker text, p_sport text, p_title text,
                                                        p_summary text, p_url text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_sport IS NULL OR p_sport NOT IN ('cfb', 'nfl', 'nba', 'f1', 'golf') THEN
        RAISE EXCEPTION 'add_test_sports_story: unknown sport %', p_sport;
    END IF;
    v_id := public.add_test_story(p_marker, 'sports', p_title, p_summary, p_url);
    UPDATE public.stories SET sport = p_sport WHERE id = v_id AND is_test;
    RETURN v_id;
END;
$$;

-- ===================================================================== 3b
-- News categories (scope addition, owner 2026-10-12 21:56): one per story,
-- for the /news chips. New stories get it from the summary call; stories
-- summarized before that get it from their topics by the deterministic
-- mapping in workers/feed/category.py (scripts/feed_backfill_category.py,
-- run after this paste). NULL = not categorized yet (All only).

ALTER TABLE stories ADD COLUMN IF NOT EXISTS category TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stories_category_check') THEN
        ALTER TABLE stories ADD CONSTRAINT stories_category_check CHECK (category IN ('deals', 'hospitality', 'sports_business', 'film_music', 'luxury_marine', 'betting', 'other'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS stories_category ON stories (category) WHERE category IS NOT NULL;

-- v_feed_stories (db/032, db/039) gains category, appended so CREATE OR
-- REPLACE keeps every existing column in place.
CREATE OR REPLACE VIEW v_feed_stories
WITH (security_invoker = true)
AS
SELECT st.id, st.pillars, st.sport, st.title, st.summary, st.entities, st.topics,
       st.item_count, st.first_seen_at, st.last_seen_at,
       i.id AS lead_item_id, i.url AS lead_url, i.published_at AS lead_published_at,
       s.slug AS source_slug, s.name AS source_name,
       st.why_it_matters,
       st.category
  FROM public.stories st
  JOIN public.content_items i ON i.id = st.lead_item_id
  JOIN public.sources s ON s.id = i.source_id
 WHERE NOT st.is_test
   AND st.filtered_reason IS NULL
   AND i.url IS NOT NULL;

-- The e2e writer of a test HOME story with a category (the /news chips'
-- spec): db/032's add_test_story (owner only, verified marker, a .invalid
-- URL), then the category. Still is_test: no real view or reader sees it.
CREATE OR REPLACE FUNCTION public.add_test_news_story(p_marker text, p_category text, p_title text,
                                                      p_summary text, p_url text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_category IS NULL OR p_category NOT IN ('deals', 'hospitality', 'sports_business', 'film_music',
                                                'luxury_marine', 'betting', 'other') THEN
        RAISE EXCEPTION 'add_test_news_story: unknown category %', p_category;
    END IF;
    v_id := public.add_test_story(p_marker, 'home', p_title, p_summary, p_url);
    UPDATE public.stories SET category = p_category WHERE id = v_id AND is_test;
    RETURN v_id;
END;
$$;

-- ===================================================================== 4
-- boost_windows

CREATE TABLE IF NOT EXISTS boost_windows (
    id           BIGSERIAL    PRIMARY KEY,
    sport        TEXT         NOT NULL CHECK (sport IN ('cfb', 'nfl', 'nba', 'f1', 'golf')),
    -- NULL: every story of the sport, on Sports only. Set: stories matching
    -- that follow, on both pages (owner D2).
    entity_id    BIGINT       REFERENCES entities (id) ON DELETE RESTRICT,
    label        TEXT         NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
    starts_at    TIMESTAMPTZ  NOT NULL,
    ends_at      TIMESTAMPTZ  NOT NULL,
    -- A story in the window ranks as if it were this many hours fresher.
    boost_hours  REAL         NOT NULL CHECK (boost_hours > 0 AND boost_hours <= 48),
    source_url   TEXT         NOT NULL CHECK (source_url ~ '^https://'),
    fetched_at   TIMESTAMPTZ  NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at),
    CONSTRAINT boost_windows_once UNIQUE (label, starts_at)
);

CREATE INDEX IF NOT EXISTS boost_windows_active ON boost_windows (ends_at, starts_at);

DROP TRIGGER IF EXISTS boost_windows_no_test_entity ON boost_windows;
CREATE TRIGGER boost_windows_no_test_entity
    BEFORE INSERT OR UPDATE ON boost_windows
    FOR EACH ROW EXECUTE FUNCTION public.no_test_entity();

-- docs/sports_calendar_2026.md §5. Bounds are America/Chicago wall time.
-- Alabama: Monday 00:00 of game week to the next Monday 00:00; no window in
-- the Oct 31 bye week; Chattanooga included (owner D7). F1: 00:00 the day
-- before the first listed date to 12:00 the day after the last. Golf: none
-- remains in 2026 (calendar §3; no Hero World Challenge window, owner D6).
INSERT INTO boost_windows (sport, entity_id, label, starts_at, ends_at, boost_hours, source_url, fetched_at)
SELECT w.sport, e.id, w.label,
       w.starts::timestamp AT TIME ZONE 'America/Chicago',
       w.ends::timestamp AT TIME ZONE 'America/Chicago',
       w.hours, w.url, '2026-10-12 22:20:00+00'::timestamptz
  FROM (VALUES
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: at Tennessee',    '2026-10-12 00:00', '2026-10-19 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: Texas A&M',       '2026-10-19 00:00', '2026-10-26 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: at LSU',          '2026-11-02 00:00', '2026-11-09 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: at Vanderbilt',   '2026-11-09 00:00', '2026-11-16 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: Chattanooga',     '2026-11-16 00:00', '2026-11-23 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('cfb', 'Alabama Crimson Tide', 'Alabama game week: Auburn',          '2026-11-23 00:00', '2026-11-30 00:00', 12, 'https://rolltide.com/sports/football/schedule/text'),
        ('f1',  NULL,                   'F1 race weekend: United States GP',  '2026-10-22 00:00', '2026-10-26 12:00', 12, 'https://www.formula1.com/en/racing/2026'),
        ('f1',  NULL,                   'F1 race weekend: Mexico GP',         '2026-10-29 00:00', '2026-11-02 12:00', 12, 'https://www.formula1.com/en/racing/2026'),
        ('f1',  NULL,                   'F1 race weekend: Brazil GP',         '2026-11-05 00:00', '2026-11-09 12:00', 12, 'https://www.formula1.com/en/racing/2026'),
        ('f1',  NULL,                   'F1 race weekend: Las Vegas GP',      '2026-11-18 00:00', '2026-11-22 12:00', 12, 'https://www.formula1.com/en/racing/2026'),
        ('f1',  NULL,                   'F1 race weekend: Qatar GP',          '2026-11-26 00:00', '2026-11-30 12:00', 12, 'https://www.formula1.com/en/racing/2026'),
        ('f1',  NULL,                   'F1 race weekend: Abu Dhabi GP',      '2026-12-03 00:00', '2026-12-07 12:00', 12, 'https://www.formula1.com/en/racing/2026')
       ) AS w (sport, entity, label, starts, ends, hours, url)
  LEFT JOIN entities e ON e.name = w.entity
ON CONFLICT ON CONSTRAINT boost_windows_once DO NOTHING;

-- ===================================================================== 5
-- Entities (owner D4) and follows

-- The OWGR top 15 (owgr.com, fetched 2026-10-12; calendar doc §4) less
-- Scheffler, already tier 1. Full names, plus a surname only where it is
-- distinctive in golf; every alias is golf-only.
INSERT INTO entities (kind, name, aliases, sport) VALUES
    ('person', 'Rory McIlroy',      ARRAY['rory mcilroy', 'mcilroy'],          'golf'),
    ('person', 'Matt Fitzpatrick',  ARRAY['matt fitzpatrick'],                 'golf'),
    ('person', 'Cameron Young',     ARRAY['cameron young'],                    'golf'),
    ('person', 'Wyndham Clark',     ARRAY['wyndham clark'],                    'golf'),
    ('person', 'Tommy Fleetwood',   ARRAY['tommy fleetwood', 'fleetwood'],     'golf'),
    ('person', 'Russell Henley',    ARRAY['russell henley', 'henley'],         'golf'),
    ('person', 'Chris Gotterup',    ARRAY['chris gotterup', 'gotterup'],       'golf'),
    ('person', 'Xander Schauffele', ARRAY['xander schauffele', 'schauffele'],  'golf'),
    ('person', 'Sam Burns',         ARRAY['sam burns'],                        'golf'),
    ('person', 'Collin Morikawa',   ARRAY['collin morikawa', 'morikawa'],      'golf'),
    ('person', 'J.J. Spaun',        ARRAY['j.j. spaun', 'jj spaun', 'spaun'],  'golf'),
    ('person', 'Viktor Hovland',    ARRAY['viktor hovland', 'hovland'],        'golf'),
    ('person', 'Si Woo Kim',        ARRAY['si woo kim'],                       'golf'),
    ('person', 'Jon Rahm',          ARRAY['jon rahm', 'rahm'],                 'golf'),
    -- Golf tours: move destinations, no follow.
    ('tour',   'PGA Tour',          ARRAY['pga tour'],                         'golf'),
    ('tour',   'LIV Golf',          ARRAY['liv golf'],                         'golf'),
    ('tour',   'DP World Tour',     ARRAY['dp world tour', 'european tour'],   'golf'),
    -- NBA teams (nba.com/teams, 2026-10-12), less the 76ers (db/039). A
    -- nickname that is a common word (heat, magic, jazz, kings, suns,
    -- thunder, nets, bulls) is matched only with its city.
    ('team', 'Boston Celtics',          ARRAY['boston celtics', 'celtics'],                       'nba'),
    ('team', 'Brooklyn Nets',           ARRAY['brooklyn nets'],                                   'nba'),
    ('team', 'New York Knicks',         ARRAY['new york knicks', 'knicks'],                       'nba'),
    ('team', 'Toronto Raptors',         ARRAY['toronto raptors', 'raptors'],                      'nba'),
    ('team', 'Chicago Bulls',           ARRAY['chicago bulls'],                                   'nba'),
    ('team', 'Cleveland Cavaliers',     ARRAY['cleveland cavaliers', 'cavaliers', 'cavs'],        'nba'),
    ('team', 'Detroit Pistons',         ARRAY['detroit pistons', 'pistons'],                      'nba'),
    ('team', 'Indiana Pacers',          ARRAY['indiana pacers', 'pacers'],                        'nba'),
    ('team', 'Milwaukee Bucks',         ARRAY['milwaukee bucks', 'bucks'],                        'nba'),
    ('team', 'Atlanta Hawks',           ARRAY['atlanta hawks', 'hawks'],                          'nba'),
    ('team', 'Charlotte Hornets',       ARRAY['charlotte hornets', 'hornets'],                    'nba'),
    ('team', 'Miami Heat',              ARRAY['miami heat'],                                      'nba'),
    ('team', 'Orlando Magic',           ARRAY['orlando magic'],                                   'nba'),
    ('team', 'Washington Wizards',      ARRAY['washington wizards', 'wizards'],                   'nba'),
    ('team', 'Denver Nuggets',          ARRAY['denver nuggets', 'nuggets'],                       'nba'),
    ('team', 'Minnesota Timberwolves',  ARRAY['minnesota timberwolves', 'timberwolves'],          'nba'),
    ('team', 'Oklahoma City Thunder',   ARRAY['oklahoma city thunder', 'okc thunder'],            'nba'),
    ('team', 'Portland Trail Blazers',  ARRAY['portland trail blazers', 'trail blazers', 'blazers'], 'nba'),
    ('team', 'Utah Jazz',               ARRAY['utah jazz'],                                       'nba'),
    ('team', 'Golden State Warriors',   ARRAY['golden state warriors', 'warriors'],               'nba'),
    ('team', 'LA Clippers',             ARRAY['la clippers', 'los angeles clippers', 'clippers'], 'nba'),
    ('team', 'Los Angeles Lakers',      ARRAY['los angeles lakers', 'la lakers', 'lakers'],       'nba'),
    ('team', 'Phoenix Suns',            ARRAY['phoenix suns'],                                    'nba'),
    ('team', 'Sacramento Kings',        ARRAY['sacramento kings'],                                'nba'),
    ('team', 'Dallas Mavericks',        ARRAY['dallas mavericks', 'mavericks', 'mavs'],           'nba'),
    ('team', 'Houston Rockets',         ARRAY['houston rockets', 'rockets'],                      'nba'),
    ('team', 'Memphis Grizzlies',       ARRAY['memphis grizzlies', 'grizzlies'],                  'nba'),
    ('team', 'New Orleans Pelicans',    ARRAY['new orleans pelicans', 'pelicans'],                'nba'),
    ('team', 'San Antonio Spurs',       ARRAY['san antonio spurs', 'spurs'],                      'nba'),
    -- F1 teams (formula1.com/en/teams, 2026-10-12), less Ferrari (db/039).
    ('team', 'Mercedes',                ARRAY['mercedes'],                                        'f1'),
    ('team', 'McLaren',                 ARRAY['mclaren'],                                         'f1'),
    ('team', 'Red Bull Racing',         ARRAY['red bull racing', 'red bull'],                     'f1'),
    ('team', 'Racing Bulls',            ARRAY['racing bulls'],                                    'f1'),
    ('team', 'Alpine',                  ARRAY['alpine'],                                          'f1'),
    ('team', 'Haas F1 Team',            ARRAY['haas f1 team', 'haas'],                            'f1'),
    ('team', 'Audi',                    ARRAY['audi'],                                            'f1'),
    ('team', 'Williams',                ARRAY['williams'],                                        'f1'),
    ('team', 'Aston Martin',            ARRAY['aston martin'],                                    'f1'),
    ('team', 'Cadillac',                ARRAY['cadillac'],                                        'f1')
ON CONFLICT (name) DO NOTHING;

-- The OWGR people: tier 3, Sports. Membership in this list is what "top
-- player" means for the LIV term (docs/sports_news.md §3).
INSERT INTO follows (entity_id, tier, pillar, floor)
SELECT e.id, 3, 'sports', false
  FROM entities e
 WHERE e.name IN ('Rory McIlroy', 'Matt Fitzpatrick', 'Cameron Young', 'Wyndham Clark', 'Tommy Fleetwood',
                  'Russell Henley', 'Chris Gotterup', 'Xander Schauffele', 'Sam Burns', 'Collin Morikawa',
                  'J.J. Spaun', 'Viktor Hovland', 'Si Woo Kim', 'Jon Rahm')
ON CONFLICT ON CONSTRAINT follows_once DO NOTHING;

-- Owner D3: "his team" follows the person. The worker derives tier 2 for each
-- tier-1 person's current team (v_entity_links_current), so the fixed rows go.
DELETE FROM follows f
 USING entities e
 WHERE e.id = f.entity_id
   AND e.name IN ('Philadelphia 76ers', 'Ferrari')
   AND f.tier = 2 AND f.pillar = 'sports';

-- Sports feed rule (owner, 2026-10-12, Phase 1 hotfix; sports rules belong
-- here): Golf Digest's course-directory pages ("King's Deer Golf Club: King's
-- Deer", golfdigest.com/courses/<state>/...), 61 kept stories since
-- 2026-10-12 03:39 UTC, none of them news (news is /story/ and /post/). Like
-- db/037-038 it applies to stories founded from now on; it rewrites none.
INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex', NULL, '^https?://(www\.)?golfdigest\.com/courses/',
                    'junk: Golf Digest course-directory pages (owner, 2026-10-12)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

-- ===================================================================== 6
-- Sports seed phrases (docs/sports_news.md §2), set once: only while both
-- sports arrays are empty, so a later edit by the owner is never overwritten.

UPDATE app_settings
   SET seed_interests = jsonb_set(seed_interests, '{feed,sports}', jsonb_build_object(
           'love', jsonb_build_array(
               'Alabama Crimson Tide football news',
               'LeBron James news, whatever team he''s on',
               'Lewis Hamilton and Charles Leclerc at Ferrari',
               'Scuderia Ferrari F1 team news',
               'Scottie Scheffler and Brooks Koepka golf news',
               'NFL trades, injuries and the playoff picture',
               'golf majors and big mid-majors: the Players and signature events',
               'F1 race weekend: qualifying, the race and its storylines'),
           'not_interested', jsonb_build_array(
               'plain box-score recaps with no storyline',
               'LIV Golf filler: LIV team events and lesser-known LIV players'))),
       updated_at = now()
 WHERE seed_interests ? 'feed'
   AND coalesce(jsonb_array_length(seed_interests #> '{feed,sports,love}'), 0) = 0
   AND coalesce(jsonb_array_length(seed_interests #> '{feed,sports,not_interested}'), 0) = 0;

-- ===================================================================== 7
-- feed_for_you: db/039's order, plus the boost, the entity cap and the chip.
--
--   rank = score + ln 2 * boost_h / HALF_LIFE_H - ln 2 * age_h / HALF_LIFE_H
--   boost_h = max boost_hours over windows active now that match the story:
--             same sport, and either no entity (Sports only) or an entity in
--             the story's matched follows (both pages). 0 when none.
--
-- p_sport (a chip) filters the candidates BEFORE the floor, the caps and
-- page one are computed. Page one on Sports also holds at most ENTITY_CAP
-- stories per tier-1 follow (ENTITY_CAP_BOOSTED while that entity has an
-- active window); floored stories are counted first, so they keep their
-- place. Overflow moves below page one, in rank order, like the cross cap.
--
-- The signature changes (p_sport), so the db/039 function is dropped first.
-- ctx_feed calls feed_for_you('home', 3, 0) by name and resolves to this one.

DROP FUNCTION IF EXISTS public.feed_for_you(text, integer, integer);

CREATE OR REPLACE FUNCTION public.feed_for_you(p_pillar text, p_limit integer DEFAULT 30,
                                               p_offset integer DEFAULT 0, p_sport text DEFAULT NULL,
                                               p_category text DEFAULT NULL)
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
           AND (p_pillar = ANY (v.pillars) OR sc.cross_pillar)
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

-- ===================================================================== 8
-- ctx_sports_feed: Sports' top 5, the boosts now, the open moves (for Wags;
-- not a Morning Brief section). Test stories never reach feed_for_you; test
-- proposals and test-entity windows are filtered here.

CREATE OR REPLACE FUNCTION public.ctx_sports_feed()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH top AS (
        SELECT * FROM public.feed_for_you('sports', 5, 0)
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'Top of the Sports For you feed, ranked by the owner''s sports profile, follows and event boosts.',
        'top_stories', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'title', public.ctx_trim(t.title, 160),
                       'source', t.source_name,
                       'sport', t.sport,
                       'summary', public.ctx_trim(t.summary, 260),
                       'loved', CASE WHEN t.reaction = 'love' THEN true END))
                     ORDER BY t.pos)
              FROM top t), '[]'::jsonb),
        'boosts_now', coalesce((
            SELECT jsonb_agg(jsonb_build_object('label', w.label, 'sport', w.sport, 'ends_at', w.ends_at)
                             ORDER BY w.ends_at, w.id)
              FROM public.boost_windows w
              LEFT JOIN public.entities e ON e.id = w.entity_id
             WHERE w.starts_at <= now() AND now() < w.ends_at
               AND NOT coalesce(e.is_test, false)), '[]'::jsonb),
        'proposed_moves', coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                       'person', pe.name, 'kind', p.kind, 'from', fe.name, 'to', te.name))
                     ORDER BY p.created_at, p.id)
              FROM public.entity_link_proposals p
              JOIN public.entities pe ON pe.id = p.person_entity_id
              LEFT JOIN public.entities fe ON fe.id = p.from_entity_id
              LEFT JOIN public.entities te ON te.id = p.to_entity_id
             WHERE p.decision IS NULL AND NOT p.is_test), '[]'::jsonb),
        'note', CASE WHEN NOT EXISTS (SELECT 1 FROM top) THEN 'No ranked sports stories yet.' END
    ));
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('sports_feed', 'Sports', 'public.ctx_sports_feed()'::regprocedure, false, 45)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 9
-- Access.

ALTER TABLE boost_windows         ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_link_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_link_ends      ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON boost_windows, entity_link_proposals, entity_link_ends, v_entity_links_current
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE boost_windows_id_seq, entity_link_proposals_id_seq, entity_link_ends_id_seq
    FROM anon, authenticated, PUBLIC;

GRANT SELECT ON boost_windows, entity_link_proposals, entity_link_ends TO authenticated;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['boost_windows', 'entity_link_proposals', 'entity_link_ends'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_owner_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING ((SELECT public.is_owner()))',
            t || '_owner_read', t);
    END LOOP;
END
$$;

-- The owner decides: UPDATE of `decision` only; the trigger makes it
-- write-once and sets decided_at.
GRANT UPDATE (decision) ON entity_link_proposals TO authenticated;
DROP POLICY IF EXISTS entity_link_proposals_owner_decide ON entity_link_proposals;
CREATE POLICY entity_link_proposals_owner_decide ON entity_link_proposals
    FOR UPDATE TO authenticated
    USING ((SELECT public.is_owner()))
    WITH CHECK ((SELECT public.is_owner()));

-- A confirm's two writes, and nothing else: a link or an end only for an
-- OPEN proposal, matching it, dated today.
GRANT INSERT ON entity_links, entity_link_ends TO authenticated;
GRANT USAGE ON SEQUENCE entity_links_id_seq, entity_link_ends_id_seq TO authenticated;

DROP POLICY IF EXISTS entity_links_owner_confirm ON entity_links;
CREATE POLICY entity_links_owner_confirm ON entity_links
    FOR INSERT TO authenticated
    WITH CHECK (
        (SELECT public.is_owner())
        AND confirmed_by_owner
        AND valid_from = public.owner_today()
        AND EXISTS (SELECT 1 FROM public.entity_link_proposals p
                     WHERE p.id = entity_links.proposal_id
                       AND p.decision IS NULL
                       AND p.person_entity_id = entity_links.from_entity_id
                       AND p.to_entity_id = entity_links.to_entity_id
                       AND p.link_kind = entity_links.kind));

DROP POLICY IF EXISTS entity_link_ends_owner_confirm ON entity_link_ends;
CREATE POLICY entity_link_ends_owner_confirm ON entity_link_ends
    FOR INSERT TO authenticated
    WITH CHECK (
        (SELECT public.is_owner())
        AND ended_on = public.owner_today()
        AND EXISTS (SELECT 1 FROM public.entity_link_proposals p
                      JOIN public.entity_links l ON l.id = entity_link_ends.link_id
                     WHERE p.id = entity_link_ends.proposal_id
                       AND p.decision IS NULL
                       AND l.from_entity_id = p.person_entity_id
                       AND l.kind = p.link_kind));

GRANT SELECT ON v_entity_links_current TO authenticated, valemont_readonly;
-- db/018's defaults cover the tables; restated so the file says what it expects.
GRANT SELECT ON boost_windows, entity_link_proposals, entity_link_ends TO valemont_readonly;

REVOKE ALL ON FUNCTION public.entities_is_test_fixed() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.no_test_entity() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.entity_links_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.entity_link_proposals_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.owner_today() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.decide_entity_link_proposal(bigint, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_move_proposal(text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_sports_story(text, text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_news_story(text, text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.feed_for_you(text, integer, integer, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_sports_feed() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.owner_today() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.decide_entity_link_proposal(bigint, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_move_proposal(text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_sports_story(text, text, text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_news_story(text, text, text, text, text) TO authenticated;
-- v_feed_stories was replaced above; its grants survive CREATE OR REPLACE,
-- restated so the file says what it expects.
GRANT SELECT ON v_feed_stories TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.feed_for_you(text, integer, integer, text, text) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_sports_feed() TO authenticated, valemont_readonly;

-- ===================================================================== 10
-- (reserved: nothing between access and the self-test)

-- ===================================================================== 11
-- Self-test, run on paste. Everything happens inside a subtransaction rolled
-- back at the end ('selftest-rollback'), so no row survives; only sequence
-- values are consumed. A wrong result raises SELFTEST FAILED and nothing in
-- this file is applied.
--
-- Its stories are REAL (is_test = false, from a throwaway real source) with
-- scores of 20 to 40, above any real score (about +-10), and its entities
-- are its own ("Selftest 042 ..."), so the seeded windows (Alabama, F1) and
-- real stories cannot change what it checks. Golf and NBA carry no seeded
-- window. The floor may lift up to 3 real Alabama stories onto page one;
-- no check below counts them.

DO $$
DECLARE
    v_src      bigint;
    v_prog     bigint;
    v_person   bigint;
    v_team_a   bigint;
    v_team_b   bigint;
    v_story    bigint;
    v_item     bigint;
    v_g1       bigint[] := '{}';
    v_plain    bigint;
    v_golf     bigint;
    v_golf_h   bigint;
    v_home_e   bigint;
    v_p1       bigint;
    v_p2       bigint;
    v_p3       bigint;
    v_tp       bigint;
    v_tperson  bigint;
    v_ta       bigint;
    v_tb       bigint;
    v_n        bigint;
    v_rank     float8;
    v_res      jsonb;
    v_ctx      jsonb;
    v_refused  int;
    i          int;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-042', 'Self-test 042', ARRAY['home', 'sports'], 'rss', 'https://selftest.invalid/042')
        RETURNING id INTO v_src;

        INSERT INTO public.entities (kind, name, aliases, sport)
        VALUES ('program', 'Selftest 042 Program', ARRAY['selftest042 program'], 'cfb') RETURNING id INTO v_prog;
        INSERT INTO public.follows (entity_id, tier, pillar, floor) VALUES (v_prog, 1, NULL, false);

        -- G1: 12 cfb stories matching the tier-1 program, rank ~40.
        FOR i IN 1..12 LOOP
            INSERT INTO public.stories (pillars, title, sport)
            VALUES (ARRAY['sports'], 'self-test 042 g1 #' || i, 'cfb') RETURNING id INTO v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest042:g1:' || i, 'https://selftest.invalid/042/g1/' || i,
                    'self-test 042 g1 #' || i, now(), v_story) RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
            INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier)
            VALUES (v_story, 'sports', 40 - i * 0.01, '{"follows": ["Selftest 042 Program"]}'::jsonb, 1);
            v_g1 := v_g1 || v_story;
        END LOOP;
        -- G2: 30 nba stories with no follow, rank ~38: enough to fill page one.
        FOR i IN 1..30 LOOP
            INSERT INTO public.stories (pillars, title, sport)
            VALUES (ARRAY['sports'], 'self-test 042 g2 #' || i, 'nba') RETURNING id INTO v_story;
            INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
            VALUES (v_src, 'selftest042:g2:' || i, 'https://selftest.invalid/042/g2/' || i,
                    'self-test 042 g2 #' || i, now(), v_story) RETURNING id INTO v_item;
            UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
            INSERT INTO public.story_scores (story_id, pillar, score, components)
            VALUES (v_story, 'sports', 38 - i * 0.01, '{"follows": []}'::jsonb);
        END LOOP;

        -- 1. The entity cap: 6 of the program's 12 stories on Sports page one.
        SELECT count(*) INTO v_n FROM public.feed_for_you('sports', 30, 0) f WHERE f.id = ANY (v_g1);
        IF v_n <> 6 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stories of one tier-1 follow on Sports page one (cap 6)', v_n;
        END IF;
        IF (SELECT array_agg(f.id ORDER BY f.pos) FROM public.feed_for_you('sports', 30, 0) f
             WHERE f.id = ANY (v_g1)) IS DISTINCT FROM v_g1[1:6] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the cap kept the wrong stories';
        END IF;
        -- The overflow (7th to 12th) sits below page one.
        IF EXISTS (SELECT 1 FROM public.feed_for_you('sports', 1000, 0) f
                    WHERE f.id = ANY (v_g1[7:12]) AND f.pos <= 30) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a story over the entity cap is on page one';
        END IF;

        -- 2. An entity window: the cap rises to 9 and the stories rank 12 h
        --    fresher (+ ln 2 * 12 / 18).
        INSERT INTO public.boost_windows (sport, entity_id, label, starts_at, ends_at, boost_hours,
                                          source_url, fetched_at)
        VALUES ('cfb', v_prog, 'selftest 042 window', now() - interval '1 hour', now() + interval '1 hour',
                12, 'https://selftest.invalid/042', now());
        SELECT count(*) INTO v_n FROM public.feed_for_you('sports', 30, 0) f WHERE f.id = ANY (v_g1);
        IF v_n <> 9 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stories of a boosted follow on page one (cap 9)', v_n;
        END IF;
        SELECT f.rank INTO v_rank FROM public.feed_for_you('sports', 1000, 0) f WHERE f.id = v_g1[1];
        IF abs(v_rank - (39.99 + ln(2.0) * 12 / 18)) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: boosted rank %, expected %', v_rank, 39.99 + ln(2.0) * 12 / 18;
        END IF;
        -- ... the chip computes the cap within the sport too.
        SELECT count(*) INTO v_n FROM public.feed_for_you('sports', 30, 0, 'cfb') f WHERE f.id = ANY (v_g1);
        IF v_n <> 9 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % program stories on the CFB chip''s page one (cap 9)', v_n;
        END IF;

        -- 3. The entity window needs the follow: a cfb story without it is
        --    not boosted.
        INSERT INTO public.stories (pillars, title, sport)
        VALUES (ARRAY['sports'], 'self-test 042 plain cfb', 'cfb') RETURNING id INTO v_plain;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest042:plain', 'https://selftest.invalid/042/plain', 'self-test 042 plain cfb',
                now(), v_plain) RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_plain;
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_plain, 'sports', 20, '{"follows": []}'::jsonb);
        SELECT f.rank INTO v_rank FROM public.feed_for_you('sports', 1000, 0) f WHERE f.id = v_plain;
        IF abs(v_rank - 20) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an entity window boosted a story without the follow (%)', v_rank;
        END IF;

        -- 4. Sport-wide windows: only an active one counts (max, not sum),
        --    and on Sports only.
        INSERT INTO public.boost_windows (sport, entity_id, label, starts_at, ends_at, boost_hours,
                                          source_url, fetched_at)
        VALUES ('golf', NULL, 'selftest 042 golf now',    now() - interval '1 hour', now() + interval '1 hour',
                6, 'https://selftest.invalid/042', now()),
               ('golf', NULL, 'selftest 042 golf past',   now() - interval '2 hours', now() - interval '1 minute',
                40, 'https://selftest.invalid/042', now()),
               ('golf', NULL, 'selftest 042 golf future', now() + interval '1 hour', now() + interval '2 hours',
                40, 'https://selftest.invalid/042', now());
        INSERT INTO public.stories (pillars, title, sport)
        VALUES (ARRAY['sports'], 'self-test 042 golf', 'golf') RETURNING id INTO v_golf;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest042:golf', 'https://selftest.invalid/042/golf', 'self-test 042 golf',
                now(), v_golf) RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_golf;
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_golf, 'sports', 30, '{"follows": []}'::jsonb);
        SELECT f.rank INTO v_rank FROM public.feed_for_you('sports', 1000, 0) f WHERE f.id = v_golf;
        IF abs(v_rank - (30 + ln(2.0) * 6 / 18)) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sport-wide boost rank %, expected % (only the active window)',
                v_rank, 30 + ln(2.0) * 6 / 18;
        END IF;
        INSERT INTO public.stories (pillars, title, sport)
        VALUES (ARRAY['home'], 'self-test 042 golf home', 'golf') RETURNING id INTO v_golf_h;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest042:golfh', 'https://selftest.invalid/042/golfh', 'self-test 042 golf home',
                now(), v_golf_h) RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_golf_h;
        INSERT INTO public.story_scores (story_id, pillar, score, components)
        VALUES (v_golf_h, 'home', 30, '{"follows": []}'::jsonb);
        SELECT f.rank INTO v_rank FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_golf_h;
        IF abs(v_rank - 30) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a sport-wide window boosted Home (%)', v_rank;
        END IF;
        -- An entity window applies on Home too.
        INSERT INTO public.stories (pillars, title, sport)
        VALUES (ARRAY['home'], 'self-test 042 program home', 'cfb') RETURNING id INTO v_home_e;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest042:home', 'https://selftest.invalid/042/home', 'self-test 042 program home',
                now(), v_home_e) RETURNING id INTO v_item;
        UPDATE public.stories SET lead_item_id = v_item WHERE id = v_home_e;
        INSERT INTO public.story_scores (story_id, pillar, score, components, follow_tier)
        VALUES (v_home_e, 'home', 30, '{"follows": ["Selftest 042 Program"]}'::jsonb, 1);
        SELECT f.rank INTO v_rank FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_home_e;
        IF abs(v_rank - (30 + ln(2.0) * 12 / 18)) > 0.01 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an entity window did not boost Home (%)', v_rank;
        END IF;

        -- 5. Chips filter: every row is the chip's sport.
        IF EXISTS (SELECT 1 FROM public.feed_for_you('sports', 1000, 0, 'golf') f WHERE f.sport IS DISTINCT FROM 'golf')
           OR NOT EXISTS (SELECT 1 FROM public.feed_for_you('sports', 1000, 0, 'golf') f WHERE f.id = v_golf)
           OR EXISTS (SELECT 1 FROM public.feed_for_you('sports', 1000, 0, 'cfb') f WHERE f.sport IS DISTINCT FROM 'cfb') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a chip returned another sport';
        END IF;

        -- 5b. News categories: the CHECK, and the /news chip filter (Home).
        UPDATE public.stories SET category = 'deals' WHERE id = v_golf_h;
        UPDATE public.stories SET category = 'film_music' WHERE id = v_home_e;
        IF EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0, NULL, 'deals') f
                    WHERE f.id IN (SELECT s.id FROM public.stories s WHERE s.category IS DISTINCT FROM 'deals'))
           OR NOT EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0, NULL, 'deals') f WHERE f.id = v_golf_h)
           OR EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0, NULL, 'deals') f WHERE f.id = v_home_e)
           OR NOT EXISTS (SELECT 1 FROM public.feed_for_you('home', 1000, 0) f WHERE f.id = v_home_e) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the category filter';
        END IF;
        IF (SELECT v.category FROM public.v_feed_stories v WHERE v.id = v_golf_h) IS DISTINCT FROM 'deals' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_feed_stories does not carry category';
        END IF;
        v_refused := 0;
        BEGIN
            UPDATE public.stories SET category = 'gossip' WHERE id = v_golf_h;
        EXCEPTION WHEN check_violation THEN v_refused := 1;
        END;
        IF v_refused <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unknown category was accepted';
        END IF;

        -- 6. Proposals and links.
        INSERT INTO public.entities (kind, name, aliases, sport)
        VALUES ('person', 'Selftest 042 Person', ARRAY['selftest042 person'], 'nba') RETURNING id INTO v_person;
        INSERT INTO public.entities (kind, name, aliases, sport)
        VALUES ('team', 'Selftest 042 Team A', ARRAY['selftest042 team a'], 'nba') RETURNING id INTO v_team_a;
        INSERT INTO public.entities (kind, name, aliases, sport)
        VALUES ('team', 'Selftest 042 Team B', ARRAY['selftest042 team b'], 'nba') RETURNING id INTO v_team_b;
        INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
        VALUES (v_person, v_team_a, 'plays_for', '2026-01-01', true);

        v_refused := 0;
        -- a stale `from` (B, while the current link is A)
        BEGIN
            INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, story_id)
            VALUES (v_person, 'trade', 'plays_for', v_team_b, v_team_a, v_golf);
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        -- a second current link
        BEGIN
            INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
            VALUES (v_person, v_team_b, 'plays_for', '2026-02-01', true);
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        -- a move to something that is not a team or a tour (a program)
        BEGIN
            INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, story_id)
            VALUES (v_person, 'trade', 'plays_for', v_team_a, v_prog, v_golf);
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 3 bad proposals or links refused', v_refused;
        END IF;

        INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, story_id)
        VALUES (v_person, 'trade', 'plays_for', v_team_a, v_team_b, v_golf) RETURNING id INTO v_p1;

        v_refused := 0;
        BEGIN  -- a second open proposal for the person
            INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, story_id)
            VALUES (v_person, 'release', 'plays_for', v_team_a, v_golf);
        EXCEPTION WHEN unique_violation THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- a confirm that skips decide_entity_link_proposal
            UPDATE public.entity_link_proposals SET decision = 'confirmed' WHERE id = v_p1;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- any column but the decision
            UPDATE public.entity_link_proposals SET kind = 'signing' WHERE id = v_p1;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            DELETE FROM public.entity_link_proposals WHERE id = v_p1;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            TRUNCATE public.entity_link_proposals;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 5 proposal mutations refused', v_refused;
        END IF;

        -- Confirm: the old link ends, the new one is current, the decision is written.
        v_res := public.decide_entity_link_proposal(v_p1, 'confirmed');
        IF (v_res ->> 'ended')::int <> 1 OR v_res ->> 'new_link' IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: confirm result %', v_res;
        END IF;
        IF (SELECT array_agg(c.to_entity_id) FROM public.v_entity_links_current c
             WHERE c.from_entity_id = v_person) IS DISTINCT FROM ARRAY[v_team_b]
           OR NOT EXISTS (SELECT 1 FROM public.entity_link_ends x
                            JOIN public.entity_links l ON l.id = x.link_id
                           WHERE l.from_entity_id = v_person AND l.to_entity_id = v_team_a
                             AND x.proposal_id = v_p1 AND x.ended_on = public.owner_today())
           OR NOT EXISTS (SELECT 1 FROM public.entity_links l
                           WHERE l.from_entity_id = v_person AND l.to_entity_id = v_team_b
                             AND l.proposal_id = v_p1 AND l.valid_from = public.owner_today()
                             AND l.confirmed_by_owner) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: confirm did not end the old link and add the new one';
        END IF;
        IF (SELECT decision FROM public.entity_link_proposals WHERE id = v_p1) IS DISTINCT FROM 'confirmed'
           OR (SELECT decided_at FROM public.entity_link_proposals WHERE id = v_p1) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the decision was not recorded';
        END IF;

        v_refused := 0;
        BEGIN  -- a second decision
            PERFORM public.decide_entity_link_proposal(v_p1, 'dismissed');
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            UPDATE public.entity_link_proposals SET decision = 'dismissed' WHERE id = v_p1;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            UPDATE public.entity_links SET valid_from = '2026-03-01' WHERE from_entity_id = v_person;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            DELETE FROM public.entity_links WHERE from_entity_id = v_person;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            DELETE FROM public.entity_link_ends WHERE proposal_id = v_p1;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            TRUNCATE public.entity_link_ends;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 6 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 6 decision and link mutations refused', v_refused;
        END IF;

        -- Dismiss changes nothing but the proposal.
        INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, story_id)
        VALUES (v_person, 'trade', 'plays_for', v_team_b, v_team_a, v_golf) RETURNING id INTO v_p2;
        PERFORM public.decide_entity_link_proposal(v_p2, 'dismissed');
        IF (SELECT array_agg(c.to_entity_id) FROM public.v_entity_links_current c
             WHERE c.from_entity_id = v_person) IS DISTINCT FROM ARRAY[v_team_b]
           OR (SELECT decision FROM public.entity_link_proposals WHERE id = v_p2) IS DISTINCT FROM 'dismissed' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: dismiss changed a link or was not recorded';
        END IF;

        -- A retirement ends the link and adds none.
        INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, story_id)
        VALUES (v_person, 'retirement', 'plays_for', v_team_b, v_golf) RETURNING id INTO v_p3;
        v_res := public.decide_entity_link_proposal(v_p3, 'confirmed');
        IF EXISTS (SELECT 1 FROM public.v_entity_links_current c WHERE c.from_entity_id = v_person)
           OR (v_res ->> 'ended')::int <> 1 OR v_res ->> 'new_link' IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a retirement left a current link (%)', v_res;
        END IF;

        -- 7. Test entities stay apart.
        v_refused := 0;
        BEGIN  -- a test entity must be named "Test ..."
            INSERT INTO public.entities (kind, name, aliases, sport, is_test)
            VALUES ('person', 'Selftest 042 Bad', ARRAY['selftest042 bad'], 'nba', true);
        EXCEPTION WHEN check_violation THEN v_refused := v_refused + 1;
        END;
        INSERT INTO public.entities (kind, name, aliases, sport, is_test)
        VALUES ('person', 'Test 042 Player', ARRAY['test 042 player'], 'nba', true) RETURNING id INTO v_tperson;
        INSERT INTO public.entities (kind, name, aliases, sport, is_test)
        VALUES ('team', 'Test 042 Team A', ARRAY['test 042 team a'], 'nba', true) RETURNING id INTO v_ta;
        INSERT INTO public.entities (kind, name, aliases, sport, is_test)
        VALUES ('team', 'Test 042 Team B', ARRAY['test 042 team b'], 'nba', true) RETURNING id INTO v_tb;
        BEGIN  -- never followed
            INSERT INTO public.follows (entity_id, tier, pillar, floor) VALUES (v_tperson, 1, 'sports', false);
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- never in a window
            INSERT INTO public.boost_windows (sport, entity_id, label, starts_at, ends_at, boost_hours,
                                              source_url, fetched_at)
            VALUES ('nba', v_ta, 'selftest 042 test window', now(), now() + interval '1 hour', 1,
                    'https://selftest.invalid/042', now());
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- never linked to a real entity
            INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
            VALUES (v_tperson, v_team_a, 'plays_for', '2026-01-01', true);
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- never changes sides
            UPDATE public.entities SET is_test = false WHERE id = v_tperson;
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- a real proposal names its story
            INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, to_entity_id)
            VALUES (v_person, 'signing', 'plays_for', v_team_a);
        EXCEPTION WHEN check_violation THEN v_refused := v_refused + 1;
        END;
        BEGIN  -- the e2e writers are the owner's only (no session here)
            PERFORM public.add_test_move_proposal('x', 'selftest');
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            PERFORM public.add_test_sports_story('x', 'golf', 'selftest', NULL, 'https://selftest.invalid/042/t');
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        BEGIN
            PERFORM public.add_test_news_story('x', 'deals', 'selftest', NULL, 'https://selftest.invalid/042/n');
        EXCEPTION WHEN raise_exception THEN v_refused := v_refused + 1;
        END;
        IF v_refused <> 9 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 9 test-isolation checks refused', v_refused;
        END IF;
        INSERT INTO public.entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
        VALUES (v_tperson, v_ta, 'plays_for', '2026-01-01', true);
        INSERT INTO public.entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, is_test)
        VALUES (v_tperson, 'trade', 'plays_for', v_ta, v_tb, true) RETURNING id INTO v_tp;

        -- 8. ctx_sports_feed: five stories, the top of Sports For you first,
        --    the active window, no test proposal; not a brief section.
        v_ctx := public.ctx_sports_feed();
        IF jsonb_array_length(v_ctx -> 'top_stories') <> 5
           OR (v_ctx -> 'top_stories' -> 0 ->> 'title')
              IS DISTINCT FROM (SELECT f.title FROM public.feed_for_you('sports', 1, 0) f)
           OR NOT (v_ctx -> 'boosts_now') @> '[{"label": "selftest 042 window"}]'::jsonb
           OR jsonb_typeof(v_ctx -> 'proposed_moves') IS DISTINCT FROM 'array'
           OR v_ctx::text LIKE '%Test 042%' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_sports_feed shape or content: %', left(v_ctx::text, 400);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers
                        WHERE key = 'sports_feed' AND NOT brief_section AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the sports_feed provider is missing or a brief section';
        END IF;
        -- ctx_feed still resolves feed_for_you (now with p_sport).
        IF jsonb_typeof(public.ctx_feed() -> 'top_stories') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_feed broke';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '042 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('042_sports')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) three new tables: RLS on; boost_windows 1 policy, entity_link_ends 2,
--       entity_link_proposals 2; anon nothing; entity_links 2 policies
--       (read, confirm). authenticated: INSERT on entity_links and
--       entity_link_ends only; UPDATE only on entity_link_proposals.decision
--   (b) valemont_readonly reads the three tables and the view, executes
--       feed_for_you(4 args) and ctx_sports_feed; anon executes nothing
--   (c) boost_windows: 12 (cfb 6 with entity Alabama Crimson Tide, f1 6);
--       the Tennessee window active now
--   (d) follows: tier 1: 7, tier 2: 0, tier 3: 23; entities by kind; 3 links,
--       all current
--   (e) seed_interests.feed.sports: love 8, not interested 2; home unchanged
--       (love 10, not interested 7)
--   (f) providers: sports_feed sort 45, brief_section false
--   (g) the migration_log row (and the NOTICE "042 self-test passed")
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any,
       has_table_privilege('authenticated', c.oid, 'INSERT') AS auth_insert,
       has_table_privilege('authenticated', c.oid, 'DELETE') AS auth_delete,
       has_any_column_privilege('authenticated', c.oid, 'UPDATE') AS auth_update_any_column
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('boost_windows', 'entity_link_proposals', 'entity_link_ends', 'entity_links')
 ORDER BY c.relname;                                                                      -- (a)

SELECT c.relname, has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('boost_windows', 'entity_link_proposals', 'entity_link_ends', 'v_entity_links_current')
 ORDER BY c.relname;
SELECT has_function_privilege('valemont_readonly', 'public.feed_for_you(text, integer, integer, text, text)', 'EXECUTE') AS readonly_for_you,
       has_function_privilege('valemont_readonly', 'public.ctx_sports_feed()', 'EXECUTE') AS readonly_ctx_sports,
       has_function_privilege('anon', 'public.feed_for_you(text, integer, integer, text, text)', 'EXECUTE') AS anon_for_you,
       has_function_privilege('anon', 'public.decide_entity_link_proposal(bigint, text)', 'EXECUTE') AS anon_decide; -- (b)

SELECT w.sport, e.name AS entity, count(*) AS windows,
       count(*) FILTER (WHERE w.starts_at <= now() AND now() < w.ends_at) AS active_now,
       min(w.starts_at) AS first_start, max(w.ends_at) AS last_end
  FROM boost_windows w LEFT JOIN entities e ON e.id = w.entity_id
 GROUP BY w.sport, e.name ORDER BY w.sport;                                               -- (c)

SELECT f.tier, count(*) FROM follows f GROUP BY f.tier ORDER BY f.tier;
SELECT kind, sport, count(*) FROM entities WHERE NOT is_test GROUP BY kind, sport ORDER BY kind, sport;
SELECT count(*) AS links, (SELECT count(*) FROM v_entity_links_current) AS current FROM entity_links; -- (d)

SELECT jsonb_array_length(seed_interests #> '{feed,sports,love}') AS sports_love,
       jsonb_array_length(seed_interests #> '{feed,sports,not_interested}') AS sports_ni,
       jsonb_array_length(seed_interests #> '{feed,home,love}') AS home_love,
       jsonb_array_length(seed_interests #> '{feed,home,not_interested}') AS home_ni
  FROM app_settings;                                                                      -- (e)

SELECT key, sort, brief_section, enabled FROM context_providers ORDER BY sort;           -- (f)
SELECT id, kind, pattern, active FROM feed_rules WHERE pattern LIKE '%golfdigest%';       -- (f2) one active url_regex
SELECT coalesce(category, '(none yet)') AS category, count(*) FROM stories
 WHERE NOT is_test GROUP BY 1 ORDER BY 2 DESC;                                             -- (f3) all '(none yet)' until the backfill script runs

SELECT name, applied_at FROM migration_log WHERE name = '042_sports';                     -- (g)
