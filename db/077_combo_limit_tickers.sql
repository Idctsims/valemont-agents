-- 077_combo_limit_tickers.sql: at most 20 combo requests in any 10 minutes,
-- and market tickers for Combo mode (owner, Nov 11; Chat 8 Phase 3;
-- docs/correlation.md).
--
-- A request can hold the worker ~17 s (Monte Carlo at 1,000,000 draws), so
-- the owner's inserts are capped at 20 per rolling 10 minutes. The 21st is
-- refused at insert with the reason and the time the next one frees up
-- ("combo_requests: at most 20 combo prices in 10 minutes; the next one
-- frees up at 21:14:03 UTC"); the web shows it as the sheet's message.
--
-- combo_requests_guard is db/076's, with the count added to the INSERT
-- branch after the row is forced fresh (an advisory lock per owner, so two
-- inserts at once count each other).
--
-- Section 2: kalshi_board_game(..., p_tickers) (below).
--
-- Paste-by-hand, top to bottom, no BEGIN/COMMIT (section 5). The self-test
-- at the end exercises the guard (the limit, and db/076's insert rules still
-- in force) and rolls back.
--
-- Prerequisites: db/076.

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.combo_requests_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_leg   jsonb;
    v_n     int;
    v_test  int;
    v_recent int;
    v_free  timestamptz;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.status := 'queued';
        NEW.reason := NULL;
        NEW.answered_at := NULL;
        -- db/077: at most 20 requests in any 10 minutes per owner (a request
        -- can hold the worker ~17 s). One owner's inserts queue on a lock, so
        -- the count is exact.
        PERFORM pg_advisory_xact_lock(hashtext('combo_requests_rate'), hashtext(coalesce(NEW.owner_id::text, '')));
        SELECT count(*), min(r.created_at) + interval '10 minutes' INTO v_recent, v_free
          FROM public.combo_requests r
         WHERE r.owner_id IS NOT DISTINCT FROM NEW.owner_id AND r.created_at > now() - interval '10 minutes';
        IF v_recent >= 20 THEN
            RAISE EXCEPTION 'combo_requests: at most 20 combo prices in 10 minutes; the next one frees up at % UTC',
                to_char(v_free AT TIME ZONE 'UTC', 'HH24:MI:SS');
        END IF;
        IF jsonb_typeof(NEW.legs) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'combo_requests: legs must be a JSON array';
        END IF;
        v_n := jsonb_array_length(NEW.legs);
        IF v_n < 2 OR v_n > 25 THEN
            RAISE EXCEPTION 'combo_requests: 2 to 25 legs (got %)', v_n;
        END IF;
        FOR v_leg IN SELECT x FROM jsonb_array_elements(NEW.legs) AS t (x) LOOP
            -- The type first, alone: jsonb_object_keys raises on a non-object.
            IF jsonb_typeof(v_leg) IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION 'combo_requests: each leg is {"market_ticker": "<KX…>", "side": "yes"|"no"} (got %)',
                    left(v_leg::text, 120);
            END IF;
            IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(v_leg) AS o (k))
                  IS DISTINCT FROM ARRAY['market_ticker', 'side']
               OR jsonb_typeof(v_leg -> 'market_ticker') IS DISTINCT FROM 'string'
               OR NOT (v_leg ->> 'market_ticker') ~ '^[A-Z0-9][A-Z0-9._-]{0,99}$'
               OR (v_leg ->> 'side') IS NULL OR (v_leg ->> 'side') NOT IN ('yes', 'no') THEN
                RAISE EXCEPTION 'combo_requests: each leg is {"market_ticker": "<KX…>", "side": "yes"|"no"} (got %)',
                    left(v_leg::text, 120);
            END IF;
        END LOOP;
        -- A test request is one whose every leg is a test board market.
        SELECT count(*) INTO v_test
          FROM jsonb_array_elements(NEW.legs) AS t (x)
          JOIN public.kalshi_board b ON b.market_ticker = t.x ->> 'market_ticker'
         WHERE b.is_test;
        IF v_test > 0 AND v_test < v_n THEN
            RAISE EXCEPTION 'combo_requests: a request never mixes test and real legs';
        END IF;
        NEW.is_test := (v_test = v_n);
        RETURN NEW;
    END IF;

    -- UPDATE: the worker settles a queued request, once.
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.legs IS DISTINCT FROM OLD.legs OR NEW.is_test IS DISTINCT FROM OLD.is_test
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'combo_requests: a request''s legs and identity never change';
    END IF;
    IF OLD.status <> 'queued' OR NEW.status NOT IN ('answered', 'refused', 'failed') THEN
        RAISE EXCEPTION 'combo_requests: a request settles once, from queued (% -> %)', OLD.status, NEW.status;
    END IF;
    NEW.answered_at := now();
    IF NEW.status = 'answered' THEN
        IF NEW.reason IS NOT NULL THEN
            RAISE EXCEPTION 'combo_requests: an answered request carries no reason';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.combo_quotes q WHERE q.request_id = NEW.id) THEN
            RAISE EXCEPTION 'combo_requests: answered needs its quote first';
        END IF;
    ELSE
        IF NEW.reason IS NULL OR btrim(NEW.reason) = '' THEN
            RAISE EXCEPTION 'combo_requests: % needs a reason', NEW.status;
        END IF;
        IF EXISTS (SELECT 1 FROM public.combo_quotes q WHERE q.request_id = NEW.id) THEN
            RAISE EXCEPTION 'combo_requests: a quoted request is answered, never %', NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- ===================================================================== 2
-- kalshi_board_game gains p_tickers (default false: every caller unchanged).
-- In Combo mode the page asks for each market's ticker suffix `x`, so a
-- ticked leg names its market; off, the same fields as db/074's
-- (the 115 KB target). The market ticker is the last ORDER BY key, so the
-- order is total. Dropped and re-created: a second overload with a default
-- would make every 4-argument call ambiguous.
DROP FUNCTION IF EXISTS public.kalshi_board_game(text, text, boolean, boolean);
CREATE FUNCTION public.kalshi_board_game(p_game text, p_kind text DEFAULT NULL,
                                         p_combo_only boolean DEFAULT false,
                                         p_tests boolean DEFAULT false,
                                         p_tickers boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH x AS (
        SELECT x.*, coalesce(x.edge_no, -9) > coalesce(x.edge_yes, -9) AS isno
          FROM public.leg_consensus_for(ARRAY[p_game]) x
    ),
    q AS (
        SELECT x.market_ticker,
               CASE WHEN x.p_consensus IS NOT NULL
                         AND (CASE WHEN x.isno THEN x.edge_no ELSE x.edge_yes END) IS NOT NULL THEN
                   (CASE x.tier WHEN 'books' THEN 'b' || x.n_books WHEN 'espn' THEN 'e' ELSE 'k' END)
                   || chr(97 + (CASE WHEN x.isno THEN 8 ELSE 0 END) + (CASE WHEN x.edge_claimed THEN 1 ELSE 0 END)
                              + (CASE WHEN x.stale_kalshi THEN 2 ELSE 0 END)
                              + (CASE WHEN x.stale_outside THEN 4 ELSE 0 END))
                   || lpad(least(round(x.p_consensus * 1000), 999)::int::text, 3, '0')
                   || round((CASE WHEN x.isno THEN x.edge_no ELSE x.edge_yes END) * 1000)::int
               END AS packed
          FROM x
    ),
    m AS (
        SELECT b.market_ticker, e.event_ticker, s.title AS series_title, e.combo_eligible, e.combo_yes_only, e.combo_size_max,
               e.fetched_at, b.payout,
               b.market_kind, b.variant, b.label, b.subject, b.line, b.yes_bid, b.yes_ask, b.taker_fee_at_ask,
               CASE b.market_kind WHEN 'game_line' THEN 0 WHEN 'prop' THEN 1 ELSE 2 END AS k_order,
               q.packed AS cq
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
          LEFT JOIN q ON q.market_ticker = b.market_ticker
         WHERE e.game_ticker = p_game AND (NOT b.is_test OR p_tests)
           AND (p_kind IS NULL OR b.market_kind = p_kind)
           AND (NOT p_combo_only OR (e.combo_eligible AND b.payout = 'binary'))
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'e', m.event_ticker, 'sr', min(m.series_title),
               'c', CASE WHEN bool_or(m.combo_eligible AND m.payout = 'binary') THEN true END,
               'y', CASE WHEN bool_or(m.combo_yes_only AND m.payout = 'binary') THEN true END,
               'sm', max(m.combo_size_max) FILTER (WHERE m.payout = 'binary'),
               'asof', max(m.fetched_at),
               'm', jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'k', m.market_kind, 'lb', m.label, 'b', m.yes_bid, 'a', m.yes_ask,
                        'f', m.taker_fee_at_ask, 'v', m.variant,
                        's', CASE WHEN m.payout = 'scalar' THEN true END, 'q', m.cq,
                        -- db/077: the ticker after the event's ("" when they are one), Combo mode only.
                        'x', CASE WHEN p_tickers THEN substr(m.market_ticker, length(m.event_ticker) + 2) END))
                    ORDER BY m.k_order, m.subject NULLS LAST, m.line NULLS FIRST, m.label, m.market_ticker)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.k_order), min(m.series_title), m.event_ticker;
$$;
REVOKE ALL ON FUNCTION public.kalshi_board_game(text, text, boolean, boolean, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.kalshi_board_game(text, text, boolean, boolean, boolean)
    TO authenticated, valemont_readonly;

INSERT INTO migration_log (name) VALUES ('077_combo_limit_tickers')
ON CONFLICT (name) DO NOTHING;

-- Self-test, run on paste, rolled back ('selftest-rollback'). Requests
-- already in the table for the owner in the last 10 minutes count toward the
-- limit; the test reads that count first and fills up to 20 itself.
DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-077-' || md5(random()::text);
    v_game    text;
    v_legs    jsonb;
    v_have    int;
    v_id      bigint;
    i         int;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        v_legs := jsonb_build_array(jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes'),
                                    jsonb_build_object('market_ticker', v_game || 'PTS-P1-10', 'side', 'yes'));

        SELECT count(*) INTO v_have FROM public.combo_requests
         WHERE owner_id = v_owner AND created_at > now() - interval '10 minutes';
        IF v_have > 20 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % requests already in the window', v_have;
        END IF;
        FOR i IN 1 .. 20 - v_have LOOP
            INSERT INTO public.combo_requests (legs) VALUES (v_legs);
        END LOOP;
        BEGIN
            INSERT INTO public.combo_requests (legs) VALUES (v_legs);
            RAISE EXCEPTION 'SELFTEST FAILED: the 21st request in 10 minutes was accepted';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: at most 20 combo prices in 10 minutes; the next one frees up at %UTC' THEN
                RAISE;
            END IF;
        END;
        -- Requests older than 10 minutes free their slots.
        ALTER TABLE public.combo_requests DISABLE TRIGGER combo_requests_guard;
        UPDATE public.combo_requests SET created_at = now() - interval '11 minutes'
         WHERE owner_id = v_owner AND created_at > now() - interval '10 minutes';
        ALTER TABLE public.combo_requests ENABLE TRIGGER combo_requests_guard;
        INSERT INTO public.combo_requests (legs, status) VALUES (v_legs, 'answered') RETURNING id INTO v_id;
        IF (SELECT status FROM public.combo_requests WHERE id = v_id) <> 'queued'
           OR NOT (SELECT is_test FROM public.combo_requests WHERE id = v_id) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a request after the window is not a fresh queued test request';
        END IF;
        -- db/076's insert rules are still in force.
        BEGIN
            INSERT INTO public.combo_requests (legs) VALUES ('[{"market_ticker": "KXA-1", "side": "yes"}]');
            RAISE EXCEPTION 'SELFTEST FAILED: one leg accepted';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: 2 to 25 legs%' THEN RAISE; END IF;
        END;
        BEGIN
            INSERT INTO public.combo_requests (legs) VALUES (jsonb_build_array(
                jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes'),
                jsonb_build_object('market_ticker', 'KXNOTONBOARD-1', 'side', 'yes')));
            RAISE EXCEPTION 'SELFTEST FAILED: test and real legs mixed';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: a request never mixes%' THEN RAISE; END IF;
        END;
        -- The reader: tickers only when asked, each naming a real market of the game.
        IF EXISTS (SELECT 1 FROM public.kalshi_board_game(v_game, NULL, false, true) ev,
                          jsonb_array_elements(ev -> 'm') mk WHERE mk ? 'x') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: tickers without p_tickers';
        END IF;
        IF (SELECT count(*) FROM public.kalshi_board_game(v_game, NULL, false, true, true) ev,
                   jsonb_array_elements(ev -> 'm') mk
             WHERE EXISTS (SELECT 1 FROM public.kalshi_board b
                            WHERE b.market_ticker = CASE WHEN mk ->> 'x' = '' THEN ev ->> 'e'
                                                         ELSE (ev ->> 'e') || '-' || (mk ->> 'x') END))
           <> (SELECT count(*) FROM public.kalshi_board b JOIN public.kalshi_board_events e USING (event_ticker)
                WHERE e.game_ticker = v_game) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the tickers do not name the game''s markets';
        END IF;
        IF has_function_privilege('anon', 'public.kalshi_board_game(text, text, boolean, boolean, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_game(text, text, boolean, boolean, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_game(text, text, boolean, boolean, boolean)', 'EXECUTE')
           OR to_regprocedure('public.kalshi_board_game(text, text, boolean, boolean)') IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_game grants or the old signature';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '077_combo_limit_tickers') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF (SELECT tgenabled FROM pg_trigger WHERE tgname = 'combo_requests_guard') <> 'O' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the guard is not enabled';
        END IF;
        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '077 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '077_combo_limit_tickers';
-- (b) md5 of combo_requests_guard's body against the scratch copy's (CRs removed);
-- (c) SELECT tgenabled FROM pg_trigger WHERE tgname = 'combo_requests_guard';   -- O
-- (d) md5 of kalshi_board_game's body against the scratch copy's; its grants;
--     octet_length of one expanded NBA game, p_tickers off (<= 115 KB) and on.
