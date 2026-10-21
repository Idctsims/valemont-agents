-- 059_arsenal_phase2.sql — The Arsenal, Phase 2: Arm Me, My Arsenal saves
-- and the Monday quiz (Chat 5 Phase 2; owner, 2026-10-21; docs/arsenal.md §9).
--
-- What it changes:
--   1. arsenal_refs: status gains 'arm', origin_kind gains 'arm_me', and an
--      arm card is exactly an arm_me one (CHECK). The reference guard (db/055's)
--      lets a reference enter as 'arm'; an arm reference never changes status
--      (like a seed), so it never enters a drop (the picker and the drop guard
--      take 'pool' only).
--   2. arm_requests (APPEND-ONLY, TRUNCATE-guarded): one row per Arm Me request
--      that reached the model. Written only through arsenal_arm_record.
--      arsenal_arm_card upserts one card by canonical_key (SECURITY DEFINER,
--      the owner only): an existing key comes back as it is, a new one enters
--      'arm' / 'pending' / 'arm_me'. arsenal_arm_requests_since: the rate limit.
--   3. arsenal_saves (APPEND-ONLY, TRUNCATE-guarded): save / unsave events on a
--      reference or a word; is_test from the target; a redundant event is
--      refused. v_arsenal_saved: the current saved set. A save of a pending
--      'arm' reference queues ONE arsenal_arm_verify job (job_queue), ever.
--   4. arsenal_quizzes (APPEND-ONLY): a week's quiz, built once (one real quiz
--      per owner per week). arsenal_quiz_reviews (APPEND-ONLY): one row per
--      answer, its FSRS state after, (quiz, question) UNIQUE; the database
--      recomputes `correct` from the stored answer and the rating from that.
--      v_arsenal_quiz_attempts: MASTER_PLAN §3's arsenal_quiz_attempts, a VIEW
--      over the reviews (one source of truth).
--   5. arsenal_weights(): a save counts as two Loves (+0.30), while saved, real
--      only. Same signature as db/054, so nothing that reads it changes.
--   6. arsenal_quiz_line(day, with_tests): {questions: N} on a Monday with at
--      least 4 saved items, else NULL. ctx_arsenal gains saved_count,
--      recent_saves (5) and last_quiz; ctx_arsenal_brief gains `quiz` (and on a
--      Monday with no drop, the quiz alone).
--   7. Test helpers (the verified e2e marker): add_test_arm_ref, add_test_save.
--   8. Access: RLS on the four tables; anon nothing; the owner reads her rows,
--      inserts saves, quizzes and reviews; arm_requests and arm cards are
--      written only through the two functions; valemont_readonly reads all.
--
-- A self-test runs on paste (section 9), rolled back, and raises on any wrong
-- result, so a bad paste applies nothing.
-- ORDER (db/055's lesson): every table a trigger function reads is created
-- before any statement that fires it. The fixture rule (db/053's): no
-- explicit NULL into a NOT NULL column, defaults or not.
-- Prerequisites: db/020 (job_queue, is_owner), db/042 (owner_today), db/051
-- (e2e_marker_ok), db/054–db/057.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- PASTE BEFORE THE MERGE: the web and the worker read the new schema.

-- ===================================================================== 1
-- arsenal_refs: the 'arm' status and the 'arm_me' origin.

ALTER TABLE arsenal_refs DROP CONSTRAINT IF EXISTS arsenal_refs_status_check;
ALTER TABLE arsenal_refs ADD CONSTRAINT arsenal_refs_status_check
    CHECK (status IN ('seed', 'pool', 'dropped', 'rejected', 'arm'));
ALTER TABLE arsenal_refs DROP CONSTRAINT IF EXISTS arsenal_refs_origin_kind_check;
ALTER TABLE arsenal_refs ADD CONSTRAINT arsenal_refs_origin_kind_check
    CHECK (origin_kind IN ('pool', 'seed', 'arm_me'));
ALTER TABLE arsenal_refs DROP CONSTRAINT IF EXISTS arsenal_refs_arm;
ALTER TABLE arsenal_refs ADD CONSTRAINT arsenal_refs_arm CHECK ((origin_kind = 'arm_me') = (status = 'arm'));

-- db/055's reference guard; a reference may enter as 'arm' too. Every other
-- rule is unchanged (an arm reference's card changes only while pending, its
-- verification settles once, its status never changes).
CREATE OR REPLACE FUNCTION public.arsenal_refs_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_rev boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.verified_at := CASE WHEN NEW.verification <> 'pending' THEN now() END;
        IF NEW.status NOT IN ('pool', 'seed', 'arm') THEN
            RAISE EXCEPTION 'arsenal_refs: a reference enters as pool, seed or arm, never %', NEW.status;
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
    -- The one re-verification (db/055): only inside arsenal_reverify_ref, for
    -- an unverified pool reference, matching the row it just recorded.
    v_rev := false;
    IF OLD.status = 'pool' AND OLD.verification = 'unverified' AND NEW.status = OLD.status
       AND coalesce(current_setting('arsenal.reverify', true), '') = 'ref:' || OLD.id THEN
        v_rev := EXISTS (SELECT 1 FROM public.arsenal_reverifications v
                          WHERE v.ref_id = OLD.id AND v.new_verification = NEW.verification
                            AND v.new_note IS NOT DISTINCT FROM NEW.verification_note);
    END IF;
    IF v_rev THEN
        IF NEW.verification = 'pending' THEN
            RAISE EXCEPTION 'arsenal_refs: a re-verification settles; it never returns to pending';
        END IF;
        NEW.verified_at := now();
        RETURN NEW;
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

-- ===================================================================== 2
-- arm_requests: append-only. The two functions are the only writers.

CREATE TABLE IF NOT EXISTS arm_requests (
    id             BIGSERIAL    PRIMARY KEY,
    owner_id       UUID         NOT NULL REFERENCES auth.users (id) ON DELETE RESTRICT,
    situation      TEXT         NOT NULL CHECK (char_length(btrim(situation)) BETWEEN 1 AND 600),
    room_hint      TEXT         CHECK (room_hint IS NULL OR room_hint IN ('boardroom', 'pitch', 'negotiation', 'homies')),
    -- The model that answered ('mock' in local e2e); NULL when none was reached.
    model          TEXT         CHECK (model IS NULL OR model ~ '^[a-z0-9.-]{1,64}$'),
    first_card_ms  INTEGER      CHECK (first_card_ms IS NULL OR first_card_ms >= 0),
    total_ms       INTEGER      CHECK (total_ms IS NULL OR total_ms >= 0),
    ai_usage_id    BIGINT       REFERENCES ai_usage (id) ON DELETE RESTRICT,
    -- The cards shown, in order.
    ref_ids        BIGINT[]     NOT NULL DEFAULT '{}' CHECK (cardinality(ref_ids) <= 3),
    error          TEXT         CHECK (error IS NULL OR char_length(error) <= 500),
    note           TEXT         CHECK (note IS NULL OR char_length(note) <= 500),
    is_test        BOOLEAN      NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS arm_requests_recent ON arm_requests (owner_id, created_at DESC);

CREATE OR REPLACE FUNCTION public.arm_requests_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.created_at := now();
    IF array_position(NEW.ref_ids, NULL) IS NOT NULL
       OR (SELECT count(*) FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids))
          <> (SELECT count(DISTINCT x) FROM unnest(NEW.ref_ids) x)
       OR (SELECT count(DISTINCT x) FROM unnest(NEW.ref_ids) x) <> cardinality(NEW.ref_ids) THEN
        RAISE EXCEPTION 'arm_requests: ref_ids must name distinct, existing references';
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r WHERE r.id = ANY (NEW.ref_ids) AND r.is_test <> NEW.is_test) THEN
        RAISE EXCEPTION 'arm_requests: a real request shows real references only, a test request test ones only';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arm_requests_guard ON arm_requests;
CREATE TRIGGER arm_requests_guard
    BEFORE INSERT ON arm_requests
    FOR EACH ROW EXECUTE FUNCTION public.arm_requests_guard();
DROP TRIGGER IF EXISTS arm_requests_immutable ON arm_requests;
CREATE TRIGGER arm_requests_immutable
    BEFORE UPDATE OR DELETE ON arm_requests
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arm_requests_no_truncate ON arm_requests;
CREATE TRIGGER arm_requests_no_truncate
    BEFORE TRUNCATE ON arm_requests
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 3
-- arsenal_saves: append-only. The table and its view come before the
-- functions of section 4, which read them.

CREATE TABLE IF NOT EXISTS arsenal_saves (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    ref_id      BIGINT       REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    word_id     BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    action      TEXT         NOT NULL CHECK (action IN ('save', 'unsave')),
    -- The target's, set by trigger: a test save never trains the real weights.
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_saves_one_target CHECK (num_nonnulls(ref_id, word_id) = 1)
);
CREATE INDEX IF NOT EXISTS arsenal_saves_ref ON arsenal_saves (ref_id, created_at DESC, id DESC) WHERE ref_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS arsenal_saves_word ON arsenal_saves (word_id, created_at DESC, id DESC) WHERE word_id IS NOT NULL;

-- The current saved set: the latest event per owner and target is a save.
CREATE OR REPLACE VIEW v_arsenal_saved WITH (security_invoker = true) AS
SELECT l.id AS save_id, l.owner_id, l.ref_id, l.word_id, l.created_at AS saved_at, l.is_test
  FROM (SELECT DISTINCT ON (s.owner_id, s.ref_id, s.word_id) s.id, s.owner_id, s.ref_id, s.word_id, s.action,
               s.is_test, s.created_at
          FROM public.arsenal_saves s
         ORDER BY s.owner_id, s.ref_id, s.word_id, s.created_at DESC, s.id DESC) l
 WHERE l.action = 'save';

CREATE OR REPLACE FUNCTION public.arsenal_saves_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_test    boolean;
    v_current text;
BEGIN
    NEW.created_at := now();
    IF NEW.ref_id IS NOT NULL THEN
        SELECT r.is_test INTO v_test FROM public.arsenal_refs r WHERE r.id = NEW.ref_id;
    ELSE
        SELECT w.is_test INTO v_test FROM public.arsenal_words w WHERE w.id = NEW.word_id;
    END IF;
    IF v_test IS NULL THEN
        RAISE EXCEPTION 'arsenal_saves: no such reference or word';
    END IF;
    NEW.is_test := v_test;
    -- One event at a time per target, so the redundancy check below holds.
    PERFORM pg_advisory_xact_lock(hashtext('arsenal_saves'),
                                  (CASE WHEN NEW.ref_id IS NOT NULL THEN NEW.ref_id ELSE -NEW.word_id END
                                   % 2000000000)::int);
    SELECT s.action INTO v_current FROM public.arsenal_saves s
     WHERE s.owner_id = NEW.owner_id AND s.ref_id IS NOT DISTINCT FROM NEW.ref_id
       AND s.word_id IS NOT DISTINCT FROM NEW.word_id
     ORDER BY s.created_at DESC, s.id DESC LIMIT 1;
    IF NEW.action = 'save' AND v_current = 'save' THEN
        RAISE EXCEPTION 'arsenal_saves: already saved';
    END IF;
    IF NEW.action = 'unsave' AND coalesce(v_current, 'unsave') = 'unsave' THEN
        RAISE EXCEPTION 'arsenal_saves: not saved';
    END IF;
    RETURN NEW;
END;
$$;

-- After a save: a pending 'arm' reference is queued for verification ONCE
-- (an advisory lock per reference; no job of the kind for it may exist yet;
-- it must still be pending), so save, unsave, save never queues twice.
CREATE OR REPLACE FUNCTION public.arsenal_saves_queue_verify()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.action <> 'save' OR NEW.ref_id IS NULL THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('arsenal_arm_verify'), (NEW.ref_id % 2000000000)::int);
    IF EXISTS (SELECT 1 FROM public.arsenal_refs r
                WHERE r.id = NEW.ref_id AND r.status = 'arm' AND r.verification = 'pending')
       AND NOT EXISTS (SELECT 1 FROM public.job_queue q
                        WHERE q.kind = 'arsenal_arm_verify' AND q.payload ->> 'ref_id' = NEW.ref_id::text) THEN
        INSERT INTO public.job_queue (kind, payload) VALUES ('arsenal_arm_verify', jsonb_build_object('ref_id', NEW.ref_id));
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_saves_guard ON arsenal_saves;
CREATE TRIGGER arsenal_saves_guard
    BEFORE INSERT ON arsenal_saves
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_saves_guard();
DROP TRIGGER IF EXISTS arsenal_saves_queue_verify ON arsenal_saves;
CREATE TRIGGER arsenal_saves_queue_verify
    AFTER INSERT ON arsenal_saves
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_saves_queue_verify();
DROP TRIGGER IF EXISTS arsenal_saves_immutable ON arsenal_saves;
CREATE TRIGGER arsenal_saves_immutable
    BEFORE UPDATE OR DELETE ON arsenal_saves
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_saves_no_truncate ON arsenal_saves;
CREATE TRIGGER arsenal_saves_no_truncate
    BEFORE TRUNCATE ON arsenal_saves
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 4
-- Arm Me's functions (they read arsenal_saves, created above).

-- The owner's Arm Me requests in the last p_seconds, by the database clock.
CREATE OR REPLACE FUNCTION public.arsenal_arm_requests_since(p_seconds integer)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT count(*)::integer FROM public.arm_requests a
     WHERE a.owner_id = auth.uid() AND a.created_at > now() - make_interval(secs => p_seconds);
$$;

-- One Arm Me card, upserted by canonical_key. The owner only; a non-NULL
-- marker must verify and makes a new card a test one. Returns the card as
-- shown (an existing key comes back exactly as it is, with its real tag),
-- or NULL when the key belongs to the other world (a real request never
-- shows a test row, a test request never a real one).
CREATE OR REPLACE FUNCTION public.arsenal_arm_card(p_card jsonb, p_marker text DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_test     boolean := false;
    v_key      text := p_card ->> 'canonical_key';
    v_id       bigint;
    v_existing boolean := true;
    r          public.arsenal_refs%ROWTYPE;
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'arsenal_arm_card: the owner only';
    END IF;
    IF p_marker IS NOT NULL THEN
        IF NOT public.e2e_marker_ok(p_marker) THEN
            RAISE EXCEPTION 'arsenal_arm_card: the test marker does not verify';
        END IF;
        v_test := true;
    END IF;
    SELECT * INTO r FROM public.arsenal_refs WHERE canonical_key = v_key;
    IF NOT FOUND THEN
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         quote, quote_by, source_kind, source_page, status, verification,
                                         origin_kind, is_test)
        VALUES (v_key, p_card ->> 'category', p_card ->> 'title', p_card ->> 'origin', p_card ->> 'meaning',
                p_card ->> 'deploy_line', p_card ->> 'room', p_card ->> 'source_work',
                nullif(p_card ->> 'quote', ''), CASE WHEN nullif(p_card ->> 'quote', '') IS NOT NULL
                                                     THEN nullif(p_card ->> 'quote_by', '') END,
                coalesce(nullif(p_card ->> 'source_kind', ''), 'other'), nullif(p_card ->> 'source_page', ''),
                'arm', 'pending', 'arm_me', v_test)
        ON CONFLICT (canonical_key) DO NOTHING
        RETURNING id INTO v_id;
        v_existing := v_id IS NULL;  -- lost a race to another request: show that row
        SELECT * INTO r FROM public.arsenal_refs WHERE canonical_key = v_key;
    END IF;
    IF r.is_test <> v_test THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object(
        'id', r.id, 'canonical_key', r.canonical_key, 'category', r.category, 'title', r.title, 'origin', r.origin,
        'meaning', r.meaning, 'deploy_line', r.deploy_line, 'room', r.room, 'source_work', r.source_work,
        'quote', r.quote, 'quote_by', r.quote_by, 'source_url', r.source_url, 'verification', r.verification,
        'verification_note', r.verification_note, 'status', r.status, 'is_test', r.is_test,
        'existing', v_existing,
        'held', EXISTS (SELECT 1 FROM public.v_arsenal_active_holds h WHERE h.ref_id = r.id),
        'saved', EXISTS (SELECT 1 FROM public.v_arsenal_saved s WHERE s.ref_id = r.id AND s.owner_id = auth.uid()),
        'in_arsenal', v_existing AND (r.status IN ('seed', 'dropped')
                                      OR EXISTS (SELECT 1 FROM public.v_arsenal_saved s
                                                  WHERE s.ref_id = r.id AND s.owner_id = auth.uid())));
END;
$$;

-- One arm_requests row (the only writer). The owner only; a non-NULL marker
-- must verify and makes the row a test one. Returns its id.
CREATE OR REPLACE FUNCTION public.arsenal_arm_record(p_situation text, p_room_hint text, p_model text,
                                                     p_first_card_ms integer, p_total_ms integer,
                                                     p_ai_usage_id bigint, p_ref_ids bigint[], p_error text,
                                                     p_note text, p_marker text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_test boolean := false;
    v_id   bigint;
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'arsenal_arm_record: the owner only';
    END IF;
    IF p_marker IS NOT NULL THEN
        IF NOT public.e2e_marker_ok(p_marker) THEN
            RAISE EXCEPTION 'arsenal_arm_record: the test marker does not verify';
        END IF;
        v_test := true;
    END IF;
    INSERT INTO public.arm_requests (owner_id, situation, room_hint, model, first_card_ms, total_ms, ai_usage_id,
                                     ref_ids, error, note, is_test)
    VALUES (auth.uid(), left(btrim(p_situation), 600), p_room_hint, p_model, p_first_card_ms, p_total_ms,
            p_ai_usage_id, coalesce(p_ref_ids, '{}'), left(p_error, 500), left(p_note, 500), v_test)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 5
-- The quiz: arsenal_quizzes and arsenal_quiz_reviews, append-only.

CREATE TABLE IF NOT EXISTS arsenal_quizzes (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    -- The Monday (owner's date) the week starts on.
    week_start  DATE         NOT NULL CHECK (extract(isodow FROM week_start) = 1),
    -- [{no, kind, ref_id | word_id, prompt, options[4], answer}], 1..10, in order.
    questions   JSONB        NOT NULL,
    -- All the targets', set by trigger.
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_quizzes_one_a_week ON arsenal_quizzes (owner_id, week_start) WHERE NOT is_test;
CREATE INDEX IF NOT EXISTS arsenal_quizzes_week ON arsenal_quizzes (week_start DESC, id DESC);

CREATE OR REPLACE FUNCTION public.arsenal_quizzes_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    q       jsonb;
    i       int := 0;
    v_n     int;
    v_tests int := 0;
    v_test  boolean;
BEGIN
    NEW.created_at := now();
    IF jsonb_typeof(NEW.questions) <> 'array' OR jsonb_array_length(NEW.questions) NOT BETWEEN 1 AND 10 THEN
        RAISE EXCEPTION 'arsenal_quizzes: a quiz holds 1 to 10 questions';
    END IF;
    v_n := jsonb_array_length(NEW.questions);
    FOR q IN SELECT x FROM jsonb_array_elements(NEW.questions) x LOOP
        i := i + 1;
        IF (q ->> 'no') IS DISTINCT FROM i::text
           OR coalesce(q ->> 'kind', '') NOT IN ('fits', 'means', 'sentence')
           OR jsonb_typeof(q -> 'options') IS DISTINCT FROM 'array' OR jsonb_array_length(q -> 'options') <> 4
           OR (SELECT count(DISTINCT o) FROM jsonb_array_elements_text(q -> 'options') o
                WHERE btrim(o) <> '') <> 4
           OR coalesce(q ->> 'answer', '') !~ '^[0-3]$'
           OR coalesce(btrim(q ->> 'prompt'), '') = '' THEN
            RAISE EXCEPTION 'arsenal_quizzes: question % is malformed (no, kind, prompt, 4 distinct options, answer 0-3)', i;
        END IF;
        IF q ->> 'kind' = 'sentence' THEN
            IF q ? 'ref_id' OR coalesce(q ->> 'word_id', '') !~ '^[0-9]+$' THEN
                RAISE EXCEPTION 'arsenal_quizzes: question % (sentence) names a word', i;
            END IF;
            SELECT w.is_test INTO v_test FROM public.arsenal_words w WHERE w.id = (q ->> 'word_id')::bigint;
            IF NOT EXISTS (SELECT 1 FROM public.v_arsenal_saved s
                            WHERE s.owner_id = NEW.owner_id AND s.word_id = (q ->> 'word_id')::bigint) THEN
                RAISE EXCEPTION 'arsenal_quizzes: question % asks about a word that is not saved', i;
            END IF;
        ELSE
            IF q ? 'word_id' OR coalesce(q ->> 'ref_id', '') !~ '^[0-9]+$' THEN
                RAISE EXCEPTION 'arsenal_quizzes: question % (%) names a reference', i, q ->> 'kind';
            END IF;
            SELECT r.is_test INTO v_test FROM public.arsenal_refs r WHERE r.id = (q ->> 'ref_id')::bigint;
            IF NOT EXISTS (SELECT 1 FROM public.v_arsenal_saved s
                            WHERE s.owner_id = NEW.owner_id AND s.ref_id = (q ->> 'ref_id')::bigint) THEN
                RAISE EXCEPTION 'arsenal_quizzes: question % asks about a reference that is not saved', i;
            END IF;
        END IF;
        v_tests := v_tests + (CASE WHEN v_test THEN 1 ELSE 0 END);
    END LOOP;
    IF v_tests NOT IN (0, v_n) THEN
        RAISE EXCEPTION 'arsenal_quizzes: a quiz is all real or all test';
    END IF;
    NEW.is_test := v_tests = v_n;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_quizzes_guard ON arsenal_quizzes;
CREATE TRIGGER arsenal_quizzes_guard
    BEFORE INSERT ON arsenal_quizzes
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_quizzes_guard();
DROP TRIGGER IF EXISTS arsenal_quizzes_immutable ON arsenal_quizzes;
CREATE TRIGGER arsenal_quizzes_immutable
    BEFORE UPDATE OR DELETE ON arsenal_quizzes
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_quizzes_no_truncate ON arsenal_quizzes;
CREATE TRIGGER arsenal_quizzes_no_truncate
    BEFORE TRUNCATE ON arsenal_quizzes
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

CREATE TABLE IF NOT EXISTS arsenal_quiz_reviews (
    id             BIGSERIAL    PRIMARY KEY,
    owner_id       UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    quiz_id        BIGINT       NOT NULL REFERENCES arsenal_quizzes (id) ON DELETE RESTRICT,
    question_no    INTEGER      NOT NULL CHECK (question_no BETWEEN 1 AND 10),
    -- Copied from the quiz and its question by the guard.
    week_start     DATE         NOT NULL,
    ref_id         BIGINT       REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    word_id        BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    question_kind  TEXT         NOT NULL CHECK (question_kind IN ('fits', 'means', 'sentence')),
    chosen         INTEGER      NOT NULL CHECK (chosen BETWEEN 0 AND 3),
    correct        BOOLEAN      NOT NULL,
    rating         TEXT         NOT NULL CHECK (rating IN ('again', 'good')),
    -- ts-fsrs's Card after this answer; due_at is its `due`.
    fsrs_state     JSONB        NOT NULL,
    due_at         TIMESTAMPTZ  NOT NULL,
    is_test        BOOLEAN      NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_quiz_reviews_one_target CHECK (num_nonnulls(ref_id, word_id) = 1),
    CONSTRAINT arsenal_quiz_reviews_once UNIQUE (quiz_id, question_no)
);
CREATE INDEX IF NOT EXISTS arsenal_quiz_reviews_ref ON arsenal_quiz_reviews (ref_id, created_at DESC, id DESC) WHERE ref_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS arsenal_quiz_reviews_word ON arsenal_quiz_reviews (word_id, created_at DESC, id DESC) WHERE word_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.arsenal_quiz_reviews_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_quiz public.arsenal_quizzes%ROWTYPE;
    q      jsonb;
BEGIN
    NEW.created_at := now();
    SELECT * INTO v_quiz FROM public.arsenal_quizzes z WHERE z.id = NEW.quiz_id;
    IF NOT FOUND OR v_quiz.owner_id <> NEW.owner_id THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: no such quiz of yours';
    END IF;
    q := v_quiz.questions -> (NEW.question_no - 1);
    IF q IS NULL THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: quiz % has no question %', NEW.quiz_id, NEW.question_no;
    END IF;
    IF (NEW.ref_id IS NOT NULL AND NEW.ref_id::text IS DISTINCT FROM q ->> 'ref_id')
       OR (NEW.word_id IS NOT NULL AND NEW.word_id::text IS DISTINCT FROM q ->> 'word_id')
       OR NEW.question_kind IS DISTINCT FROM q ->> 'kind' THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: the target or kind is not question %''s', NEW.question_no;
    END IF;
    NEW.ref_id := (q ->> 'ref_id')::bigint;
    NEW.word_id := (q ->> 'word_id')::bigint;
    NEW.week_start := v_quiz.week_start;
    NEW.is_test := v_quiz.is_test;
    IF NEW.correct IS DISTINCT FROM (NEW.chosen = (q ->> 'answer')::int) THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: correct disagrees with the stored answer';
    END IF;
    IF NEW.rating <> CASE WHEN NEW.correct THEN 'good' ELSE 'again' END THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: a right answer is rated good, a wrong one again';
    END IF;
    IF jsonb_typeof(NEW.fsrs_state) <> 'object'
       OR NOT NEW.fsrs_state ?& ARRAY['due', 'stability', 'difficulty', 'reps', 'lapses', 'state'] THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: fsrs_state is a ts-fsrs Card (due, stability, difficulty, reps, lapses, state)';
    END IF;
    IF NEW.due_at IS DISTINCT FROM (NEW.fsrs_state ->> 'due')::timestamptz THEN
        RAISE EXCEPTION 'arsenal_quiz_reviews: due_at must be the card''s due';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_quiz_reviews_guard ON arsenal_quiz_reviews;
CREATE TRIGGER arsenal_quiz_reviews_guard
    BEFORE INSERT ON arsenal_quiz_reviews
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_quiz_reviews_guard();
DROP TRIGGER IF EXISTS arsenal_quiz_reviews_immutable ON arsenal_quiz_reviews;
CREATE TRIGGER arsenal_quiz_reviews_immutable
    BEFORE UPDATE OR DELETE ON arsenal_quiz_reviews
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_quiz_reviews_no_truncate ON arsenal_quiz_reviews;
CREATE TRIGGER arsenal_quiz_reviews_no_truncate
    BEFORE TRUNCATE ON arsenal_quiz_reviews
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- MASTER_PLAN §3's arsenal_quiz_attempts: a view, so the reviews stay the
-- one source of truth.
CREATE OR REPLACE VIEW v_arsenal_quiz_attempts WITH (security_invoker = true) AS
SELECT r.id, r.owner_id, r.quiz_id, r.week_start, r.ref_id, r.word_id, r.correct, r.is_test, r.created_at
  FROM public.arsenal_quiz_reviews r;

-- ===================================================================== 6
-- Weights with saves, and the context providers.

-- db/054's weights: base x clamp(1 + 0.15 loves + 0.30 saves - 0.15 not
-- interested, 0.3, 2.0). A save counts as two Loves while it is saved; real
-- rows only. Same signature as db/054 (loves counts reactions only).
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
    saved AS (
        SELECT s.ref_id, s.word_id, 'save'::text AS reaction FROM public.v_arsenal_saved s WHERE NOT s.is_test
    ),
    marks AS (
        SELECT * FROM latest UNION ALL SELECT * FROM saved
    ),
    tallies AS (
        SELECT 'category'::text AS dim, f.category AS key, m.reaction
          FROM marks m JOIN public.arsenal_refs f ON f.id = m.ref_id
        UNION ALL
        SELECT 'room', f.room, m.reaction FROM marks m JOIN public.arsenal_refs f ON f.id = m.ref_id
        UNION ALL
        SELECT 'room', w.room, m.reaction FROM marks m JOIN public.arsenal_words w ON w.id = m.word_id
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
                                               + 0.30 * count(*) FILTER (WHERE t.reaction = 'save')
                                               - 0.15 * count(*) FILTER (WHERE t.reaction = 'not_interested'))))::real
      FROM bases b
      LEFT JOIN tallies t ON t.dim = b.dim AND t.key = b.key
     GROUP BY b.dim, b.key, b.base
     ORDER BY b.dim, b.key;
$$;

-- The quiz line's data: {questions: N} when p_day is a Monday and at least 4
-- items are saved (real, or test with p_with_tests), else NULL. N is the
-- quiz's size: min(10, 2 x saved references + saved words) (§9.6).
CREATE OR REPLACE FUNCTION public.arsenal_quiz_line(p_day date, p_with_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_build_object('questions', least(10, 2 * c.refs + c.words))
      FROM (SELECT count(*) FILTER (WHERE s.ref_id IS NOT NULL) AS refs,
                   count(*) FILTER (WHERE s.word_id IS NOT NULL) AS words
              FROM public.v_arsenal_saved s WHERE s.is_test = p_with_tests) c
     WHERE extract(isodow FROM p_day) = 1 AND c.refs + c.words >= 4;
$$;

-- db/054's ctx_arsenal, plus saved_count, recent_saves (5) and last_quiz.
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
    ),
    saved AS (
        SELECT s.ref_id, s.word_id, s.saved_at FROM public.v_arsenal_saved s WHERE NOT s.is_test
    ),
    quiz AS (
        SELECT z.week_start, jsonb_array_length(z.questions) AS questions,
               count(v.id) AS answered, count(v.id) FILTER (WHERE v.correct) AS right_answers
          FROM public.arsenal_quizzes z
          JOIN public.arsenal_quiz_reviews v ON v.quiz_id = z.id
         WHERE NOT z.is_test
         GROUP BY z.id, z.week_start, z.questions
         ORDER BY z.week_start DESC, z.id DESC
         LIMIT 1
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'about', 'The Arsenal: a daily drop of three references (each with where it comes from, what it means, '
                 'a line to deploy and the room it plays in: boardroom, pitch, negotiation or homies) and a Power '
                 'Word. "today" is today''s drop; "last_7_days" the week before; "weights" how the owner''s '
                 'reactions and saves tilt categories and rooms (1.0 is neutral); "saved_count" and '
                 '"recent_saves" are My Arsenal (what the owner saved, including Arm Me cards); "last_quiz" is '
                 'the latest Monday quiz on the saved cards.',
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
                      FROM public.arsenal_weights() x),
        'saved_count', (SELECT count(*) FROM saved),
        'recent_saves', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                      'kind', CASE WHEN x.ref_id IS NOT NULL THEN 'reference' ELSE 'word' END,
                                      'title', coalesce(r.title, w.lemma), 'room', coalesce(r.room, w.room),
                                      'saved_at', x.saved_at) ORDER BY x.saved_at DESC)
                                    FROM (SELECT * FROM saved ORDER BY saved_at DESC LIMIT 5) x
                                    LEFT JOIN public.arsenal_refs r ON r.id = x.ref_id
                                    LEFT JOIN public.arsenal_words w ON w.id = x.word_id), '[]'::jsonb),
        'last_quiz', (SELECT jsonb_build_object('week_start', q.week_start,
                                                'score', q.right_answers || '/' || q.questions,
                                                'answered', q.answered, 'questions', q.questions)
                        FROM quiz q)
    ));
$$;

-- db/054's brief line, plus the Monday quiz: `quiz` {questions: N} on a
-- Monday with at least 4 real saved items. NULL (silent) when today has
-- neither a drop nor a quiz to announce.
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
    ),
    quiz AS (
        SELECT public.arsenal_quiz_line(public.owner_today(), false) AS line
    )
    SELECT CASE WHEN EXISTS (SELECT 1 FROM t) OR (SELECT line FROM quiz) IS NOT NULL THEN
           jsonb_strip_nulls(jsonb_build_object(
               'power_word', (SELECT wd.lemma FROM t JOIN public.arsenal_words wd ON wd.id = t.word_id),
               'word_meaning', (SELECT wd.meaning FROM t JOIN public.arsenal_words wd ON wd.id = t.word_id),
               'ref', (SELECT jsonb_build_object('title', b.title, 'room', b.room, 'category', b.category) FROM best b),
               'quiz', (SELECT line FROM quiz)))
           END;
$$;

-- ===================================================================== 7
-- Test rows (e2e). Owner only, the verified e2e marker (db/051 e2e_marker_ok).

-- A TEST Arm Me card: status arm, pending, key e2e_<random>. Its meaning
-- names its title, so test cards differ in meaning too (the quiz's "What
-- does it mean?" needs distinct meanings to choose from).
CREATE OR REPLACE FUNCTION public.add_test_arm_ref(p_marker text, p_title text,
                                                   p_category text DEFAULT 'history_military',
                                                   p_room text DEFAULT 'pitch')
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
        RAISE EXCEPTION 'add_test_arm_ref: the owner and a verified test marker only';
    END IF;
    INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                     source_kind, source_page, status, verification, origin_kind, is_test)
    VALUES (v_key, p_category, p_title, 'Where ' || p_title || ' comes from, in two short lines.',
            left('What ' || p_title || ' means when you use it.', 300), 'The line you would actually say in the room.',
            p_room, 'Test work (2026)', 'wikipedia', 'Test page', 'arm', 'pending', 'arm_me', true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- A TEST save (or unsave) on a TEST reference or word. Returns the event's id.
CREATE OR REPLACE FUNCTION public.add_test_save(p_marker text, p_ref_id bigint DEFAULT NULL,
                                                p_word_id bigint DEFAULT NULL, p_action text DEFAULT 'save')
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_save: the owner and a verified test marker only';
    END IF;
    IF NOT coalesce((SELECT r.is_test FROM public.arsenal_refs r WHERE r.id = p_ref_id),
                    (SELECT w.is_test FROM public.arsenal_words w WHERE w.id = p_word_id), false) THEN
        RAISE EXCEPTION 'add_test_save: a test save is on a test reference or word';
    END IF;
    INSERT INTO public.arsenal_saves (owner_id, ref_id, word_id, action)
    VALUES (auth.uid(), p_ref_id, p_word_id, p_action)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 8
-- Access.

ALTER TABLE arm_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_saves ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_quizzes ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_quiz_reviews ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON arm_requests, arsenal_saves, arsenal_quizzes, arsenal_quiz_reviews, v_arsenal_saved,
              v_arsenal_quiz_attempts FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE arm_requests_id_seq, arsenal_saves_id_seq, arsenal_quizzes_id_seq,
                       arsenal_quiz_reviews_id_seq FROM anon, authenticated, PUBLIC;

GRANT SELECT ON arm_requests, arsenal_saves, arsenal_quizzes, arsenal_quiz_reviews, v_arsenal_saved,
                v_arsenal_quiz_attempts TO authenticated;
GRANT INSERT ON arsenal_saves, arsenal_quizzes, arsenal_quiz_reviews TO authenticated;
GRANT USAGE ON SEQUENCE arsenal_saves_id_seq, arsenal_quizzes_id_seq, arsenal_quiz_reviews_id_seq TO authenticated;

-- The owner's rows. arm_requests has no insert policy: arsenal_arm_record writes it.
DROP POLICY IF EXISTS arm_requests_owner_read ON arm_requests;
CREATE POLICY arm_requests_owner_read ON arm_requests FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_saves_owner_read ON arsenal_saves;
CREATE POLICY arsenal_saves_owner_read ON arsenal_saves FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_saves_owner_insert ON arsenal_saves;
CREATE POLICY arsenal_saves_owner_insert ON arsenal_saves FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_quizzes_owner_read ON arsenal_quizzes;
CREATE POLICY arsenal_quizzes_owner_read ON arsenal_quizzes FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_quizzes_owner_insert ON arsenal_quizzes;
CREATE POLICY arsenal_quizzes_owner_insert ON arsenal_quizzes FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_quiz_reviews_owner_read ON arsenal_quiz_reviews;
CREATE POLICY arsenal_quiz_reviews_owner_read ON arsenal_quiz_reviews FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS arsenal_quiz_reviews_owner_insert ON arsenal_quiz_reviews;
CREATE POLICY arsenal_quiz_reviews_owner_insert ON arsenal_quiz_reviews FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));

GRANT SELECT ON arm_requests, arsenal_saves, arsenal_quizzes, arsenal_quiz_reviews, v_arsenal_saved,
                v_arsenal_quiz_attempts TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arm_requests_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_saves_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_saves_queue_verify() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_quizzes_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_quiz_reviews_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_arm_requests_since(integer) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.arsenal_arm_card(jsonb, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.arsenal_arm_record(text, text, text, integer, integer, bigint, bigint[], text, text, text)
    FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.arsenal_quiz_line(date, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_arm_ref(text, text, text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.add_test_save(text, bigint, bigint, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.arsenal_arm_requests_since(integer) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.arsenal_arm_card(jsonb, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.arsenal_arm_record(text, text, text, integer, integer, bigint, bigint[], text, text, text)
    TO authenticated;
GRANT EXECUTE ON FUNCTION public.arsenal_quiz_line(date, boolean) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_arm_ref(text, text, text, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.add_test_save(text, bigint, bigint, text) TO authenticated;
-- db/054's grants on arsenal_weights, ctx_arsenal and ctx_arsenal_brief stand
-- (CREATE OR REPLACE keeps them).

INSERT INTO migration_log (name) VALUES ('059_arsenal_phase2')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 9
-- Self-test, run on paste, rolled back ('selftest-rollback'). Expected
-- refusals run in nested blocks and count only with the expected message or
-- SQLSTATE. The owner's session is simulated with request.jwt claims; the
-- marker is a throwaway e2e_markers row (rolled back with everything else).
-- Real-row checks use their own rows; production has no real save yet, and
-- the counts it compares are deltas.

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-059-' || md5(random()::text);
    v_card    jsonb;
    v_again   jsonb;
    v_arm     bigint;
    v_tarm    bigint;
    v_pool    bigint;
    v_r       bigint[] := '{}';
    v_w       bigint;
    v_id      bigint;
    v_quiz    bigint;
    v_n       int;
    v_ok      boolean;
    v_before  real;
    v_after   real;
    v_monday  date := date_trunc('week', now())::date;
    v_ctx     jsonb;
    v_state   jsonb := jsonb_build_object('due', '2026-11-01T00:00:00.000Z', 'stability', 1.2, 'difficulty', 5.1,
                                          'reps', 1, 'lapses', 0, 'state', 1, 'elapsed_days', 0,
                                          'scheduled_days', 3, 'learning_steps', 0, 'last_review', '2026-10-21T00:00:00.000Z');
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        -- The owner's session, and a throwaway marker.
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));

        -- ------------------------------------------------- 1. arm cards
        v_card := public.arsenal_arm_card(jsonb_build_object(
            'canonical_key', 'selftest_059_arm', 'category', 'history_military', 'title', 'Selftest arm',
            'origin', 'An origin long enough to pass.', 'meaning', 'A meaning.', 'deploy_line', 'A line to deploy.',
            'room', 'pitch', 'source_work', 'A work (2000)', 'quote', NULL, 'quote_by', NULL,
            'source_kind', 'wikipedia', 'source_page', 'Selftest'));
        v_arm := (v_card ->> 'id')::bigint;
        IF (v_card ->> 'existing')::boolean OR v_card ->> 'status' <> 'arm' OR v_card ->> 'verification' <> 'pending'
           OR (v_card ->> 'is_test')::boolean OR (v_card ->> 'in_arsenal')::boolean
           OR (SELECT origin_kind FROM public.arsenal_refs WHERE id = v_arm) <> 'arm_me' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a new arm card (got %)', v_card;
        END IF;
        v_again := public.arsenal_arm_card(jsonb_build_object('canonical_key', 'selftest_059_arm', 'title', 'Other'));
        IF NOT (v_again ->> 'existing')::boolean OR (v_again ->> 'id')::bigint <> v_arm OR v_again ->> 'title' <> 'Selftest arm' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an existing key did not come back as it is (got %)', v_again;
        END IF;
        -- A seed comes back "in your Arsenal".
        v_again := public.arsenal_arm_card(jsonb_build_object('canonical_key', 'crossing_the_rubicon'));
        IF NOT (v_again ->> 'in_arsenal')::boolean OR v_again ->> 'status' <> 'seed' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a seed is not "From your Arsenal" (got %)', v_again;
        END IF;
        -- A test request never shows a real row; a test card is test.
        IF public.arsenal_arm_card(jsonb_build_object('canonical_key', 'selftest_059_arm'), v_marker) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test request was shown a real row';
        END IF;
        v_card := public.arsenal_arm_card(jsonb_build_object(
            'canonical_key', 'e2e_selftest_059_tarm', 'category', 'sports', 'title', 'Selftest test arm',
            'origin', 'An origin long enough to pass.', 'meaning', 'A meaning.', 'deploy_line', 'A line to deploy.',
            'room', 'homies', 'source_work', 'A work (2000)', 'source_kind', 'wikipedia'), v_marker);
        v_tarm := (v_card ->> 'id')::bigint;
        IF NOT (v_card ->> 'is_test')::boolean THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test request wrote a real card';
        END IF;
        IF public.arsenal_arm_card(jsonb_build_object('canonical_key', 'e2e_selftest_059_tarm')) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a real request was shown a test row';
        END IF;
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_arm_card(jsonb_build_object('canonical_key', 'selftest_059_x'), 'not-the-marker');
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%marker does not verify%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a bad marker was accepted'; END IF;
        -- An arm reference never changes status, so it never drops.
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_refs SET status = 'pool' WHERE id = v_arm;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%arm reference never changes status%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, status, origin_kind)
            VALUES ('selftest_059_mismatch', 'sports', 'x title', 'An origin long enough.', 'A meaning.', 'A line.',
                    'pitch', 'A work', 'wikipedia', 'arm', 'pool');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 arm-status refusals', v_n; END IF;
        -- A drop holding an arm card is refused (test rows: two pool refs and the test arm card).
        FOR v_n IN 1..2 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification, origin_kind,
                                             is_test)
            VALUES ('selftest_059_t' || v_n, (ARRAY['finance_business', 'film_tv'])[v_n], 'Selftest t' || v_n,
                    'An origin long enough.', 'A meaning.', 'A line to deploy.', 'boardroom', 'A work', 'wikipedia',
                    'https://selftest.invalid/t' || v_n, 'pool', 'verified', 'pool', true)
            RETURNING id INTO v_id;
            v_r := v_r || v_id;
        END LOOP;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest word 059', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'pitch',
                'A sentence with selftest word 059 in it.', 'verified', true)
        RETURNING id INTO v_w;
        v_ok := false;
        BEGIN
            INSERT INTO public.arsenal_drops (drop_date, ref_ids, word_id, is_test)
            VALUES ('2000-02-01', ARRAY[v_r[1], v_r[2], v_tarm], v_w, true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%only a pool reference drops%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a drop took an arm card'; END IF;

        -- ------------------------------------------------- 2. arm_requests
        v_id := public.arsenal_arm_record('A selftest situation.', 'pitch', 'claude-sonnet-5-5', 900, 4200, NULL,
                                          ARRAY[v_arm], NULL, NULL);
        IF (SELECT is_test FROM public.arm_requests WHERE id = v_id)
           OR (SELECT owner_id FROM public.arm_requests WHERE id = v_id) <> v_owner
           OR public.arsenal_arm_requests_since(60) < 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a real request row (or the rate count)';
        END IF;
        IF NOT (SELECT is_test FROM public.arm_requests
                 WHERE id = public.arsenal_arm_record('A test one.', NULL, 'mock', 10, 20, NULL, ARRAY[v_tarm],
                                                      NULL, NULL, v_marker)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a marked request is not a test one';
        END IF;
        v_n := 0;
        BEGIN
            PERFORM public.arsenal_arm_record('Mixed.', NULL, 'mock', 1, 2, NULL, ARRAY[v_arm, v_tarm], NULL, NULL);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%real request shows real references only%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arm_requests SET total_ms = 1 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%append-only%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            PERFORM public.arsenal_arm_record(repeat('x', 10), 'kitchen', 'mock', 1, 2, NULL, '{}', NULL, NULL);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 arm_requests refusals', v_n; END IF;

        -- ------------------------------------------------- 3. saves, the verify queue, weights
        SELECT weight INTO v_before FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'history_military';
        INSERT INTO public.arsenal_saves (owner_id, ref_id, action) VALUES (v_owner, v_arm, 'save');
        IF (SELECT count(*) FROM public.job_queue WHERE kind = 'arsenal_arm_verify' AND payload ->> 'ref_id' = v_arm::text) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a save of a pending arm card did not queue exactly one verify';
        END IF;
        SELECT weight INTO v_after FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'history_military';
        IF v_before < 1.7 AND abs(v_after - v_before - 0.30) > 0.001 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a save did not count as two Loves (% -> %)', v_before, v_after;
        END IF;
        INSERT INTO public.arsenal_saves (owner_id, ref_id, action) VALUES (v_owner, v_arm, 'unsave');
        IF (SELECT weight FROM public.arsenal_weights() WHERE dim = 'category' AND key = 'history_military') <> v_before THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unsave did not take the save back';
        END IF;
        INSERT INTO public.arsenal_saves (owner_id, ref_id, action) VALUES (v_owner, v_arm, 'save');
        IF (SELECT count(*) FROM public.job_queue WHERE kind = 'arsenal_arm_verify' AND payload ->> 'ref_id' = v_arm::text) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: save, unsave, save queued a second verify';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.v_arsenal_saved WHERE ref_id = v_arm AND NOT is_test) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_arsenal_saved does not show the save';
        END IF;
        v_n := 0;
        BEGIN
            INSERT INTO public.arsenal_saves (owner_id, ref_id, action) VALUES (v_owner, v_arm, 'save');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%already saved%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            INSERT INTO public.arsenal_saves (owner_id, word_id, action) VALUES (v_owner, v_w, 'unsave');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%not saved%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            INSERT INTO public.arsenal_saves (owner_id, ref_id, word_id, action) VALUES (v_owner, v_arm, v_w, 'save');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_saves WHERE ref_id = v_arm;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%append-only%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 save refusals', v_n; END IF;
        -- A save of a pool reference (not arm) queues nothing; a test save is test.
        v_pool := v_r[1];
        v_id := public.add_test_save(v_marker, v_pool);
        IF NOT (SELECT is_test FROM public.arsenal_saves WHERE id = v_id)
           OR EXISTS (SELECT 1 FROM public.job_queue WHERE kind = 'arsenal_arm_verify' AND payload ->> 'ref_id' = v_pool::text) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test save on a pool reference (is_test, no verify job)';
        END IF;
        PERFORM public.add_test_save(v_marker, v_tarm);
        IF (SELECT count(*) FROM public.job_queue WHERE kind = 'arsenal_arm_verify' AND payload ->> 'ref_id' = v_tarm::text) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test save of a test arm card did not queue its verify';
        END IF;
        v_ok := false;
        BEGIN
            PERFORM public.add_test_save(v_marker, v_arm);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%test save is on a test%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: add_test_save took a real target'; END IF;
        v_id := public.add_test_arm_ref(v_marker, 'Selftest helper arm');
        IF (SELECT status || '/' || verification || '/' || is_test FROM public.arsenal_refs WHERE id = v_id) <> 'arm/pending/true' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: add_test_arm_ref';
        END IF;

        -- ------------------------------------------------- 4. the quiz line
        -- Test saves so far: v_pool, v_tarm (2). Two more make 4.
        PERFORM public.add_test_save(v_marker, v_r[2]);
        PERFORM public.add_test_save(v_marker, NULL, v_w);
        IF public.arsenal_quiz_line(v_monday, true) IS NULL
           OR (public.arsenal_quiz_line(v_monday, true) ->> 'questions')::int
              <> (SELECT least(10, 2 * count(*) FILTER (WHERE ref_id IS NOT NULL) + count(*) FILTER (WHERE word_id IS NOT NULL))
                    FROM public.v_arsenal_saved WHERE is_test)
           OR public.arsenal_quiz_line(v_monday + 1, true) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the quiz line (Monday %, got %)', v_monday, public.arsenal_quiz_line(v_monday, true);
        END IF;

        -- ------------------------------------------------- 5. quizzes and reviews (test rows)
        INSERT INTO public.arsenal_quizzes (owner_id, week_start, questions)
        VALUES (v_owner, v_monday, jsonb_build_array(
            jsonb_build_object('no', 1, 'kind', 'fits', 'ref_id', v_pool, 'prompt', 'A meaning.',
                               'options', jsonb_build_array('A', 'B', 'C', 'D'), 'answer', 2),
            jsonb_build_object('no', 2, 'kind', 'sentence', 'word_id', v_w, 'prompt', 'selftest word 059',
                               'options', jsonb_build_array('S1', 'S2', 'S3', 'S4'), 'answer', 0)))
        RETURNING id INTO v_quiz;
        IF NOT (SELECT is_test FROM public.arsenal_quizzes WHERE id = v_quiz) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a quiz on test items is not a test quiz';
        END IF;
        v_n := 0;
        BEGIN  -- not a Monday
            INSERT INTO public.arsenal_quizzes (owner_id, week_start, questions)
            VALUES (v_owner, v_monday + 1, jsonb_build_array(jsonb_build_object('no', 1, 'kind', 'fits', 'ref_id', v_pool,
                    'prompt', 'x', 'options', jsonb_build_array('A', 'B', 'C', 'D'), 'answer', 0)));
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- an unsaved target (v_arm was saved real; v_r[1] is saved; the helper arm is not)
            INSERT INTO public.arsenal_quizzes (owner_id, week_start, questions)
            VALUES (v_owner, v_monday, jsonb_build_array(jsonb_build_object('no', 1, 'kind', 'means', 'ref_id', v_id,
                    'prompt', 'x', 'options', jsonb_build_array('A', 'B', 'C', 'D'), 'answer', 0)));
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%not saved%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- three options
            INSERT INTO public.arsenal_quizzes (owner_id, week_start, questions)
            VALUES (v_owner, v_monday, jsonb_build_array(jsonb_build_object('no', 1, 'kind', 'fits', 'ref_id', v_pool,
                    'prompt', 'x', 'options', jsonb_build_array('A', 'B', 'C'), 'answer', 0)));
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%malformed%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- real and test mixed (v_arm is a real saved card)
            INSERT INTO public.arsenal_quizzes (owner_id, week_start, questions)
            VALUES (v_owner, v_monday, jsonb_build_array(
                jsonb_build_object('no', 1, 'kind', 'fits', 'ref_id', v_pool, 'prompt', 'x',
                                   'options', jsonb_build_array('A', 'B', 'C', 'D'), 'answer', 0),
                jsonb_build_object('no', 2, 'kind', 'fits', 'ref_id', v_arm, 'prompt', 'x',
                                   'options', jsonb_build_array('A', 'B', 'C', 'D'), 'answer', 0)));
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%all real or all test%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 quiz refusals', v_n; END IF;

        v_n := 0;
        BEGIN  -- correct disagrees with the stored answer (2)
            INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                     correct, rating, fsrs_state, due_at)
            VALUES (v_owner, v_quiz, 1, v_monday, 'fits', 1, true, 'good', v_state, '2026-11-01T00:00:00Z');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%disagrees with the stored answer%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- a wrong answer rated good
            INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                     correct, rating, fsrs_state, due_at)
            VALUES (v_owner, v_quiz, 1, v_monday, 'fits', 1, false, 'good', v_state, '2026-11-01T00:00:00Z');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%rated good%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- due_at not the card's due
            INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                     correct, rating, fsrs_state, due_at)
            VALUES (v_owner, v_quiz, 1, v_monday, 'fits', 2, true, 'good', v_state, '2026-11-02T00:00:00Z');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%card''s due%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- the wrong kind for the question
            INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                     correct, rating, fsrs_state, due_at)
            VALUES (v_owner, v_quiz, 1, v_monday, 'means', 2, true, 'good', v_state, '2026-11-01T00:00:00Z');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%target or kind%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 review refusals', v_n; END IF;
        INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                 correct, rating, fsrs_state, due_at)
        VALUES (v_owner, v_quiz, 1, v_monday, 'fits', 2, true, 'good', v_state, '2026-11-01T00:00:00Z')
        RETURNING id INTO v_id;
        IF (SELECT ref_id FROM public.arsenal_quiz_reviews WHERE id = v_id) <> v_pool
           OR NOT (SELECT is_test FROM public.arsenal_quiz_reviews WHERE id = v_id)
           OR NOT EXISTS (SELECT 1 FROM public.v_arsenal_quiz_attempts WHERE id = v_id AND correct) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a review (target, is_test, the attempts view)';
        END IF;
        v_n := 0;
        BEGIN
            INSERT INTO public.arsenal_quiz_reviews (owner_id, quiz_id, question_no, week_start, question_kind, chosen,
                                                     correct, rating, fsrs_state, due_at)
            VALUES (v_owner, v_quiz, 1, v_monday, 'fits', 0, false, 'again', v_state, '2026-11-01T00:00:00Z');
        EXCEPTION WHEN unique_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.arsenal_quiz_reviews SET chosen = 0 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%append-only%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            DELETE FROM public.arsenal_quizzes WHERE id = v_quiz;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%append-only%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 append-only quiz refusals', v_n; END IF;

        -- ------------------------------------------------- 6. context: test rows invisible, real ones counted
        v_ctx := public.ctx_arsenal();
        IF (v_ctx ->> 'saved_count')::int <> (SELECT count(*) FROM public.v_arsenal_saved WHERE NOT is_test)
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'recent_saves') x WHERE x ->> 'title' = 'Selftest arm')
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx -> 'recent_saves') x WHERE x ->> 'title' LIKE 'Selftest t%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_arsenal saves (got %)', v_ctx -> 'recent_saves';
        END IF;
        IF v_ctx -> 'last_quiz' IS NOT NULL AND (v_ctx -> 'last_quiz' ->> 'week_start')::date = v_monday
           AND NOT EXISTS (SELECT 1 FROM public.arsenal_quizzes WHERE NOT is_test AND week_start = v_monday) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_arsenal reported a test quiz';
        END IF;
        PERFORM public.ctx_arsenal_brief();

        -- ------------------------------------------------- 7. TRUNCATE (every referencing table named, db/042's lesson)
        v_n := 0;
        BEGIN
            TRUNCATE public.arm_requests;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            TRUNCATE public.arsenal_saves;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            TRUNCATE public.arsenal_quizzes, public.arsenal_quiz_reviews;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            TRUNCATE public.arsenal_quiz_reviews;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%TRUNCATE is refused%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 TRUNCATE refusals', v_n; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '059 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- SELECT name, applied_at FROM migration_log WHERE name = '059_arsenal_phase2';
-- SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = 'public.arsenal_refs'::regclass AND conname IN ('arsenal_refs_status_check',
--        'arsenal_refs_origin_kind_check', 'arsenal_refs_arm');
-- SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname),
--        has_table_privilege('anon', c.oid, 'SELECT'), has_table_privilege('authenticated', c.oid, 'INSERT'),
--        has_table_privilege('authenticated', c.oid, 'UPDATE,DELETE,TRUNCATE'),
--        has_table_privilege('valemont_readonly', c.oid, 'SELECT')
--   FROM pg_class c WHERE c.relname IN ('arm_requests', 'arsenal_saves', 'arsenal_quizzes', 'arsenal_quiz_reviews',
--        'v_arsenal_saved', 'v_arsenal_quiz_attempts');
-- SELECT count(*) FROM arm_requests; SELECT count(*) FROM arsenal_saves;  -- 0 and 0
