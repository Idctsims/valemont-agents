-- 057_chat5_fixes.sql — the prune's freeze fix, the outcome check on the
-- record (and the picker's gate on it), and the daily egress samples
-- (owner, 2026-10-20; branch chat5-fixes).
--
-- What it changes:
--   1. content_items_frozen: the generated youtube_key (db/053) is left out
--      of the comparison. In a BEFORE UPDATE trigger a stored generated
--      column is not computed yet, so NEW.youtube_key reads NULL; on every
--      trailer item (youtube_key set) the comparison saw a change and the
--      nightly prune's embedding clear raised "content_items 16900 is
--      frozen" (feed_prune failed Oct 18 and 19, 08:15 UTC; 67 trailer items
--      blocked the whole clear of 5,572 due rows). An UPDATE cannot set a
--      generated column anyway, so leaving it out loosens nothing.
--   2. arsenal_outcome_checks (APPEND-ONLY, TRUNCATE-guarded): one row per
--      outcome check of a reference (the item 5 check, docs/arsenal.md §8c):
--      the votes, the page read, the note, the controls that passed in the
--      same run, and who ran it (worker, script, backfill). A guard refuses
--      a check of an unchecked reference, a test/real mix, votes that
--      disagree with the conflict flag, or a run whose controls did not all
--      pass. A conflict still writes an arsenal_holds row (the worker's, in
--      the same transaction); its release (db/056) makes the reference
--      eligible again, so the gate is "a check on record", not "the latest
--      check passed".
--   3. arsenal_pick_drop: db/056's, plus one line: a reference is eligible
--      only with a check on record (owner, 2026-10-20: "record checks, the
--      picker gates"). Nothing unchecked can drop.
--   4. The backfill: the 26 real references in the pool today, each checked
--      with the current prompt and no conflict: 14 from batch run 14 in the
--      round B dry run of 2026-10-16 (all 21 then in the pool nnn,
--      docs/arsenal.md §8c), and 12 from batch run 38 on 2026-10-20 (~19:50
--      UTC, --write, controls 3/3 in the same run, all nnn, 0 holds). The
--      rows say so (source 'backfill'); created_at is the paste.
--   5. egress_samples (APPEND-ONLY, TRUNCATE-guarded): the worker's day
--      total (core/egress.py), one row per egress run (every 15 min), and a
--      final row for the day just closed at the Chicago rollover. Before
--      this the total lived only in job_health's latest note, and Oct 16-18
--      were lost when the worker died. v_egress_days: each day's latest
--      sample.
--
-- A self-test runs on paste (section 8), rolled back, on TEST rows only.
-- Prerequisites: db/053 (youtube_key), db/056.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- ORDER: the checks table comes before the picker that reads it (db/055's
-- lesson); the backfill runs before the self-test, which asserts it.
-- PASTE BEFORE THE MERGE: the worker reads and writes the new tables.

-- ===================================================================== 1
-- The freeze: the generated youtube_key is not compared.

CREATE OR REPLACE FUNCTION public.content_items_frozen()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    -- youtube_key is GENERATED (db/053): NULL in NEW inside a BEFORE trigger,
    -- and no UPDATE can set it.
    IF (NEW.embedding IS NOT NULL AND NEW.embedding::text IS DISTINCT FROM OLD.embedding::text)
       OR (NEW.title_embedding IS NOT NULL AND NEW.title_embedding::text IS DISTINCT FROM OLD.title_embedding::text)
       OR (to_jsonb(NEW) - 'embedding' - 'title_embedding' - 'youtube_key')
          IS DISTINCT FROM (to_jsonb(OLD) - 'embedding' - 'title_embedding' - 'youtube_key') THEN
        RAISE EXCEPTION 'content_items % is frozen: only clearing its embeddings is allowed', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 2
-- Outcome checks, append-only.

CREATE TABLE IF NOT EXISTS arsenal_outcome_checks (
    id          BIGSERIAL    PRIMARY KEY,
    ref_id      BIGINT       NOT NULL REFERENCES arsenal_refs (id) ON DELETE RESTRICT,
    conflict    BOOLEAN      NOT NULL,
    -- One letter a read, in order: Y a conflict, n none. Empty: no page to read.
    votes       TEXT         NOT NULL CHECK (votes ~ '^[Yn]{0,9}$'),
    page        TEXT         CHECK (page IS NULL OR char_length(page) BETWEEN 1 AND 300),
    note        TEXT         CHECK (note IS NULL OR char_length(note) <= 500),
    -- The controls that passed in the same run, "passed/total".
    controls    TEXT         NOT NULL CHECK (controls ~ '^[0-9]{1,2}/[0-9]{1,2}$'),
    source      TEXT         NOT NULL CHECK (source IN ('worker', 'script', 'backfill')),
    is_test     BOOLEAN      NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS arsenal_outcome_checks_ref ON arsenal_outcome_checks (ref_id);

CREATE OR REPLACE FUNCTION public.arsenal_outcome_checks_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_ref    record;
    v_yes    int := char_length(NEW.votes) - char_length(replace(NEW.votes, 'Y', ''));
BEGIN
    SELECT r.is_test, r.verification INTO v_ref FROM public.arsenal_refs r WHERE r.id = NEW.ref_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'arsenal_outcome_checks: no reference %', NEW.ref_id;
    END IF;
    IF v_ref.is_test IS DISTINCT FROM NEW.is_test THEN
        RAISE EXCEPTION 'arsenal_outcome_checks: a check is test exactly when its reference is';
    END IF;
    IF v_ref.verification NOT IN ('verified', 'corrected') THEN
        RAISE EXCEPTION 'arsenal_outcome_checks: reference % is %, not verified or corrected', NEW.ref_id,
            v_ref.verification;
    END IF;
    -- A conflict is a strict majority of Y; no reads, no conflict.
    IF NEW.conflict IS DISTINCT FROM (char_length(NEW.votes) > 0 AND v_yes * 2 > char_length(NEW.votes)) THEN
        RAISE EXCEPTION 'arsenal_outcome_checks: conflict % disagrees with votes %', NEW.conflict, NEW.votes;
    END IF;
    IF split_part(NEW.controls, '/', 1)::int <> split_part(NEW.controls, '/', 2)::int
       OR split_part(NEW.controls, '/', 2)::int = 0 THEN
        RAISE EXCEPTION 'arsenal_outcome_checks: every control must pass in the same run (got %)', NEW.controls;
    END IF;
    NEW.created_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS arsenal_outcome_checks_guard ON arsenal_outcome_checks;
CREATE TRIGGER arsenal_outcome_checks_guard
    BEFORE INSERT ON arsenal_outcome_checks
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_outcome_checks_guard();
DROP TRIGGER IF EXISTS arsenal_outcome_checks_immutable ON arsenal_outcome_checks;
CREATE TRIGGER arsenal_outcome_checks_immutable
    BEFORE UPDATE OR DELETE ON arsenal_outcome_checks
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS arsenal_outcome_checks_no_truncate ON arsenal_outcome_checks;
CREATE TRIGGER arsenal_outcome_checks_no_truncate
    BEFORE TRUNCATE ON arsenal_outcome_checks
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 3
-- The picker: db/056's, with one more condition on a reference (a check on record).

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
           AND EXISTS (SELECT 1 FROM public.arsenal_outcome_checks oc WHERE oc.ref_id = r.id)
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

-- ===================================================================== 4
-- Egress samples, append-only, and each day's latest.

CREATE TABLE IF NOT EXISTS egress_samples (
    id             BIGSERIAL    PRIMARY KEY,
    -- The owner's date (America/Chicago), as core/egress.py counts it.
    day            DATE         NOT NULL,
    total_bytes    BIGINT       NOT NULL CHECK (total_bytes >= 0),
    -- Of the total, bytes carried from before this worker process started.
    carried_bytes  BIGINT       NOT NULL DEFAULT 0 CHECK (carried_bytes >= 0 AND carried_bytes <= total_bytes),
    -- {job: bytes} counted by this process today.
    by_job         JSONB        NOT NULL CHECK (jsonb_typeof(by_job) = 'object'),
    -- The day had closed (the Chicago rollover) when this was written.
    final          BOOLEAN      NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS egress_samples_day ON egress_samples (day, id);

DROP TRIGGER IF EXISTS egress_samples_stamp ON egress_samples;
CREATE TRIGGER egress_samples_stamp
    BEFORE INSERT ON egress_samples
    FOR EACH ROW EXECUTE FUNCTION public.arsenal_stamp_created();
DROP TRIGGER IF EXISTS egress_samples_immutable ON egress_samples;
CREATE TRIGGER egress_samples_immutable
    BEFORE UPDATE OR DELETE ON egress_samples
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS egress_samples_no_truncate ON egress_samples;
CREATE TRIGGER egress_samples_no_truncate
    BEFORE TRUNCATE ON egress_samples
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

CREATE OR REPLACE VIEW v_egress_days WITH (security_invoker = true) AS
SELECT DISTINCT ON (s.day) s.day, s.total_bytes, s.by_job, s.final, s.created_at AS sampled_at
  FROM egress_samples s
 ORDER BY s.day, s.id DESC;

-- ===================================================================== 5
-- Access: the owner reads; the worker writes (DATABASE_URL); readonly reads.

ALTER TABLE arsenal_outcome_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE egress_samples ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON arsenal_outcome_checks, egress_samples, v_egress_days FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE arsenal_outcome_checks_id_seq, egress_samples_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON arsenal_outcome_checks, egress_samples, v_egress_days TO authenticated;
DROP POLICY IF EXISTS arsenal_outcome_checks_owner_read ON arsenal_outcome_checks;
CREATE POLICY arsenal_outcome_checks_owner_read ON arsenal_outcome_checks FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS egress_samples_owner_read ON egress_samples;
CREATE POLICY egress_samples_owner_read ON egress_samples FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON arsenal_outcome_checks, egress_samples, v_egress_days TO valemont_readonly;

REVOKE ALL ON FUNCTION public.arsenal_pick_drop(date, boolean, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.arsenal_outcome_checks_guard() FROM PUBLIC, anon, authenticated;

-- ===================================================================== 6
-- The backfill: the 26 references in the pool today, each already checked
-- with the current prompt and no conflict (see the header, item 4).

INSERT INTO arsenal_outcome_checks (ref_id, conflict, votes, page, note, controls, source)
SELECT r.id, false, 'nnn', NULL,
       'backfill (owner, 2026-10-20): scripts.arsenal_outcome_check, round B dry run on 2026-10-16, the 21 then '
       || 'in the pool all nnn; controls Thermopylae, Custer, Miracle on Ice 3/3 that round (docs/arsenal.md §8c)',
       '3/3', 'backfill'
  FROM arsenal_refs r
 WHERE r.id IN (191, 195, 196, 197, 199, 204, 205, 206, 207, 208, 213, 214, 217, 218)
   AND NOT EXISTS (SELECT 1 FROM arsenal_outcome_checks c WHERE c.ref_id = r.id);

INSERT INTO arsenal_outcome_checks (ref_id, conflict, votes, page, note, controls, source)
SELECT r.id, false, 'nnn', v.page,
       'backfill (owner, 2026-10-20): scripts.arsenal_outcome_check --write on 2026-10-20 ~19:50 UTC, controls 3/3 '
       || 'in the same run, all 12 nnn, 0 holds',
       '3/3', 'backfill'
  FROM (VALUES (711::bigint, 'See''s Candies'), (712, 'Blockbuster (retailer)'), (713, 'George Soros'),
               (714, 'Apple Inc.'), (719, 'Battle of Salamis'), (720, 'Battle of Midway'),
               (721, 'Berlin Blockade'), (728, 'Super Bowl LI'),
               (732, '2017 Australian Open – Women''s singles'), (735, 'Beyoncé (album)'),
               (742, 'Siren (mythology)'), (743, 'Goliath')) AS v(id, page)
  JOIN arsenal_refs r ON r.id = v.id
 WHERE NOT EXISTS (SELECT 1 FROM arsenal_outcome_checks c WHERE c.ref_id = r.id);

-- ===================================================================== 7
-- The backfill, asserted on real rows (a failure rolls the whole paste back).

DO $$
DECLARE
    v_n        int;
    v_missing  bigint[];
BEGIN
    SELECT count(*) INTO v_n FROM public.arsenal_outcome_checks
     WHERE source = 'backfill' AND NOT is_test AND votes = 'nnn' AND NOT conflict;
    IF v_n <> 26 THEN
        RAISE EXCEPTION 'BACKFILL FAILED: % backfill checks, 26 expected', v_n;
    END IF;
    SELECT array_agg(r.id ORDER BY r.id) INTO v_missing FROM public.arsenal_refs r
     WHERE NOT r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected')
       AND NOT EXISTS (SELECT 1 FROM public.arsenal_outcome_checks c WHERE c.ref_id = r.id);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'BACKFILL FAILED: ready references with no check: % (a batch landed since; re-run '
            'the outcome check on them first)', v_missing;
    END IF;
END
$$;

-- ===================================================================== 8
-- Self-test (rolled back), on TEST rows only. Every other eligible test row
-- (e2e leftovers) is first marked 'known' inside the rolled-back block.

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_src    bigint;
    v_story  bigint;
    v_item   bigint;
    v_vec    text := '[' || array_to_string(array_fill(0.05::real, ARRAY[384]), ',') || ']';
    v_ids    bigint[] := '{}';
    v_unv    bigint;
    v_real   bigint;
    v_r      bigint;
    v_w      bigint;
    v_c      bigint;
    v_h      bigint;
    v_drop   bigint;
    v_ok     boolean;
    v_n      int;
    i        int;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the freeze on a trailer item
        SELECT id INTO v_src FROM public.sources WHERE slug = '_e2e' AND is_test;
        INSERT INTO public.stories (pillars, title, is_test) VALUES (ARRAY['home'], 'self-test 057', true)
        RETURNING id INTO v_story;
        INSERT INTO public.content_items (source_id, external_id, url, title, published_at, story_id,
                                          embedding, title_embedding, is_test)
        VALUES (v_src, 'selftest:057', 'https://www.youtube.com/watch?v=selftest057x', 'self-test trailer', now(),
                v_story, v_vec::extensions.halfvec, v_vec::extensions.halfvec, true)
        RETURNING id INTO v_item;
        IF (SELECT youtube_key FROM public.content_items WHERE id = v_item) IS DISTINCT FROM 'selftest057x' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the fixture has no youtube_key';
        END IF;
        UPDATE public.content_items SET embedding = NULL, title_embedding = NULL WHERE id = v_item;
        IF (SELECT embedding IS NOT NULL OR title_embedding IS NOT NULL FROM public.content_items WHERE id = v_item) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a trailer item''s embeddings were not cleared';
        END IF;
        v_ok := false;
        BEGIN
            UPDATE public.content_items SET title = 'edited' WHERE id = v_item;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%is frozen%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a trailer item''s title could be changed';
        END IF;

        -- ---------------------------------------------------- 2. the picker needs a check on record
        INSERT INTO public.arsenal_reactions (owner_id, ref_id, reaction)
        SELECT v_owner, r.id, 'known' FROM public.arsenal_refs r
         WHERE r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected');
        INSERT INTO public.arsenal_reactions (owner_id, word_id, reaction)
        SELECT v_owner, w.id, 'known' FROM public.arsenal_words w
         WHERE w.is_test AND w.status = 'pool' AND w.verification IN ('verified', 'corrected');
        FOR i IN 1..3 LOOP
            INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                             source_work, source_kind, source_url, status, verification,
                                             origin_kind, is_test)
            VALUES ('selftest_057_' || i, (ARRAY['finance_business', 'sports', 'film_tv'])[i],
                    'Selftest 057 ' || i, 'An origin long enough.', 'A meaning.', 'A line to deploy.',
                    (ARRAY['boardroom', 'pitch', 'negotiation'])[i], 'A work', 'wikipedia',
                    'https://selftest.invalid/r', 'pool', 'verified', 'pool', true)
            RETURNING id INTO v_r;
            v_ids := v_ids || v_r;
        END LOOP;
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room,
                                         source_work, source_kind, source_url, status, verification,
                                         origin_kind, is_test)
        VALUES ('selftest_057_unv', 'sports', 'Selftest 057 unverified', 'An origin long enough.', 'A meaning.',
                'A line to deploy.', 'homies', 'A work', 'wikipedia', 'https://selftest.invalid/r', 'pool',
                'unverified', 'pool', true)
        RETURNING id INTO v_unv;
        INSERT INTO public.arsenal_words (lemma, pronunciation, etymology, etymology_url, meaning, room, sentence,
                                          verification, is_test)
        VALUES ('selftest 057 w', '/x/', 'From x.', 'https://selftest.invalid/w', 'A meaning.', 'homies',
                'A sentence.', 'verified', true)
        RETURNING id INTO v_w;

        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-09-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%(0 categories%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: unchecked references were eligible';
        END IF;
        INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, page, note, controls, source, is_test)
        VALUES (v_ids[1], false, 'nnn', 'A page', 'selftest', '3/3', 'worker', true),
               (v_ids[2], false, 'nYn', 'A page', 'selftest', '3/3', 'worker', true);
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-09-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%(2 categories%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the third, unchecked reference was eligible';
        END IF;

        -- A conflict holds (the worker writes both); a release makes it eligible.
        INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, page, note, controls, source, is_test)
        VALUES (v_ids[3], true, 'YYn', 'A page', 'selftest', '3/3', 'worker', true);
        INSERT INTO public.arsenal_holds (ref_id, reason, detail) VALUES (v_ids[3], 'outcome_conflict', 'selftest')
        RETURNING id INTO v_h;
        v_ok := false;
        BEGIN
            PERFORM public.arsenal_pick_drop('2001-09-01', true);
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%(2 categories%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a checked but held reference was eligible';
        END IF;
        PERFORM public.arsenal_release_hold(v_h, 'selftest: a false positive');
        v_drop := public.arsenal_pick_drop('2001-09-01', true);
        IF (SELECT ref_ids @> v_ids AND word_id = v_w FROM public.arsenal_drops WHERE id = v_drop)
           IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the three checked references were not picked';
        END IF;

        -- ---------------------------------------------------- 3. the checks' guard
        v_n := 0;
        BEGIN  -- an unverified reference
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_unv, false, 'nnn', '3/3', 'worker', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%not verified or corrected%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- a test check on a real reference
            SELECT id INTO v_real FROM public.arsenal_refs WHERE NOT is_test LIMIT 1;
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_real, false, 'nnn', '3/3', 'worker', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%test exactly when%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- the flag disagrees with the votes
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_ids[1], false, 'YYn', '3/3', 'worker', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%disagrees with votes%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- no reads is never a conflict
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_ids[1], true, '', '3/3', 'worker', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%disagrees with votes%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- a control failed in the run
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_ids[1], false, 'nnn', '2/3', 'worker', true);
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%every control must pass%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN  -- votes other than Y / n
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_ids[1], false, 'nnx', '3/3', 'worker', true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- an unknown source
            INSERT INTO public.arsenal_outcome_checks (ref_id, conflict, votes, controls, source, is_test)
            VALUES (v_ids[1], false, 'nnn', '3/3', 'by hand', true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        -- Append-only.
        SELECT id INTO v_c FROM public.arsenal_outcome_checks WHERE ref_id = v_ids[1] LIMIT 1;
        BEGIN
            UPDATE public.arsenal_outcome_checks SET conflict = true, votes = 'YYY' WHERE id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.arsenal_outcome_checks WHERE id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.arsenal_outcome_checks;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 10 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: outcome check refusals (% of 10)', v_n;
        END IF;
        IF (SELECT created_at FROM public.arsenal_outcome_checks WHERE id = v_c) IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: created_at is not the database''s';
        END IF;

        -- ---------------------------------------------------- 4. egress samples
        INSERT INTO public.egress_samples (day, total_bytes, carried_bytes, by_job)
        VALUES ('2001-09-01', 100, 0, '{"a": 100}'), ('2001-09-01', 250, 0, '{"a": 250}'),
               ('2001-09-02', 7, 0, '{}');
        INSERT INTO public.egress_samples (day, total_bytes, carried_bytes, by_job, final)
        VALUES ('2001-09-01', 300, 0, '{"a": 300}', true);
        IF (SELECT total_bytes || '/' || final FROM public.v_egress_days WHERE day = '2001-09-01')
           IS DISTINCT FROM '300/true'
           OR (SELECT total_bytes FROM public.v_egress_days WHERE day = '2001-09-02') IS DISTINCT FROM 7 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_egress_days is not each day''s latest sample';
        END IF;
        v_n := 0;
        SELECT id INTO v_c FROM public.egress_samples WHERE day = '2001-09-02';
        BEGIN
            UPDATE public.egress_samples SET total_bytes = 1 WHERE id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.egress_samples WHERE id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.egress_samples;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN  -- carried over the total
            INSERT INTO public.egress_samples (day, total_bytes, carried_bytes, by_job) VALUES ('2001-09-03', 5, 9, '{}');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN  -- by_job not an object
            INSERT INTO public.egress_samples (day, total_bytes, by_job) VALUES ('2001-09-03', 5, '[]');
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: egress sample refusals (% of 5)', v_n;
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '057 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('057_chat5_fixes')
ON CONFLICT (name) DO NOTHING;

-- Verify (read-only, after the paste):
-- SELECT name, applied_at FROM migration_log WHERE name = '057_chat5_fixes';
-- SELECT pg_get_functiondef('public.content_items_frozen()'::regprocedure) LIKE '%youtube_key%';   -- true
-- SELECT source, count(*) FROM arsenal_outcome_checks GROUP BY 1;                                    -- backfill 26
-- SELECT prosrc LIKE '%arsenal_outcome_checks%' FROM pg_proc WHERE proname = 'arsenal_pick_drop';   -- true
-- SELECT count(*) FROM egress_samples;                                                               -- 0 until the worker writes
-- SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('arsenal_outcome_checks', 'egress_samples');
