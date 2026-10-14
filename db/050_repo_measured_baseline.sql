-- 050_repo_measured_baseline.sql — the resurface rule's baseline is MEASURED
-- (Chat 4, Phase 2; owner, 2026-10-14).
--
-- db/049's repo_may_surface doubled the FIRST surfacing's stars_7d, whatever
-- its source: an estimate (trending's stars in period, the age average) or
-- nothing at all, in which case the repo could never resurface. Owner's
-- change: a repo's baseline is a MEASURED stars_7d.
--
--   * the first surfacing's stars_7d, when that was measured;
--   * otherwise the repo's FIRST measured stars_7d from our own snapshots:
--     the earliest day D with a snapshot on D and on D - 7, stars(D) -
--     stars(D - 7). Until 7 days of snapshots exist there is no baseline and
--     the repo does not resurface (yet).
--   * doubling and the 14-day floor are unchanged: stars_7d at least
--     2 × baseline, and the last surfacing at least 14 days before the day.
--   * a measured baseline of 0 counts as 1 (a measured 0 is known, not
--     missing): the repo then needs at least 2.
--
-- Only repo_may_surface changes (same signature, CREATE OR REPLACE); the
-- surfacing trigger calls it, so the database enforces the new rule at once.
-- The worker is unchanged (ledger.repo_may_surface asks the same function).
--
-- A self-test runs on paste, rolled back: no row survives. It writes
-- surfacings and snapshots on past and far-future days with the two guards
-- switched off INSIDE its own subtransaction (ALTER TABLE ... DISABLE TRIGGER
-- is transactional), because both guards stamp today's date. A wrong result
-- raises SELFTEST FAILED and nothing in this file is applied.
--
-- Prerequisites: db/049.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

CREATE OR REPLACE FUNCTION public.repo_may_surface(p_repo bigint, p_stars_7d integer, p_day date)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH s AS (
        SELECT x.surfaced_on, x.stars_7d, x.stars_7d_source, x.id FROM public.repo_surfacings x
         WHERE x.repo_id = p_repo AND NOT x.is_test
    ),
    first_shown AS (SELECT s.stars_7d, s.stars_7d_source FROM s ORDER BY s.surfaced_on, s.id LIMIT 1),
    -- The repo's first measured stars_7d from its own snapshots.
    first_measured AS (
        SELECT t.stars - w.stars AS stars_7d
          FROM public.repo_star_snapshots t
          JOIN public.repo_star_snapshots w ON w.repo_id = t.repo_id AND w.snapped_on = t.snapped_on - 7
         WHERE t.repo_id = p_repo AND NOT t.is_test
         ORDER BY t.snapped_on
         LIMIT 1
    ),
    baseline AS (
        SELECT CASE WHEN f.stars_7d_source = 'measured' THEN f.stars_7d
                    ELSE (SELECT m.stars_7d FROM first_measured m) END AS stars_7d
          FROM first_shown f
    )
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM s) THEN true
        ELSE p_stars_7d IS NOT NULL
             AND (SELECT max(s.surfaced_on) FROM s) <= p_day - 14
             AND (SELECT b.stars_7d FROM baseline b) IS NOT NULL
             AND p_stars_7d >= 2 * greatest((SELECT b.stars_7d FROM baseline b), 1)
    END;
$$;

REVOKE ALL ON FUNCTION public.repo_may_surface(bigint, integer, date) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.repo_may_surface(bigint, integer, date) TO authenticated, valemont_readonly;

-- ===================================================================== self-test

DO $$
DECLARE
    d0       date := DATE '2099-01-01';   -- a day no real row will ever carry
    v_repo   bigint;
    v_story  bigint;
    i        int;
BEGIN
    BEGIN
        ALTER TABLE public.repo_surfacings DISABLE TRIGGER repo_surfacings_guard;
        ALTER TABLE public.repo_star_snapshots DISABLE TRIGGER repo_star_snapshots_guard;

        -- 1. Never surfaced: may surface.
        INSERT INTO public.repos (full_name, html_url, stars) VALUES ('selftest-050/never', 'https://selftest.invalid/050/n', 1)
        RETURNING id INTO v_repo;
        IF NOT public.repo_may_surface(v_repo, NULL, d0) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a never-surfaced repo may not surface';
        END IF;

        -- 2. First surfacing MEASURED (10): the baseline is 10, as in db/049.
        INSERT INTO public.repos (full_name, html_url, stars) VALUES ('selftest-050/measured', 'https://selftest.invalid/050/m', 1)
        RETURNING id INTO v_repo;
        INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-050 m', 'repos') RETURNING id INTO v_story;
        INSERT INTO public.repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source)
        VALUES (v_repo, v_story, d0, 100, 10, 'measured');
        IF public.repo_may_surface(v_repo, 1000, d0 + 13)
           OR NOT public.repo_may_surface(v_repo, 20, d0 + 14)
           OR public.repo_may_surface(v_repo, 19, d0 + 14)
           OR public.repo_may_surface(v_repo, NULL, d0 + 30) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a measured first surfacing (baseline 10)';
        END IF;

        -- 3. First surfacing an ESTIMATE (500), no 7 days of snapshots: no baseline yet.
        INSERT INTO public.repos (full_name, html_url, stars) VALUES ('selftest-050/estimate', 'https://selftest.invalid/050/e', 1)
        RETURNING id INTO v_repo;
        INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-050 e', 'repos') RETURNING id INTO v_story;
        INSERT INTO public.repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source)
        VALUES (v_repo, v_story, d0, 100, 500, 'trending_weekly');
        FOR i IN 0..6 LOOP
            INSERT INTO public.repo_star_snapshots (repo_id, snapped_on, stars) VALUES (v_repo, d0 + i, 100 + i);
        END LOOP;
        IF public.repo_may_surface(v_repo, 1000000, d0 + 30) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an estimate became a baseline (or 6 days of snapshots measured)';
        END IF;
        -- Day 7 exists: first measured = stars(d0+7) - stars(d0) = 130 - 100 = 30.
        -- A later, bigger measurement (day 8: 200 - 101 = 99) is not the first.
        INSERT INTO public.repo_star_snapshots (repo_id, snapped_on, stars) VALUES (v_repo, d0 + 7, 130), (v_repo, d0 + 8, 200);
        IF NOT public.repo_may_surface(v_repo, 60, d0 + 14)
           OR public.repo_may_surface(v_repo, 59, d0 + 14)
           OR public.repo_may_surface(v_repo, 1000, d0 + 13) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the first measured stars_7d (30) is not the baseline';
        END IF;

        -- 4. First surfacing with NO stars_7d: the measured baseline applies too (db/049: never).
        INSERT INTO public.repos (full_name, html_url, stars) VALUES ('selftest-050/null', 'https://selftest.invalid/050/x', 1)
        RETURNING id INTO v_repo;
        INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-050 x', 'repos') RETURNING id INTO v_story;
        INSERT INTO public.repo_surfacings (repo_id, story_id, surfaced_on, stars) VALUES (v_repo, v_story, d0, 100);
        INSERT INTO public.repo_star_snapshots (repo_id, snapped_on, stars) VALUES (v_repo, d0 - 3, 50), (v_repo, d0 + 4, 90);
        -- first measured = 90 - 50 = 40
        IF NOT public.repo_may_surface(v_repo, 80, d0 + 14) OR public.repo_may_surface(v_repo, 79, d0 + 14) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NULL first stars_7d does not take the measured baseline (40)';
        END IF;

        -- 5. A measured baseline of 0 counts as 1.
        INSERT INTO public.repos (full_name, html_url, stars) VALUES ('selftest-050/zero', 'https://selftest.invalid/050/z', 1)
        RETURNING id INTO v_repo;
        INSERT INTO public.stories (pillars, title, category) VALUES (ARRAY['tech'], 'selftest-050 z', 'repos') RETURNING id INTO v_story;
        INSERT INTO public.repo_surfacings (repo_id, story_id, surfaced_on, stars, stars_7d, stars_7d_source)
        VALUES (v_repo, v_story, d0, 100, 0, 'measured');
        IF public.repo_may_surface(v_repo, 1, d0 + 14) OR NOT public.repo_may_surface(v_repo, 2, d0 + 14) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a measured baseline of 0 is not treated as 1';
        END IF;

        -- 6. Test rows never count: a test snapshot is not a measurement.
        INSERT INTO public.repos (full_name, html_url, stars, is_test) VALUES ('selftest-050/test', 'https://selftest.invalid/050/t', 1, true)
        RETURNING id INTO v_repo;
        INSERT INTO public.repo_star_snapshots (repo_id, snapped_on, stars, is_test) VALUES (v_repo, d0, 1, true), (v_repo, d0 + 7, 9, true);
        IF NOT public.repo_may_surface(v_repo, NULL, d0) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a repo with only test rows is treated as surfaced';
        END IF;

        -- The guards are back on (and the rollback below undoes all of this anyway).
        ALTER TABLE public.repo_surfacings ENABLE TRIGGER repo_surfacings_guard;
        ALTER TABLE public.repo_star_snapshots ENABLE TRIGGER repo_star_snapshots_guard;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    -- Both guards are enabled after the rollback (ALTER TABLE is transactional).
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname IN ('repo_surfacings_guard', 'repo_star_snapshots_guard')
                AND tgenabled = 'D') THEN
        RAISE EXCEPTION 'SELFTEST FAILED: a repo guard is still disabled';
    END IF;
    RAISE NOTICE '050 self-test passed (rolled back)';
END
$$;

INSERT INTO migration_log (name) VALUES ('050_repo_measured_baseline')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) repo_may_surface's body reads first_measured; readonly executes it, anon not
--   (b) both repo guards enabled ('O')
--   (c) the log row
--
-- (a)
-- SELECT position('first_measured' in p.prosrc) > 0 AS new_rule,
--        has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS ro,
--        has_function_privilege('anon', p.oid, 'EXECUTE') AS anon
--   FROM pg_proc p WHERE p.proname = 'repo_may_surface';
-- (b)
-- SELECT tgname, tgenabled FROM pg_trigger
--  WHERE tgname IN ('repo_surfacings_guard', 'repo_star_snapshots_guard') ORDER BY 1;
-- (c)
-- SELECT name, applied_at FROM migration_log WHERE name = '050_repo_measured_baseline';
