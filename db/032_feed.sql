-- 032_feed.sql — the learning engine's feed: sources, items, stories, rules
-- (Chat 3, Phase 1).
--
-- What it holds:
--   sources        one row per publisher feed. The worker syncs it from
--                  workers/feed/sources.toml at boot (upsert by slug, never
--                  delete; a removed slug goes active = false) and records
--                  every fetch on the row.
--   content_items  one row per item fetched. The record of what the source
--                  published: after insert, the only change allowed is
--                  clearing `embedding` (nightly, after 72 h). Deleted only by
--                  the prune, through its story.
--   stories        one row per real-world story; items from several outlets
--                  join one story. The worker updates it as items join
--                  (centroid, counts, lead) and when Haiku summarizes it.
--   feed_rules     the drop list applied before any AI spend.
--
-- Not a ledger: nothing here is a commitment, so these are app tables the
-- worker edits in place. The guard that matters is content_items_frozen.
--
-- Embeddings: gte-small, 384 dimensions, stored as halfvec(384) (pgvector
-- 0.8.2 is available here; halfvec needs >= 0.7). No vector index in this
-- phase: the story join compares against stories seen in the last 48 h, a
-- few hundred rows, found through stories_last_seen.
--
-- Two owner decisions (2026-10-11, docs/feed_sources.md):
--   * sources.include_categories: ingest only items carrying one of these
--     feed categories (Semafor: Business, Media). NULL = everything.
--   * sources.gnews_window_days: the `when:` window of a Google News
--     fallback query (default 2; Axios Miami and The Real Deal Miami use 7).
--   * feed_rules kind url_regex, seeded with the Axios Pro drop.
--
-- Test rows: is_test stories and items come only from add_test_story() with
-- the verified e2e marker (db/027's rule), or from tests_live as the worker,
-- and always through the `_e2e` source (is_test, never active). A trigger
-- keeps is_test consistent across source, item and story. v_feed_stories is
-- the real selector and never shows them.
--
-- Access (db/019, db/020): RLS on every table, the owner SELECTs, no web
-- writes this phase, anon nothing. The worker writes via DATABASE_URL.
-- valemont_readonly reads all four through db/018's default privileges.
--
-- Prerequisites: db/015 (migration_log), db/018, db/020 (is_owner,
-- reject_truncate, ai_usage), db/027 (e2e_markers).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run (IF NOT EXISTS everywhere; seeds ON CONFLICT DO NOTHING).

-- ===================================================================== 0
-- pgvector, in the extensions schema (Supabase's convention). Types are
-- referenced schema-qualified below so no search_path is assumed.

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

DO $$
BEGIN
    IF (SELECT string_to_array(extversion, '.')::int[] FROM pg_extension WHERE extname = 'vector')
       < ARRAY[0, 7] THEN
        RAISE EXCEPTION 'pgvector % is older than 0.7: halfvec is unavailable. Nothing was applied.',
            (SELECT extversion FROM pg_extension WHERE extname = 'vector');
    END IF;
END
$$;

-- ===================================================================== 1
-- sources

CREATE TABLE IF NOT EXISTS sources (
    id                    BIGSERIAL    PRIMARY KEY,
    slug                  TEXT         NOT NULL UNIQUE CHECK (slug ~ '^_?[a-z0-9][a-z0-9-]*$'),
    name                  TEXT         NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    pillars               TEXT[]       NOT NULL
                                       CHECK (cardinality(pillars) >= 1
                                              AND pillars <@ ARRAY['home', 'sports']::text[]),
    sport                 TEXT         CHECK (sport IN ('cfb', 'nfl', 'nba', 'f1', 'golf')),
    kind                  TEXT         NOT NULL CHECK (kind IN ('rss', 'gnews')),
    url                   TEXT         NOT NULL CHECK (url ~ '^https://'),
    -- Lower wins the dedupe lead.
    priority              INTEGER      NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
    active                BOOLEAN      NOT NULL DEFAULT true,
    -- Feed categories to keep (exact, case-insensitive at ingest); NULL = all.
    include_categories    TEXT[]       CHECK (include_categories IS NULL OR cardinality(include_categories) >= 1),
    gnews_window_days     INTEGER      NOT NULL DEFAULT 2 CHECK (gnews_window_days BETWEEN 1 AND 30),
    -- Why a source is inactive, or anything else worth a line.
    notes                 TEXT         CHECK (length(notes) <= 500),
    -- Conditional GET state.
    etag                  TEXT         CHECK (length(etag) <= 500),
    last_modified         TEXT         CHECK (length(last_modified) <= 100),
    last_fetched_at       TIMESTAMPTZ,
    -- NULL until the first successful fetch: that is "first fetch" (the
    -- 72-hour guard, and seed-and-skip for undated feeds).
    last_ok_at            TIMESTAMPTZ,
    -- 'ok', 'not_modified', or 'error'.
    last_status           TEXT         CHECK (last_status IN ('ok', 'not_modified', 'error')),
    -- Redacted by the writer (type and message, no URL query); capped here.
    last_error            TEXT         CHECK (length(last_error) <= 500),
    consecutive_failures  INTEGER      NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    -- The one push after 6 consecutive failures: set when sent, cleared on
    -- the next success, so an outage alerts once.
    alerted_at            TIMESTAMPTZ,
    is_test               BOOLEAN      NOT NULL DEFAULT false,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (NOT is_test OR NOT active),
    CHECK (kind = 'rss' OR url ~ '^https://news\.google\.com/rss/search\?')
);

-- The one test source. Never active; the toml sync never touches it.
INSERT INTO sources (slug, name, pillars, kind, url, priority, active, is_test, notes)
VALUES ('_e2e', 'Test source', ARRAY['home', 'sports'], 'rss', 'https://e2e.invalid/feed',
        1000, false, true, 'tests_live and add_test_story() only')
ON CONFLICT (slug) DO NOTHING;

-- ===================================================================== 2
-- stories and content_items. Circular references: an item belongs to a
-- story, a story names its lead item. The worker inserts the story (lead
-- NULL), then its first item, then sets the lead.

CREATE TABLE IF NOT EXISTS stories (
    id                BIGSERIAL    PRIMARY KEY,
    pillars           TEXT[]       NOT NULL
                                   CHECK (cardinality(pillars) >= 1
                                          AND pillars <@ ARRAY['home', 'sports']::text[]),
    sport             TEXT         CHECK (sport IN ('cfb', 'nfl', 'nba', 'f1', 'golf')),
    lead_item_id      BIGINT,
    title             TEXT         NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    -- Haiku's: at most 2 sentences, 45 words (validated by the worker; the
    -- length cap is the backstop). NULL = the card shows the headline.
    summary           TEXT         CHECK (length(summary) <= 400),
    entities          TEXT[]       NOT NULL DEFAULT '{}',
    topics            TEXT[]       NOT NULL DEFAULT '{}',
    -- Running mean of member items' embeddings (not re-normalized).
    centroid          extensions.halfvec(384),
    item_count        INTEGER      NOT NULL DEFAULT 1 CHECK (item_count >= 1),
    first_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- NULL = kept. Otherwise e.g. 'title_regex:12', 'url_regex:31',
    -- 'seed_phrase:44 (0.83)'. A filtered story is never summarized.
    filtered_reason   TEXT         CHECK (length(filtered_reason) <= 200),
    -- Summary passes tried: one retry in the next run, then left NULL.
    summary_attempts  SMALLINT     NOT NULL DEFAULT 0 CHECK (summary_attempts BETWEEN 0 AND 5),
    summarized_at     TIMESTAMPTZ,
    ai_usage_id       BIGINT       REFERENCES ai_usage (id) ON DELETE RESTRICT,
    is_test           BOOLEAN      NOT NULL DEFAULT false,
    CHECK (last_seen_at >= first_seen_at),
    -- A real summary names the call that wrote it. A test story's summary
    -- (add_test_story) has no call behind it.
    CHECK (summary IS NULL OR summarized_at IS NOT NULL),
    CHECK (summary IS NULL OR is_test OR ai_usage_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS content_items (
    id             BIGSERIAL    PRIMARY KEY,
    source_id      BIGINT       NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    -- The feed's guid/id, else its link.
    external_id    TEXT         NOT NULL CHECK (length(external_id) BETWEEN 1 AND 2000),
    -- Canonical publisher URL (utm_*/fbclid stripped, no fragment, host
    -- normalized). NULL exactly when a Google News link failed to decode.
    url            TEXT         CHECK (url ~ '^https?://' AND length(url) <= 2000),
    -- The news.google.com link, kept only for fallback sources.
    google_url     TEXT         CHECK (google_url ~ '^https://news\.google\.com/' AND length(google_url) <= 2000),
    title          TEXT         NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    -- HTML stripped, at most 500 characters.
    teaser         TEXT         CHECK (length(teaser) <= 500),
    -- Capped at fetched_at (ESPN dates items ahead); first-seen time for an
    -- undated feed.
    published_at   TIMESTAMPTZ  NOT NULL,
    fetched_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- NULL only for a seeded item (seed-and-skip): recorded as seen on an
    -- undated source's first fetch, never shown, never clustered.
    story_id       BIGINT       REFERENCES stories (id) ON DELETE CASCADE,
    seeded         BOOLEAN      NOT NULL DEFAULT false,
    -- gte-small of title + teaser. Cleared after 72 h.
    embedding      extensions.halfvec(384),
    decode_failed  BOOLEAN      NOT NULL DEFAULT false,
    is_test        BOOLEAN      NOT NULL DEFAULT false,
    CONSTRAINT content_items_source_external UNIQUE (source_id, external_id),
    CHECK (seeded = (story_id IS NULL)),
    CHECK (decode_failed = (url IS NULL)),
    CHECK (NOT decode_failed OR google_url IS NOT NULL),
    CHECK (published_at <= fetched_at)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stories_lead_item_fk') THEN
        ALTER TABLE stories ADD CONSTRAINT stories_lead_item_fk
            FOREIGN KEY (lead_item_id) REFERENCES content_items (id) ON DELETE SET NULL;
    END IF;
END
$$;

-- Exact dedupe, level 1: one row per canonical URL, across sources.
CREATE UNIQUE INDEX IF NOT EXISTS content_items_url ON content_items (url) WHERE url IS NOT NULL;
CREATE INDEX IF NOT EXISTS content_items_story ON content_items (story_id);
CREATE INDEX IF NOT EXISTS content_items_fetched ON content_items (fetched_at);
-- The nightly embedding clear.
CREATE INDEX IF NOT EXISTS content_items_embedded ON content_items (fetched_at) WHERE embedding IS NOT NULL;
-- The 48-hour story candidates, the feed (newest first) and the prune.
CREATE INDEX IF NOT EXISTS stories_last_seen ON stories (last_seen_at);
CREATE INDEX IF NOT EXISTS stories_first_seen ON stories (first_seen_at DESC);
-- The summarizer's queue.
CREATE INDEX IF NOT EXISTS stories_to_summarize ON stories (first_seen_at DESC)
    WHERE summarized_at IS NULL AND filtered_reason IS NULL;

-- An item, once written, is the record of what its source published. The
-- only change allowed is clearing the embedding.
CREATE OR REPLACE FUNCTION public.content_items_frozen()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.embedding IS NOT NULL
       OR (to_jsonb(NEW) - 'embedding') IS DISTINCT FROM (to_jsonb(OLD) - 'embedding') THEN
        RAISE EXCEPTION 'content_items % is frozen: only clearing its embedding is allowed', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS content_items_frozen ON content_items;
CREATE TRIGGER content_items_frozen
    BEFORE UPDATE ON content_items
    FOR EACH ROW EXECUTE FUNCTION public.content_items_frozen();

-- is_test is all or nothing: a test item comes from the test source and
-- belongs to a test story; a real item comes from a real source and belongs
-- to a real story. And a story's lead is one of its own items WITH a url: an
-- item whose Google News link failed to decode can never lead. A story
-- whose only items failed to decode keeps lead_item_id NULL (and so never
-- reaches v_feed_stories) until a linkable item joins. url is frozen
-- (content_items_frozen), so a valid lead stays valid.
CREATE OR REPLACE FUNCTION public.feed_test_consistent()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_TABLE_NAME = 'content_items' THEN
        IF (SELECT s.is_test FROM public.sources s WHERE s.id = NEW.source_id) IS DISTINCT FROM NEW.is_test THEN
            RAISE EXCEPTION 'content_items: is_test must match its source';
        END IF;
        IF NEW.story_id IS NOT NULL
           AND (SELECT st.is_test FROM public.stories st WHERE st.id = NEW.story_id) IS DISTINCT FROM NEW.is_test THEN
            RAISE EXCEPTION 'content_items: is_test must match its story';
        END IF;
    ELSIF NEW.lead_item_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.content_items i
                           WHERE i.id = NEW.lead_item_id AND i.story_id = NEW.id
                             AND i.url IS NOT NULL) THEN
        RAISE EXCEPTION 'stories: lead item % is not a linkable item of story % (url must not be NULL)',
            NEW.lead_item_id, NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS content_items_test_consistent ON content_items;
CREATE TRIGGER content_items_test_consistent
    BEFORE INSERT ON content_items
    FOR EACH ROW EXECUTE FUNCTION public.feed_test_consistent();
DROP TRIGGER IF EXISTS stories_lead_consistent ON stories;
CREATE TRIGGER stories_lead_consistent
    BEFORE INSERT OR UPDATE OF lead_item_id ON stories
    FOR EACH ROW EXECUTE FUNCTION public.feed_test_consistent();

-- ===================================================================== 3
-- feed_rules. Patterns are case-insensitive and must be valid in both
-- Python `re` and Postgres regex (no \b, \d or lookarounds); the CHECK
-- compiles each one here, and a test compiles them in Python.

CREATE TABLE IF NOT EXISTS feed_rules (
    id          BIGSERIAL    PRIMARY KEY,
    kind        TEXT         NOT NULL CHECK (kind IN ('title_regex', 'url_regex', 'seed_phrase_drop')),
    -- NULL = both pillars.
    pillar      TEXT         CHECK (pillar IN ('home', 'sports')),
    pattern     TEXT         NOT NULL CHECK (length(pattern) BETWEEN 1 AND 300),
    active      BOOLEAN      NOT NULL DEFAULT true,
    note        TEXT         CHECK (length(note) <= 200),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT feed_rules_once UNIQUE NULLS NOT DISTINCT (kind, pillar, pattern),
    -- Raises on an invalid regex, so a bad pattern cannot be inserted.
    CHECK (kind = 'seed_phrase_drop' OR ('' ~* pattern) IS NOT NULL)
);

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    -- Box scores, plain recaps, viewing guides, picks.
    ('title_regex', NULL, 'box score',            'recap'),
    ('title_regex', NULL, 'final score',          'recap'),
    ('title_regex', NULL, 'player grades',        'recap'),
    ('title_regex', NULL, 'game recap',           'recap'),
    ('title_regex', NULL, 'recap$',               'recap'),
    ('title_regex', NULL, 'highlights$',          'recap'),
    ('title_regex', NULL, 'live updates',         'live'),
    ('title_regex', NULL, 'live blog',            'live'),
    ('title_regex', NULL, 'live stream',          'viewing guide'),
    ('title_regex', NULL, 'how to watch',         'viewing guide'),
    ('title_regex', NULL, 'where to watch',       'viewing guide (owner, 2026-10-11)'),
    ('title_regex', NULL, 'where to stream',      'viewing guide'),
    ('title_regex', NULL, 'what channel',         'viewing guide'),
    ('title_regex', NULL, 'tv channel',           'viewing guide'),
    ('title_regex', NULL, 'odds, picks',          'picks'),
    ('title_regex', NULL, 'picks, predictions',   'picks'),
    ('title_regex', NULL, 'predictions, odds',    'picks'),
    ('title_regex', NULL, 'prediction:',          'picks'),
    -- SEO listicles and commerce.
    ('title_regex', NULL, '^[0-9]+ (best|things|reasons|ways|times) ', 'listicle'),
    ('title_regex', NULL, 'best .* deals',        'commerce'),
    ('title_regex', NULL, 'deals of the day',     'commerce'),
    ('title_regex', NULL, 'promo code',           'commerce'),
    ('title_regex', NULL, 'bonus code',           'commerce'),
    -- Axios Pro (paid) pages; the owner named pro.axios.com, and the decoded
    -- links are www.axios.com/pro/, so both are covered.
    ('url_regex',   NULL, '^https?://(pro\.axios\.com|(www\.)?axios\.com/pro/)', 'paywalled Axios Pro (owner, 2026-10-11)'),
    -- The owner's don't-cares, matched by embedding (cosine >= 0.80).
    ('seed_phrase_drop', NULL, 'celebrity gossip and dating rumors',                       'celebrity'),
    ('seed_phrase_drop', NULL, 'celebrity couple breakup and relationship drama',          'celebrity'),
    ('seed_phrase_drop', NULL, 'reality TV star feud and red carpet fashion',              'celebrity'),
    ('seed_phrase_drop', NULL, 'presidential election polls and campaign horse race',      'politics'),
    ('seed_phrase_drop', NULL, 'partisan culture war outrage between Republicans and Democrats', 'politics'),
    ('seed_phrase_drop', NULL, 'how artificial intelligence will change everything about your life', 'ai hype'),
    ('seed_phrase_drop', NULL, 'AI will replace all jobs and transform the world',         'ai hype'),
    ('seed_phrase_drop', NULL, 'Bitcoin price prediction today',                           'crypto price'),
    ('seed_phrase_drop', NULL, 'crypto prices rally as Bitcoin and Ethereum surge',        'crypto price')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

-- ===================================================================== 4
-- The real selector: kept, non-test stories with their lead item and source.
-- security_invoker: RLS applies. Apps/web filters by pillar.
-- A story with no item whose url IS NOT NULL never appears: the inner join
-- needs a lead, a lead must have a url (trigger above), and the view checks
-- i.url again so it holds even without the trigger.

CREATE OR REPLACE VIEW v_feed_stories
WITH (security_invoker = true)
AS
SELECT st.id, st.pillars, st.sport, st.title, st.summary, st.entities, st.topics,
       st.item_count, st.first_seen_at, st.last_seen_at,
       i.id AS lead_item_id, i.url AS lead_url, i.published_at AS lead_published_at,
       s.slug AS source_slug, s.name AS source_name
  FROM public.stories st
  JOIN public.content_items i ON i.id = st.lead_item_id
  JOIN public.sources s ON s.id = i.source_id
 WHERE NOT st.is_test
   AND st.filtered_reason IS NULL
   AND i.url IS NOT NULL;

-- ===================================================================== 5
-- The one web writer of test stories (e2e, verified marker; db/027's rule).

CREATE OR REPLACE FUNCTION public.add_test_story(p_marker text, p_pillar text, p_title text,
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
        RAISE EXCEPTION 'add_test_story: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_story: test marker refused';
    END IF;
    IF p_url !~ '^https://[a-z0-9.-]+\.invalid/' THEN
        RAISE EXCEPTION 'add_test_story: test URLs must be on a .invalid host';
    END IF;
    SELECT id INTO v_source FROM public.sources WHERE slug = '_e2e' AND is_test;
    -- p_summary NULL makes a headline-only card.
    INSERT INTO public.stories (pillars, title, summary, summarized_at, is_test)
    VALUES (ARRAY[p_pillar], p_title, p_summary,
            CASE WHEN p_summary IS NOT NULL THEN now() END, true)
    RETURNING id INTO v_story;
    INSERT INTO public.content_items (source_id, external_id, url, title, published_at,
                                      story_id, is_test)
    VALUES (v_source, 'e2e:' || gen_random_uuid(), p_url, p_title, now(), v_story, true)
    RETURNING id INTO v_item;
    UPDATE public.stories SET lead_item_id = v_item WHERE id = v_story;
    RETURN v_story;
END;
$$;

-- ===================================================================== 6
-- Access.

ALTER TABLE sources       ENABLE ROW LEVEL SECURITY;
ALTER TABLE stories       ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE feed_rules    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON sources, stories, content_items, feed_rules, v_feed_stories FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE sources_id_seq, stories_id_seq, content_items_id_seq, feed_rules_id_seq
    FROM anon, authenticated, PUBLIC;

-- The owner reads; only the worker (DATABASE_URL) writes.
GRANT SELECT ON sources, stories, content_items, feed_rules, v_feed_stories TO authenticated;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['sources', 'stories', 'content_items', 'feed_rules'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_owner_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING ((SELECT public.is_owner()))',
            t || '_owner_read', t);
    END LOOP;
END
$$;

-- db/018's default privileges cover the tables; the view is granted
-- explicitly, as db/028 does for every view.
GRANT SELECT ON v_feed_stories TO valemont_readonly;

REVOKE ALL ON FUNCTION public.content_items_frozen() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.feed_test_consistent() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_test_story(text, text, text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_story(text, text, text, text, text) TO authenticated;

-- ===================================================================== 7
-- Self-test of the lead rule and the view, run on paste. Everything happens
-- inside a subtransaction that is rolled back at the end (the closing
-- 'selftest-rollback' exception), so no row survives; only sequence values
-- are consumed. A wrong result raises SELFTEST FAILED and nothing in this
-- file is applied.

DO $$
DECLARE
    v_src    bigint;
    v_story  bigint;
    v_bad    bigint;
    v_good   bigint;
    v_caught boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.sources (slug, name, pillars, kind, url)
        VALUES ('selftest-032', 'Self-test', ARRAY['home'], 'rss', 'https://selftest.invalid/feed')
        RETURNING id INTO v_src;
        INSERT INTO public.stories (pillars, title) VALUES (ARRAY['home'], 'self-test story')
        RETURNING id INTO v_story;
        -- An item whose Google News link failed to decode.
        INSERT INTO public.content_items (source_id, external_id, url, google_url, title,
                                          published_at, story_id, decode_failed)
        VALUES (v_src, 'selftest:bad', NULL, 'https://news.google.com/rss/articles/selftest',
                'self-test item', now(), v_story, true)
        RETURNING id INTO v_bad;

        -- 1. It cannot lead.
        BEGIN
            UPDATE public.stories SET lead_item_id = v_bad WHERE id = v_story;
        EXCEPTION WHEN raise_exception THEN
            v_caught := true;
        END;
        IF NOT v_caught THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an item with url NULL became a lead';
        END IF;

        -- 2. A story with no linkable item is not in the view.
        IF EXISTS (SELECT 1 FROM public.v_feed_stories WHERE id = v_story) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a story with no url item is in v_feed_stories';
        END IF;

        -- 3. Once a linkable item joins and leads, the story appears, linked.
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id)
        VALUES (v_src, 'selftest:good', 'https://selftest.invalid/a', 'self-test item', now(), v_story)
        RETURNING id INTO v_good;
        UPDATE public.stories SET lead_item_id = v_good WHERE id = v_story;
        IF (SELECT count(*) FROM public.v_feed_stories
             WHERE id = v_story AND lead_url = 'https://selftest.invalid/a') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a story led by a url item is missing from v_feed_stories';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '032 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('032_feed')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) vector >= 0.7 in schema extensions
--   (b) four tables: RLS on, one owner read policy each, anon nothing,
--       authenticated SELECT only
--   (c) valemont_readonly can SELECT all four and v_feed_stories
--   (d) feed_rules: title_regex 23, url_regex 1, seed_phrase_drop 9
--   (e) sources: one row, _e2e, inactive, is_test
--   (f) the migration_log row
--   (g) lead rule: the trigger function checks url, the view filters on it
--       (both true), and the self-test above passed (the paste would have
--       failed otherwise)
--   (h) 0 stories led by an item with url NULL; 0 v_feed_stories rows with
--       lead_url NULL; 0 v_feed_stories rows whose story has no url item.
--       Trivially 0 on an empty table; worth re-running once ingest is live.
-- ---------------------------------------------------------------------------

SELECT e.extversion, n.nspname
  FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
 WHERE e.extname = 'vector';                                                              -- (a)

SELECT c.relname, c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any,
       has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE') AS auth_write
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('sources', 'stories', 'content_items', 'feed_rules')
 ORDER BY c.relname;                                                                      -- (b)

SELECT c.relname, has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace
   AND c.relname IN ('sources', 'stories', 'content_items', 'feed_rules', 'v_feed_stories')
 ORDER BY c.relname;                                                                      -- (c)

SELECT kind, count(*) FROM feed_rules GROUP BY kind ORDER BY kind;                       -- (d)

SELECT slug, active, is_test FROM sources;                                                -- (e)

SELECT name, applied_at FROM migration_log WHERE name = '032_feed';                       -- (f)

SELECT pg_get_functiondef('public.feed_test_consistent()'::regprocedure)
           LIKE '%AND i.url IS NOT NULL%' AS trigger_checks_url,
       pg_get_viewdef('public.v_feed_stories'::regclass)
           LIKE '%i.url IS NOT NULL%' AS view_filters_url;                               -- (g)

SELECT (SELECT count(*) FROM stories st JOIN content_items i ON i.id = st.lead_item_id
         WHERE i.url IS NULL) AS stories_led_by_unlinked,
       (SELECT count(*) FROM v_feed_stories WHERE lead_url IS NULL) AS view_rows_unlinked,
       (SELECT count(*) FROM v_feed_stories v
         WHERE NOT EXISTS (SELECT 1 FROM content_items i
                            WHERE i.story_id = v.id AND i.url IS NOT NULL)) AS view_rows_no_url_item; -- (h)
