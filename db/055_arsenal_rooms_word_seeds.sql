-- 055_arsenal_rooms_word_seeds.sql — The Arsenal: rooms rotate in the
-- picker, and the owner's example words become seeds (Chat 5 Phase 1
-- gate; owner, 2026-10-16).
--
-- What it changes:
--   1. arsenal_words.status gains 'seed' (the refs' mechanism: taste only,
--      never in a drop). A word enters as pool or seed; a pool word may
--      become seed, dropped or rejected; any other word never changes
--      status (db/054's guard, otherwise unchanged).
--   2. The owner's example words, asymmetric, inflection, provenance and
--      fiduciary, are known lemmas: the three still in the pool become
--      'seed'. asymmetric already dropped (Oct 16, drop 61) and stays as it
--      is; every lemma is UNIQUE, so none of the four can be written again.
--   3. arsenal_pick_drop: rooms rotate like categories. The categories are
--      drawn as before; then, of the eligible references in them (at most
--      12 per category, by the room-weighted draw), the picker takes the
--      three with the most distinct rooms, then the fewest rooms used
--      yesterday, then the draw. Slots: no slot repeats yesterday's
--      category (as before) and, when an ordering allows it, nor yesterday's
--      room. The Power Word's room is the one the references use least (a
--      room none of them uses when one exists): it differs from the
--      majority whenever the pool allows. Held items are skipped (5).
--   4. ONE re-verification (owner, 2026-10-16): a POOL reference or word
--      whose verification is 'unverified' may be settled again exactly
--      once, only through arsenal_reverify_ref / arsenal_reverify_word
--      (the worker's; nobody else executes them), and every one is recorded
--      in arsenal_reverifications (APPEND-ONLY: the target, old -> new
--      verification and note, who, the database role, when; UNIQUE per
--      target, so never twice). Everything else in db/054's "verification
--      settles once" stands.
--   5. arsenal_holds (APPEND-ONLY, TRUNCATE-guarded): a reference or a word
--      held out of every drop, with a snake_case reason ('outcome_conflict':
--      its real ending undercuts its line) and a detail. The picker never
--      takes a held item. A hold is permanent.
--   6. Item 11's results, written through (4): 206 Moneyball corrected (the
--      book's page), 216 "What is up to us" verified (the Enchiridion's
--      page). The other six unverified references stay unverified.
--
-- PASTE BEFORE THE MERGE of the follow-up PR: the worker's pool status
-- reads arsenal_holds.
-- A self-test runs on paste (section 8), rolled back.
-- Prerequisites: db/054.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Word seeds.

ALTER TABLE arsenal_words DROP CONSTRAINT IF EXISTS arsenal_words_status_check;
ALTER TABLE arsenal_words ADD CONSTRAINT arsenal_words_status_check
    CHECK (status IN ('seed', 'pool', 'dropped', 'rejected'));

CREATE OR REPLACE FUNCTION public.arsenal_words_guard()
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
        IF NEW.status NOT IN ('pool', 'seed') THEN
            RAISE EXCEPTION 'arsenal_words: a word enters as pool or seed, never %', NEW.status;
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
    -- The one re-verification (section 4): only inside arsenal_reverify_word,
    -- for an unverified pool word, matching the row it just recorded.
    v_rev := OLD.status = 'pool' AND OLD.verification = 'unverified' AND NEW.status = OLD.status
             AND coalesce(current_setting('arsenal.reverify', true), '') = 'word:' || OLD.id
             AND EXISTS (SELECT 1 FROM public.arsenal_reverifications v
                          WHERE v.word_id = OLD.id AND v.new_verification = NEW.verification
                            AND v.new_note IS NOT DISTINCT FROM NEW.verification_note);
    IF v_rev THEN
        IF NEW.verification = 'pending' THEN
            RAISE EXCEPTION 'arsenal_words: a re-verification settles; it never returns to pending';
        END IF;
        NEW.verified_at := now();
        RETURN NEW;
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
        IF NEW.status NOT IN ('dropped', 'rejected', 'seed') THEN
            RAISE EXCEPTION 'arsenal_words: a pool word becomes dropped, rejected or seed, never %', NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 2
-- The owner's example words are known.

UPDATE arsenal_words SET status = 'seed'
 WHERE NOT is_test AND status = 'pool' AND lemma IN ('asymmetric', 'inflection', 'provenance', 'fiduciary');

-- ===================================================================== 3
-- ONE re-verification, recorded append-only.

CREATE TABLE IF NOT EXISTS arsenal_reverifications (
    id                BIGSERIAL    PRIMARY KEY,
    ref_id            BIGINT       REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    word_id           BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    old_verification  TEXT         NOT NULL CHECK (old_verification = 'unverified'),
    new_verification  TEXT         NOT NULL CHECK (new_verification IN ('verified', 'corrected', 'unverified')),
    old_note          TEXT,
    new_note          TEXT,
    -- Who asked for it, in words ("owner, 2026-10-16: item 11"), and the
    -- database role that wrote it.
    by_whom           TEXT         NOT NULL CHECK (char_length(btrim(by_whom)) BETWEEN 3 AND 200),
    db_role           TEXT         NOT NULL DEFAULT current_user,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_reverifications_one_target CHECK (num_nonnulls(ref_id, word_id) = 1)
);
-- Once per target, ever.
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_reverifications_once_ref ON arsenal_reverifications (ref_id)
    WHERE ref_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS arsenal_reverifications_once_word ON arsenal_reverifications (word_id)
    WHERE word_id IS NOT NULL;

DROP TRIGGER IF EXISTS arsenal_reverifications_stamp ON arsenal_reverifications;
CREATE TRIGGER arsenal_reverifications_stamp
    BEFORE INSERT ON arsenal_reverifications
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS arsenal_reverifications_immutable ON arsenal_reverifications;
CREATE TRIGGER arsenal_reverifications_immutable
    BEFORE UPDATE OR DELETE ON arsenal_reverifications
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_reverifications_no_truncate ON arsenal_reverifications;
CREATE TRIGGER arsenal_reverifications_no_truncate
    BEFORE TRUNCATE ON arsenal_reverifications
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- db/054's reference guard, with the one re-verification branch.
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
    -- The one re-verification (section 3): only inside arsenal_reverify_ref,
    -- for an unverified pool reference, matching the row it just recorded.
    v_rev := OLD.status = 'pool' AND OLD.verification = 'unverified' AND NEW.status = OLD.status
             AND coalesce(current_setting('arsenal.reverify', true), '') = 'ref:' || OLD.id
             AND EXISTS (SELECT 1 FROM public.arsenal_reverifications v
                          WHERE v.ref_id = OLD.id AND v.new_verification = NEW.verification
                            AND v.new_note IS NOT DISTINCT FROM NEW.verification_note);
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

-- Re-verify one unverified pool reference, once, recorded. Returns the
-- record's id. Raises for anything else (already re-verified, not
-- unverified, not in the pool, no "who").
CREATE OR REPLACE FUNCTION public.arsenal_reverify_ref(p_ref_id bigint, p_verification text, p_note text, p_by text,
                                                       p_source_url text DEFAULT NULL, p_origin text DEFAULT NULL,
                                                       p_source_work text DEFAULT NULL, p_meaning text DEFAULT NULL,
                                                       p_deploy_line text DEFAULT NULL, p_drop_quote boolean DEFAULT false)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_status text;
    v_old    text;
    v_note   text;
    v_id     bigint;
BEGIN
    SELECT r.status, r.verification, r.verification_note INTO v_status, v_old, v_note
      FROM public.arsenal_refs r WHERE r.id = p_ref_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'arsenal_reverify_ref: no reference %', p_ref_id;
    END IF;
    IF v_status <> 'pool' OR v_old <> 'unverified' THEN
        RAISE EXCEPTION 'arsenal_reverify_ref: only an unverified pool reference is re-verified (% is %, %)',
            p_ref_id, v_status, v_old;
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_reverifications v WHERE v.ref_id = p_ref_id) THEN
        RAISE EXCEPTION 'arsenal_reverify_ref: reference % was already re-verified once', p_ref_id;
    END IF;
    INSERT INTO public.arsenal_reverifications (ref_id, old_verification, new_verification, old_note, new_note, by_whom)
    VALUES (p_ref_id, v_old, p_verification, v_note, p_note, p_by)
    RETURNING id INTO v_id;
    PERFORM set_config('arsenal.reverify', 'ref:' || p_ref_id, true);
    UPDATE public.arsenal_refs
       SET verification = p_verification, verification_note = p_note,
           source_url = coalesce(p_source_url, source_url), origin = coalesce(p_origin, origin),
           source_work = coalesce(p_source_work, source_work), meaning = coalesce(p_meaning, meaning),
           deploy_line = coalesce(p_deploy_line, deploy_line),
           quote = CASE WHEN p_drop_quote THEN NULL ELSE quote END,
           quote_by = CASE WHEN p_drop_quote THEN NULL ELSE quote_by END
     WHERE id = p_ref_id;
    PERFORM set_config('arsenal.reverify', '', true);
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.arsenal_reverify_word(p_word_id bigint, p_verification text, p_note text, p_by text,
                                                        p_pronunciation text DEFAULT NULL, p_etymology text DEFAULT NULL,
                                                        p_etymology_url text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_status text;
    v_old    text;
    v_note   text;
    v_id     bigint;
BEGIN
    SELECT w.status, w.verification, w.verification_note INTO v_status, v_old, v_note
      FROM public.arsenal_words w WHERE w.id = p_word_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'arsenal_reverify_word: no word %', p_word_id;
    END IF;
    IF v_status <> 'pool' OR v_old <> 'unverified' THEN
        RAISE EXCEPTION 'arsenal_reverify_word: only an unverified pool word is re-verified (% is %, %)',
            p_word_id, v_status, v_old;
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_reverifications v WHERE v.word_id = p_word_id) THEN
        RAISE EXCEPTION 'arsenal_reverify_word: word % was already re-verified once', p_word_id;
    END IF;
    INSERT INTO public.arsenal_reverifications (word_id, old_verification, new_verification, old_note, new_note, by_whom)
    VALUES (p_word_id, v_old, p_verification, v_note, p_note, p_by)
    RETURNING id INTO v_id;
    PERFORM set_config('arsenal.reverify', 'word:' || p_word_id, true);
    UPDATE public.arsenal_words
       SET verification = p_verification, verification_note = p_note,
           pronunciation = coalesce(p_pronunciation, pronunciation), etymology = coalesce(p_etymology, etymology),
           etymology_url = coalesce(p_etymology_url, etymology_url)
     WHERE id = p_word_id;
    PERFORM set_config('arsenal.reverify', '', true);
    RETURN v_id;
END;
$$;

-- ===================================================================== 4
-- Holds: out of every drop, permanently, with the reason.

CREATE TABLE IF NOT EXISTS arsenal_holds (
    id          BIGSERIAL    PRIMARY KEY,
    ref_id      BIGINT       REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    word_id     BIGINT       REFERENCES arsenal_words (id) ON DELETE RESTRICT,
    -- snake_case: 'outcome_conflict' (item 5).
    reason      TEXT         NOT NULL CHECK (reason ~ '^[a-z][a-z0-9_]{1,59}$'),
    -- The check's one sentence.
    detail      TEXT         CHECK (detail IS NULL OR char_length(detail) <= 500),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT arsenal_holds_one_target CHECK (num_nonnulls(ref_id, word_id) = 1)
);
CREATE INDEX IF NOT EXISTS arsenal_holds_ref ON arsenal_holds (ref_id) WHERE ref_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS arsenal_holds_word ON arsenal_holds (word_id) WHERE word_id IS NOT NULL;

DROP TRIGGER IF EXISTS arsenal_holds_stamp ON arsenal_holds;
CREATE TRIGGER arsenal_holds_stamp
    BEFORE INSERT ON arsenal_holds
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS arsenal_holds_immutable ON arsenal_holds;
CREATE TRIGGER arsenal_holds_immutable
    BEFORE UPDATE OR DELETE ON arsenal_holds
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_holds_no_truncate ON arsenal_holds;
CREATE TRIGGER arsenal_holds_no_truncate
    BEFORE TRUNCATE ON arsenal_holds
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 5
-- The picker.

CREATE OR REPLACE FUNCTION public.arsenal_pick_drop(p_date date, p_with_tests boolean DEFAULT false,
                                                    p_topped_up boolean DEFAULT false)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_id      bigint;
    v_y       text[];
    v_yrooms  text[];
    v_cats    text[];
    v_ids     bigint[];
    v_rooms   text[];
    v_ncats   int;
    v_word    bigint;
    -- The six orderings of three slots, in preference order.
    v_order   int[] := ARRAY[[1, 2, 3], [1, 3, 2], [2, 1, 3], [2, 3, 1], [3, 1, 2], [3, 2, 1]];
    v_pick    int := 0;
    v_ok      boolean;
    v_pass    int;
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

    -- Yesterday's categories and rooms, by slot (the latest drop of the same kind).
    SELECT array_agg(r.category ORDER BY s.ord), array_agg(r.room ORDER BY s.ord) INTO v_y, v_yrooms
      FROM (SELECT d.ref_ids FROM public.arsenal_drops d
             WHERE d.drop_date = p_date - 1 AND d.is_test = p_with_tests
             ORDER BY d.id DESC LIMIT 1) y
      CROSS JOIN LATERAL unnest(y.ref_ids) WITH ORDINALITY AS s(ref_id, ord)
      JOIN public.arsenal_refs r ON r.id = s.ref_id;

    -- Three categories by weight (one draw each: -ln(u)/w, smallest wins);
    -- then the trio of references in them with the most distinct rooms.
    WITH w AS MATERIALIZED (
        SELECT * FROM public.arsenal_weights()
    ),
    latest AS (
        SELECT DISTINCT ON (x.ref_id) x.ref_id, x.reaction
          FROM public.arsenal_reactions x
         WHERE x.ref_id IS NOT NULL AND x.is_test = p_with_tests
         ORDER BY x.ref_id, x.created_at DESC, x.id DESC
    ),
    elig AS MATERIALIZED (
        SELECT r.id, r.category, r.room
          FROM public.arsenal_refs r
         WHERE r.is_test = p_with_tests AND r.status = 'pool' AND r.verification IN ('verified', 'corrected')
           AND NOT EXISTS (SELECT 1 FROM latest l WHERE l.ref_id = r.id AND l.reaction IN ('known', 'not_interested'))
           AND NOT EXISTS (SELECT 1 FROM public.arsenal_holds h WHERE h.ref_id = r.id)
    ),
    cats AS MATERIALIZED (
        SELECT c.category, -ln(1 - random()) / greatest(cw.weight, 0.0001) AS k
          FROM (SELECT DISTINCT e.category FROM elig e) c
          JOIN w cw ON cw.dim = 'category' AND cw.key = c.category
         ORDER BY 2
         LIMIT 3
    ),
    -- Each eligible reference in a drawn category, with its room-weighted draw.
    cand AS MATERIALIZED (
        SELECT e.id, e.category, e.room, -ln(1 - random()) / greatest(rw.weight, 0.0001) AS k
          FROM elig e
          JOIN cats c ON c.category = e.category
          JOIN w rw ON rw.dim = 'room' AND rw.key = e.room
    ),
    ranked AS MATERIALIZED (
        SELECT c.*, row_number() OVER (PARTITION BY c.category ORDER BY c.k) AS rn FROM cand c
    ),
    best AS (
        SELECT a.category AS a_cat, a.id AS a_id, a.room AS a_room,
               b.category AS b_cat, b.id AS b_id, b.room AS b_room,
               c.category AS c_cat, c.id AS c_id, c.room AS c_room
          FROM ranked a
          JOIN ranked b ON b.category > a.category
          JOIN ranked c ON c.category > b.category
         WHERE a.rn <= 12 AND b.rn <= 12 AND c.rn <= 12
         ORDER BY (CASE WHEN a.room = b.room AND b.room = c.room THEN 1
                        WHEN a.room = b.room OR b.room = c.room OR a.room = c.room THEN 2
                        ELSE 3 END) DESC,
                  ((a.room = ANY (coalesce(v_yrooms, '{}')))::int + (b.room = ANY (coalesce(v_yrooms, '{}')))::int
                   + (c.room = ANY (coalesce(v_yrooms, '{}')))::int),
                  a.k + b.k + c.k
         LIMIT 1
    ),
    slots AS (
        SELECT u.cat, u.id, u.room
          FROM best b
          CROSS JOIN LATERAL (VALUES (b.a_cat, b.a_id, b.a_room), (b.b_cat, b.b_id, b.b_room),
                                     (b.c_cat, b.c_id, b.c_room)) AS u(cat, id, room)
    )
    SELECT array_agg(s.cat ORDER BY c.k), array_agg(s.id ORDER BY c.k), array_agg(s.room ORDER BY c.k),
           (SELECT count(*) FROM cats)
      INTO v_cats, v_ids, v_rooms, v_ncats
      FROM slots s JOIN cats c ON c.category = s.cat;

    IF coalesce(cardinality(v_ids), 0) < 3 THEN
        RAISE EXCEPTION 'arsenal_pick_drop: the verified pool is short (% categories with an eligible reference; 3 needed)',
            coalesce(v_ncats, 0);
    END IF;

    -- The Power Word: the room the references use least, then at random.
    SELECT wd.id INTO v_word
      FROM public.arsenal_words wd
     WHERE wd.is_test = p_with_tests AND wd.status = 'pool' AND wd.verification IN ('verified', 'corrected')
       AND NOT EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (x.word_id) x.reaction
                                        FROM public.arsenal_reactions x WHERE x.word_id = wd.id
                                       ORDER BY x.word_id, x.created_at DESC, x.id DESC) lw
                        WHERE lw.reaction IN ('known', 'not_interested'))
       AND NOT EXISTS (SELECT 1 FROM public.arsenal_holds h WHERE h.word_id = wd.id)
     ORDER BY (SELECT count(*) FROM unnest(v_rooms) AS rm WHERE rm = wd.room), random()
     LIMIT 1;
    IF v_word IS NULL THEN
        RAISE EXCEPTION 'arsenal_pick_drop: the verified pool is short (no eligible word)';
    END IF;

    -- Slot order: pass 1 wants no slot repeating yesterday's category or
    -- room; pass 2 only the category (with three distinct categories an
    -- ordering always exists).
    FOR v_pass IN 1..2 LOOP
        FOR p IN 1..6 LOOP
            v_ok := true;
            FOR i IN 1..3 LOOP
                IF v_y IS NOT NULL AND cardinality(v_y) >= i AND v_cats[v_order[p][i]] = v_y[i] THEN
                    v_ok := false;
                END IF;
                IF v_pass = 1 AND v_yrooms IS NOT NULL AND cardinality(v_yrooms) >= i
                   AND v_rooms[v_order[p][i]] = v_yrooms[i] THEN
                    v_ok := false;
                END IF;
            END LOOP;
            IF v_ok THEN
                v_pick := p;
                EXIT;
            END IF;
        END LOOP;
        EXIT WHEN v_pick > 0;
    END LOOP;
    IF v_pick = 0 THEN
        v_pick := 1;
    END IF;

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

-- ===================================================================== 6
-- Access: the owner reads both new tables; the worker writes them
-- (DATABASE_URL bypasses RLS); the re-verify functions are the worker's.

ALTER TABLE arsenal_reverifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE arsenal_holds ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON arsenal_reverifications, arsenal_holds FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE arsenal_reverifications_id_seq, arsenal_holds_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON arsenal_reverifications, arsenal_holds TO authenticated;
DROP POLICY IF EXISTS arsenal_reverifications_owner_read ON arsenal_reverifications;
CREATE POLICY arsenal_reverifications_owner_read ON arsenal_reverifications FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS arsenal_holds_owner_read ON arsenal_holds;
CREATE POLICY arsenal_holds_owner_read ON arsenal_holds FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON arsenal_reverifications, arsenal_holds TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arsenal_refs_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_words_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_pick_drop(date, boolean, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_reverify_ref(bigint, text, text, text, text, text, text, text, text, boolean)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_reverify_word(bigint, text, text, text, text, text, text)
    FROM PUBLIC, anon, authenticated;

-- ===================================================================== 7
-- Item 11 (owner, 2026-10-16): the dry run's two clears, written once.
-- Each only while the card is still the unverified pool card the dry run
-- read; the self-test (section 8) fails the paste if either is missing.

SELECT public.arsenal_reverify_ref(
    206, 'corrected',
    're-verified once (owner, 2026-10-16, item 11; source: Moneyball: The Art of Winning an Unfair Game): '
    || 'corrected by the source: a $44 million payroll and the 2002 playoffs; the book''s full title. '
    || 'The 20-game streak was not in the excerpt and was left out.',
    'owner, 2026-10-16: item 11 (scripts.arsenal_reverify dry run; db/055)',
    'https://en.wikipedia.org/wiki/Moneyball%3A_The_Art_of_Winning_an_Unfair_Game',
    'Billy Beane''s Oakland A''s used sabermetrics to find undervalued players and reached the 2002 playoffs '
    || 'on a $44 million payroll, against the Yankees'' $125 million.',
    'Moneyball: The Art of Winning an Unfair Game (Michael Lewis, 2003)')
 WHERE EXISTS (SELECT 1 FROM public.arsenal_refs
                WHERE id = 206 AND canonical_key = 'moneyball_oakland' AND status = 'pool'
                  AND verification = 'unverified' AND NOT is_test);

SELECT public.arsenal_reverify_ref(
    216, 'verified',
    're-verified once (owner, 2026-10-16, item 11; source: Enchiridion of Epictetus): supported.',
    'owner, 2026-10-16: item 11 (scripts.arsenal_reverify dry run; db/055)',
    'https://en.wikipedia.org/wiki/Enchiridion_of_Epictetus')
 WHERE EXISTS (SELECT 1 FROM public.arsenal_refs
                WHERE id = 216 AND canonical_key = 'epictetus_what_is_up_to_us' AND status = 'pool'
                  AND verification = 'unverified' AND NOT is_test);

-- ===================================================================== 8
-- Self-test (rolled back): word seeds, re-verification, holds, then the
-- picker's rooms, on TEST rows only. Every other eligible test row (e2e
-- leftovers) is first marked 'known' inside the rolled-back block, so the
-- picker sees only these.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_w      bigint;
    v_w2     bigint;
    v_ids    bigint[];
    v_drop   bigint;
    v_rooms  text[];
    v_ok     boolean;
    v_n      int;
    v_r      bigint;
    v_r2     bigint;
    i        int;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the example words
        IF EXISTS (SELECT 1 FROM public.arsenal_words WHERE NOT is_test AND status = 'pool'
                     AND lemma IN ('asymmetric', 'inflection', 'provenance', 'fiduciary')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an example word is still in the pool';
        END IF;

        -- ---------------------------------------------------- 2. the word guard
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 055 a', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'pitch',
                'A sentence.', 'verified', true)
        RETURNING id INTO v_w;
        UPDATE public.arsenal_words SET status = 'seed' WHERE id = v_w;
        v_n := 0;
        BEGIN
            UPDATE public.arsenal_words SET status = 'pool' WHERE id = v_w;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%a seed word never changes status%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            INSERT INTO public.arsenal_words (lemma, meaning, room, sentence, status, is_test)
            VALUES ('selftest 055 b', 'A meaning.', 'pitch', 'A sentence.', 'dropped', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%enters as pool or seed%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the word guard (% of 2 refusals)', v_n;
        END IF;

        -- ---------------------------------------------------- 3. only my rows are eligible
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction)
        SELECT v_owner, r.id, 'known' FROM public.arsenal_refs r
         WHERE r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected');
        INSERT INTO public.arsenal_reactions (owner_id, word_id, reaction)
        SELECT v_owner, w.id, 'known' FROM public.arsenal_words w
         WHERE w.is_test AND w.status = 'pool' AND w.verification IN ('verified', 'corrected');

        -- ---------------------------------------------------- 4. a seed word is never picked
        -- Three categories, each with a boardroom reference and one other room.
        FOR i IN 1..3 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification,
                                             origin_kind, is_test)
            SELECT 'selftest_055_' || i || '_' || rm, (ARRAY['finance_business', 'sports', 'film_tv'])[i],
                   'Selftest ' || i || ' ' || rm, 'An origin long enough.', 'A meaning.', 'A line to deploy.', rm,
                   'A work', 'wikipedia', 'https://selftest.invalid/r', 'pool', 'verified', 'pool', true
              FROM unnest(ARRAY['boardroom', (ARRAY['pitch', 'negotiation', 'homies'])[i]]) AS rm;
        END LOOP;
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-05-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%no eligible word%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a seed word was eligible';
        END IF;

        -- ---------------------------------------------------- 5. three distinct rooms
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 055 c', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'homies',
                'A sentence.', 'verified', true);
        v_drop := public.arsenal_pick_drop('2001-05-01', true);
        SELECT array_agg(r.room) INTO v_rooms
          FROM public.arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x JOIN public.arsenal_refs r ON r.id = x
         WHERE d.id = v_drop;
        IF (SELECT count(DISTINCT rm) FROM unnest(v_rooms) rm) <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the rooms were not distinct: %', v_rooms;
        END IF;

        -- ---------------------------------------------------- 6. the word's room differs from the majority
        -- Only boardroom references are left; a boardroom and a pitch word.
        FOR i IN 1..3 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification,
                                             origin_kind, is_test)
            VALUES ('selftest_055_more_' || i, (ARRAY['finance_business', 'sports', 'film_tv'])[i],
                    'Selftest more ' || i, 'An origin long enough.', 'A meaning.', 'A line to deploy.', 'boardroom',
                    'A work', 'wikipedia', 'https://selftest.invalid/r', 'pool', 'verified', 'pool', true);
        END LOOP;
        -- The leftover boardroom refs of section 4 are still eligible; mark them known.
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction)
        SELECT v_owner, r.id, 'known' FROM public.arsenal_refs r
         WHERE r.is_test AND r.status = 'pool' AND r.canonical_key LIKE 'selftest\_055\_%\_%' ESCAPE '\'
           AND r.canonical_key NOT LIKE 'selftest\_055\_more\_%' ESCAPE '\';
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 055 d', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'boardroom',
                'A sentence.', 'verified', true),
               ('selftest 055 e', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'pitch',
                'A sentence.', 'verified', true);
        v_drop := public.arsenal_pick_drop('2001-05-03', true);
        IF (SELECT w.lemma FROM public.arsenal_drops d JOIN public.arsenal_words w ON w.id = d.word_id
             WHERE d.id = v_drop) <> 'selftest 055 e' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the word took the references'' majority room';
        END IF;

        -- ---------------------------------------------------- 7. item 11, written once
        IF (SELECT verification FROM public.arsenal_refs WHERE id = 206) <> 'corrected'
           OR (SELECT origin FROM public.arsenal_refs WHERE id = 206) NOT LIKE '%$44 million payroll%'
           OR (SELECT verification FROM public.arsenal_refs WHERE id = 216) <> 'verified'
           OR (SELECT source_url FROM public.arsenal_refs WHERE id = 216) <> 'https://en.wikipedia.org/wiki/Enchiridion_of_Epictetus'
           OR (SELECT count(*) FROM public.arsenal_reverifications
                WHERE ref_id IN (206, 216) AND old_verification = 'unverified' AND by_whom LIKE 'owner, 2026-10-16%') <> 2
           OR (SELECT count(*) FROM public.arsenal_refs
                WHERE id IN (190, 192, 201, 209, 212, 215) AND verification = 'unverified' AND status = 'pool') <> 6 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: item 11 (206 corrected, 216 verified, the other six unverified)';
        END IF;

        -- ---------------------------------------------------- 8. one re-verification, recorded
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                         source_work, source_kind, status, verification, verification_note,
                                         origin_kind, is_test)
        VALUES ('selftest_055_rev_a', 'sports', 'Selftest rev a', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'pitch', 'A work', 'wikipedia', 'pool', 'unverified', 'insufficient: x', 'pool', true),
               ('selftest_055_rev_b', 'sports', 'Selftest rev b', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'pitch', 'A work', 'wikipedia', 'pool', 'unverified', 'insufficient: y', 'pool', true);
        SELECT id INTO v_r FROM public.arsenal_refs WHERE canonical_key = 'selftest_055_rev_a';
        SELECT id INTO v_r2 FROM public.arsenal_refs WHERE canonical_key = 'selftest_055_rev_b';
        v_n := 0;
        -- Never by a plain UPDATE, nor by a forged setting without the record.
        BEGIN
            UPDATE public.arsenal_refs SET verification = 'unverified', verification_note = 'rewritten' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%settles once%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            PERFORM set_config('arsenal.reverify', 'ref:' || v_r, true);
            UPDATE public.arsenal_refs SET verification = 'unverified', verification_note = 'rewritten' WHERE id = v_r;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%settles once%' THEN v_n := v_n + 1; END IF;
        END;
        PERFORM set_config('arsenal.reverify', '', true);
        -- Through the function: settled, stamped, recorded old -> new with who.
        PERFORM public.arsenal_reverify_ref(v_r, 'corrected', 'second look', 'selftest 055', 'https://selftest.invalid/r',
                                            'A corrected origin, long enough.');
        IF NOT EXISTS (SELECT 1 FROM public.arsenal_refs
                        WHERE id = v_r AND verification = 'corrected' AND origin = 'A corrected origin, long enough.'
                          AND verification_note = 'second look' AND verified_at IS NOT NULL)
           OR NOT EXISTS (SELECT 1 FROM public.arsenal_reverifications
                           WHERE ref_id = v_r AND old_verification = 'unverified' AND new_verification = 'corrected'
                             AND old_note = 'insufficient: x' AND new_note = 'second look' AND by_whom = 'selftest 055'
                             AND db_role = current_user AND created_at IS NOT NULL) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the re-verification or its record';
        END IF;
        -- Once: a re-verification that stays unverified still uses the one chance.
        PERFORM public.arsenal_reverify_ref(v_r2, 'unverified', 'still nothing', 'selftest 055');
        BEGIN
            PERFORM public.arsenal_reverify_ref(v_r2, 'verified', 'third try', 'selftest 055', 'https://selftest.invalid/r');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%already re-verified once%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            PERFORM public.arsenal_reverify_ref(v_r, 'verified', 'again', 'selftest 055');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%only an unverified pool reference%' THEN v_n := v_n + 1; END IF;
        END;
        -- The record is append-only.
        BEGIN
            UPDATE public.arsenal_reverifications SET by_whom = 'someone else' WHERE ref_id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_reverifications WHERE ref_id = v_r;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.arsenal_reverifications;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        -- A word, the same way.
        INSERT INTO public.arsenal_words (lemma, meaning, room, sentence, verification, verification_note, is_test)
        VALUES ('selftest 055 rev', 'A meaning.', 'pitch', 'A sentence.', 'unverified', 'no pronunciation', true)
        RETURNING id INTO v_w2;
        PERFORM public.arsenal_reverify_word(v_w2, 'verified', 'found it', 'selftest 055', '/x/', 'From x.',
                                             'https://selftest.invalid/w');
        IF (SELECT verification FROM public.arsenal_words WHERE id = v_w2) <> 'verified' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the word re-verification';
        END IF;
        IF v_n <> 7 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: re-verification refusals (% of 7)', v_n;
        END IF;

        -- ---------------------------------------------------- 9. holds
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction)
        SELECT v_owner, r.id, 'known' FROM public.arsenal_refs r
         WHERE r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected');
        INSERT INTO public.arsenal_reactions (owner_id, word_id, reaction)
        SELECT v_owner, w.id, 'known' FROM public.arsenal_words w
         WHERE w.is_test AND w.status = 'pool' AND w.verification IN ('verified', 'corrected');
        v_ids := '{}';
        FOR i IN 1..4 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification,
                                             origin_kind, is_test)
            VALUES ('selftest_055_hold_' || i, (ARRAY['finance_business', 'sports', 'film_tv', 'finance_business'])[i],
                    'Selftest hold ' || i, 'An origin long enough.', 'A meaning.', 'A line to deploy.', 'pitch',
                    'A work', 'wikipedia', 'https://selftest.invalid/r', 'pool', 'verified', 'pool', true)
            RETURNING id INTO v_r;
            v_ids := v_ids || v_r;
        END LOOP;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 055 hold', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'homies',
                'A sentence.', 'verified', true)
        RETURNING id INTO v_w;
        -- Both finance references held: two categories left.
        INSERT INTO public.arsenal_holds (ref_id, reason, detail)
        VALUES (v_ids[1], 'outcome_conflict', 'selftest'), (v_ids[4], 'outcome_conflict', 'selftest');
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-05-10', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%(2 categories%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a held reference was eligible';
        END IF;
        -- The word held: no word.
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                         source_work, source_kind, source_url, status, verification, origin_kind, is_test)
        VALUES ('selftest_055_hold_5', 'history_military', 'Selftest hold 5', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'pitch', 'A work', 'wikipedia', 'https://selftest.invalid/r', 'pool', 'verified',
                'pool', true);
        INSERT INTO public.arsenal_holds (word_id, reason) VALUES (v_w, 'outcome_conflict');
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-05-10', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%no eligible word%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a held word was eligible';
        END IF;
        v_n := 0;
        BEGIN
            INSERT INTO public.arsenal_holds (ref_id, reason) VALUES (v_ids[2], 'Outcome Conflict');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_holds WHERE ref_id = v_ids[1];
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.arsenal_holds;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: holds are append-only with a snake_case reason (% of 3)', v_n;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '055 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('055_arsenal_rooms_word_seeds')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '055_arsenal_rooms_word_seeds';
-- SELECT lemma, status FROM arsenal_words WHERE lemma IN ('asymmetric', 'inflection', 'provenance', 'fiduciary');
--   -- asymmetric dropped; the other three seed
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'arsenal_words_status_check';
-- SELECT p.oid::regprocedure, p.proacl FROM pg_proc p
--  WHERE proname IN ('arsenal_pick_drop', 'arsenal_words_guard', 'arsenal_refs_guard', 'arsenal_reverify_ref',
--                    'arsenal_reverify_word');
-- SELECT id, verification, source_url FROM arsenal_refs WHERE id IN (206, 216);   -- corrected, verified
-- SELECT ref_id, old_verification, new_verification, by_whom, db_role FROM arsenal_reverifications;  -- 2 rows
-- SELECT count(*) FROM arsenal_holds;                                               -- 0 until the outcome check
