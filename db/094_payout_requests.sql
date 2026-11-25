-- db/094_payout_requests.sql
--
-- "Bet $X to win $Y" (E1; owner, 2026-11-25; docs/payout_target.md): the
-- owner asks for a slip that pays back a target, the worker answers with up
-- to three tickets. The pattern is db/076/077's combo quotes. Nothing here
-- bets, and nothing here is a commitment (CLAUDE.md §1, §2): a suggestion is
-- kept 7 days and never scored.
--
--   payout_requests  owner app table: stake and payout (what comes back in
--     total if every leg hits). An insert is forced to a fresh `queued` row and
--     queues ONE job_queue row `build_payout`; the worker settles it once
--     (answered with its answer, or failed with a reason). At most 20 requests
--     in any 10 minutes per owner. is_test: the request searches the test board
--     only (the e2e spec's), never mixed with real rows.
--   payout_answers   one per answered request, written while it is queued,
--     never changed: the tickets (0-3), whether they are the nearest reachable
--     rather than the target, and the search's own counts.
--   payout_prune(cutoff, tests_only)  the 7-day prune (worker only).
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

-- ===================================================================== 1. the requests
CREATE TABLE IF NOT EXISTS payout_requests (
    id           BIGSERIAL      PRIMARY KEY,
    owner_id     UUID           NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    stake        NUMERIC(10, 2) NOT NULL CHECK (stake >= 1 AND stake <= 10000),
    payout       NUMERIC(12, 2) NOT NULL CHECK (payout <= 1000000),
    status       TEXT           NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'answered', 'failed')),
    reason       TEXT           CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 500),
    is_test      BOOLEAN        NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),
    answered_at  TIMESTAMPTZ,
    CONSTRAINT payout_requests_more_back CHECK (payout > stake),
    CONSTRAINT payout_requests_settled CHECK ((status = 'queued') = (answered_at IS NULL)),
    CONSTRAINT payout_requests_reason CHECK ((status = 'failed') = (reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS payout_requests_created ON payout_requests (created_at);
CREATE INDEX IF NOT EXISTS payout_requests_owner ON payout_requests (owner_id, created_at DESC);
COMMENT ON TABLE public.payout_requests IS
    'The owner''s "bet $X to win $Y" (E1). Always inserted queued; the worker settles it once (answered with a '
    'payout_answers row, or failed with a reason). Pruned after 7 days. Never a commitment. db/094.';

-- ===================================================================== 2. the answers (before the guards that read it)
CREATE TABLE IF NOT EXISTS payout_answers (
    request_id    BIGINT          PRIMARY KEY REFERENCES payout_requests (id) ON DELETE CASCADE,
    tickets       JSONB           NOT NULL
                                  CHECK (jsonb_typeof(tickets) = 'array' AND jsonb_array_length(tickets) <= 3),
    nearest       BOOLEAN         NOT NULL,
    target_price  NUMERIC(12, 8)  NOT NULL CHECK (target_price > 0 AND target_price < 1),
    candidates    INTEGER         NOT NULL CHECK (candidates >= 0),
    rho_version   TEXT            NOT NULL CHECK (rho_version ~ '^[0-9a-f]{64}$'),
    compute_ms    INTEGER         NOT NULL CHECK (compute_ms >= 0),
    is_test       BOOLEAN         NOT NULL DEFAULT false,
    computed_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    -- No ticket at all is only ever the nearest answer (nothing on the board to build from).
    CONSTRAINT payout_answers_empty_is_nearest CHECK (jsonb_array_length(tickets) > 0 OR nearest)
);
COMMENT ON TABLE public.payout_answers IS
    'One answer per answered payout_requests row, written by the worker while the request is queued, never '
    'changed; deleted only with its request (the 7-day prune). Each ticket is priced by the combo pricer '
    '(Gaussian copula within a game, independent across games). db/094.';

CREATE OR REPLACE FUNCTION public.payout_answers_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_status text;
    v_test   boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'payout_answers: an answer never changes';
    END IF;
    SELECT r.status, r.is_test INTO v_status, v_test
      FROM public.payout_requests r WHERE r.id = NEW.request_id FOR UPDATE;
    IF v_status IS DISTINCT FROM 'queued' THEN
        RAISE EXCEPTION 'payout_answers: request % is not queued (%)', NEW.request_id, coalesce(v_status, 'missing');
    END IF;
    NEW.is_test := v_test;
    NEW.computed_at := now();
    RETURN NEW;
END;
$$;

-- ===================================================================== 3. the request guard and the queue
CREATE OR REPLACE FUNCTION public.payout_requests_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_recent int;
    v_free   timestamptz;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.status := 'queued';
        NEW.reason := NULL;
        NEW.answered_at := NULL;
        -- At most 20 requests in any 10 minutes per owner (db/077's rule; a search
        -- can hold the worker ~20 s). One owner's inserts queue on a lock, so the count is exact.
        PERFORM pg_advisory_xact_lock(hashtext('payout_requests_rate'), hashtext(coalesce(NEW.owner_id::text, '')));
        SELECT count(*), min(r.created_at) + interval '10 minutes' INTO v_recent, v_free
          FROM public.payout_requests r
         WHERE r.owner_id IS NOT DISTINCT FROM NEW.owner_id AND r.created_at > now() - interval '10 minutes';
        IF v_recent >= 20 THEN
            RAISE EXCEPTION 'payout_requests: at most 20 in 10 minutes; the next one frees up at % UTC',
                to_char(v_free AT TIME ZONE 'UTC', 'HH24:MI:SS');
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: the worker settles a queued request, once.
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.stake IS DISTINCT FROM OLD.stake OR NEW.payout IS DISTINCT FROM OLD.payout
       OR NEW.is_test IS DISTINCT FROM OLD.is_test OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'payout_requests: a request''s stake, payout and identity never change';
    END IF;
    IF OLD.status <> 'queued' OR NEW.status NOT IN ('answered', 'failed') THEN
        RAISE EXCEPTION 'payout_requests: a request settles once, from queued (% -> %)', OLD.status, NEW.status;
    END IF;
    NEW.answered_at := now();
    IF NEW.status = 'answered' THEN
        IF NEW.reason IS NOT NULL THEN
            RAISE EXCEPTION 'payout_requests: an answered request carries no reason';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.payout_answers a WHERE a.request_id = NEW.id) THEN
            RAISE EXCEPTION 'payout_requests: answered needs its answer first';
        END IF;
    ELSE
        IF NEW.reason IS NULL OR btrim(NEW.reason) = '' THEN
            RAISE EXCEPTION 'payout_requests: failed needs a reason';
        END IF;
        IF EXISTS (SELECT 1 FROM public.payout_answers a WHERE a.request_id = NEW.id) THEN
            RAISE EXCEPTION 'payout_requests: an answered request is never failed';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- After an insert: ONE build_payout job. Runs as the inserting role (the
-- owner's job_queue insert policy admits a fresh queued job, as price_combo's).
CREATE OR REPLACE FUNCTION public.payout_requests_queue()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.job_queue (kind, payload) VALUES ('build_payout', jsonb_build_object('request_id', NEW.id));
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS payout_answers_guard ON payout_answers;
CREATE TRIGGER payout_answers_guard
    BEFORE INSERT OR UPDATE ON payout_answers
    FOR EACH ROW EXECUTE FUNCTION public.payout_answers_guard();
DROP TRIGGER IF EXISTS payout_requests_guard ON payout_requests;
CREATE TRIGGER payout_requests_guard
    BEFORE INSERT OR UPDATE ON payout_requests
    FOR EACH ROW EXECUTE FUNCTION public.payout_requests_guard();
DROP TRIGGER IF EXISTS payout_requests_queue ON payout_requests;
CREATE TRIGGER payout_requests_queue
    AFTER INSERT ON payout_requests
    FOR EACH ROW EXECUTE FUNCTION public.payout_requests_queue();

-- ===================================================================== 4. the prune
CREATE OR REPLACE FUNCTION public.payout_prune(p_cutoff timestamptz DEFAULT now() - interval '7 days',
                                               p_tests_only boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        DELETE FROM public.payout_requests r
         WHERE r.created_at < p_cutoff AND (r.is_test OR NOT p_tests_only)
        RETURNING 1
    )
    SELECT count(*)::integer FROM d;
$$;

-- ===================================================================== 5. access
ALTER TABLE payout_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE payout_answers ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON payout_requests, payout_answers FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE payout_requests_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON payout_requests TO authenticated;
GRANT USAGE ON SEQUENCE payout_requests_id_seq TO authenticated;
GRANT SELECT ON payout_answers TO authenticated;
GRANT SELECT ON payout_requests, payout_answers TO valemont_readonly;
REVOKE ALL ON FUNCTION public.payout_prune(timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.payout_requests_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.payout_requests_queue() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.payout_answers_guard() FROM PUBLIC, anon, authenticated;

DROP POLICY IF EXISTS payout_requests_owner_read ON payout_requests;
CREATE POLICY payout_requests_owner_read ON payout_requests FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS payout_requests_owner_insert ON payout_requests;
CREATE POLICY payout_requests_owner_insert ON payout_requests FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS payout_answers_owner_read ON payout_answers;
CREATE POLICY payout_answers_owner_read ON payout_answers FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

INSERT INTO migration_log (name) VALUES ('094_payout_requests')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 6. self-test
-- Run on paste, rolled back ('selftest-rollback'). The owner's session is
-- simulated as in db/091 (request.jwt.claims); the worker's writes run as the
-- paste's role. A statement that calls a writing function stores its result
-- first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_id    bigint;
    v_id2   bigint;
    v_n     int;
    v_row   record;
    v_rho   text := repeat('a', 64);
BEGIN
    BEGIN
        IF v_owner IS NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: no owner'; END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);

        -- ------------------------------------------------ 1. an insert is forced queued and queues one job
        INSERT INTO public.payout_requests (stake, payout, status, reason, answered_at, created_at, is_test)
        VALUES (10, 100, 'answered', 'x', now(), now() - interval '9 days', true)
        RETURNING id INTO v_id;
        SELECT status, reason, answered_at, created_at > now() - interval '1 minute' AS fresh, owner_id
          INTO v_row FROM public.payout_requests WHERE id = v_id;
        IF v_row.status <> 'queued' OR v_row.reason IS NOT NULL OR v_row.answered_at IS NOT NULL OR NOT v_row.fresh
           OR v_row.owner_id IS DISTINCT FROM v_owner THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the insert was not forced fresh and queued: %', v_row;
        END IF;
        SELECT count(*) INTO v_n FROM public.job_queue
         WHERE kind = 'build_payout' AND (payload ->> 'request_id')::bigint = v_id;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: % build_payout jobs', v_n; END IF;

        -- ------------------------------------------------ 2. the shape
        v_n := 0;
        BEGIN
            INSERT INTO public.payout_requests (stake, payout, is_test) VALUES (10, 10, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            INSERT INTO public.payout_requests (stake, payout, is_test) VALUES (0.5, 100, true);
        EXCEPTION WHEN check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 bad shapes refused', v_n; END IF;

        -- ------------------------------------------------ 3. settle once; the answer first; never changed
        v_n := 0;
        BEGIN
            UPDATE public.payout_requests SET status = 'answered' WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: answered without its answer'; END IF;
        INSERT INTO public.payout_answers (request_id, tickets, nearest, target_price, candidates, rho_version,
                                           compute_ms, is_test)
        VALUES (v_id, '[{"k": 2}]'::jsonb, false, 0.0935, 1200, v_rho, 900, false);
        IF (SELECT is_test FROM public.payout_answers WHERE request_id = v_id) IS NOT TRUE THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the answer did not take the request''s is_test';
        END IF;
        UPDATE public.payout_requests SET status = 'answered' WHERE id = v_id;
        IF (SELECT status FROM public.payout_requests WHERE id = v_id) <> 'answered'
           OR (SELECT answered_at FROM public.payout_requests WHERE id = v_id) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: not answered';
        END IF;
        v_n := 0;
        BEGIN
            UPDATE public.payout_requests SET status = 'failed', reason = 'late' WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.payout_answers SET nearest = true WHERE request_id = v_id;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            INSERT INTO public.payout_answers (request_id, tickets, nearest, target_price, candidates, rho_version,
                                               compute_ms)
            VALUES (v_id, '[]'::jsonb, true, 0.1, 0, v_rho, 1);
        EXCEPTION WHEN raise_exception OR unique_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.payout_requests SET stake = 20 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 4 changes refused', v_n; END IF;

        -- A second request: a queued request's stake never changes; it fails with a
        -- reason (failed needs one); then it never settles again and takes no answer.
        INSERT INTO public.payout_requests (stake, payout, is_test) VALUES (5, 50, true) RETURNING id INTO v_id2;
        v_n := 0;
        BEGIN
            UPDATE public.payout_requests SET stake = 6, status = 'failed', reason = 'x' WHERE id = v_id2;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.payout_requests SET status = 'failed' WHERE id = v_id2;
        EXCEPTION WHEN raise_exception OR check_violation THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 (a changed stake, failed without a reason)', v_n; END IF;
        UPDATE public.payout_requests SET status = 'failed', reason = 'the search failed: test' WHERE id = v_id2;
        v_n := 0;
        BEGIN
            UPDATE public.payout_requests SET reason = 'a second reason' WHERE id = v_id2;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            INSERT INTO public.payout_answers (request_id, tickets, nearest, target_price, candidates, rho_version,
                                               compute_ms)
            VALUES (v_id2, '[]'::jsonb, true, 0.1, 0, v_rho, 1);
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 (a failed request re-settled or answered)', v_n; END IF;

        -- ------------------------------------------------ 4. the rate limit: the 21st in 10 minutes is refused
        SELECT count(*) INTO v_n FROM public.payout_requests
         WHERE owner_id = v_owner AND created_at > now() - interval '10 minutes';
        FOR i IN 1 .. greatest(20 - v_n, 0) LOOP
            INSERT INTO public.payout_requests (stake, payout, is_test) VALUES (1, 2, true);
        END LOOP;
        v_n := 0;
        BEGIN
            INSERT INTO public.payout_requests (stake, payout, is_test) VALUES (1, 2, true);
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'payout_requests: at most 20 in 10 minutes%' THEN v_n := 1; ELSE RAISE; END IF;
        END;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the 21st request was not refused'; END IF;

        -- ------------------------------------------------ 5. the prune (tests only, everything older than now)
        v_n := public.payout_prune(now() + interval '1 second', true);
        IF v_n < 20 OR EXISTS (SELECT 1 FROM public.payout_requests WHERE id IN (v_id, v_id2))
           OR EXISTS (SELECT 1 FROM public.payout_answers WHERE request_id = v_id) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the prune deleted % (and the answer must go with its request)', v_n;
        END IF;

        -- ------------------------------------------------ 6. access
        IF has_table_privilege('anon', 'public.payout_requests', 'SELECT')
           OR has_table_privilege('authenticated', 'public.payout_requests', 'UPDATE')
           OR has_table_privilege('authenticated', 'public.payout_requests', 'DELETE')
           OR has_table_privilege('authenticated', 'public.payout_answers', 'INSERT')
           OR NOT has_table_privilege('authenticated', 'public.payout_requests', 'INSERT')
           OR NOT has_table_privilege('valemont_readonly', 'public.payout_answers', 'SELECT')
           OR has_function_privilege('authenticated', 'public.payout_prune(timestamptz, boolean)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '094 self-test passed (rolled back)';
END
$$;
