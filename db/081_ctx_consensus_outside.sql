-- 081_ctx_consensus_outside.sql
--
-- Chat 8 (owner, 2026-11-14). Paste into the Supabase SQL Editor AFTER
-- db/080. Runs top-to-bottom, no BEGIN/COMMIT.
--
-- WHY. ctx_consensus() runs on every Wags turn and every Morning Brief
-- (context_snapshot). It priced EVERY market of every game with an outside
-- line in the next 12 hours, then kept only the rows that claim an edge.
-- Only a books row can claim. On Nov 14 at 23:46 UTC that was ~13,000
-- markets priced for 499 outside rows: 4.35-4.42 s on production, by itself
-- more than half the signed-in role's 8 s statement limit, and the owner's
-- four Wags turns that evening were all refused at the snapshot.
--
-- WHAT.
--   1. leg_consensus_priced(games, outside_only): db/080's leg_consensus_for
--      body, plus one WHERE: with outside_only, only a market that has a
--      leg_consensus row is priced and returned. The event window is still
--      the whole event, so a normalized Kalshi mid is unchanged.
--   2. leg_consensus_for(games) = leg_consensus_priced(games, false). Same
--      signature, columns and values: the view, kalshi_board_game, the tab
--      readers and the worker's reads are untouched. One pricing
--      implementation, as before.
--   3. ctx_consensus(): the same five keys and the same text; its
--      best_edges_tonight reads leg_consensus_priced(..., true). The kalshi
--      tier stays a count (`counts`), as it already was.

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.leg_consensus_priced(p_games text[], p_outside_only boolean)
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
         -- db/081: with p_outside_only, only a market that has an outside line (a leg_consensus row) is
         -- priced and returned. kk above still spans the whole event, so a normalized Kalshi mid is the
         -- same number either way.
         WHERE NOT p_outside_only OR c.market_ticker IS NOT NULL
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
    -- The one pricing implementation is leg_consensus_priced (db/081); this is every market of the games.
    SELECT * FROM public.leg_consensus_priced(p_games, false);
$$;

-- ===================================================================== 3
CREATE OR REPLACE FUNCTION public.ctx_consensus()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago') AS tz),
    up AS (
        SELECT s.sport, b.market_ticker, e.game_ticker, e.starts_at, c.tier
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
          LEFT JOIN public.leg_consensus c ON c.market_ticker = b.market_ticker
         WHERE NOT b.is_test AND b.payout = 'binary' AND e.starts_at > now()
    ),
    best AS (
        SELECT x.*, greatest(coalesce(x.edge_yes, -9), coalesce(x.edge_no, -9)) AS edge
          -- Never NULL (NULL is the whole board): no game, no rows.
          FROM public.leg_consensus_priced(coalesce((SELECT array_agg(DISTINCT up.game_ticker) FROM up
                                                    WHERE up.tier IS NOT NULL
                                                      AND up.starts_at < now() + interval '12 hours'),
                                                 '{}'::text[]),
                                             -- db/081: only a market with an outside line can claim an edge.
                                             -- The kalshi tier is counted in `counts`, never priced here.
                                             true) x
         WHERE x.edge_claimed AND NOT x.is_test AND x.starts_at > now()
         ORDER BY edge DESC
         LIMIT 10
    )
    SELECT jsonb_build_object(
        'about', 'The consensus engine: each binary Kalshi market''s probability from de-vigged sportsbook lines '
                 '(books tier: the median of 2+ books at the exact line; espn tier: ESPN''s one book) blended '
                 '0.7 / 0.3 with Kalshi''s mid, or Kalshi''s own mid when no book has the exact line (kalshi '
                 'tier). Net edge is that probability minus the Kalshi ask and its taker fee; a kalshi-tier '
                 'edge is <= 0. Edge is not profit and not advice; nothing is ever bet automatically.',
        'as_of', (SELECT max(c.computed_at) FROM public.leg_consensus c
                    JOIN public.kalshi_board b ON b.market_ticker = c.market_ticker WHERE NOT b.is_test),
        'counts', coalesce((SELECT jsonb_object_agg(t.sport, t.tiers) FROM (
                      SELECT u.sport, jsonb_object_agg(u.tier, u.n) AS tiers
                        FROM (SELECT up.sport, coalesce(up.tier, 'kalshi') AS tier, count(*) AS n
                                FROM up GROUP BY 1, 2) u
                       GROUP BY u.sport) t), '{}'::jsonb),
        'best_edges_tonight', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                  'market', public.ctx_trim(b.label, 100), 'sport', b.sport, 'tier', b.tier,
                                  'books', b.n_books,
                                  'at', to_char(b.starts_at AT TIME ZONE z.tz, 'HH24:MI'),
                                  'side', CASE WHEN coalesce(b.edge_no, -9) > coalesce(b.edge_yes, -9)
                                               THEN 'no' ELSE 'yes' END,
                                  'p', b.p_consensus,
                                  'ask', CASE WHEN coalesce(b.edge_no, -9) > coalesce(b.edge_yes, -9)
                                              THEN b.no_ask ELSE b.yes_ask END,
                                  'edge', b.edge))
                                  ORDER BY b.edge DESC)
                                FROM best b CROSS JOIN z), '[]'::jsonb),
        'credits', public.odds_api_usage());
$$;

-- ===================================================================== 4
REVOKE ALL ON FUNCTION public.leg_consensus_priced(text[], boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leg_consensus_priced(text[], boolean) TO authenticated, valemont_readonly;

-- ===================================================================== 5
INSERT INTO migration_log (name) VALUES ('081_ctx_consensus_outside')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 6
-- Self-test, run on paste, rolled back ('selftest-rollback'): db/080's cases
-- through leg_consensus_for (now the wrapper), then outside-only against the
-- whole game row for row, then ctx_consensus's keys, its claims and its
-- counts. Rows use series KXSELFTEST081 (no real series has it). A statement
-- that calls a writing function stores its result first and reads back in a
-- later statement (db/062's rule). No INSERT ... VALUES tuple in this file
-- except the log row.
DO $$
DECLARE
    v_t0   timestamptz := now() - interval '1 minute';
    v_soon timestamptz := now() + interval '2 hours';
    v_n    int;
    v_row  record;
    v_j    jsonb;
    v_gt   text := 'KXSELFTEST081GAME-26NOV13AAABBB';
    v_ev   text := 'KXSELFTEST081-26NOV13AAABBB';
    v_a    text := 'KXSELFTEST081-26NOV13AAABBB-AAA';
    v_b    text := 'KXSELFTEST081-26NOV13AAABBB-BBB';
    v_ev2  text := 'KXSELFTEST081-26NOV13CCCDDD';
    v_c    text := 'KXSELFTEST081-26NOV13CCCDDD-CCC';
    v_d    text := 'KXSELFTEST081-26NOV13CCCDDD-DDD';
BEGIN
    BEGIN
        v_n := public.kalshi_board_series_upsert('[{"series_ticker": "KXSELFTEST081", "title": "Selftest 078",
            "sport": "Basketball", "league": "nba", "category": "Sports", "tags": ["Basketball"],
            "fee_type": "quadratic", "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"},
            "combo_eligible": false, "collection_tickers": []}]'::jsonb);
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', v_ev, 'series_ticker', 'KXSELFTEST081', 'game_ticker', v_gt, 'game_title', 'AAA vs BBB',
            'milestone_start', v_soon, 'mutually_exclusive', true),
            jsonb_build_object('event_ticker', v_ev2, 'series_ticker', 'KXSELFTEST081', 'game_ticker', v_gt,
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

        -- ------------------------------------------------ 5d. outside-only is the same rows, fewer of them
        -- At this point AAA and BBB are books rows (spans), CCC is espn, DDD is books with no span: 4 outside rows.
        -- Remove DDD's row so one market of the game is kalshi-tier.
        DELETE FROM public.leg_consensus WHERE market_ticker = v_d;
        -- A market in ANOTHER game: the named game's read must not grow by it.
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', 'KXSELFTEST081-26NOV13EEEFFF', 'series_ticker', 'KXSELFTEST081',
            'game_ticker', 'KXSELFTEST081GAME-26NOV13EEEFFF', 'game_title', 'EEE vs FFF', 'milestone_start', v_soon)), v_t0);
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', 'KXSELFTEST081-26NOV13EEEFFF-EEE', 'event_ticker', 'KXSELFTEST081-26NOV13EEEFFF',
            'market_kind', 'game_line', 'subject', 'EEE', 'label', 'EEE', 'yes_bid', 0.50, 'yes_ask', 0.52)), v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the other game''s market wrote %', v_n; END IF;
        SELECT count(*) AS n, count(*) FILTER (WHERE x.tier = 'kalshi') AS k INTO v_row
          FROM public.leg_consensus_for(ARRAY[v_gt]) x;
        IF v_row.n <> 4 OR v_row.k <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the whole game reads % (4 markets, 1 kalshi-tier)', v_row;
        END IF;
        SELECT count(*) AS n, count(*) FILTER (WHERE x.tier = 'kalshi') AS k INTO v_row
          FROM public.leg_consensus_priced(ARRAY[v_gt], true) x;
        IF v_row.n <> 3 OR v_row.k <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: outside-only reads % (3 markets, no kalshi-tier)', v_row;
        END IF;
        -- Every outside row equals the whole-game row, column for column (CCC's mid is normalized with DDD's).
        IF EXISTS ((SELECT * FROM public.leg_consensus_priced(ARRAY[v_gt], true)
                    EXCEPT SELECT * FROM public.leg_consensus_for(ARRAY[v_gt]))
                   UNION ALL
                   (SELECT * FROM public.leg_consensus_for(ARRAY[v_gt]) x WHERE x.tier <> 'kalshi'
                    EXCEPT SELECT * FROM public.leg_consensus_priced(ARRAY[v_gt], true))) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an outside-only row differs from its whole-game row';
        END IF;
        IF NOT (SELECT x.normalized FROM public.leg_consensus_priced(ARRAY[v_gt], true) x WHERE x.market_ticker = v_c) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: CCC is not normalized when its sibling is left out';
        END IF;

        -- ------------------------------------------------ 5e. ctx_consensus: same keys, the claim listed, kalshi counted
        v_j := public.ctx_consensus();
        IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(v_j) k)
           IS DISTINCT FROM ARRAY['about', 'as_of', 'best_edges_tonight', 'counts', 'credits'] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_consensus keys read %', (SELECT array_agg(k) FROM jsonb_object_keys(v_j) k);
        END IF;
        -- AAA (YES) claims: it is listed unless ten larger real edges fill the list.
        IF jsonb_array_length(v_j->'best_edges_tonight') < 10
           AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') e
                            WHERE e->>'market' = 'AAA' AND e->>'side' = 'yes' AND e->>'tier' = 'books'
                              AND (e->>'edge')::numeric > 0) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: best_edges_tonight reads %', v_j->'best_edges_tonight';
        END IF;
        -- BBB's outside price is 13 h old (5b): positive, not claimed, so never listed. CCC (espn) neither.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') e WHERE e->>'market' IN ('BBB', 'CCC')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unclaimed row is listed: %', v_j->'best_edges_tonight';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'best_edges_tonight') e WHERE e->>'tier' <> 'books') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a tier other than books is listed as an edge: %', v_j->'best_edges_tonight';
        END IF;
        IF coalesce((v_j->'counts'->'Basketball'->>'kalshi')::int, 0) < 1
           OR coalesce((v_j->'counts'->'Basketball'->>'books')::int, 0) < 2
           OR coalesce((v_j->'counts'->'Basketball'->>'espn')::int, 0) < 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: counts read %', v_j->'counts';
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
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '081_ctx_consensus_outside') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_function_privilege('anon', 'public.leg_consensus_for(text[])', 'EXECUTE')
           OR has_function_privilege('anon', 'public.leg_consensus_priced(text[], boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.leg_consensus_priced(text[], boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.leg_consensus_priced(text[], boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.ctx_consensus()', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.ctx_consensus()', 'EXECUTE')
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
    RAISE NOTICE '081 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '081_ctx_consensus_outside';
-- (b) md5 of the three function bodies against the scratch copy's; grants.
-- (c) three runs each on a quiet database: v_leg_consensus (full read), ctx_consensus(),
--     leg_consensus_for(ARRAY['<largest game>']), and the three db/079 tab calls.
-- (d) SELECT jsonb_object_keys(ctx_consensus());
