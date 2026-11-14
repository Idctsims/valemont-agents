-- 080_consensus_span_pass.sql
--
-- Chat 8 Phase 3.5 (owner, 2026-11-14). Paste into the Supabase SQL Editor
-- AFTER db/078. Runs top-to-bottom, no BEGIN/COMMIT.
--
-- WHY. db/078's leg_consensus_for priced EVERY row three times: once at the
-- shipped method, then at p_books_lo and p_books_hi for the four-method test,
-- although only a books row that carries a span needs the second and third
-- pass. consensus_leg costs ~0.11 ms a call and is nearly the whole function.
-- Measured on two local copies of the Nov 14 board (52,291 markets, the same
-- rows; three runs each):
--                                  db/074      db/078        this file
--   v_leg_consensus, full read     6.3 s       18.1 s        (see the PR)
--   ctx_consensus()                1.8 s        5.0 s
--   leg_consensus_for, one NBA game 165 ms     432 ms
-- On production a full v_leg_consensus read hit the 30 s statement timeout.
--
-- WHAT. leg_consensus_for only, same signature, same columns, same values:
-- the second and third pricing run inside a CASE, only for a span row. The
-- claim rule is db/078's, unchanged: books tier only, fresh prices both
-- sides, positive under the shipped method AND the least favourable one.
-- No table, column, grant or other function changes.

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.leg_consensus_for(p_games text[])
RETURNS TABLE (market_ticker text, event_ticker text, game_ticker text, series_ticker text, sport text,
               league text, starts_at timestamptz, market_kind text, subject text, stat text, line numeric,
               label text, is_test boolean, tier text, n_books smallint, p_books numeric, p_books_mult numeric,
               p_espn numeric, p_kalshi numeric, normalized boolean, p_consensus numeric, yes_bid numeric,
               yes_ask numeric, no_ask numeric, fee_yes numeric, fee_no numeric, edge_yes numeric,
               edge_no numeric, edge_claimed boolean, kalshi_at timestamptz, outside_at timestamptz,
               stale_kalshi boolean, stale_outside boolean, inputs jsonb, computed_at timestamptz)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (
        SELECT b.market_ticker, b.event_ticker, e.game_ticker, e.series_ticker, s.sport, s.league, e.starts_at,
               b.market_kind, b.subject, b.stat, b.line, b.label, b.is_test, b.yes_bid, b.yes_ask,
               b.last_changed_at, s.fee_multiplier, e.mutually_exclusive, e.fetched_at AS kalshi_at,
               (b.yes_bid + b.yes_ask) / 2 AS mid,
               sum((b.yes_bid + b.yes_ask) / 2) OVER w AS mid_sum,
               count(*) OVER w AS n_ev,
               count(b.yes_bid + b.yes_ask) OVER w AS n_quoted
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE b.payout = 'binary' AND (p_games IS NULL OR e.game_ticker = ANY(p_games))
        WINDOW w AS (PARTITION BY b.event_ticker)
    ),
    k AS (
        SELECT m.*,
               coalesce(m.mutually_exclusive AND m.n_ev >= 2 AND m.n_quoted = m.n_ev
                        AND m.mid_sum BETWEEN 0.95 AND 1.05, false) AS norm
          FROM m
    ),
    kk AS (
        SELECT k.*, round(CASE WHEN k.norm THEN k.mid / k.mid_sum ELSE k.mid END, 6) AS pk FROM k
    ),
    r AS (
        SELECT kk.*, c.tier AS c_tier, c.n_books AS c_n, c.p_books AS c_pb, c.p_books_mult AS c_pbm,
               c.p_espn AS c_pe, c.inputs AS c_inputs, c.computed_at AS c_computed, c.source_at AS c_source,
               q.p_consensus AS q_p, q.fee_yes AS q_fy, q.fee_no AS q_fn, q.edge_yes AS q_ey, q.edge_no AS q_en,
               -- The same blend and fee at the least favourable of the four methods (db/078). db/080: only a
               -- row that carries a span is priced a second and third time. A CASE runs its subquery only
               -- when the guard holds (a WHERE inside a lateral was planned as a filter AFTER the call and
               -- saved nothing; a joined-back CTE cost a mis-sized hash join).
               CASE WHEN c.tier = 'books' AND c.p_books_lo IS NOT NULL AND c.p_books_hi IS NOT NULL THEN
                    (SELECT x.edge_yes
                       FROM public.consensus_leg(c.p_books_lo, kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) x)
               END AS lo_ey,
               CASE WHEN c.tier = 'books' AND c.p_books_lo IS NOT NULL AND c.p_books_hi IS NOT NULL THEN
                    (SELECT x.edge_no
                       FROM public.consensus_leg(c.p_books_hi, kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) x)
               END AS hi_en,
               (c.tier = 'books' AND c.p_books_lo IS NOT NULL AND c.p_books_hi IS NOT NULL) AS tested,
               -- The Kalshi price is fresh when its event was read in the last 20 min.
               (kk.kalshi_at < now() - interval '20 minutes') AS sk,
               -- The outside price is fresh when fetched in the last 12 h (no outside: not stale).
               coalesce(c.source_at < now() - interval '12 hours', false) AS so
          FROM kk
          LEFT JOIN public.leg_consensus c ON c.market_ticker = kk.market_ticker
         CROSS JOIN LATERAL public.consensus_leg(CASE c.tier WHEN 'books' THEN c.p_books WHEN 'espn' THEN c.p_espn END,
                                                 kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) q
    )
    SELECT r.market_ticker, r.event_ticker, r.game_ticker, r.series_ticker, r.sport, r.league, r.starts_at,
           r.market_kind, r.subject, r.stat, r.line, r.label, r.is_test,
           coalesce(r.c_tier, 'kalshi'), coalesce(r.c_n, 0)::smallint, r.c_pb, r.c_pbm, r.c_pe,
           r.pk, r.norm, r.q_p, r.yes_bid, r.yes_ask, 1 - r.yes_bid, r.q_fy, r.q_fn, r.q_ey, r.q_en,
           -- Only the books tier can claim, only on fresh prices both sides, and only when the side's edge
           -- is positive under the shipped method AND under the least favourable of the four.
           (coalesce(r.tested, false) AND NOT r.sk AND NOT r.so
            AND ((coalesce(r.q_ey, -1) > 0 AND coalesce(r.lo_ey, -1) > 0)
                 OR (coalesce(r.q_en, -1) > 0 AND coalesce(r.hi_en, -1) > 0))),
           r.kalshi_at, r.c_source, r.sk, r.so,
           r.c_inputs, coalesce(r.c_computed, r.last_changed_at)
      FROM r;
$$;

-- ===================================================================== 2
INSERT INTO migration_log (name) VALUES ('080_consensus_span_pass')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 3
-- Self-test, run on paste, rolled back ('selftest-rollback'): db/078's cases
-- against this body (a books YES claim, a books NO claim, a pass at the
-- shipped method that fails the least favourable one, espn never, no span
-- never, a stale Kalshi price never), plus a stale outside price and one row
-- per market. Rows use series KXSELFTEST080 (no real series has it). A
-- statement that calls a writing function stores its result first and reads
-- back in a later statement (db/062's rule). No INSERT ... VALUES tuple in
-- this file except the log row.
DO $$
DECLARE
    v_t0   timestamptz := now() - interval '1 minute';
    v_soon timestamptz := now() + interval '2 hours';
    v_n    int;
    v_row  record;
    v_gt   text := 'KXSELFTEST080GAME-26NOV13AAABBB';
    v_ev   text := 'KXSELFTEST080-26NOV13AAABBB';
    v_a    text := 'KXSELFTEST080-26NOV13AAABBB-AAA';
    v_b    text := 'KXSELFTEST080-26NOV13AAABBB-BBB';
    v_ev2  text := 'KXSELFTEST080-26NOV13CCCDDD';
    v_c    text := 'KXSELFTEST080-26NOV13CCCDDD-CCC';
    v_d    text := 'KXSELFTEST080-26NOV13CCCDDD-DDD';
BEGIN
    BEGIN
        v_n := public.kalshi_board_series_upsert('[{"series_ticker": "KXSELFTEST080", "title": "Selftest 078",
            "sport": "Basketball", "league": "nba", "category": "Sports", "tags": ["Basketball"],
            "fee_type": "quadratic", "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"},
            "combo_eligible": false, "collection_tickers": []}]'::jsonb);
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', v_ev, 'series_ticker', 'KXSELFTEST080', 'game_ticker', v_gt, 'game_title', 'AAA vs BBB',
            'milestone_start', v_soon, 'mutually_exclusive', true),
            jsonb_build_object('event_ticker', v_ev2, 'series_ticker', 'KXSELFTEST080', 'game_ticker', v_gt,
            'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', true)), v_t0);
        -- Mids 0.62 + 0.36 = 0.98 (normalized). AAA: ask 0.63, fee 0.016317, so YES costs 0.646317.
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(
            jsonb_build_object('market_ticker', v_a, 'event_ticker', v_ev, 'market_kind', 'game_line',
                               'subject', 'AAA', 'label', 'AAA', 'yes_bid', 0.61, 'yes_ask', 0.63,
                               'taker_fee_at_ask', 0.016317),
            jsonb_build_object('market_ticker', v_b, 'event_ticker', v_ev, 'market_kind', 'game_line',
                               'subject', 'BBB', 'label', 'BBB', 'yes_bid', 0.35, 'yes_ask', 0.37,
                               'taker_fee_at_ask', 0.016317),
            -- Mids 0.475 + 0.475 = 0.95, normalized to 0.5: Kalshi's own number already reads YES +0.002528.
            jsonb_build_object('market_ticker', v_c, 'event_ticker', v_ev2, 'market_kind', 'game_line',
                               'subject', 'CCC', 'label', 'CCC', 'yes_bid', 0.47, 'yes_ask', 0.48),
            jsonb_build_object('market_ticker', v_d, 'event_ticker', v_ev2, 'market_kind', 'game_line',
                               'subject', 'DDD', 'label', 'DDD', 'yes_bid', 0.47, 'yes_ask', 0.48)), v_t0);
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: market insert wrote %', v_n; END IF;

        -- ------------------------------------------------ 1. books, all four agree: claimed
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.69,
            'p_books_lo', 0.69, 'p_books_hi', 0.70, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: consensus insert wrote %', v_n; END IF;
        SELECT x.tier, x.edge_yes, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_a;
        IF v_row.tier <> 'books' OR v_row.edge_yes <= 0 OR v_row.edge_claimed IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a books leg positive under all four reads %', v_row;
        END IF;

        -- ------------------------------------------------ 2. books, positive under power only: not claimed
        -- p_lo 0.64: 0.7 * 0.64 + 0.3 * 0.632653 = 0.637796, under the 0.646317 cost.
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.64,
            'p_books_lo', 0.64, 'p_books_hi', 0.70, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_yes, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_a;
        IF v_n <> 1 OR v_row.edge_yes <= 0 OR v_row.edge_claimed IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an edge that fails the least favourable method reads % (wrote %)',
                v_row, v_n;
        END IF;

        -- ------------------------------------------------ 3. books, not tested (the deployed worker): not claimed
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.64,
            'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_yes, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_a;
        IF v_n <> 1 OR v_row.edge_yes <= 0 OR v_row.edge_claimed IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an untested books leg reads % (wrote %)', v_row, v_n;
        END IF;

        -- ------------------------------------------------ 4. espn: the edge is shown, never claimed
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'espn', 'n_books', 1, 'p_espn', 0.80,
            'inputs', '{"m": "power", "src": "espn"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.tier, x.edge_yes, x.edge_claimed, x.stale_kalshi, x.stale_outside INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_a;
        IF v_n <> 1 OR v_row.tier <> 'espn' OR v_row.edge_yes <= 0.05 OR v_row.stale_kalshi OR v_row.stale_outside
           OR v_row.edge_claimed IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a fresh espn leg with a large edge reads % (wrote %)', v_row, v_n;
        END IF;

        -- Where Kalshi's own normalized number is already positive, the least favourable test alone would
        -- pass (a NULL span prices at the Kalshi mid). The tier decides: espn and an untested books row
        -- still claim nothing.
        v_n := public.leg_consensus_upsert(jsonb_build_array(
            jsonb_build_object('market_ticker', v_c, 'tier', 'espn', 'n_books', 1, 'p_espn', 0.60,
                               'inputs', '{"m": "power", "src": "espn"}'::jsonb, 'source_at', now()),
            jsonb_build_object('market_ticker', v_d, 'tier', 'books', 'n_books', 2, 'p_books', 0.60,
                               'p_books_mult', 0.60, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: the CCC/DDD rows wrote %', v_n; END IF;
        SELECT count(*) FILTER (WHERE x.edge_yes > 0) AS pos, count(*) FILTER (WHERE x.edge_claimed) AS claimed
          INTO v_row FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.event_ticker = v_ev2;
        IF v_row.pos <> 2 OR v_row.claimed <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: espn and untested books over a positive Kalshi number read %', v_row;
        END IF;

        -- ------------------------------------------------ 5. the NO side is tested at p_books_hi
        -- BBB: no_ask 0.65, fee 0.015925, NO costs 0.665925. p 0.20: NO edge positive at hi 0.22, not at 0.40.
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_b, 'tier', 'books', 'n_books', 2, 'p_books', 0.20, 'p_books_mult', 0.21,
            'p_books_lo', 0.20, 'p_books_hi', 0.22, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_no, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_b;
        IF v_n <> 1 OR v_row.edge_no <= 0 OR v_row.edge_claimed IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NO edge positive under all four reads % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_b, 'tier', 'books', 'n_books', 2, 'p_books', 0.20, 'p_books_mult', 0.40,
            'p_books_lo', 0.20, 'p_books_hi', 0.40, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_no, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_b;
        IF v_n <> 1 OR v_row.edge_no <= 0 OR v_row.edge_claimed IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NO edge that fails at p_books_hi reads % (wrote %)', v_row, v_n;
        END IF;

        -- ------------------------------------------------ 5b. an outside price over 12 h old never claims
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_b, 'tier', 'books', 'n_books', 2, 'p_books', 0.20, 'p_books_mult', 0.21,
            'p_books_lo', 0.20, 'p_books_hi', 0.22, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_claimed INTO v_row FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_b;
        IF v_n <> 1 OR v_row.edge_claimed IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the NO claim did not come back: % (wrote %)', v_row, v_n;
        END IF;
        UPDATE public.leg_consensus SET source_at = now() - interval '13 hours' WHERE market_ticker = v_b;
        SELECT x.edge_no, x.edge_claimed, x.stale_outside INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_b;
        IF v_row.edge_no <= 0 OR v_row.edge_claimed IS DISTINCT FROM false OR v_row.stale_outside IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a tested books leg on a stale outside price reads %', v_row;
        END IF;

        -- ------------------------------------------------ 5c. one row per market, with two span rows in the game
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.69,
            'p_books_lo', 0.69, 'p_books_hi', 0.70, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the second span row wrote %', v_n; END IF;
        SELECT count(*) AS n, count(DISTINCT x.market_ticker) AS d INTO v_row FROM public.leg_consensus_for(ARRAY[v_gt]) x;
        IF v_row.n <> 4 OR v_row.d <> 4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the game reads % rows for 4 markets', v_row;
        END IF;

        -- ------------------------------------------------ 6. stale prices still never claim
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_a, 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.69,
            'p_books_lo', 0.69, 'p_books_hi', 0.70, 'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        UPDATE public.kalshi_board_events SET fetched_at = now() - interval '21 minutes' WHERE event_ticker = v_ev;
        SELECT x.edge_claimed, x.stale_kalshi INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_a;
        IF v_row.edge_claimed IS DISTINCT FROM false OR v_row.stale_kalshi IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a tested books leg on a stale Kalshi price reads %', v_row;
        END IF;

        -- ------------------------------------------------ 8. objects
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '080_consensus_span_pass') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_function_privilege('anon', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.leg_consensus_upsert(jsonb, timestamptz, boolean)',
                                     'EXECUTE')
           OR has_table_privilege('anon', 'public.v_leg_consensus', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_leg_consensus', 'SELECT')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '080 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '080_consensus_span_pass';
-- (b) md5 of leg_consensus_for's body against the scratch copy's; grants unchanged.
-- (c) EXPLAIN (ANALYZE, TIMING OFF) SELECT * FROM v_leg_consensus;   -- three runs, quiet database
--     the same for SELECT ctx_consensus() and leg_consensus_for(ARRAY['<largest game>']).
-- (d) SELECT tier, count(*), count(*) FILTER (WHERE edge_claimed) FROM v_leg_consensus GROUP BY 1;
