-- db/092_parlay_days.sql
--
-- The Parlays page's day menu (owner, 2026-11-25): every Chicago day the
-- parlay agents committed on, newest first, with that day's ticket counts.
-- Read only: no table, column, trigger or ledger row changes, and db/091's
-- readers are untouched.
--
--   parlay_days(tests)  jsonb array of {d, n, open, hit, miss}, newest first.
--     d     the Chicago date (as parlay_day's window counts days)
--     n     tickets committed that day (paper, as parlay_day lists them)
--     open  of those, with no resolution yet
--     hit   resolved 'hit'
--     miss  resolved 'miss'
--     A 'partial' (or any other) outcome counts in n only, never as a hit.
--   Counts of tickets, never a sum of stakes or pnl (§9.2).
--
-- Test rows (the _e2e_parlays agent) only with p_tests, through db/091's
-- parlay_agent_ok, exactly as parlay_day.
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

CREATE OR REPLACE FUNCTION public.parlay_days(p_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'd', x.d, 'n', x.n, 'open', x.open, 'hit', x.hit, 'miss', x.miss) ORDER BY x.d DESC), '[]'::jsonb)
      FROM (
        SELECT (c.committed_at AT TIME ZONE 'America/Chicago')::date AS d,
               count(*) AS n,
               count(*) FILTER (WHERE r.id IS NULL) AS open,
               count(*) FILTER (WHERE r.outcome = 'hit') AS hit,
               count(*) FILTER (WHERE r.outcome = 'miss') AS miss
          FROM public.commitments c
          JOIN public.agents a ON a.id = c.agent_id
          LEFT JOIN public.resolutions r ON r.commitment_id = c.id
         WHERE c.mode = 'paper' AND public.parlay_agent_ok(a.slug, a.is_test, p_tests)
         GROUP BY 1
      ) x;
$$;

REVOKE ALL ON FUNCTION public.parlay_days(boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.parlay_days(boolean) TO authenticated, valemont_readonly;

INSERT INTO migration_log (name) VALUES ('092_parlay_days')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== self-test
-- Run on paste, rolled back ('selftest-rollback'). Two test tickets for the
-- _e2e_parlays agent, today in Chicago: one open, one resolved 'hit', one
-- resolved 'partial' (counted in n only). A
-- statement that calls a writing function stores its result first and reads
-- back later (db/062's rule); parlay_days writes nothing.
DO $$
DECLARE
    v_agent  smallint := (SELECT id FROM public.agents WHERE slug = '_e2e_parlays');
    v_run    bigint;
    v_open   bigint;
    v_hit    bigint;
    v_part   bigint;
    v_today  date := (now() AT TIME ZONE 'America/Chicago')::date;
    v_before jsonb;
    v_j      jsonb;
    v_d      jsonb;
    v_b      jsonb;
BEGIN
    BEGIN
        IF v_agent IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no _e2e_parlays agent';
        END IF;
        v_before := public.parlay_days(true);
        SELECT x INTO v_b FROM jsonb_array_elements(v_before) x WHERE (x->>'d')::date = v_today;
        v_b := coalesce(v_b, '{"n": 0, "open": 0, "hit": 0, "miss": 0}'::jsonb);

        INSERT INTO public.runs (agent_id, status) VALUES (v_agent, 'ok') RETURNING id INTO v_run;
        INSERT INTO public.commitments (agent_id, run_id, kind, thesis, payload, resolves_after, closes_at)
        VALUES (v_agent, v_run, 'event_contract', 'selftest 092 open',
                '{"tier": "safer", "scope": "sport", "sport": "Basketball"}'::jsonb,
                now() + interval '3 hours', now() + interval '2 hours')
        RETURNING id INTO v_open;
        INSERT INTO public.commitments (agent_id, run_id, kind, thesis, payload, resolves_after, closes_at)
        VALUES (v_agent, v_run, 'event_contract', 'selftest 092 hit',
                '{"tier": "mid", "scope": "sport", "sport": "Basketball"}'::jsonb,
                now() + interval '3 hours', now() + interval '2 hours')
        RETURNING id INTO v_hit;
        INSERT INTO public.commitments (agent_id, run_id, kind, thesis, payload, resolves_after, closes_at)
        VALUES (v_agent, v_run, 'event_contract', 'selftest 092 partial',
                '{"tier": "lotto", "scope": "sport", "sport": "Basketball"}'::jsonb,
                now() + interval '3 hours', now() + interval '2 hours')
        RETURNING id INTO v_part;
        INSERT INTO public.resolution_attempts (commitment_id, result)
        VALUES (v_hit, 'answered'), (v_part, 'answered');
        INSERT INTO public.resolutions (commitment_id, resolved_at, outcome, pnl)
        VALUES (v_hit, now() + interval '4 hours', 'hit', 1.5),
               (v_part, now() + interval '4 hours', 'partial', -0.4);

        -- ------------------------------------------------ 1. today, with p_tests
        v_j := public.parlay_days(true);
        SELECT x INTO v_d FROM jsonb_array_elements(v_j) x WHERE (x->>'d')::date = v_today;
        IF v_d IS NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: today is not listed: %', v_j; END IF;
        IF (v_d->>'n')::int <> (v_b->>'n')::int + 3
           OR (v_d->>'open')::int <> (v_b->>'open')::int + 1
           OR (v_d->>'hit')::int <> (v_b->>'hit')::int + 1
           OR (v_d->>'miss')::int <> (v_b->>'miss')::int THEN
            RAISE EXCEPTION 'SELFTEST FAILED: today reads % (before %)', v_d, v_b;
        END IF;
        -- Newest first, one entry per day.
        IF (SELECT count(*) FROM jsonb_array_elements(v_j) x) <> (SELECT count(DISTINCT x->>'d') FROM jsonb_array_elements(v_j) x)
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_j) WITH ORDINALITY a (x, i)
                        JOIN jsonb_array_elements(v_j) WITH ORDINALITY b (y, k) ON k = i + 1
                       WHERE (a.x->>'d')::date <= (b.y->>'d')::date) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the days are not newest first, once each: %', v_j;
        END IF;

        -- ------------------------------------------------ 2. without p_tests: none of it
        -- Every listed day counts exactly its real paper tickets, and a day
        -- with only test tickets is not listed at all.
        v_j := public.parlay_days(false);
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j) x
                    WHERE (x->>'n')::int <> (SELECT count(*) FROM public.commitments c
                                                JOIN public.agents a ON a.id = c.agent_id
                                               WHERE c.mode = 'paper' AND NOT a.is_test
                                                 AND a.slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto')
                                                 AND (c.committed_at AT TIME ZONE 'America/Chicago')::date = (x->>'d')::date)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a real day''s count differs from its commitments';
        END IF;

        -- ------------------------------------------------ 3. access
        IF has_function_privilege('anon', 'public.parlay_days(boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.parlay_days(boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.parlay_days(boolean)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '092 self-test passed (rolled back)';
END
$$;
