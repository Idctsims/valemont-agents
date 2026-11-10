-- 074_consensus_fresh.sql: stale prices never claim edge; the compact q; the
-- account's own credit count (Chat 8 Phase 2; owner decisions, Nov 9, on
-- PR #106; docs/consensus.md).
--
-- The first real run (Nov 9, 23:02 UTC) claimed 23 edges that were stale
-- BOARD prices (the refresh had skipped those events at its call cap), and
-- Wags' "best edges tonight" would have shown them as real. So:
--
--   1. leg_consensus_for (dropped and re-created: its columns change) gains
--      kalshi_at (the event's last pull that asked for it,
--      kalshi_board_events.fetched_at: the full pull and the refresh both
--      touch it), outside_at (leg_consensus.source_at), stale_kalshi
--      (kalshi_at older than 20 min), stale_outside (outside_at older than
--      12 h). edge_claimed additionally requires both fresh; a stale leg
--      still shows its edge, tagged, and claims nothing. v_leg_consensus is
--      re-created on it (same grants).
--   2. leg_consensus_upsert advances source_at when an unchanged row is
--      confirmed by a newer pull (computed_at still moves only on a change),
--      so an outside line that did not move is not taken for stale.
--   3. kalshi_board_game's q is compact (owner, Nov 9; target <= 115 KB for
--      an expanded NBA game): the five fields [tier, p, side, edge, flags]
--      PACKED in one string, because no JSON array met the target (measured
--      on DEN at HOU, 960 markets, 95.2 KB without q: arrays at 6 dp 52.5 KB,
--      4 dp 35.5 KB, basis points 30.6 KB; this string 16.1 KB, ~111 KB):
--        q = <tier><letter><p><edge>, e.g. "b9c515-93"
--        tier   'b<n>' (books, n of them) | 'e' (espn) | 'k' (kalshi)
--        letter 'a' + flags: 8 the better side is NO, 1 claimed,
--               2 Kalshi price stale, 4 outside price stale
--        p      p_consensus in thousandths, 3 digits (0.1%)
--        edge   that side's net edge in thousandths of a dollar (0.1 cent)
--      The page takes the side's ask from the market (YES: a; NO: 1 - b);
--      its fee is the market's own f for YES, and p(side) - ask - edge for
--      NO (to 0.1 cent). A market with no p (no Kalshi mid) carries no q.
--   4. odds_api_usage gains `used`: the latest real call's x-requests-used,
--      the account's own month count (the worker's credit guard reads it;
--      the odds_api_calls sum misses Step 0's 11 credits).
-- Then the migration_log row and the rolled-back self-test.
--
-- Backward-compatible with the deployed worker, which reads none of these.

-- ===================================================================== 1
DROP VIEW IF EXISTS v_leg_consensus;
DROP FUNCTION IF EXISTS public.leg_consensus_for(text[]);

CREATE FUNCTION public.leg_consensus_for(p_games text[])
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
           -- Only an outside line can claim edge, and only on fresh prices both sides.
           (r.c_tier IS NOT NULL AND NOT r.sk AND NOT r.so
            AND greatest(coalesce(r.q_ey, -1), coalesce(r.q_en, -1)) > 0),
           r.kalshi_at, r.c_source, r.sk, r.so,
           r.c_inputs, coalesce(r.c_computed, r.last_changed_at)
      FROM r;
$$;

CREATE VIEW v_leg_consensus WITH (security_invoker = true) AS
SELECT * FROM public.leg_consensus_for(NULL);

-- ===================================================================== 2
CREATE OR REPLACE FUNCTION public.leg_consensus_upsert(p_rows jsonb, p_at timestamptz, p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.leg_consensus AS c
               (market_ticker, tier, n_books, p_books, p_books_mult, p_espn, inputs, source_at, computed_at)
        SELECT r.market_ticker, r.tier, r.n_books, r.p_books, r.p_books_mult, r.p_espn, r.inputs, r.source_at, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   market_ticker text, tier text, n_books smallint, p_books numeric, p_books_mult numeric,
                   p_espn numeric, inputs jsonb, source_at timestamptz)
          JOIN public.kalshi_board b ON b.market_ticker = r.market_ticker
         WHERE b.is_test = p_is_test AND b.payout = 'binary'
        ON CONFLICT (market_ticker) DO UPDATE SET
            tier = EXCLUDED.tier, n_books = EXCLUDED.n_books, p_books = EXCLUDED.p_books,
            p_books_mult = EXCLUDED.p_books_mult, p_espn = EXCLUDED.p_espn, inputs = EXCLUDED.inputs,
            source_at = greatest(c.source_at, EXCLUDED.source_at),
            -- computed_at: when the values last changed (a confirmation alone does not move it).
            computed_at = CASE WHEN (c.tier, c.n_books, c.p_books, c.p_books_mult, c.p_espn, c.inputs)
                                    IS DISTINCT FROM
                                    (EXCLUDED.tier, EXCLUDED.n_books, EXCLUDED.p_books, EXCLUDED.p_books_mult,
                                     EXCLUDED.p_espn, EXCLUDED.inputs)
                               THEN EXCLUDED.computed_at ELSE c.computed_at END
         WHERE (c.tier, c.n_books, c.p_books, c.p_books_mult, c.p_espn, c.inputs)
               IS DISTINCT FROM
               (EXCLUDED.tier, EXCLUDED.n_books, EXCLUDED.p_books, EXCLUDED.p_books_mult, EXCLUDED.p_espn,
                EXCLUDED.inputs)
            OR EXCLUDED.source_at > c.source_at
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- ===================================================================== 3
CREATE OR REPLACE FUNCTION public.kalshi_board_game(p_game text, p_kind text DEFAULT NULL,
                                                    p_combo_only boolean DEFAULT false,
                                                    p_tests boolean DEFAULT false)
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
        SELECT e.event_ticker, s.title AS series_title, e.combo_eligible, e.combo_yes_only, e.combo_size_max,
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
                        's', CASE WHEN m.payout = 'scalar' THEN true END, 'q', m.cq))
                    ORDER BY m.k_order, m.subject NULLS LAST, m.line NULLS FIRST, m.label)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.k_order), min(m.series_title), m.event_ticker;
$$;

-- ===================================================================== 4
CREATE OR REPLACE FUNCTION public.odds_api_usage()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (SELECT date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS start)
    SELECT jsonb_build_object(
        'month_start', m.start,
        'spent', coalesce((SELECT sum(c.cost) FROM public.odds_api_calls c
                            WHERE NOT c.is_test AND c.created_at >= m.start), 0),
        -- The account's own count (x-requests-used of the latest real call
        -- that reported one): what the credit guard reads (owner, Nov 9).
        'used', (SELECT c.used FROM public.odds_api_calls c
                  WHERE NOT c.is_test AND c.used IS NOT NULL ORDER BY c.id DESC LIMIT 1),
        'calls', (SELECT count(*) FROM public.odds_api_calls c WHERE NOT c.is_test AND c.created_at >= m.start),
        'remaining', (SELECT c.remaining FROM public.odds_api_calls c
                       WHERE NOT c.is_test AND c.remaining IS NOT NULL ORDER BY c.id DESC LIMIT 1),
        'last_at', (SELECT max(c.created_at) FROM public.odds_api_calls c WHERE NOT c.is_test),
        'allowance', 500)
      FROM m;
$$;

-- ===================================================================== 5
-- Access: the view and function were re-created (their grants with them).
REVOKE ALL ON v_leg_consensus FROM anon, authenticated, PUBLIC;
GRANT SELECT ON v_leg_consensus TO authenticated, valemont_readonly;
REVOKE ALL ON FUNCTION public.leg_consensus_for(text[]) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leg_consensus_for(text[]) TO authenticated, valemont_readonly;
-- leg_consensus_upsert, kalshi_board_game and odds_api_usage keep db/073's
-- and db/071's grants (CREATE OR REPLACE).

-- ===================================================================== 6
INSERT INTO migration_log (name) VALUES ('074_consensus_fresh')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 7
-- Self-test, run on paste, rolled back ('selftest-rollback'). Real rows use
-- series KXSELFTEST074 (no real series has it). Every statement that calls a
-- writing function stores its result first and reads back in a later
-- statement (db/062's rule); every INSERT tuple was checked against its
-- column list before handover (db/054's rule).
DO $$
DECLARE
    v_t0      timestamptz := now() - interval '1 minute';
    v_soon    timestamptz := now() + interval '2 hours';
    v_n       int;
    v_j       jsonb;
    v_q       text[];
    v_row     record;
    v_id      bigint;
    v_gt      text := 'KXSELFTEST074GAME-26NOV10AAABBB';
    v_ev      text := 'KXSELFTEST074-26NOV10AAABBB';
    v_ev2     text := 'KXSELFTEST074-26NOV10CCCDDD';
    v_cons    jsonb;
BEGIN
    BEGIN
        v_n := public.kalshi_board_series_upsert('[{"series_ticker": "KXSELFTEST074", "title": "Selftest 074",
            "sport": "Basketball", "league": "nba", "category": "Sports", "tags": ["Basketball"],
            "fee_type": "quadratic", "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"},
            "combo_eligible": false, "collection_tickers": []}]'::jsonb);
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', v_ev, 'series_ticker', 'KXSELFTEST074', 'game_ticker', v_gt, 'game_title', 'AAA vs BBB',
            'milestone_start', v_soon, 'mutually_exclusive', true),
            -- Mids 0.475 + 0.475 = 0.95, normalized: YES +0.002528 on the kalshi tier.
            jsonb_build_object('event_ticker', v_ev2, 'series_ticker', 'KXSELFTEST074', 'game_ticker', v_gt,
            'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', true)), v_t0);
        -- Two teams, mids 0.62 + 0.36 = 0.98 (normalized).
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(
            jsonb_build_object('market_ticker', v_ev || '-AAA', 'event_ticker', v_ev, 'market_kind', 'game_line',
                               'subject', 'AAA', 'label', 'AAA', 'yes_bid', 0.61, 'yes_ask', 0.63,
                               'taker_fee_at_ask', 0.016317),
            jsonb_build_object('market_ticker', v_ev || '-BBB', 'event_ticker', v_ev, 'market_kind', 'game_line',
                               'subject', 'BBB', 'label', 'BBB', 'yes_bid', 0.35, 'yes_ask', 0.37,
                               'taker_fee_at_ask', 0.016317),
            jsonb_build_object('market_ticker', v_ev2 || '-CCC', 'event_ticker', v_ev2, 'market_kind', 'game_line',
                               'subject', 'CCC', 'label', 'CCC', 'yes_bid', 0.47, 'yes_ask', 0.48),
            jsonb_build_object('market_ticker', v_ev2 || '-DDD', 'event_ticker', v_ev2, 'market_kind', 'game_line',
                               'subject', 'DDD', 'label', 'DDD', 'yes_bid', 0.47, 'yes_ask', 0.48)), v_t0);
        IF v_n <> 4 THEN RAISE EXCEPTION 'SELFTEST FAILED: market insert wrote %', v_n; END IF;
        -- A books row on AAA well above Kalshi: YES edge positive.
        v_cons := jsonb_build_array(jsonb_build_object(
            'market_ticker', v_ev || '-AAA', 'tier', 'books', 'n_books', 3, 'p_books', 0.70, 'p_books_mult', 0.69,
            'inputs', '{"m": "power"}'::jsonb, 'source_at', now() - interval '1 hour'));
        v_n := public.leg_consensus_upsert(v_cons, v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: consensus insert wrote %', v_n; END IF;

        -- ------------------------------------------------ 1. fresh: claimed
        SELECT x.edge_claimed, x.stale_kalshi, x.stale_outside, x.edge_yes, x.kalshi_at, x.outside_at INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_row.edge_claimed IS DISTINCT FROM true OR v_row.stale_kalshi OR v_row.stale_outside
           OR v_row.edge_yes <= 0 OR v_row.kalshi_at <> v_t0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a fresh books leg reads %', v_row;
        END IF;
        -- Fresh, positive, kalshi tier: shown, never claimed.
        SELECT x.tier, x.edge_claimed, x.edge_yes, x.stale_kalshi INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev2 || '-CCC';
        IF v_row.tier <> 'kalshi' OR v_row.edge_claimed IS DISTINCT FROM false OR v_row.edge_yes <> 0.002528
           OR v_row.stale_kalshi THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a fresh kalshi-tier positive reads %', v_row;
        END IF;
        -- A books row whose better side is NO: q carries side n and the NO edge.
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_ev || '-BBB', 'tier', 'books', 'n_books', 2, 'p_books', 0.20, 'p_books_mult', 0.20,
            'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT x.edge_no, x.edge_claimed INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-BBB';
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_ev;
        SELECT regexp_match(m->>'q', '^([bek][0-9]*)([a-p])([0-9]{3})(-?[0-9]+)$') INTO v_q
          FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'BBB';
        -- NO side (8) + claimed (1) = 'j'.
        IF v_n <> 1 OR v_q IS NULL OR v_q[1] <> 'b2' OR v_q[2] <> 'j'
           OR v_q[4]::int <> round(v_row.edge_no * 1000)::int
           OR v_row.edge_no <= 0 OR v_row.edge_claimed IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NO-side q reads % (%)', v_q, v_row;
        END IF;
        DELETE FROM public.leg_consensus WHERE market_ticker = v_ev || '-BBB';

        -- ------------------------------------------------ 2. the Kalshi limit: 20 min
        UPDATE public.kalshi_board_events SET fetched_at = now() - interval '19 minutes' WHERE event_ticker = v_ev;
        SELECT x.edge_claimed, x.stale_kalshi INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_row.edge_claimed IS DISTINCT FROM true OR v_row.stale_kalshi THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a Kalshi price 19 min old reads %', v_row;
        END IF;
        UPDATE public.kalshi_board_events SET fetched_at = now() - interval '21 minutes' WHERE event_ticker = v_ev;
        SELECT x.edge_claimed, x.stale_kalshi, x.edge_yes INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_row.edge_claimed IS DISTINCT FROM false OR v_row.stale_kalshi IS DISTINCT FROM true
           OR v_row.edge_yes <= 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a Kalshi price 21 min old reads %', v_row;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_ev;
        SELECT regexp_match(m->>'q', '^([bek][0-9]*)([a-p])([0-9]{3})(-?[0-9]+)$') INTO v_q
          FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'AAA';
        -- YES side, Kalshi stale (2), not claimed: 'c'; p in thousandths.
        SELECT x.p_consensus, x.edge_yes INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_q IS NULL OR v_q[1] <> 'b3' OR v_q[2] <> 'c' OR v_q[3]::int <> round(v_row.p_consensus * 1000)::int
           OR v_q[4]::int <> round(v_row.edge_yes * 1000)::int OR v_q[4]::int <= 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a stale leg''s q reads % (%)', v_q, v_row;
        END IF;
        UPDATE public.kalshi_board_events SET fetched_at = now() WHERE event_ticker = v_ev;

        -- ------------------------------------------------ 3. the outside limit: 12 h
        UPDATE public.leg_consensus SET source_at = now() - interval '11 hours' WHERE market_ticker = v_ev || '-AAA';
        SELECT x.edge_claimed, x.stale_outside INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_row.edge_claimed IS DISTINCT FROM true OR v_row.stale_outside THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an outside price 11 h old reads %', v_row;
        END IF;
        UPDATE public.leg_consensus SET source_at = now() - interval '13 hours' WHERE market_ticker = v_ev || '-AAA';
        SELECT x.edge_claimed, x.stale_outside INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-AAA';
        IF v_row.edge_claimed IS DISTINCT FROM false OR v_row.stale_outside IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an outside price 13 h old reads %', v_row;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_ev;
        SELECT regexp_match(m->>'q', '^([bek][0-9]*)([a-p])([0-9]{3})(-?[0-9]+)$') INTO v_q
          FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'AAA';
        IF v_q IS NULL OR v_q[2] <> 'e' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: q % for a stale outside (want flag letter e)', v_q;
        END IF;
        -- A kalshi-tier leg has no outside price: never outside-stale, never claimed.
        SELECT x.stale_outside, x.edge_claimed, x.outside_at INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.market_ticker = v_ev || '-BBB';
        IF v_row.stale_outside OR v_row.edge_claimed OR v_row.outside_at IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a kalshi-tier leg reads %', v_row;
        END IF;
        SELECT regexp_match(m->>'q', '^([bek][0-9]*)([a-p])([0-9]{3})(-?[0-9]+)$') INTO v_q
          FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'BBB';
        IF v_q IS NULL OR v_q[1] <> 'k' OR v_q[2] NOT IN ('a', 'i') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a kalshi-tier q reads %', v_q;
        END IF;
        -- The band-edge kalshi positive: shown (edge > 0), flag letter 'a' (not claimed).
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x WHERE x->>'e' = v_ev2;
        SELECT regexp_match(m->>'q', '^([bek][0-9]*)([a-p])([0-9]{3})(-?[0-9]+)$') INTO v_q
          FROM jsonb_array_elements(v_j->'m') m WHERE m->>'lb' = 'CCC';
        IF v_q IS NULL OR v_q[1] <> 'k' OR v_q[2] <> 'a' OR v_q[3] <> '500' OR v_q[4]::int <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the kalshi-tier positive''s q reads %', v_q;
        END IF;

        -- ------------------------------------------------ 4. a confirmation advances source_at only
        SELECT computed_at INTO v_row FROM public.leg_consensus WHERE market_ticker = v_ev || '-AAA';
        v_n := public.leg_consensus_upsert(jsonb_set(v_cons, '{0,source_at}', to_jsonb(now())), now());
        SELECT source_at, computed_at, v_row.computed_at AS before INTO v_row
          FROM public.leg_consensus WHERE market_ticker = v_ev || '-AAA';
        IF v_n <> 1 OR v_row.source_at <> now() OR v_row.computed_at <> v_row.before THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a confirmation reads % (wrote %)', v_row, v_n;
        END IF;
        -- An older pull with a changed value: the value is written, source_at never moves back.
        v_n := public.leg_consensus_upsert(jsonb_set(jsonb_set(v_cons, '{0,source_at}',
                                                               to_jsonb(now() - interval '2 hours')),
                                                     '{0,p_books}', '0.72'), now());
        SELECT source_at INTO v_row FROM public.leg_consensus WHERE market_ticker = v_ev || '-AAA';
        IF v_n <> 1 OR v_row.source_at <> now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an older confirmation moved source_at back: % (wrote %)', v_row, v_n;
        END IF;
        v_n := public.leg_consensus_upsert(jsonb_set(jsonb_set(v_cons, '{0,source_at}', to_jsonb(now())),
                                                     '{0,p_books}', '0.73'), now());
        SELECT computed_at INTO v_row FROM public.leg_consensus WHERE market_ticker = v_ev || '-AAA';
        IF v_n <> 1 OR v_row.computed_at <> now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a changed value did not move computed_at: % (wrote %)', v_row, v_n;
        END IF;

        -- ------------------------------------------------ 5. ctx lists fresh claimed edges only
        v_j := public.ctx_consensus();
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') x WHERE x->>'market' = 'AAA') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx lacks the fresh claimed edge: %', v_j->'best_edges_tonight';
        END IF;
        UPDATE public.kalshi_board_events SET fetched_at = now() - interval '30 minutes' WHERE event_ticker = v_ev;
        v_j := public.ctx_consensus();
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') x WHERE x->>'market' = 'AAA') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx lists a stale edge';
        END IF;

        -- ------------------------------------------------ 6. the account's own count
        INSERT INTO public.odds_api_calls (endpoint, sport, markets, regions, status, cost, used, remaining, bytes)
        VALUES ('/sports/:sport/odds', 'americanfootball_nfl', 'h2h,spreads,totals', 'us', 200, 3, 11, 489, 74501);
        INSERT INTO public.odds_api_calls (endpoint, sport, markets, regions, status, cost, used, remaining, bytes)
        VALUES ('/sports/:sport/odds', 'basketball_nba', 'h2h,spreads,totals', 'us', 200, 3, 20, 480, 48940);
        INSERT INTO public.odds_api_calls (endpoint, sport, status, cost, used, remaining, bytes, is_test)
        VALUES ('/sports/:sport/events', 'basketball_nba', 200, 0, 999, 1, 10, true);
        v_j := public.odds_api_usage();
        IF (v_j->>'used')::int <> 20 OR (v_j->>'remaining')::int <> 480 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: odds_api_usage reads %', v_j;
        END IF;

        -- ------------------------------------------------ 7. objects
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '074_consensus_fresh') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_table_privilege('anon', 'public.v_leg_consensus', 'SELECT')
           OR NOT has_table_privilege('authenticated', 'public.v_leg_consensus', 'SELECT')
           OR has_table_privilege('authenticated', 'public.v_leg_consensus', 'INSERT,UPDATE,DELETE')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_leg_consensus', 'SELECT')
           OR has_function_privilege('anon', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.leg_consensus_upsert(jsonb, timestamptz, boolean)',
                                     'EXECUTE')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '074 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '074_consensus_fresh';
-- (b) SELECT tier, count(*), count(*) FILTER (WHERE edge_claimed), count(*) FILTER (WHERE stale_kalshi),
--            count(*) FILTER (WHERE stale_outside) FROM v_leg_consensus GROUP BY 1;
-- (c) SELECT public.odds_api_usage();                                   -- used 20, remaining 480
-- (d) the size of one expanded NBA game: SELECT octet_length(string_agg(x::text, ''))
--       FROM kalshi_board_game('<game ticker>') x;                     -- target <= 115 KB
-- (e) md5 of the four function bodies against the scratch copy's.
