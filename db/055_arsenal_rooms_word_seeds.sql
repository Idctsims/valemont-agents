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
--      majority whenever the pool allows.
--
-- A self-test runs on paste (section 4), rolled back.
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

-- ===================================================================== 4
-- Self-test (rolled back): word seeds, then the picker's rooms, on TEST
-- rows only. Every other eligible test row (e2e leftovers) is first marked
-- 'known' inside the rolled-back block, so the picker sees only these.

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
-- SELECT p.oid::regprocedure, p.proacl FROM pg_proc p WHERE proname IN ('arsenal_pick_drop', 'arsenal_words_guard');
