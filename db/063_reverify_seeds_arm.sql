-- 063_reverify_seeds_arm.sql — the one re-verification, widened to seeds and
-- Arm Me cards (owner, 2026-11-04; the verifier yield fix, docs/arsenal.md §11).
--
-- db/055 gave every unverified POOL reference ONE re-verification, recorded in
-- arsenal_reverifications (append-only, UNIQUE per target). The owner's
-- decision (Nov 4): the 18 unverified seeds and the unverified Arm Me cards
-- get it too. What changes:
--   1. arsenal_reverify_ref accepts an unverified reference whose status is
--      pool, seed or arm (was: pool only). Everything else is db/055's: once
--      per reference ever, from unverified only, recorded before the write.
--   2. The reference guard (db/062's) opens its re-verify branch for the same
--      three statuses. Its status rule is unchanged: a seed stays a seed (it
--      never drops), an arm card stays arm. Only the verdict and the card's
--      corrected wording move.
-- Nothing is re-verified here: the worker's script spends them, only after
-- the verifier's done-when passes (owner, Nov 4).
--
-- A self-test runs on paste (section 3), rolled back, and raises on any wrong
-- result. Its fixture rows are REAL (a seed and an arm card: the change is
-- about real rows) and are rolled back with everything else.
-- Rules kept: every statement a trigger reads runs after its tables exist
-- (db/055); no explicit NULL into a NOT NULL column (db/053); a self-test
-- never reads back in the same statement as a function that writes (db/062).
-- Prerequisites: db/055, db/059, db/062.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The re-verification function: pool, seed or arm.

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
    IF v_status NOT IN ('pool', 'seed', 'arm') OR v_old <> 'unverified' THEN
        RAISE EXCEPTION 'arsenal_reverify_ref: only an unverified pool, seed or arm reference is re-verified (% is %, %)',
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

REVOKE ALL ON FUNCTION public.arsenal_reverify_ref(bigint, text, text, text, text, text, text, text, text, boolean)
    FROM PUBLIC, anon, authenticated;

-- ===================================================================== 2
-- db/062's reference guard, with ONE condition widened (the re-verify branch:
-- pool, seed or arm). Generated from db/062's text; every other line is the same.

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
        -- db/062: a finished title's card. (With no title, the
        -- arsenal_refs_title_hook CHECK refuses it.)
        IF NEW.origin_kind = 'title_hook' AND NEW.source_title_id IS NOT NULL THEN
            IF NEW.status <> 'pool' THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card enters the pool';
            END IF;
            IF NEW.is_test IS DISTINCT FROM (SELECT t.is_test FROM public.titles t WHERE t.id = NEW.source_title_id) THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card is test exactly when its title is';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM public.arsenal_title_hooks h
                            WHERE h.title_id = NEW.source_title_id AND h.status IN ('queued', 'failed')) THEN
                RAISE EXCEPTION 'arsenal_refs: a title_hook card needs its title''s hook still to draft (once per title)';
            END IF;
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
    -- The one re-verification (db/055; db/063 widens it to seeds and arm
    -- cards): only inside arsenal_reverify_ref, for an unverified pool, seed or
    -- arm reference, matching the row it just recorded. Status never changes.
    v_rev := false;
    IF OLD.status IN ('pool', 'seed', 'arm') AND OLD.verification = 'unverified' AND NEW.status = OLD.status
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

INSERT INTO migration_log (name) VALUES ('063_reverify_seeds_arm')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 3
-- Self-test, run on paste, rolled back ('selftest-rollback').

DO $$
DECLARE
    v_seed  bigint;
    v_arm   bigint;
    v_pool  bigint;
    v_rev   bigint;
    v_n     int;
BEGIN
    BEGIN
        -- A real seed and a real arm card, each settled unverified by the worker's path.
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, status, origin_kind)
        VALUES ('selftest_063_seed', 'history_military', 'Selftest 063 seed', 'An origin long enough to pass.',
                'A meaning.', 'A line to deploy.', 'boardroom', 'A work (2000)', 'wikipedia', 'seed', 'seed')
        RETURNING id INTO v_seed;
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, status, origin_kind)
        VALUES ('selftest_063_arm', 'sports', 'Selftest 063 arm', 'An origin long enough to pass.',
                'A meaning.', 'A line to deploy.', 'pitch', 'A work (2000)', 'wikipedia', 'arm', 'arm_me')
        RETURNING id INTO v_arm;
        INSERT INTO public.arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                         source_kind, status, origin_kind)
        VALUES ('selftest_063_pool', 'sports', 'Selftest 063 pool', 'An origin long enough to pass.',
                'A meaning.', 'A line to deploy.', 'pitch', 'A work (2000)', 'wikipedia', 'pool', 'pool')
        RETURNING id INTO v_pool;
        UPDATE public.arsenal_refs SET verification = 'unverified', verification_note = 'selftest: insufficient'
         WHERE id IN (v_seed, v_arm, v_pool);

        -- 1. a seed is re-verified once; it stays a seed, with the corrected wording.
        v_rev := public.arsenal_reverify_ref(v_seed, 'corrected', 'selftest: corrected by trimming', 'selftest 063',
                                             'https://en.wikipedia.org/wiki/Selftest', 'A corrected origin, long enough.');
        IF (SELECT status || '/' || verification || '/' || origin FROM public.arsenal_refs WHERE id = v_seed)
           <> 'seed/corrected/A corrected origin, long enough.'
           OR (SELECT count(*) FROM public.arsenal_reverifications WHERE ref_id = v_seed AND id = v_rev) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the seed re-verification';
        END IF;
        -- 2. an arm card likewise; it stays arm.
        PERFORM public.arsenal_reverify_ref(v_arm, 'verified', 'selftest: supported', 'selftest 063',
                                            'https://en.wikipedia.org/wiki/Selftest');
        IF (SELECT status || '/' || verification FROM public.arsenal_refs WHERE id = v_arm) <> 'arm/verified' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the arm re-verification';
        END IF;
        -- 3. a pool card still works (db/055 unchanged).
        PERFORM public.arsenal_reverify_ref(v_pool, 'unverified', 'selftest: still insufficient', 'selftest 063');
        IF (SELECT verification FROM public.arsenal_refs WHERE id = v_pool) <> 'unverified'
           OR NOT EXISTS (SELECT 1 FROM public.arsenal_reverifications WHERE ref_id = v_pool) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the pool re-verification';
        END IF;

        -- 4. refusals: a second time; a settled (not unverified) seed; a direct
        --    verification change outside the function; a status change on a seed.
        v_n := 0;
        BEGIN
            PERFORM public.arsenal_reverify_ref(v_seed, 'verified', 'again', 'selftest 063', 'https://e.invalid/x');
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%already re-verified once%'
                                                OR SQLERRM LIKE '%only an unverified%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET verification = 'unverified', verification_note = 'x' WHERE id = v_arm;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%verification settles once%' THEN v_n := v_n + 1; END IF;
        END;
        BEGIN
            UPDATE public.arsenal_refs SET status = 'pool' WHERE id = v_seed;
        EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE '%never changes status%' THEN v_n := v_n + 1; END IF;
        END;
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 3 refusals', v_n; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '063 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '063_reverify_seeds_arm';
-- (b) SELECT position('''pool'', ''seed'', ''arm''' in pg_get_functiondef('public.arsenal_reverify_ref'::regproc)) > 0,
--            position('IN (''pool'', ''seed'', ''arm'') AND OLD.verification' in pg_get_functiondef('public.arsenal_refs_guard'::regproc)) > 0,
--            position('title_hook' in pg_get_functiondef('public.arsenal_refs_guard'::regproc)) > 0;   -- t, t, t
-- (c) SELECT has_function_privilege('authenticated', 'public.arsenal_reverify_ref(bigint,text,text,text,text,text,text,text,text,boolean)', 'EXECUTE');  -- f
-- (d) SELECT count(*) FROM arsenal_reverifications WHERE created_at > now() - interval '1 hour';  -- 0 (nothing spent)
