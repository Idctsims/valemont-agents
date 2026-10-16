-- 056_arsenal_hold_release.sql — The Arsenal: a hold can be released, once,
-- on the record (owner, 2026-10-16).
--
-- Why: on 2026-10-16 an intermediate outcome prompt held three references
-- that the final prompt clears (arsenal_holds 9, 10, 11: The Trojan Horse,
-- Cannae, The Midas touch). A hold is append-only and stays; its RELEASE is
-- a new append-only row.
--
-- What it adds:
--   1. arsenal_hold_releases (APPEND-ONLY, TRUNCATE-guarded): hold_id
--      UNIQUE (a hold is released at most once), the reason, released_by
--      (the database role, set by default), created_at (stamped).
--   2. v_arsenal_active_holds (security_invoker): a hold with no release.
--      Every reader of "held" reads this, so active means one thing.
--   3. arsenal_release_hold(hold_id, reason): the worker's (nobody else
--      executes it). Raises for an unknown hold, one already released, or a
--      blank reason.
--   4. arsenal_pick_drop: db/055's picker, unchanged except that it skips
--      ACTIVE holds only (two lines).
-- The releases of 9, 10 and 11 are NOT in this file: they run after the
-- paste is verified (scripts.arsenal_release_hold).
--
-- A self-test runs on paste (section 5), rolled back.
-- Prerequisites: db/055.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- ORDER: the table and the view come first; the picker that reads the view
-- is replaced after them (db/055's lesson).

-- ===================================================================== 1
-- Releases, append-only.

CREATE TABLE IF NOT EXISTS arsenal_hold_releases (
    id           BIGSERIAL    PRIMARY KEY,
    hold_id      BIGINT       NOT NULL UNIQUE REFERENCES arsenal_holds (id) ON DELETE RESTRICT,
    reason       TEXT         NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 3 AND 500),
    released_by  TEXT         NOT NULL DEFAULT current_user,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS arsenal_hold_releases_stamp ON arsenal_hold_releases;
CREATE TRIGGER arsenal_hold_releases_stamp
    BEFORE INSERT ON arsenal_hold_releases
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS arsenal_hold_releases_immutable ON arsenal_hold_releases;
CREATE TRIGGER arsenal_hold_releases_immutable
    BEFORE UPDATE OR DELETE ON arsenal_hold_releases
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_hold_releases_no_truncate ON arsenal_hold_releases;
CREATE TRIGGER arsenal_hold_releases_no_truncate
    BEFORE TRUNCATE ON arsenal_hold_releases
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 2
-- Active holds: a hold with no release.

CREATE OR REPLACE VIEW v_arsenal_active_holds WITH (security_invoker = true) AS
SELECT h.id, h.ref_id, h.word_id, h.reason, h.detail, h.created_at
  FROM arsenal_holds h
 WHERE NOT EXISTS (SELECT 1 FROM arsenal_hold_releases r WHERE r.hold_id = h.id);

-- ===================================================================== 3
-- The release (the worker's).

CREATE OR REPLACE FUNCTION public.arsenal_release_hold(p_hold_id bigint, p_reason text)
RETURNS bigint
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.arsenal_holds h WHERE h.id = p_hold_id) THEN
        RAISE EXCEPTION 'arsenal_release_hold: no hold %', p_hold_id;
    END IF;
    IF EXISTS (SELECT 1 FROM public.arsenal_hold_releases r WHERE r.hold_id = p_hold_id) THEN
        RAISE EXCEPTION 'arsenal_release_hold: hold % was already released', p_hold_id;
    END IF;
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'arsenal_release_hold: a reason is required';
    END IF;
    INSERT INTO public.arsenal_hold_releases (hold_id, reason) VALUES (p_hold_id, p_reason)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 4
-- The picker: db/055's, skipping ACTIVE holds only.

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
           AND NOT EXISTS (SELECT 1 FROM public.v_arsenal_active_holds h WHERE h.ref_id = r.id)
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
       AND NOT EXISTS (SELECT 1 FROM public.v_arsenal_active_holds h WHERE h.word_id = wd.id)
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

-- ===================================================================== 5
-- Access: the owner reads the releases and the view; the worker writes.

ALTER TABLE arsenal_hold_releases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON arsenal_hold_releases, v_arsenal_active_holds FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE arsenal_hold_releases_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON arsenal_hold_releases, v_arsenal_active_holds TO authenticated;
DROP POLICY IF EXISTS arsenal_hold_releases_owner_read ON arsenal_hold_releases;
CREATE POLICY arsenal_hold_releases_owner_read ON arsenal_hold_releases FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON arsenal_hold_releases, v_arsenal_active_holds TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arsenal_release_hold(bigint, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_pick_drop(date, boolean, boolean) FROM PUBLIC, anon, authenticated;

-- ===================================================================== 6
-- Self-test (rolled back), on TEST rows only. Every other eligible test row
-- (e2e leftovers) is first marked 'known' inside the rolled-back block.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_ids    bigint[] := '{}';
    v_r      bigint;
    v_w      bigint;
    v_h      bigint;
    v_hw     bigint;
    v_rel    bigint;
    v_drop   bigint;
    v_ok     boolean;
    v_n      int;
    i        int;
BEGIN
    BEGIN
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction)
        SELECT v_owner, r.id, 'known' FROM public.arsenal_refs r
         WHERE r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected');
        INSERT INTO public.arsenal_reactions (owner_id, word_id, reaction)
        SELECT v_owner, w.id, 'known' FROM public.arsenal_words w
         WHERE w.is_test AND w.status = 'pool' AND w.verification IN ('verified', 'corrected');

        -- ---------------------------------------------------- 1. a held ref and word are skipped
        FOR i IN 1..3 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification,
                                             origin_kind, is_test)
            VALUES ('selftest_056_' || i, (ARRAY['finance_business', 'sports', 'film_tv'])[i],
                    'Selftest 056 ' || i, 'An origin long enough.', 'A meaning.', 'A line to deploy.',
                    (ARRAY['boardroom', 'pitch', 'negotiation'])[i], 'A work', 'wikipedia',
                    'https://selftest.invalid/r', 'pool', 'verified', 'pool', true)
            RETURNING id INTO v_r;
            v_ids := v_ids || v_r;
        END LOOP;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 056 w', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'homies',
                'A sentence.', 'verified', true)
        RETURNING id INTO v_w;
        INSERT INTO public.arsenal_holds (ref_id, reason, detail) VALUES (v_ids[1], 'outcome_conflict', 'selftest')
        RETURNING id INTO v_h;
        INSERT INTO public.arsenal_holds (word_id, reason, detail) VALUES (v_w, 'outcome_conflict', 'selftest')
        RETURNING id INTO v_hw;
        IF (SELECT count(*) FROM public.v_arsenal_active_holds WHERE id IN (v_h, v_hw)) <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: two new holds are not both active';
        END IF;
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-07-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%(2 categories%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an active hold on a reference was eligible';
        END IF;

        -- ---------------------------------------------------- 2. released: eligible again
        v_rel := public.arsenal_release_hold(v_h, 'selftest: a false positive');
        IF NOT EXISTS (SELECT 1 FROM public.arsenal_hold_releases
                        WHERE id = v_rel AND hold_id = v_h AND reason = 'selftest: a false positive'
                          AND released_by = current_user AND created_at IS NOT NULL)
           OR EXISTS (SELECT 1 FROM public.v_arsenal_active_holds WHERE id = v_h)
           OR NOT EXISTS (SELECT 1 FROM public.arsenal_holds WHERE id = v_h) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the release, its record, or the hold kept as history';
        END IF;
        -- The word is still held: no eligible word.
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-07-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%no eligible word%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an active hold on a word was eligible';
        END IF;
        PERFORM public.arsenal_release_hold(v_hw, 'selftest: a false positive');
        v_drop := public.arsenal_pick_drop('2001-07-01', true);
        IF (SELECT ref_ids @> ARRAY[v_ids[1]] AND word_id = v_w FROM public.arsenal_drops WHERE id = v_drop)
           IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a released reference or word was not picked';
        END IF;

        -- ---------------------------------------------------- 3. once, on the record, append-only
        v_n := 0;
        BEGIN
            PERFORM public.arsenal_release_hold(v_h, 'again');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%already released%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            PERFORM public.arsenal_release_hold(-1, 'no such hold');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%no hold%' THEN v_n := v_n + 1; END IF;
        END;
        INSERT INTO public.arsenal_holds (ref_id, reason) VALUES (v_ids[2], 'outcome_conflict') RETURNING id INTO v_h;
        BEGIN
            PERFORM public.arsenal_release_hold(v_h, '  ');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%a reason is required%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_hold_releases SET reason = 'rewritten' WHERE id = v_rel;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_hold_releases WHERE id = v_rel;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.arsenal_hold_releases;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        -- arsenal_holds is now referenced: its TRUNCATE names the releases too (db/042's lesson).
        BEGIN
            TRUNCATE public.arsenal_holds, public.arsenal_hold_releases;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        -- A new hold on a released reference is active again.
        INSERT INTO public.arsenal_holds (ref_id, reason) VALUES (v_ids[3], 'outcome_conflict');
        IF NOT EXISTS (SELECT 1 FROM public.v_arsenal_active_holds WHERE ref_id = v_ids[3]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a new hold is not active';
        END IF;
        IF v_n <> 7 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: release refusals (% of 7)', v_n;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '056 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('056_arsenal_hold_release')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '056_arsenal_hold_release';
-- SELECT count(*) FROM arsenal_hold_releases;                              -- 0 until the releases
-- SELECT id, ref_id FROM v_arsenal_active_holds ORDER BY id;               -- 9, 10, 11 until released
-- SELECT p.oid::regprocedure, p.proacl FROM pg_proc p WHERE proname IN ('arsenal_release_hold', 'arsenal_pick_drop');
-- SELECT prosrc LIKE '%v_arsenal_active_holds%' FROM pg_proc WHERE proname = 'arsenal_pick_drop';   -- true
