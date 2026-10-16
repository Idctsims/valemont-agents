-- 054_arsenal.sql — The Arsenal core (Chat 5, Phase 1; owner, 2026-10-15).
--
-- The reference pool, verification, the daily drop and the Power Word.
-- Design: docs/arsenal.md. The worker: workers/arsenal/.
--
-- What it holds:
--   1. arsenal_pool_runs  APPEND-ONLY: one row per pool event (a batch or a
--                         top-up submitted, collected, its verification
--                         settled, or failed).
--   2. arsenal_refs       the pool and the owner's seeds. canonical_key
--                         UNIQUE. Content may change only while pending
--                         (the verifier's correction); a dropped, seed or
--                         rejected row never changes status; no DELETE, no
--                         TRUNCATE; a real ref enters pending.
--   3. arsenal_words      the Power Word pool. lemma UNIQUE. Same lifecycle.
--   4. arsenal_drops      APPEND-ONLY. One real drop per owner per date;
--                         exactly 3 references in 3 categories plus 1 word;
--                         the guard refuses anything already dropped, a seed,
--                         anything not verified or corrected, and a test row
--                         in a real drop (or the reverse). It marks its rows
--                         dropped.
--   5. arsenal_reactions  APPEND-ONLY: love / not_interested / known / none
--                         (an un-tap) on a ref or a word. is_test is the
--                         target's, so test reactions never train weights.
--   6. arsenal_near_dup(embedding, threshold): cosine inside Postgres; the
--      vector goes in, a boolean comes out, nothing is read back.
--   7. arsenal_weights(): category and room weights from base weights and
--      the latest real reaction per target.
--   8. arsenal_pick_drop(date, with_tests, topped_up): the picker (weighted
--      categories, weighted rooms, no category in yesterday's slot).
--   9. The 27 seeds (status 'seed': taste only, never a drop).
--  10. Test helpers (the verified e2e marker, db/051 e2e_marker_ok):
--      add_test_arsenal_ref, add_test_arsenal_word, add_test_arsenal_drop.
--  11. ctx_arsenal (Wags) and ctx_arsenal_brief (one brief line), sort 60.
--  12. Access: RLS on all five; anon nothing; the owner reads, and inserts
--      her own reactions; valemont_readonly reads all.
--
-- A self-test runs on paste (section 13), rolled back. PASTE BEFORE THE
-- MERGE: the worker and the web read the new schema.
-- Prerequisites: db/020, db/029, db/051 (titles, e2e_marker_ok), db/053.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- arsenal_pool_runs: append-only.

CREATE TABLE IF NOT EXISTS arsenal_pool_runs (
    id               BIGSERIAL     PRIMARY KEY,
    kind             TEXT          NOT NULL CHECK (kind IN ('batch', 'topup')),
    status           TEXT          NOT NULL CHECK (status IN ('submitted', 'collected', 'verified', 'failed')),
    -- The 'submitted' row a later row belongs to (NULL on 'submitted').
    run_id           BIGINT        REFERENCES arsenal_pool_runs (id) ON DELETE RESTRICT,
    batch_id         TEXT          CHECK (batch_id IS NULL OR batch_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    model            TEXT          CHECK (model IS NULL OR model ~ '^[a-z0-9.-]{1,64}$'),
    refs_requested   INTEGER       NOT NULL DEFAULT 0 CHECK (refs_requested >= 0),
    words_requested  INTEGER       NOT NULL DEFAULT 0 CHECK (words_requested >= 0),
    inserted_refs    INTEGER       NOT NULL DEFAULT 0 CHECK (inserted_refs >= 0),
    inserted_words   INTEGER       NOT NULL DEFAULT 0 CHECK (inserted_words >= 0),
    skipped_dup      INTEGER       NOT NULL DEFAULT 0 CHECK (skipped_dup >= 0),
    skipped_invalid  INTEGER       NOT NULL DEFAULT 0 CHECK (skipped_invalid >= 0),
    verified         INTEGER       NOT NULL DEFAULT 0 CHECK (verified >= 0),
    corrected        INTEGER       NOT NULL DEFAULT 0 CHECK (corrected >= 0),
    unverified       INTEGER       NOT NULL DEFAULT 0 CHECK (unverified >= 0),
    rejected         INTEGER       NOT NULL DEFAULT 0 CHECK (rejected >= 0),
    cost_usd         NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    note             TEXT          CHECK (note IS NULL OR char_length(note) <= 500),
    is_test          BOOLEAN       NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_pool_runs_shape CHECK (
        ((status = 'submitted') = (run_id IS NULL))
        AND (kind <> 'batch' OR status <> 'submitted' OR batch_id IS NOT NULL)
        AND (kind <> 'topup' OR batch_id IS NULL))
);
CREATE INDEX IF NOT EXISTS arsenal_pool_runs_run ON arsenal_pool_runs (run_id, status);
-- A run is collected (or failed) once, and its verification closed once:
-- reading a batch twice would record its usage twice.
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_pool_runs_one_close ON arsenal_pool_runs (run_id)
    WHERE status IN ('collected', 'failed');
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_pool_runs_one_verified ON arsenal_pool_runs (run_id)
    WHERE status = 'verified';
CREATE INDEX IF NOT EXISTS arsenal_pool_runs_open ON arsenal_pool_runs (created_at DESC) WHERE status = 'submitted';

CREATE OR REPLACE FUNCTION public.arsenal_stamp_created()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_pool_runs_stamp ON arsenal_pool_runs;
CREATE TRIGGER arsenal_pool_runs_stamp
    BEFORE INSERT ON arsenal_pool_runs
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS arsenal_pool_runs_immutable ON arsenal_pool_runs;
CREATE TRIGGER arsenal_pool_runs_immutable
    BEFORE UPDATE OR DELETE ON arsenal_pool_runs
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_pool_runs_no_truncate ON arsenal_pool_runs;
CREATE TRIGGER arsenal_pool_runs_no_truncate
    BEFORE TRUNCATE ON arsenal_pool_runs
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 2
-- arsenal_refs: the pool and the seeds.

CREATE TABLE IF NOT EXISTS arsenal_refs (
    id                 BIGSERIAL    PRIMARY KEY,
    canonical_key      TEXT         NOT NULL UNIQUE CHECK (canonical_key ~ '^[a-z0-9][a-z0-9_]{1,79}$'),
    category           TEXT         NOT NULL CHECK (category IN ('finance_business', 'history_military', 'film_tv',
                                                                 'sports', 'music_culture', 'philosophy',
                                                                 'literature_myth')),
    title              TEXT         NOT NULL CHECK (char_length(btrim(title)) BETWEEN 2 AND 120),
    -- At most two lines on the card.
    origin             TEXT         NOT NULL CHECK (char_length(btrim(origin)) BETWEEN 10 AND 280),
    meaning            TEXT         NOT NULL CHECK (char_length(btrim(meaning)) BETWEEN 5 AND 300),
    -- One breath.
    deploy_line        TEXT         NOT NULL CHECK (char_length(btrim(deploy_line)) BETWEEN 5 AND 220),
    room               TEXT         NOT NULL CHECK (room IN ('boardroom', 'pitch', 'negotiation', 'homies')),
    source_work        TEXT         NOT NULL CHECK (char_length(btrim(source_work)) BETWEEN 2 AND 160),
    -- Optional, at most 15 words, attributed to its work.
    quote              TEXT         CHECK (quote IS NULL OR (btrim(quote) <> '' AND char_length(quote) <= 160
                                           AND cardinality(regexp_split_to_array(btrim(quote), '\s+')) <= 15)),
    quote_by           TEXT         CHECK (quote_by IS NULL OR char_length(btrim(quote_by)) BETWEEN 1 AND 80),
    source_kind        TEXT         NOT NULL CHECK (source_kind IN ('wikipedia', 'wikiquote', 'other')),
    -- The page title the verifier reads (the model names it; the URL is the fetched page's own).
    source_page        TEXT         CHECK (source_page IS NULL OR char_length(source_page) BETWEEN 1 AND 200),
    source_url         TEXT         CHECK (source_url IS NULL OR (source_url ~ '^https://' AND char_length(source_url) <= 500)),
    status             TEXT         NOT NULL CHECK (status IN ('seed', 'pool', 'dropped', 'rejected')),
    verification       TEXT         NOT NULL DEFAULT 'pending'
                                    CHECK (verification IN ('pending', 'verified', 'corrected', 'unverified')),
    verification_note  TEXT         CHECK (verification_note IS NULL OR char_length(verification_note) <= 500),
    verified_at        TIMESTAMPTZ,
    -- Phases 2-3 add arm_me and title_hook.
    origin_kind        TEXT         NOT NULL CHECK (origin_kind IN ('pool', 'seed')),
    -- Phase 3 (a finished show's references). NULL in Phase 1.
    source_title_id    BIGINT       REFERENCES titles (id) ON DELETE RESTRICT,
    pool_run_id        BIGINT       REFERENCES arsenal_pool_runs (id) ON DELETE RESTRICT,
    -- gte-small (core/embed.py) of "title — source_work"; set once; never read back.
    embedding          extensions.halfvec(384),
    is_test            BOOLEAN      NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_refs_seed CHECK ((origin_kind = 'seed') = (status = 'seed')),
    CONSTRAINT arsenal_refs_settled CHECK ((verification = 'pending') = (verified_at IS NULL)),
    CONSTRAINT arsenal_refs_quote_by CHECK (quote_by IS NULL OR quote IS NOT NULL),
    CONSTRAINT arsenal_refs_dropped_checked CHECK (status <> 'dropped' OR verification IN ('verified', 'corrected')),
    CONSTRAINT arsenal_refs_checked_source CHECK (verification NOT IN ('verified', 'corrected') OR source_url IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS arsenal_refs_eligible ON arsenal_refs (category, room)
    WHERE status = 'pool' AND verification IN ('verified', 'corrected');
CREATE INDEX IF NOT EXISTS arsenal_refs_pending ON arsenal_refs (created_at) WHERE verification = 'pending';
CREATE INDEX IF NOT EXISTS arsenal_refs_run ON arsenal_refs (pool_run_id) WHERE pool_run_id IS NOT NULL;

-- ===================================================================== 3
-- arsenal_words: the Power Word pool.

CREATE TABLE IF NOT EXISTS arsenal_words (
    id                 BIGSERIAL    PRIMARY KEY,
    lemma              TEXT         NOT NULL UNIQUE,
    pronunciation      TEXT         CHECK (pronunciation IS NULL OR char_length(pronunciation) BETWEEN 1 AND 80),
    -- Two lines, from kaikki/Wiktionary, never from the model.
    etymology          TEXT         CHECK (etymology IS NULL OR char_length(etymology) BETWEEN 1 AND 300),
    etymology_url      TEXT         CHECK (etymology_url IS NULL OR (etymology_url ~ '^https://' AND char_length(etymology_url) <= 500)),
    meaning            TEXT         NOT NULL CHECK (char_length(btrim(meaning)) BETWEEN 5 AND 300),
    room               TEXT         NOT NULL CHECK (room IN ('boardroom', 'pitch', 'negotiation', 'homies')),
    sentence           TEXT         NOT NULL CHECK (char_length(btrim(sentence)) BETWEEN 5 AND 240),
    status             TEXT         NOT NULL DEFAULT 'pool' CHECK (status IN ('pool', 'dropped', 'rejected')),
    verification       TEXT         NOT NULL DEFAULT 'pending'
                                    CHECK (verification IN ('pending', 'verified', 'corrected', 'unverified')),
    verification_note  TEXT         CHECK (verification_note IS NULL OR char_length(verification_note) <= 500),
    verified_at        TIMESTAMPTZ,
    pool_run_id        BIGINT       REFERENCES arsenal_pool_runs (id) ON DELETE RESTRICT,
    is_test            BOOLEAN      NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_words_lemma CHECK (lemma = lower(lemma) AND char_length(lemma) BETWEEN 2 AND 60
                                          AND (is_test OR lemma ~ '^[a-z][a-z'' -]*[a-z]$')),
    CONSTRAINT arsenal_words_settled CHECK ((verification = 'pending') = (verified_at IS NULL)),
    CONSTRAINT arsenal_words_dropped_checked CHECK (status <> 'dropped' OR verification IN ('verified', 'corrected')),
    CONSTRAINT arsenal_words_checked_source CHECK (verification NOT IN ('verified', 'corrected')
        OR (pronunciation IS NOT NULL AND etymology IS NOT NULL AND etymology_url IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS arsenal_words_pending ON arsenal_words (created_at) WHERE verification = 'pending';

-- ===================================================================== 4
-- The lifecycle guards for refs and words.

CREATE OR REPLACE FUNCTION public.arsenal_refs_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.verified_at := CASE WHEN NEW.verification <> 'pending' THEN now() END;
        IF NEW.status NOT IN ('pool', 'seed') THEN
            RAISE EXCEPTION 'arsenal_refs: a reference enters as pool or seed, never %', NEW.status;
        END IF;
        IF NOT NEW.is_test AND NEW.verification <> 'pending' THEN
            RAISE EXCEPTION 'arsenal_refs: a real reference enters pending; verification is its own step';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'arsenal_refs: a reference is never deleted (status rejected retires one)';
    END IF;
    -- UPDATE.
    IF NEW.id <> OLD.id OR NEW.canonical_key <> OLD.canonical_key OR NEW.is_test <> OLD.is_test
       OR NEW.origin_kind <> OLD.origin_kind OR NEW.created_at <> OLD.created_at
       OR NEW.pool_run_id IS DISTINCT FROM OLD.pool_run_id OR NEW.source_title_id IS DISTINCT FROM OLD.source_title_id THEN
        RAISE EXCEPTION 'arsenal_refs: a reference''s identity never changes';
    END IF;
    -- The embedding is set once (compared as text: no pgvector operator here).
    IF OLD.embedding IS NOT NULL AND NEW.embedding::text IS DISTINCT FROM OLD.embedding::text THEN
        RAISE EXCEPTION 'arsenal_refs: the embedding is set once';
    END IF;
    IF (NEW.category, NEW.title, NEW.origin, NEW.meaning, NEW.deploy_line, NEW.room, NEW.source_work, NEW.quote,
        NEW.quote_by, NEW.source_kind, NEW.source_page, NEW.source_url)
       IS DISTINCT FROM
       (OLD.category, OLD.title, OLD.origin, OLD.meaning, OLD.deploy_line, OLD.room, OLD.source_work, OLD.quote,
        OLD.quote_by, OLD.source_kind, OLD.source_page, OLD.source_url)
       AND OLD.verification <> 'pending' THEN
        RAISE EXCEPTION 'arsenal_refs: a reference''s card changes only while it is pending verification';
    END IF;
    IF OLD.verification <> 'pending' AND (NEW.verification, NEW.verification_note, NEW.verified_at)
                                          IS DISTINCT FROM (OLD.verification, OLD.verification_note, OLD.verified_at) THEN
        RAISE EXCEPTION 'arsenal_refs: verification settles once';
    END IF;
    IF OLD.verification = 'pending' AND NEW.verification <> 'pending' THEN
        NEW.verified_at := now();
    END IF;
    IF NEW.status <> OLD.status THEN
        IF OLD.status <> 'pool' THEN
            RAISE EXCEPTION 'arsenal_refs: a % reference never changes status', OLD.status;
        END IF;
        IF NEW.status = 'dropped'
           AND NOT EXISTS (SELECT 1 FROM public.arsenal_drops d WHERE NEW.id = ANY (d.ref_ids)) THEN
            RAISE EXCEPTION 'arsenal_refs: only a drop marks a reference dropped';
        END IF;
        IF NEW.status NOT IN ('dropped', 'rejected') THEN
            RAISE EXCEPTION 'arsenal_refs: a pool reference becomes dropped or rejected, never %', NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_refs_guard ON arsenal_refs;
CREATE TRIGGER arsenal_refs_guard
    BEFORE INSERT OR UPDATE OR DELETE ON arsenal_refs
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_refs_guard();
DROP TRIGGER IF EXISTS arsenal_refs_no_truncate ON arsenal_refs;
CREATE TRIGGER arsenal_refs_no_truncate
    BEFORE TRUNCATE ON arsenal_refs
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

CREATE OR REPLACE FUNCTION public.arsenal_words_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.verified_at := CASE WHEN NEW.verification <> 'pending' THEN now() END;
        IF NEW.status <> 'pool' THEN
            RAISE EXCEPTION 'arsenal_words: a word enters as pool, never %', NEW.status;
        END IF;
        IF NOT NEW.is_test AND NEW.verification <> 'pending' THEN
            RAISE EXCEPTION 'arsenal_words: a real word enters pending; verification is its own step';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'arsenal_words: a word is never deleted (status rejected retires one)';
    END IF;
    IF NEW.id <> OLD.id OR NEW.lemma <> OLD.lemma OR NEW.is_test <> OLD.is_test OR NEW.created_at <> OLD.created_at
       OR NEW.pool_run_id IS DISTINCT FROM OLD.pool_run_id THEN
        RAISE EXCEPTION 'arsenal_words: a word''s identity never changes';
    END IF;
    IF (NEW.pronunciation, NEW.etymology, NEW.etymology_url, NEW.meaning, NEW.room, NEW.sentence)
       IS DISTINCT FROM (OLD.pronunciation, OLD.etymology, OLD.etymology_url, OLD.meaning, OLD.room, OLD.sentence)
       AND OLD.verification <> 'pending' THEN
        RAISE EXCEPTION 'arsenal_words: a word''s card changes only while it is pending verification';
    END IF;
    IF OLD.verification <> 'pending' AND (NEW.verification, NEW.verification_note, NEW.verified_at)
                                          IS DISTINCT FROM (OLD.verification, OLD.verification_note, OLD.verified_at) THEN
        RAISE EXCEPTION 'arsenal_words: verification settles once';
    END IF;
    IF OLD.verification = 'pending' AND NEW.verification <> 'pending' THEN
        NEW.verified_at := now();
    END IF;
    IF NEW.status <> OLD.status THEN
        IF OLD.status <> 'pool' THEN
            RAISE EXCEPTION 'arsenal_words: a % word never changes status', OLD.status;
        END IF;
        IF NEW.status = 'dropped' AND NOT EXISTS (SELECT 1 FROM public.arsenal_drops d WHERE d.word_id = NEW.id) THEN
            RAISE EXCEPTION 'arsenal_words: only a drop marks a word dropped';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_words_guard ON arsenal_words;
CREATE TRIGGER arsenal_words_guard
    BEFORE INSERT OR UPDATE OR DELETE ON arsenal_words
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_words_guard();
DROP TRIGGER IF EXISTS arsenal_words_no_truncate ON arsenal_words;
CREATE TRIGGER arsenal_words_no_truncate
    BEFORE TRUNCATE ON arsenal_words
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 5
-- arsenal_drops: append-only.

CREATE TABLE IF NOT EXISTS arsenal_drops (
    id          BIGSERIAL    PRIMARY KEY,
    -- Stamped by the guard from app_settings.
    owner_id    UUID         NOT NULL REFERENCES auth.users (id) ON DELETE RESTRICT,
    drop_date   DATE         NOT NULL,
    -- Slot order: the order the page shows them in.
    ref_ids     BIGINT[]     NOT NULL CHECK (cardinality(ref_ids) = 3),
    word_id     BIGINT       NOT NULL REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    -- The pool was short and one synchronous top-up ran before the pick.
    topped_up   BOOLEAN      NOT NULL DEFAULT false,
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_drops_one_a_day ON arsenal_drops (owner_id, drop_date) WHERE NOT is_test;
CREATE INDEX IF NOT EXISTS arsenal_drops_refs ON arsenal_drops USING gin (ref_ids);
CREATE INDEX IF NOT EXISTS arsenal_drops_date ON arsenal_drops (drop_date DESC, id DESC);

CREATE OR REPLACE FUNCTION public.arsenal_drops_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_n     int;
    v_cats  int;
BEGIN
    NEW.created_at := now();
    NEW.owner_id := (SELECT s.owner_id FROM public.app_settings s LIMIT 1);
    IF NEW.owner_id IS NULL THEN
        RAISE EXCEPTION 'arsenal_drops: app_settings has no owner';
    END IF;
    IF NEW.ref_ids IS NULL OR cardinality(NEW.ref_ids) <> 3 OR array_position(NEW.ref_ids, NULL) IS NOT NULL
       OR (SELECT count(DISTINCT x) FROM unnest(NEW.ref_ids) AS x) <> 3 THEN
        RAISE EXCEPTION 'arsenal_drops: a drop holds exactly three distinct references';
    END IF;
    SELECT count(*), count(DISTINCT r.category) INTO v_n, v_cats
      FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids);
    IF v_n <> 3 THEN
        RAISE EXCEPTION 'arsenal_drops: an unknown reference';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids) AND r.status = 'seed') THEN
        RAISE EXCEPTION 'arsenal_drops: a seed never drops';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_drops d WHERE d.ref_ids && NEW.ref_ids)
       OR EXISTS (SELECT 1 FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids) AND r.status = 'dropped') THEN
        RAISE EXCEPTION 'arsenal_drops: a reference already dropped never drops again';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids) AND r.status <> 'pool') THEN
        RAISE EXCEPTION 'arsenal_drops: only a pool reference drops';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r
                WHERE r.id = ANY (NEW.ref_ids) AND r.verification NOT IN ('verified', 'corrected')) THEN
        RAISE EXCEPTION 'arsenal_drops: only a verified or corrected reference drops';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids) AND r.is_test <> NEW.is_test) THEN
        RAISE EXCEPTION 'arsenal_drops: a real drop holds real rows only, a test drop test rows only';
    END IF;
    IF v_cats <> 3 THEN
        RAISE EXCEPTION 'arsenal_drops: three references in three different categories';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_drops d WHERE d.word_id = NEW.word_id) THEN
        RAISE EXCEPTION 'arsenal_drops: a word already dropped never drops again';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.arsenal_words w
                    WHERE w.id = NEW.word_id AND w.status = 'pool' AND w.verification IN ('verified', 'corrected')
                      AND w.is_test = NEW.is_test) THEN
        RAISE EXCEPTION 'arsenal_drops: the word must be a verified pool word of the same kind (real or test)';
    END IF;
    RETURN NEW;
END;
$$;

-- After the row exists: its references and word are dropped for good.
CREATE OR REPLACE FUNCTION public.arsenal_drops_mark()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    UPDATE public.arsenal_refs SET status = 'dropped' WHERE id = ANY (NEW.ref_ids);
    UPDATE public.arsenal_words SET status = 'dropped' WHERE id = NEW.word_id;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_drops_guard ON arsenal_drops;
CREATE TRIGGER arsenal_drops_guard
    BEFORE INSERT ON arsenal_drops
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_drops_guard();
DROP TRIGGER IF EXISTS arsenal_drops_mark ON arsenal_drops;
CREATE TRIGGER arsenal_drops_mark
    AFTER INSERT ON arsenal_drops
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_drops_mark();
DROP TRIGGER IF EXISTS arsenal_drops_immutable ON arsenal_drops;
CREATE TRIGGER arsenal_drops_immutable
    BEFORE UPDATE OR DELETE ON arsenal_drops
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_drops_no_truncate ON arsenal_drops;
CREATE TRIGGER arsenal_drops_no_truncate
    BEFORE TRUNCATE ON arsenal_drops
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 6
-- arsenal_reactions: append-only. The latest row per target counts.

CREATE TABLE IF NOT EXISTS arsenal_reactions (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    ref_id      BIGINT       REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    word_id     BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    -- 'none' takes a reaction back (an un-tap).
    reaction    TEXT         NOT NULL CHECK (reaction IN ('love', 'not_interested', 'known', 'none')),
    -- The target's, set by trigger: a test reaction never trains the real weights.
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_reactions_one_target CHECK (num_nonnulls(ref_id, word_id) = 1)
);
CREATE INDEX IF NOT EXISTS arsenal_reactions_ref ON arsenal_reactions (ref_id, created_at DESC, id DESC) WHERE ref_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS arsenal_reactions_word ON arsenal_reactions (word_id, created_at DESC, id DESC) WHERE word_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.arsenal_reactions_stamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    NEW.is_test := coalesce((SELECT r.is_test FROM public.arsenal_refs r WHERE r.id = NEW.ref_id),
                            (SELECT w.is_test FROM public.arsenal_words w WHERE w.id = NEW.word_id), false);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_reactions_stamp ON arsenal_reactions;
CREATE TRIGGER arsenal_reactions_stamp
    BEFORE INSERT ON arsenal_reactions
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_reactions_stamp();
DROP TRIGGER IF EXISTS arsenal_reactions_immutable ON arsenal_reactions;
CREATE TRIGGER arsenal_reactions_immutable
    BEFORE UPDATE OR DELETE ON arsenal_reactions
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_reactions_no_truncate ON arsenal_reactions;
CREATE TRIGGER arsenal_reactions_no_truncate
    BEFORE TRUNCATE ON arsenal_reactions
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 7
-- Near-duplicates and weights.

-- True when a real reference (a seed, a dropped one, or a pool one not
-- retired as unverified or rejected) sits within p_threshold cosine of the
-- candidate. The candidate's vector is a parameter; nothing is read back.
CREATE OR REPLACE FUNCTION public.arsenal_near_dup(p_embedding extensions.halfvec, p_threshold real DEFAULT 0.92)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.arsenal_refs r
         WHERE NOT r.is_test
           AND r.embedding IS NOT NULL
           AND r.status <> 'rejected'
           AND NOT (r.status = 'pool' AND r.verification = 'unverified')
           AND 1 - extensions.cosine_distance(r.embedding, p_embedding) >= p_threshold);
$$;

-- Category and room weights: base x clamp(1 + 0.15 loves - 0.15 not
-- interested, 0.3, 2.0), from the latest REAL reaction per target. 'known'
-- and 'none' count nothing. Words feed room weights only.
CREATE OR REPLACE FUNCTION public.arsenal_weights()
RETURNS TABLE (dim text, key text, base real, loves integer, not_interested integer, weight real)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH latest AS (
        SELECT DISTINCT ON (r.ref_id, r.word_id) r.ref_id, r.word_id, r.reaction
          FROM public.arsenal_reactions r
         WHERE NOT r.is_test
         ORDER BY r.ref_id, r.word_id, r.created_at DESC, r.id DESC
    ),
    tallies AS (
        SELECT 'category'::text AS dim, f.category AS key, l.reaction
          FROM latest l JOIN public.arsenal_refs f ON f.id = l.ref_id
        UNION ALL
        SELECT 'room', f.room, l.reaction FROM latest l JOIN public.arsenal_refs f ON f.id = l.ref_id
        UNION ALL
        SELECT 'room', w.room, l.reaction FROM latest l JOIN public.arsenal_words w ON w.id = l.word_id
    ),
    bases (dim, key, base) AS (
        VALUES ('category'::text, 'finance_business'::text, 1.0::real), ('category', 'history_military', 1.0),
               ('category', 'film_tv', 1.0), ('category', 'sports', 1.0), ('category', 'music_culture', 1.0),
               ('category', 'philosophy', 0.6), ('category', 'literature_myth', 0.3),
               ('room', 'boardroom', 1.0), ('room', 'pitch', 1.0), ('room', 'negotiation', 1.0), ('room', 'homies', 1.0)
    )
    SELECT b.dim, b.key, b.base::real,
           (count(*) FILTER (WHERE t.reaction = 'love'))::integer,
           (count(*) FILTER (WHERE t.reaction = 'not_interested'))::integer,
           (b.base * greatest(0.3, least(2.0, 1 + 0.15 * count(*) FILTER (WHERE t.reaction = 'love')
                                               - 0.15 * count(*) FILTER (WHERE t.reaction = 'not_interested'))))::real
      FROM bases b
      LEFT JOIN tallies t ON t.dim = b.dim AND t.key = b.key
     GROUP BY b.dim, b.key, b.base
     ORDER BY b.dim, b.key;
$$;

-- ===================================================================== 8
-- The picker. Returns the drop's id; a date that already has a real drop
-- returns that one. with_tests picks TEST rows only (tests and the e2e
-- helpers), a real pick real rows only. Raises when the verified pool is
-- short (the worker tops up first; drop.py).
CREATE OR REPLACE FUNCTION public.arsenal_pick_drop(p_date date, p_with_tests boolean DEFAULT false,
                                                    p_topped_up boolean DEFAULT false)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_id     bigint;
    v_y      text[];
    v_cats   text[];
    v_ids    bigint[];
    v_word   bigint;
    -- The six orderings of three slots, in preference order.
    v_order  int[] := ARRAY[[1, 2, 3], [1, 3, 2], [2, 1, 3], [2, 3, 1], [3, 1, 2], [3, 2, 1]];
    v_pick   int := 1;
    v_ok     boolean;
BEGIN
    IF p_date IS NULL THEN
        RAISE EXCEPTION 'arsenal_pick_drop: a date is required';
    END IF;
    IF NOT p_with_tests THEN
        SELECT d.id INTO v_id FROM public.arsenal_drops d WHERE d.drop_date = p_date AND NOT d.is_test;
        IF v_id IS NOT NULL THEN
            RETURN v_id;
        END IF;
    END IF;

    -- Yesterday's categories, by slot (the latest drop of the same kind).
    SELECT array_agg(r.category ORDER BY s.ord) INTO v_y
      FROM (SELECT d.ref_ids FROM public.arsenal_drops d
             WHERE d.drop_date = p_date - 1 AND d.is_test = p_with_tests
             ORDER BY d.id DESC LIMIT 1) y
      CROSS JOIN LATERAL unnest(y.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
      JOIN public.arsenal_refs r ON r.id = s.ref_id;

    -- Three categories by weight (one draw each: -ln(u)/w, smallest wins),
    -- then one reference in each by room weight.
    WITH w AS (
        SELECT * FROM public.arsenal_weights()
    ),
    latest AS (
        SELECT DISTINCT ON (x.ref_id) x.ref_id, x.reaction
          FROM public.arsenal_reactions x
         WHERE x.ref_id IS NOT NULL AND x.is_test = p_with_tests
         ORDER BY x.ref_id, x.created_at DESC, x.id DESC
    ),
    elig AS (
        SELECT r.id, r.category, r.room
          FROM public.arsenal_refs r
         WHERE r.is_test = p_with_tests AND r.status = 'pool' AND r.verification IN ('verified', 'corrected')
           AND NOT EXISTS (SELECT 1 FROM latest l WHERE l.ref_id = r.id AND l.reaction IN ('known', 'not_interested'))
    ),
    cats AS (
        SELECT c.category, -ln(1 - random()) / greatest(cw.weight, 0.0001) AS k
          FROM (SELECT DISTINCT e.category FROM elig e) c
          JOIN w cw ON cw.dim = 'category' AND cw.key = c.category
         ORDER BY 2
         LIMIT 3
    ),
    picks AS (
        SELECT DISTINCT ON (e.category) e.category, e.id, c.k
          FROM elig e
          JOIN cats c ON c.category = e.category
          JOIN w rw ON rw.dim = 'room' AND rw.key = e.room
         ORDER BY e.category, -ln(1 - random()) / greatest(rw.weight, 0.0001)
    )
    SELECT array_agg(p.category ORDER BY p.k), array_agg(p.id ORDER BY p.k) INTO v_cats, v_ids FROM picks p;

    IF coalesce(cardinality(v_ids), 0) < 3 THEN
        RAISE EXCEPTION 'arsenal_pick_drop: the verified pool is short (% categories with an eligible reference; 3 needed)',
            coalesce(cardinality(v_ids), 0);
    END IF;

    SELECT wd.id INTO v_word
      FROM public.arsenal_words wd
     WHERE wd.is_test = p_with_tests AND wd.status = 'pool' AND wd.verification IN ('verified', 'corrected')
       AND NOT EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (x.word_id) x.reaction
                                        FROM public.arsenal_reactions x WHERE x.word_id = wd.id
                                       ORDER BY x.word_id, x.created_at DESC, x.id DESC) lw
                        WHERE lw.reaction IN ('known', 'not_interested'))
     ORDER BY random()
     LIMIT 1;
    IF v_word IS NULL THEN
        RAISE EXCEPTION 'arsenal_pick_drop: the verified pool is short (no eligible word)';
    END IF;

    -- The first ordering in which no slot repeats yesterday's category in
    -- that slot. With three distinct categories one always exists.
    FOR p IN 1..6 LOOP
        v_ok := true;
        FOR i IN 1..3 LOOP
            IF v_y IS NOT NULL AND cardinality(v_y) >= i AND v_cats[v_order[p][i]] = v_y[i] THEN
                v_ok := false;
            END IF;
        END LOOP;
        IF v_ok THEN
            v_pick := p;
            EXIT;
        END IF;
    END LOOP;

    BEGIN
        INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, topped_up, is_test)
        VALUES (p_date, ARRAY[v_ids[v_order[v_pick][1]], v_ids[v_order[v_pick][2]], v_ids[v_order[v_pick][3]]],
                v_word, p_topped_up, p_with_tests)
        RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        -- Another run made today's real drop first: that one stands.
        SELECT d.id INTO v_id FROM public.arsenal_drops d WHERE d.drop_date = p_date AND NOT d.is_test;
    END;
    RETURN v_id;
END;
$$;

-- ===================================================================== 9
-- The owner's canon (Chat 5 planning, Oct 15): status 'seed', taste only,
-- never a drop. Each in its accurate form; verified by the same pipeline
-- (pending until the worker's first verification pass).

INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work, quote,
                          quote_by, source_kind, source_page, status, origin_kind)
VALUES
    ('an_offer_he_cant_refuse', 'film_tv', 'An offer he can''t refuse',
     'Vito Corleone to Johnny Fontane, about a studio boss who won''t cast him, in The Godfather (1972). The offer arrives as a horse''s head.',
     'A proposal built so that saying no costs more than saying yes.',
     'Let''s not ask for the deal. Let''s structure one they can''t refuse.',
     'negotiation', 'The Godfather (1972)', 'I''m gonna make him an offer he can''t refuse.', 'Vito Corleone',
     'wikiquote', 'The Godfather'),
    ('never_tell_anybody_outside_the_family', 'film_tv', 'Never tell anybody outside the family',
     'Vito Corleone dresses down Sonny after Sonny shows his hand to Sollozzo at the sit-down, in The Godfather (1972).',
     'Never show your position to the other side, least of all in the room where it is being negotiated.',
     'We talk numbers in here, not in front of them.',
     'negotiation', 'The Godfather (1972)', 'Never tell anybody outside the family what you''re thinking again.',
     'Vito Corleone', 'wikiquote', 'The Godfather'),
    ('you_are_not_serious_people', 'film_tv', 'You are not serious people',
     'Logan Roy''s verdict on his children as would-be successors, in HBO''s Succession.',
     'The room is full of noise and nobody is willing to own a decision.',
     'I love everyone here, but right now we are not serious people.',
     'boardroom', 'Succession (HBO, 2018–2023)', 'I love you, but you are not serious people.', 'Logan Roy',
     'wikiquote', 'Succession (TV series)'),
    ('axe_and_wags', 'film_tv', 'Axe and Wags',
     'Billions (Showtime, 2016–2023): Bobby Axelrod runs Axe Capital, and Mike "Wags" Wagner, his COO, is the consigliere who says what Axe can''t.',
     'Every principal needs one person who tells them the truth and handles the rest.',
     'Every Axe needs a Wags. On this deal, I''m yours.',
     'boardroom', 'Billions (Showtime, 2016–2023)', NULL, NULL, 'wikipedia', 'Billions (TV series)'),
    ('marty_byrde_composure', 'film_tv', 'Marty Byrde''s composure',
     'Ozark (Netflix, 2017–2022): with a cartel gun on him, Marty Byrde talks his way out by pitching a bigger laundering plan in the Ozarks.',
     'Under maximum pressure, the calmest person with a plan owns the room.',
     'Nobody panics. We walk in with the plan, not the problem.',
     'pitch', 'Ozark (Netflix, 2017–2022)', NULL, NULL, 'wikipedia', 'Ozark (TV series)'),
    ('neil_mccauley_30_second_rule', 'film_tv', 'Neil McCauley''s 30-second rule',
     'Heat (1995): De Niro''s thief lives by one rule: get attached to nothing you can''t walk out on in 30 seconds flat when the heat is around the corner.',
     'Hold every position loosely enough to walk away the moment the heat is on.',
     'I like this deal, but I''m not attached to anything I can''t walk away from in thirty seconds.',
     'negotiation', 'Heat (1995)', NULL, NULL, 'wikiquote', 'Heat (1995 film)'),
    ('be_first_be_smarter_or_cheat', 'film_tv', 'Be first, be smarter, or cheat',
     'Margin Call (2011): CEO John Tuld names the only three ways to make a living in the business, the night the firm dumps its toxic book.',
     'If you can''t be smarter, be first, and never be the third option.',
     'We''re not cheating and we''re not smarter than them on this one, so we''d better be first.',
     'boardroom', 'Margin Call (2011)', 'Be first, be smarter, or cheat.', 'John Tuld', 'wikiquote', 'Margin Call'),
    ('michael_clayton_the_fixer', 'film_tv', 'Michael Clayton, the fixer',
     'Michael Clayton (2007): George Clooney plays a law firm''s fixer, the man called in to make problems quietly disappear.',
     'The person who solves the problem nobody wants to name is the most valuable person in the building.',
     'Don''t think of me as the cost. I''m the guy you buy so this goes away.',
     'negotiation', 'Michael Clayton (2007)', 'I''m not the guy you kill. I''m the guy you buy.', 'Michael Clayton',
     'wikiquote', 'Michael Clayton (film)'),
    ('crossing_the_rubicon', 'history_military', 'Crossing the Rubicon',
     'Julius Caesar led a legion across the Rubicon into Italy in 49 BC: an act of war against Rome, with no way back.',
     'A decision past the point of no return.',
     'Once we sign this lease we''ve crossed the Rubicon, so let''s be sure before we do.',
     'boardroom', 'Julius Caesar, 49 BC', 'The die is cast.', 'Julius Caesar (per Suetonius)', 'wikipedia',
     'Crossing the Rubicon'),
    ('scuttling_the_ships', 'history_military', 'Scuttling the ships',
     'Hernán Cortés scuttled his own ships at Veracruz in 1519 so his men could not turn back. He sank them; the burning is a later embellishment.',
     'Remove the retreat so the only way is forward.',
     'We''re scuttling the ships on this one. No plan B, so plan A has to work.',
     'pitch', 'Hernán Cortés, Veracruz, 1519', NULL, NULL, 'wikipedia', 'Hernán Cortés'),
    ('pyrrhic_victory', 'history_military', 'Pyrrhic victory',
     'Pyrrhus of Epirus beat Rome at Heraclea and Asculum (280–279 BC) but lost so many men that another such win would have ruined him.',
     'A win that costs more than it is worth.',
     'We can win this fight with the landlord, but it''s a Pyrrhic victory if it eats the opening budget.',
     'boardroom', 'Pyrrhus of Epirus, 279 BC', NULL, NULL, 'wikipedia', 'Pyrrhic victory'),
    ('hannibal_crosses_the_alps', 'history_military', 'Hannibal crosses the Alps',
     'In 218 BC Hannibal marched an army, war elephants included, over the Alps to strike Rome from the side nobody was guarding.',
     'Take the route everyone thinks is impossible, because nobody is defending it.',
     'Everyone''s fighting for the front door. We''re going over the Alps.',
     'pitch', 'Hannibal, Second Punic War, 218 BC', NULL, NULL, 'wikipedia', 'Hannibal''s crossing of the Alps'),
    ('napoleons_speed', 'history_military', 'Napoleon''s speed',
     'In the Ulm campaign of 1805 Napoleon moved his army faster than Austria thought possible and surrounded it before it was ready to fight.',
     'Speed of execution is a strategy, not a detail.',
     'By the time they schedule the follow-up, I want the term sheet on their desk.',
     'negotiation', 'Napoleon, Ulm campaign, 1805', NULL, NULL, 'wikipedia', 'Ulm campaign'),
    ('sun_tzu_win_first', 'philosophy', 'Win first, then go to war',
     'Sun Tzu''s Art of War: victorious warriors win first and then go to war. "Every battle is won before it is fought" is a later paraphrase.',
     'The outcome is decided in the preparation, not in the meeting.',
     'I don''t walk into that room to win. I walk in having already won.',
     'pitch', 'Sun Tzu, The Art of War', 'Victorious warriors win first and then go to war', 'Sun Tzu',
     'wikiquote', 'Sun Tzu'),
    ('machiavelli_feared_than_loved', 'philosophy', 'Safer to be feared than loved',
     'Machiavelli''s The Prince (1532) argues that a ruler forced to choose is safer feared than loved.',
     'Respect that holds under pressure beats affection that doesn''t.',
     'I''m not here to be liked in this negotiation. I''m here to be taken seriously.',
     'negotiation', 'Machiavelli, The Prince (1532)', 'It is much safer to be feared than loved', 'Niccolò Machiavelli',
     'wikiquote', 'Niccolò Machiavelli'),
    ('the_48_laws_of_power', 'philosophy', 'The 48 Laws of Power',
     'Robert Greene''s 1998 book distills power from history into 48 laws. Law 1: never outshine the master.',
     'Know the unwritten rules of a room before you try to move it.',
     'Law one: never outshine the master. Let the investor think the idea was theirs.',
     'pitch', 'Robert Greene, The 48 Laws of Power (1998)', 'Never outshine the master.', 'Robert Greene',
     'wikipedia', 'The 48 Laws of Power'),
    ('marcus_aurelius_the_obstacle', 'philosophy', 'The obstacle is the way',
     'Marcus Aurelius, the Stoic emperor, wrote in his Meditations that what stands in the way of action can become the way forward.',
     'The problem in front of you is the material you work with.',
     'The permit delay isn''t the obstacle, it''s the edge. Nobody else will wait it out.',
     'boardroom', 'Marcus Aurelius, Meditations (c. 170–180 AD)', NULL, NULL, 'wikipedia', 'Meditations'),
    ('the_medici', 'finance_business', 'The Medici',
     'The Medici banking family ran fifteenth-century Florence through money, the papal accounts and patronage rather than titles.',
     'Real power sits in the balance sheet and the relationships, not the title.',
     'I don''t need the title. I''d rather be the Medici of this deal.',
     'boardroom', 'The Medici, Florence, 15th century', NULL, NULL, 'wikipedia', 'House of Medici'),
    ('rockefeller_standard_oil', 'finance_business', 'Rockefeller''s Standard Oil',
     'John D. Rockefeller founded Standard Oil in 1870 and bought out rival refiners until he controlled most of American refining.',
     'Own the chokepoint and the market comes to you.',
     'We don''t need every venue. We need the booking calendar they all depend on.',
     'boardroom', 'John D. Rockefeller, Standard Oil (1870)', NULL, NULL, 'wikipedia', 'Standard Oil'),
    ('buffett_greedy_when_others_are_fearful', 'finance_business', 'Greedy when others are fearful',
     'Warren Buffett''s rule for when to buy, from his 1986 Berkshire Hathaway shareholder letter.',
     'Lean in when the crowd is scared; pull back when it''s euphoric.',
     'Everyone''s scared of hospitality right now, which is exactly why we''re greedy.',
     'pitch', 'Buffett, 1986 Berkshire Hathaway letter',
     'be fearful when others are greedy and greedy when others are fearful', 'Warren Buffett', 'wikiquote',
     'Warren Buffett'),
    ('sam_zell_the_grave_dancer', 'finance_business', 'The Grave Dancer',
     'Sam Zell took the nickname from his 1976 article on buying distressed real estate when everyone else was selling.',
     'The money is made buying what everyone else is desperate to get rid of.',
     'Call me a grave dancer, but distressed leases are where the margin is.',
     'pitch', 'Sam Zell, "The Grave Dancer" (1976)', NULL, NULL, 'wikipedia', 'Sam Zell'),
    ('jimmy_iovine_beats', 'music_culture', 'Jimmy Iovine and Beats',
     'Record producer and Interscope co-founder Jimmy Iovine launched Beats with Dr. Dre in 2008 and sold it to Apple in 2014 for $3 billion.',
     'Taste plus distribution beats a better product with no audience.',
     'Iovine didn''t make the best headphones, he made the ones culture wanted. That''s our play.',
     'pitch', 'Jimmy Iovine, Beats Electronics (2008)', NULL, NULL, 'wikipedia', 'Jimmy Iovine'),
    ('saban_the_process', 'sports', 'Saban''s Process',
     'Nick Saban built Alabama''s dynasty on "the Process": execute the next play, not the scoreboard.',
     'Outcomes follow from doing the next small thing right, every time.',
     'I''m not watching the scoreboard on this launch. I''m running the process.',
     'boardroom', 'Nick Saban, Alabama football', NULL, NULL, 'wikipedia', 'Nick Saban'),
    ('jordan_took_it_personally', 'sports', 'And I took that personally',
     'ESPN''s The Last Dance (2020) shows Michael Jordan turning every slight, real or invented, into fuel.',
     'Use doubt as fuel, and say so with a smile.',
     'They said Miami was saturated. And I took that personally.',
     'homies', 'The Last Dance (ESPN, 2020)', 'And I took that personally.', 'Michael Jordan', 'wikiquote',
     'Michael Jordan'),
    ('kobe_mamba_mentality', 'sports', 'Mamba Mentality',
     'Kobe Bryant''s name for his obsessive work ethic, and the title of his 2018 book.',
     'Out-prepare everyone, then do it again tomorrow.',
     'Mamba mentality on this deck: we rehearse until it''s boring.',
     'homies', 'Kobe Bryant, The Mamba Mentality (2018)', NULL, NULL, 'wikipedia', 'Kobe Bryant'),
    ('lebron_long_game', 'sports', 'LeBron''s long game',
     'LeBron James took equity, not just endorsements: a Fenway Sports Group stake from 2011 and his own media company, on the way to becoming the first active NBA billionaire in 2022.',
     'Build ownership while the paycheck is still coming in.',
     'I''d rather take the LeBron route: less fee, more equity.',
     'negotiation', 'LeBron James, Fenway Sports Group (2011)', NULL, NULL, 'wikipedia', 'LeBron James'),
    ('hamilton_comeback_seasons', 'sports', 'Hamilton''s comeback seasons',
     'Lewis Hamilton lost the 2016 Formula One title to teammate Nico Rosberg by five points, then won the next four championships (2017–2020).',
     'Losing the title is a data point. The response is the record.',
     'We lost that pitch. Watch the next four.',
     'homies', 'Lewis Hamilton, Formula One, 2017–2020', NULL, NULL, 'wikipedia', 'Lewis Hamilton')
ON CONFLICT (canonical_key) DO NOTHING;

-- ===================================================================== 10
-- Test rows (e2e). Owner only, the verified e2e marker (db/051 e2e_marker_ok).

-- A TEST reference, already settled (verified, corrected or unverified,
-- with the note). Key e2e_<random>; source on a .invalid host.
CREATE OR REPLACE FUNCTION public.add_test_arsenal_ref(p_marker text, p_category text, p_title text, p_room text,
                                                       p_verification text DEFAULT 'verified',
                                                       p_note text DEFAULT NULL, p_quote text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_key text := 'e2e_' || substr(md5(random()::text || clock_timestamp()::text), 1, 16);
    v_id  bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_arsenal_ref: the owner and a verified test marker only';
    END IF;
    IF p_verification NOT IN ('verified', 'corrected', 'unverified') THEN
        RAISE EXCEPTION 'add_test_arsenal_ref: a test reference is verified, corrected or unverified';
    END IF;
    INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                     quote, quote_by, source_kind, source_page, source_url, status, verification,
                                     verification_note, origin_kind, is_test)
    VALUES (v_key, p_category, p_title, 'Where ' || p_title || ' comes from, in two short lines.',
            'What it means when you use it.', 'The line you would actually say in the room.', p_room,
            'Test work (2026)', p_quote, CASE WHEN p_quote IS NOT NULL THEN 'A test speaker' END, 'wikipedia',
            'Test page', 'https://e2e.invalid/arsenal/' || v_key, 'pool', p_verification, p_note, 'pool', true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- A TEST Power Word, settled.
CREATE OR REPLACE FUNCTION public.add_test_arsenal_word(p_marker text, p_lemma text,
                                                        p_verification text DEFAULT 'verified')
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_arsenal_word: the owner and a verified test marker only';
    END IF;
    IF p_verification NOT IN ('verified', 'corrected', 'unverified') THEN
        RAISE EXCEPTION 'add_test_arsenal_word: a test word is verified, corrected or unverified';
    END IF;
    INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                      verification, is_test)
    VALUES (lower(p_lemma), '/ˈtɛst/', 'From the test suite, by way of the e2e marker.',
            'https://e2e.invalid/arsenal/word', 'What the test word means.', 'boardroom',
            'A sentence you would actually say with the test word.', p_verification, true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- A TEST drop of the given test references (slot order kept) and word.
-- The drop guard holds it to every rule a real drop keeps.
CREATE OR REPLACE FUNCTION public.add_test_arsenal_drop(p_marker text, p_ref_ids bigint[], p_word_id bigint,
                                                        p_date date DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_arsenal_drop: the owner and a verified test marker only';
    END IF;
    INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
    VALUES (coalesce(p_date, public.owner_today()), p_ref_ids, p_word_id, true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 11
-- Context: Wags and the brief. Real rows only.

CREATE OR REPLACE FUNCTION public.ctx_arsenal()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH drops AS (
        SELECT d.id, d.drop_date, d.ref_ids, d.word_id
          FROM public.arsenal_drops d
         WHERE NOT d.is_test AND d.drop_date BETWEEN public.owner_today() - 7 AND public.owner_today()
    ),
    cards AS (
        SELECT d.id AS drop_id, s.ord, r.title, r.category, r.room, r.source_work, r.meaning, r.deploy_line
          FROM drops d
          CROSS JOIN LATERAL unnest(d.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
          JOIN public.arsenal_refs r ON r.id = s.ref_id
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'The Arsenal: a daily drop of three references (each with where it comes from, what it means, '
                 'a line to deploy and the room it plays in: boardroom, pitch, negotiation or homies) and a Power '
                 'Word. "today" is today''s drop; "last_7_days" the week before; "weights" how the owner''s '
                 'reactions tilt categories and rooms (1.0 is neutral).',
        'today', (SELECT jsonb_build_object(
                     'date', d.drop_date,
                     'refs', (SELECT jsonb_agg(jsonb_build_object(
                                  'title', c.title, 'category', c.category, 'room', c.room,
                                  'source_work', c.source_work, 'meaning', c.meaning, 'deploy_line', c.deploy_line)
                                  ORDER BY c.ord) FROM cards c WHERE c.drop_id = d.id),
                     'power_word', (SELECT jsonb_build_object('word', w.lemma, 'meaning', w.meaning, 'room', w.room,
                                                              'sentence', w.sentence)
                                      FROM public.arsenal_words w WHERE w.id = d.word_id))
                    FROM drops d WHERE d.drop_date = public.owner_today()),
        'last_7_days', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                     'date', d.drop_date,
                                     'refs', (SELECT jsonb_agg(c.title ORDER BY c.ord) FROM cards c WHERE c.drop_id = d.id),
                                     'power_word', (SELECT w.lemma FROM public.arsenal_words w WHERE w.id = d.word_id))
                                     ORDER BY d.drop_date DESC)
                                   FROM drops d WHERE d.drop_date < public.owner_today()), '[]'::jsonb),
        'weights', (SELECT jsonb_object_agg(x.dim || ':' || x.key, round(x.weight::numeric, 2))
                      FROM public.arsenal_weights() x)
    ));
$$;

-- The brief's one line: today's Power Word and the strongest of today's
-- three (highest category x room weight, then slot order) with its room.
-- NULL (silent) when today has no drop.
CREATE OR REPLACE FUNCTION public.ctx_arsenal_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH t AS (
        SELECT d.ref_ids, d.word_id FROM public.arsenal_drops d
         WHERE NOT d.is_test AND d.drop_date = public.owner_today()
    ),
    w AS (
        SELECT * FROM public.arsenal_weights()
    ),
    best AS (
        SELECT r.title, r.room, r.category
          FROM t
          CROSS JOIN LATERAL unnest(t.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
          JOIN public.arsenal_refs r ON r.id = s.ref_id
          JOIN w cw ON cw.dim = 'category' AND cw.key = r.category
          JOIN w rw ON rw.dim = 'room' AND rw.key = r.room
         ORDER BY cw.weight * rw.weight DESC, s.ord
         LIMIT 1
    )
    SELECT jsonb_build_object(
               'power_word', wd.lemma, 'word_meaning', wd.meaning,
               'ref', (SELECT jsonb_build_object('title', b.title, 'room', b.room, 'category', b.category) FROM best b))
      FROM t JOIN public.arsenal_words wd ON wd.id = t.word_id;
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('arsenal', 'The Arsenal', 'public.ctx_arsenal()'::regprocedure, false, 60),
       ('arsenal_brief', 'Arsenal', 'public.ctx_arsenal_brief()'::regprocedure, true, 60)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 12
-- Access.

ALTER TABLE arsenal_pool_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_words ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_drops ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_reactions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON arsenal_pool_runs, arsenal_refs, arsenal_words, arsenal_drops, arsenal_reactions
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE arsenal_pool_runs_id_seq, arsenal_refs_id_seq, arsenal_words_id_seq, arsenal_drops_id_seq,
                       arsenal_reactions_id_seq FROM anon, authenticated, PUBLIC;

-- The owner reads everything here and writes only her own reactions. The
-- worker writes the rest (DATABASE_URL, which bypasses RLS).
GRANT SELECT ON arsenal_pool_runs, arsenal_refs, arsenal_words, arsenal_drops, arsenal_reactions TO authenticated;
GRANT INSERT ON arsenal_reactions TO authenticated;
GRANT USAGE ON SEQUENCE arsenal_reactions_id_seq TO authenticated;

-- Worker-written content (no owner_id): the owner reads it.
DROP POLICY IF EXISTS arsenal_pool_runs_owner_read ON arsenal_pool_runs;
CREATE POLICY arsenal_pool_runs_owner_read ON arsenal_pool_runs FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS arsenal_refs_owner_read ON arsenal_refs;
CREATE POLICY arsenal_refs_owner_read ON arsenal_refs FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS arsenal_words_owner_read ON arsenal_words;
CREATE POLICY arsenal_words_owner_read ON arsenal_words FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
-- The owner's rows.
DROP POLICY IF EXISTS arsenal_drops_owner_read ON arsenal_drops;
CREATE POLICY arsenal_drops_owner_read ON arsenal_drops FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_reactions_owner_read ON arsenal_reactions;
CREATE POLICY arsenal_reactions_owner_read ON arsenal_reactions FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_reactions_owner_insert ON arsenal_reactions;
CREATE POLICY arsenal_reactions_owner_insert ON arsenal_reactions FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));

GRANT SELECT ON arsenal_pool_runs, arsenal_refs, arsenal_words, arsenal_drops, arsenal_reactions TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arsenal_stamp_created() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_refs_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_words_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_drops_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_drops_mark() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_reactions_stamp() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_near_dup(extensions.halfvec, real) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_weights() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.arsenal_pick_drop(date, boolean, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_test_arsenal_ref(text, text, text, text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_arsenal_word(text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_arsenal_drop(text, bigint[], bigint, date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_arsenal() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_arsenal_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.arsenal_near_dup(extensions.halfvec, real) TO valemont_readonly;
GRANT EXECUTE ON FUNCTION public.arsenal_weights() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_arsenal_ref(text, text, text, text, text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_arsenal_word(text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_arsenal_drop(text, bigint[], bigint, date) TO authenticated;
GRANT EXECUTE ON FUNCTION public.ctx_arsenal() TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.ctx_arsenal_brief() TO authenticated, valemont_readonly;

-- ===================================================================== 13
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with the expected
-- message or SQLSTATE. Picks run on TEST rows (p_with_tests), so real rows
-- never take part; the real-row checks use dates in 2000.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_vec_a  text := '[' || array_to_string(array_fill(0.0::real, ARRAY[383]), ',') || ',1]';
    v_vec_b  text := '[1,' || array_to_string(array_fill(0.0::real, ARRAY[383]), ',') || ']';
    v_ids    bigint[] := '{}';
    v_t      bigint[] := '{}';
    v_id     bigint;
    v_r      bigint;
    v_w      bigint;
    v_w2     bigint;
    v_drop   bigint;
    v_cats   text[] := ARRAY['finance_business', 'history_military', 'film_tv', 'sports'];
    v_y      text[];
    v_got    text[];
    v_ok     boolean;
    v_n      int;
    v_before real;
    v_after  real;
    v_ctx    jsonb;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the seeds
        IF (SELECT count(*) FROM public.arsenal_refs WHERE status = 'seed' AND origin_kind = 'seed' AND NOT is_test) <> 27
           OR NOT EXISTS (SELECT 1 FROM public.arsenal_refs WHERE canonical_key = 'scuttling_the_ships'
                            AND origin LIKE '%scuttled%' AND origin NOT LIKE '%burned his%')
           OR (SELECT quote FROM public.arsenal_refs WHERE canonical_key = 'never_tell_anybody_outside_the_family')
              <> 'Never tell anybody outside the family what you''re thinking again.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the 27 seeds in their accurate forms';
        END IF;

        -- ---------------------------------------------------- 2. refs: insert rules and CHECKs
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, quote, source_kind, status, origin_kind)
            VALUES ('selftest_054_long_quote', 'film_tv', 'x title', 'An origin long enough.', 'A meaning.', 'A line.',
                    'pitch', 'A work', 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen',
                    'wikiquote', 'pool', 'pool');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a 16-word quote was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification, origin_kind)
            VALUES ('selftest_054_preverified', 'sports', 'x title', 'An origin long enough.', 'A meaning.', 'A line.',
                    'pitch', 'A work', 'wikipedia', 'https://selftest.invalid/x', 'pool', 'verified', 'pool');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%enters pending%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a real reference entered already verified'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, status, origin_kind)
            VALUES ('selftest_054_bad_cat', 'cooking', 'x title', 'An origin long enough.', 'A meaning.', 'A line.',
                    'pitch', 'A work', 'wikipedia', 'pool', 'pool');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an unknown category was accepted'; END IF;

        -- ---------------------------------------------------- 3. the lifecycle guard
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                         source_work, quote, quote_by, source_kind, source_page, status, origin_kind,
                                         embedding)
        VALUES ('selftest_054_alpha', 'finance_business', 'Selftest alpha', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'pitch', 'A work (2000)', 'A short quote.', 'Someone', 'wikiquote', 'Alpha',
                'pool', 'pool', v_vec_a::extensions.halfvec)
        RETURNING id INTO v_r;
        IF (SELECT verified_at FROM public.arsenal_refs WHERE id = v_r) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a pending reference has verified_at';
        END IF;
        -- The verifier's correction while pending, then settled.
        UPDATE public.arsenal_refs SET quote = NULL, quote_by = NULL, verification = 'corrected',
                                       verification_note = 'quote not found in the sourced lines',
                                       source_url = 'https://selftest.invalid/alpha'
         WHERE id = v_r;
        IF (SELECT verified_at FROM public.arsenal_refs WHERE id = v_r) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: verified_at was not stamped';
        END IF;
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_refs SET origin = 'A different origin entirely.' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%only while it is pending%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET verification = 'verified' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%settles once%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET embedding = v_vec_b::extensions.halfvec WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%set once%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET status = 'dropped' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%only a drop marks%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            DELETE FROM public.arsenal_refs WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%never deleted%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET status = 'pool' WHERE canonical_key = 'crossing_the_rubicon';
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%seed reference never changes status%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 6 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 6 lifecycle refusals on arsenal_refs', v_n; END IF;

        -- ---------------------------------------------------- 4. near-duplicates (vector in, boolean out)
        IF NOT public.arsenal_near_dup(v_vec_a::extensions.halfvec, 0.92)
           OR public.arsenal_near_dup(v_vec_b::extensions.halfvec, 0.92) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: arsenal_near_dup (the same vector, an orthogonal one)';
        END IF;

        -- ---------------------------------------------------- 5. words
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_words (lemma, meaning, room, sentence) VALUES ('Asymmetric', 'A meaning.', 'pitch', 'A sentence.');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a capitalized lemma was accepted'; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_words (lemma, meaning, room, sentence, verification)
            VALUES ('selftestword', 'A meaning.', 'pitch', 'A sentence.', 'verified');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%enters pending%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a real word entered already verified'; END IF;
        v_ok := false;
        INSERT INTO public.arsenal_words (lemma, meaning, room, sentence) VALUES ('selftestword', 'A meaning.', 'pitch', 'A sentence.')
        RETURNING id INTO v_w;
        BEGIN
            UPDATE public.arsenal_words SET verification = 'verified' WHERE id = v_w;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a word verified with no etymology source'; END IF;

        -- ---------------------------------------------------- 6. drops: the guard (TEST rows)
        -- Eight test refs: two in each of four categories; one unverified; two words.
        FOR v_n IN 1..8 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification, origin_kind,
                                             is_test)
            VALUES ('selftest_054_t' || v_n, v_cats[1 + (v_n - 1) % 4], 'Selftest test ' || v_n, 'An origin long enough.',
                    'A meaning.', 'A line to deploy.', 'boardroom', 'A work', 'wikipedia',
                    'https://selftest.invalid/t' || v_n, 'pool', 'verified', 'pool', true)
            RETURNING id INTO v_id;
            v_t := v_t || v_id;
        END LOOP;
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                         source_work, source_kind, status, verification, verification_note,
                                         origin_kind, is_test)
        VALUES ('selftest_054_unv', 'music_culture', 'Selftest unverified', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'homies', 'A work', 'wikipedia', 'pool', 'unverified', 'contradicted by the source',
                'pool', true)
        RETURNING id INTO v_id;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest word one', '/x/', 'From x.', 'https://selftest.invalid/w1', 'A meaning.', 'pitch', 'A sentence.',
                'verified', true)
        RETURNING id INTO v_w;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest word two', '/x/', 'From x.', 'https://selftest.invalid/w2', 'A meaning.', 'pitch', 'A sentence.',
                'verified', true)
        RETURNING id INTO v_w2;
        v_n := 0;
        BEGIN  -- a seed
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-01', ARRAY[v_t[1], v_t[2], (SELECT id FROM public.arsenal_refs WHERE canonical_key = 'pyrrhic_victory')], v_w, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%a seed never drops%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- unverified
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-01', ARRAY[v_t[1], v_t[2], v_id], v_w, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%only a verified or corrected%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- two in one category (t1 and t5 are both finance_business)
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-01', ARRAY[v_t[1], v_t[5], v_t[2]], v_w, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%three different categories%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- a real drop with test rows
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-01', ARRAY[v_t[1], v_t[2], v_t[3]], v_w, false);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%real drop holds real rows only%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 drop refusals', v_n; END IF;

        -- Yesterday: categories 1,2,3 in slots 1,2,3. Then today's pick from
        -- what is left (t4..t8 minus nothing: t4 sports, t5..t7 cats 1..3, t8 sports).
        INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
        VALUES ('2000-01-01', ARRAY[v_t[1], v_t[2], v_t[3]], v_w, true) RETURNING id INTO v_drop;
        IF (SELECT count(*) FROM public.arsenal_refs WHERE id = ANY (ARRAY[v_t[1], v_t[2], v_t[3]]) AND status = 'dropped') <> 3
           OR (SELECT status FROM public.arsenal_words WHERE id = v_w) <> 'dropped'
           OR (SELECT owner_id FROM public.arsenal_drops WHERE id = v_drop) IS DISTINCT FROM v_owner THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a drop did not mark its rows dropped (or stamp its owner)';
        END IF;
        v_n := 0;
        BEGIN
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-05', ARRAY[v_t[1], v_t[4], v_t[6]], v_w2, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%already dropped never drops again%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-01-05', ARRAY[v_t[4], v_t[6], v_t[7]], v_w, true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%word already dropped%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET status = 'rejected' WHERE id = v_t[1];
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%dropped reference never changes status%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 repeat refusals', v_n; END IF;

        -- The picker: test rows only, three categories, never yesterday's
        -- category in the same slot. Left: t4 (sports), t5 fin, t6 hist, t7 film, t8 sports.
        v_drop := public.arsenal_pick_drop('2000-01-02', true);
        SELECT array_agg(r.category ORDER BY s.ord) INTO v_got
          FROM public.arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
          JOIN public.arsenal_refs r ON r.id = s.ref_id WHERE d.id = v_drop;
        v_y := v_cats[1:3];
        IF cardinality(v_got) <> 3 OR (SELECT count(DISTINCT x) FROM unnest(v_got) x) <> 3
           OR v_got[1] = v_y[1] OR v_got[2] = v_y[2] OR v_got[3] = v_y[3]
           OR (SELECT word_id FROM public.arsenal_drops WHERE id = v_drop) <> v_w2
           OR EXISTS (SELECT 1 FROM public.arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x
                       WHERE d.id = v_drop AND x NOT IN (SELECT unnest(v_t))) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the pick (categories by slot %, yesterday %)', v_got, v_y;
        END IF;
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2000-01-03', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%verified pool is short%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a short pool did not refuse'; END IF;

        -- ---------------------------------------------------- 7. reactions and weights
        SELECT weight INTO v_before FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'finance_business';
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction) VALUES (v_owner, v_t[1], 'love');
        IF NOT (SELECT is_test FROM public.arsenal_reactions WHERE ref_id = v_t[1]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a reaction to a test row is not a test reaction';
        END IF;
        IF (SELECT weight FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'finance_business') <> v_before THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test reaction moved the real weights';
        END IF;
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction) VALUES (v_owner, v_r, 'love');
        SELECT weight INTO v_after FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'finance_business';
        IF v_after <= v_before AND v_before < 2.0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a Love did not raise its category (% -> %)', v_before, v_after;
        END IF;
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction) VALUES (v_owner, v_r, 'known');
        IF (SELECT weight FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'finance_business') <> v_before THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Known (the latest reaction) moved a category weight';
        END IF;
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_reactions SET reaction = 'love' WHERE ref_id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_drops WHERE id = v_drop;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.arsenal_drops SET topped_up = true WHERE id = v_drop;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 append-only refusals (reactions, drops)', v_n; END IF;
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_reactions (owner_id, ref_id, word_id, reaction) VALUES (v_owner, v_r, v_w, 'love');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a reaction with two targets was accepted'; END IF;

        -- ---------------------------------------------------- 8. pool runs: append-only, shape
        INSERT INTO public.arsenal_pool_runs (kind, status, batch_id, model, refs_requested, words_requested)
        VALUES ('batch', 'submitted', 'msgbatch_selftest054', 'claude-sonnet-5-5', 30, 10) RETURNING id INTO v_id;
        v_n := 0;
        BEGIN
            INSERT INTO public.arsenal_pool_runs (kind, status) VALUES ('batch', 'collected');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.arsenal_pool_runs SET refs_requested = 1 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 pool-run refusals', v_n; END IF;
        INSERT INTO public.arsenal_pool_runs (kind, status, run_id, inserted_refs) VALUES ('batch', 'collected', v_id, 1);
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_pool_runs (kind, status, run_id, note) VALUES ('batch', 'failed', v_id, 'twice');
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a run was closed twice'; END IF;

        -- TRUNCATE: every table that references another is named with it
        -- (CLAUDE.md §5, db/042's lesson); the first guard to fire refuses.
        v_ok := false;
        BEGIN
            TRUNCATE public.arsenal_pool_runs, public.arsenal_refs, public.arsenal_words, public.arsenal_drops,
                     public.arsenal_reactions;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%append-only. TRUNCATE is refused%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: TRUNCATE on the arsenal tables was not refused'; END IF;

        -- ---------------------------------------------------- 9. a real drop, the one-a-day rule, context
        -- Three real refs in three categories and a real word, settled as the worker would.
        v_ids := '{}';
        FOR v_n IN 1..3 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, status, origin_kind)
            VALUES ('selftest_054_real' || v_n, v_cats[v_n + 1], 'Selftest real ' || v_n, 'An origin long enough.',
                    'A meaning.', 'A line to deploy.', 'negotiation', 'A work', 'wikipedia', 'pool', 'pool')
            RETURNING id INTO v_id;
            UPDATE public.arsenal_refs SET verification = 'verified', source_url = 'https://selftest.invalid/r' || v_n
             WHERE id = v_id;
            v_ids := v_ids || v_id;
        END LOOP;
        INSERT INTO public.arsenal_words (lemma, meaning, room, sentence) VALUES ('selftestreal', 'A meaning.', 'pitch', 'A sentence.')
        RETURNING id INTO v_w;
        UPDATE public.arsenal_words SET pronunciation = '/x/', etymology = 'From x.', etymology_url = 'https://selftest.invalid/w',
                                        verification = 'verified' WHERE id = v_w;
        IF NOT EXISTS (SELECT 1 FROM public.arsenal_drops WHERE drop_date = public.owner_today() AND NOT is_test) THEN
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id) VALUES (public.owner_today(), v_ids, v_w)
            RETURNING id INTO v_drop;
            -- One real drop a day: the picker returns the existing one.
            IF public.arsenal_pick_drop(public.owner_today()) <> v_drop THEN
                RAISE EXCEPTION 'SELFTEST FAILED: the picker did not return today''s existing drop';
            END IF;
            v_ctx := public.ctx_arsenal_brief();
            IF v_ctx ->> 'power_word' <> 'selftestreal' OR (v_ctx -> 'ref' ->> 'title') NOT LIKE 'Selftest real %' THEN
                RAISE EXCEPTION 'SELFTEST FAILED: ctx_arsenal_brief: %', v_ctx;
            END IF;
            v_ctx := public.ctx_arsenal();
            IF jsonb_array_length(v_ctx -> 'today' -> 'refs') <> 3 OR (v_ctx -> 'today' -> 'power_word' ->> 'word') <> 'selftestreal'
               OR NOT (v_ctx ? 'weights') THEN
                RAISE EXCEPTION 'SELFTEST FAILED: ctx_arsenal: %', left(v_ctx::text, 400);
            END IF;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers WHERE key = 'arsenal' AND NOT brief_section AND sort = 60)
           OR NOT EXISTS (SELECT 1 FROM public.context_providers WHERE key = 'arsenal_brief' AND brief_section AND sort = 60) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the context registry rows';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '054 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('054_arsenal')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '054_arsenal';
-- SELECT status, verification, count(*) FROM arsenal_refs GROUP BY 1, 2;          -- seed / pending 27
-- SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
--   FROM pg_class c WHERE c.relname LIKE 'arsenal\_%' AND c.relkind = 'r' ORDER BY 1;
-- SELECT key, brief_section, sort FROM context_providers WHERE key LIKE 'arsenal%';
