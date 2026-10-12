-- 033_feed_calibration.sql — what the feed's 24-hour calibration report reads
-- (Chat 3, Phase 1; owner decisions 2026-10-11).
--
-- Three things the report must list, none recoverable from logs alone:
--   * the shadow `drop` category Haiku returns per story (gossip,
--     partisan_politics, ai_hype, crypto_price, recap_no_storyline,
--     listicle, none). In SHADOW it is only stored; nothing is filtered.
--     Going live later sets filtered_reason = 'drop:<category>' as well.
--   * each story's best seed-phrase similarity (>= 0.70), FOR THE RECORD
--     ONLY. The seed-phrase embedding filter is retired: gte-small is
--     anisotropic (unrelated pairs score 0.73-0.76; docs/feed_sources.md),
--     so the phrases cannot separate gossip from business news. The
--     seed_phrase_drop rows in feed_rules stay as they are (active): they
--     are still embedded and scored, they just never filter.
--   * every story join and every near miss (cosine 0.78-0.86), with the
--     cosine and the rapidfuzz score, so the 0.86 / 90 thresholds can be
--     calibrated on real pairs. Both titles come from the joined rows.
--
-- feed_similarity_log is pruned after 30 days by the nightly feed job, and
-- its rows go with their item or story.
--
-- Plus two read-only views for /settings/sources (v_feed_filtered,
-- v_feed_source_stats), so its 24-hour counts are computed in the database.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

ALTER TABLE stories
    ADD COLUMN IF NOT EXISTS drop_category TEXT
        CONSTRAINT stories_drop_category_check CHECK (drop_category IN (
            'gossip', 'partisan_politics', 'ai_hype', 'crypto_price',
            'recap_no_storyline', 'listicle', 'none')),
    -- Best cosine of any member item against any seed_phrase_drop phrase,
    -- recorded from 0.70 up; NULL below that. Never used to filter.
    ADD COLUMN IF NOT EXISTS seed_score REAL
        CONSTRAINT stories_seed_score_check CHECK (seed_score BETWEEN -1 AND 1),
    ADD COLUMN IF NOT EXISTS seed_rule_id BIGINT REFERENCES feed_rules (id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS feed_similarity_log (
    id          BIGSERIAL    PRIMARY KEY,
    -- The item being placed, and the candidate story it was compared with.
    item_id     BIGINT       NOT NULL REFERENCES content_items (id) ON DELETE CASCADE,
    story_id    BIGINT       NOT NULL REFERENCES stories (id) ON DELETE CASCADE,
    kind        TEXT         NOT NULL CHECK (kind IN ('join', 'near_miss')),
    cosine      REAL         NOT NULL CHECK (cosine BETWEEN -1 AND 1),
    -- rapidfuzz token_set_ratio of the normalized titles, 0-100.
    fuzz        REAL         NOT NULL CHECK (fuzz BETWEEN 0 AND 100),
    -- For a join: which rule admitted it.
    joined_by   TEXT         CHECK (joined_by IN ('cosine', 'fuzz', 'both')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK ((kind = 'join') = (joined_by IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS feed_similarity_log_created ON feed_similarity_log (created_at);
CREATE INDEX IF NOT EXISTS feed_similarity_log_item ON feed_similarity_log (item_id);
CREATE INDEX IF NOT EXISTS feed_similarity_log_story ON feed_similarity_log (story_id);

-- /settings/sources reads these two (security_invoker: RLS applies, so the
-- owner sees them and no one else). Counting in the database, not in the
-- page: a busy day passes PostgREST's 1,000-row page.
--
-- v_feed_filtered: every real filtered story, credited to the outlet of its
-- FIRST item (the one that founded it), for the "Filtered today" audit.
CREATE OR REPLACE VIEW v_feed_filtered
WITH (security_invoker = true)
AS
SELECT st.id, st.title, st.filtered_reason, st.first_seen_at, st.pillars,
       f.source_id, src.name AS source_name, f.url
  FROM public.stories st
  JOIN LATERAL (SELECT i.source_id, i.url FROM public.content_items i
                 WHERE i.story_id = st.id ORDER BY i.id LIMIT 1) f ON true
  JOIN public.sources src ON src.id = f.source_id
 WHERE st.filtered_reason IS NOT NULL AND NOT st.is_test;

-- v_feed_source_stats: per real source, the last 24 hours: items ingested
-- (not seeded), Google News decode failures, stories it founded that were
-- filtered.
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
           AND f.first_seen_at > now() - interval '24 hours') AS filtered_24h
  FROM public.sources s
 WHERE NOT s.is_test;

REVOKE ALL ON v_feed_filtered, v_feed_source_stats FROM anon, authenticated, PUBLIC;
GRANT SELECT ON v_feed_filtered, v_feed_source_stats TO authenticated, valemont_readonly;

ALTER TABLE feed_similarity_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON feed_similarity_log FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE feed_similarity_log_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON feed_similarity_log TO authenticated;
DROP POLICY IF EXISTS feed_similarity_log_owner_read ON feed_similarity_log;
CREATE POLICY feed_similarity_log_owner_read ON feed_similarity_log
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));

INSERT INTO migration_log (name) VALUES ('033_feed_calibration')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) stories has drop_category, seed_score, seed_rule_id
--   (b) feed_similarity_log: RLS on, one owner read policy, anon nothing,
--       valemont_readonly can SELECT
--   (c) the migration_log row
--   (d) both views: security_invoker on, anon nothing, readonly reads
-- ---------------------------------------------------------------------------

SELECT column_name, data_type FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'stories'
   AND column_name IN ('drop_category', 'seed_score', 'seed_rule_id')
 ORDER BY column_name;                                                                    -- (a)

SELECT c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = 'feed_similarity_log') AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any,
       has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c WHERE c.oid = 'public.feed_similarity_log'::regclass;                   -- (b)

SELECT name, applied_at FROM migration_log WHERE name = '033_feed_calibration';           -- (c)

SELECT c.relname, c.reloptions,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
       has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c
 WHERE c.oid IN ('public.v_feed_filtered'::regclass, 'public.v_feed_source_stats'::regclass)
 ORDER BY c.relname;                                                                      -- (d)
