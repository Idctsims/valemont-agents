-- 082_board_cards.sql
--
-- Chat 8 Phase 3.5, Workstream B (owner decisions, 2026-11-15, PR #125).
-- Paste into the Supabase SQL Editor AFTER db/080 (db/081 belongs to the Wags
-- session and is independent of this file). Runs top-to-bottom, no
-- BEGIN/COMMIT.
--
-- WHY. The board's game cards show both moneyline prices and a "Claimed
-- edges" filter. kalshi_board_games returns counts only, and running
-- leg_consensus_for over every game on a board to learn which games claim is
-- the cost db/078-db/080 were about (owner: no consensus pass over whole
-- games). A claim needs a span (db/078: the books tier, p_books_lo and
-- p_books_hi set), so only span rows are priced here.
--
-- WHAT. Read functions, and the e2e test helper. No table, column, trigger
-- or other function changes; kalshi_board_games, leg_consensus_for and the
-- readers of db/079 are untouched.
--   1. leg_span_claims(games): one row per SPAN row (books tier with both
--      bounds) on the board: its game and whether its edge is claimed, by
--      db/080's rule exactly (fresh Kalshi read <= 20 min, fresh outside
--      <= 12 h, positive under the shipped p AND under the least favourable
--      bound). Each span row's Kalshi mid is normalized over its own event
--      only, as leg_consensus_for does; no other market is priced.
--   2. kalshi_board_cards(sport, tests): kalshi_board_games' fields (no kind
--      or combo filter; asof to the second), plus 'w': the game's winner
--      markets from its own event (stat 'winner', no line, binary), each
--      [ticker after the event's, Kalshi's YES bid, YES ask], away first as
--      the title names them; 'wy': that event takes YES only in a combo; and
--      'hc': true when any span row of the game claims (leg_span_claims over
--      this sport's games only).
--   3. add_test_kalshi_board: db/076's rows, plus spans on two books rows:
--      the home moneyline CLAIMS (p 0.66 in [0.659, 0.665]: +0.17c shipped,
--      +0.10c at the low bound), and Test Player 10+ passes the shipped
--      method (+0.64c) and FAILS the least favourable (-2.16c at 0.58): no
--      claim. The e2e spec then sees the up colour on a real claim.
--
-- Measured before handover on a local PG 17 copy of production's board
-- (pg_dump as valemont_readonly, ~20:50 UTC Nov 15: 37,601 markets, 398
-- consensus rows, 269 with a span), JIT off, three runs each, the reader
-- today (kalshi_board_games) against this one (kalshi_board_cards):
--   Basketball (234 games)  25 / 25 / 25 ms, 40.7 KB  ->  54 / 54 / 55 ms, 51.7 KB
--   Football (14)           23 / 23 / 24 ms,  2.9 KB  -> 127 / 127 / 128 ms, 3.5 KB
--   Hockey (32)              9 /  9 / 10 ms,  5.5 KB  ->  19 / 19 / 19 ms,  6.8 KB
--   Soccer (70)             11 / 11 / 11 ms, 11.5 KB  ->  22 / 22 / 23 ms, 15.1 KB
--   Tennis (143)             9 /  9 / 10 ms, 23.1 KB  ->  22 / 23 / 23 ms, 29.6 KB
-- With every price made fresh (so the claim test runs in full): Basketball
-- 55-56 ms, Football 128 ms. leg_span_claims agrees with leg_consensus_for on
-- all 269 span rows (as copied: 9 claim; fresh: 18), and no claim exists on a
-- row without a span. The first draft read the normalization window through
-- a grouped CTE; inside the function it was planned once per span row: 2.6 s.
-- Self-test passes; 20 mutations, 20 red.

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.leg_span_claims(p_games text[] DEFAULT NULL)
RETURNS TABLE (market_ticker text, game_ticker text, edge_claimed boolean)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH t AS (
        SELECT c.market_ticker, c.p_books, c.p_books_lo, c.p_books_hi, c.source_at, b.event_ticker,
               e.game_ticker, b.yes_bid, b.yes_ask, s.fee_multiplier, e.mutually_exclusive,
               e.fetched_at AS kalshi_at, (b.yes_bid + b.yes_ask) / 2 AS mid
          FROM public.leg_consensus c
          JOIN public.kalshi_board b ON b.market_ticker = c.market_ticker
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE c.tier = 'books' AND c.p_books_lo IS NOT NULL AND c.p_books_hi IS NOT NULL
           AND b.payout = 'binary' AND (p_games IS NULL OR e.game_ticker = ANY(p_games))
    ),
    kk AS (
        -- leg_consensus_for's normalization window (the event's binary markets), read per span row
        -- through kalshi_board_event_idx. A grouped CTE over the board was planned once per span row
        -- inside the function (an estimate of 2 rows): 2.6 s on the Nov 15 board, 269 span rows.
        SELECT t.*, round(CASE WHEN coalesce(t.mutually_exclusive AND ev.n_ev >= 2 AND ev.n_quoted = ev.n_ev
                                             AND ev.mid_sum BETWEEN 0.95 AND 1.05, false)
                               THEN t.mid / ev.mid_sum ELSE t.mid END, 6) AS pk
          FROM t
         CROSS JOIN LATERAL (
                SELECT sum((b.yes_bid + b.yes_ask) / 2) AS mid_sum, count(*) AS n_ev,
                       count(b.yes_bid + b.yes_ask) AS n_quoted
                  FROM public.kalshi_board b
                 WHERE b.event_ticker = t.event_ticker AND b.payout = 'binary') ev
    )
    SELECT kk.market_ticker, kk.game_ticker,
           -- db/080's claim, with tested = true (every row here carries a span); never NULL.
           coalesce(NOT (kk.kalshi_at < now() - interval '20 minutes')
                    AND NOT coalesce(kk.source_at < now() - interval '12 hours', false)
                    AND ((coalesce(q.edge_yes, -1) > 0 AND coalesce(lo.edge_yes, -1) > 0)
                         OR (coalesce(q.edge_no, -1) > 0 AND coalesce(hi.edge_no, -1) > 0)), false)
      FROM kk
     CROSS JOIN LATERAL public.consensus_leg(kk.p_books, kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) q
     CROSS JOIN LATERAL public.consensus_leg(kk.p_books_lo, kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) lo
     CROSS JOIN LATERAL public.consensus_leg(kk.p_books_hi, kk.pk, kk.yes_bid, kk.yes_ask, kk.fee_multiplier) hi;
$$;

-- ===================================================================== 2
CREATE OR REPLACE FUNCTION public.kalshi_board_cards(p_sport text, p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH g AS (
        SELECT e.game_ticker,
               jsonb_build_object(
                   'g', e.game_ticker, 't', min(e.game_title), 'at', min(e.starts_at), 'lg', min(s.league),
                   'se', min(e.sports_event_id),
                   'l', nullif(count(*) FILTER (WHERE b.market_kind = 'game_line' AND b.payout = 'binary'), 0),
                   'p', nullif(count(*) FILTER (WHERE b.market_kind = 'prop' AND b.payout = 'binary'), 0),
                   'o', nullif(count(*) FILTER (WHERE b.market_kind = 'other' AND b.payout = 'binary'), 0),
                   's', nullif(count(*) FILTER (WHERE b.payout = 'scalar'), 0),
                   'c', nullif(count(*) FILTER (WHERE e.combo_eligible AND b.payout = 'binary'), 0),
                   'asof', date_trunc('second', max(e.fetched_at)),
                   'test', CASE WHEN bool_or(b.is_test) THEN true END) AS card,
               min(e.starts_at) AS at, min(e.game_title) AS title
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE (NOT b.is_test OR p_tests) AND s.sport = p_sport AND e.starts_at > now() - interval '6 hours'
         GROUP BY e.game_ticker
    ),
    hc AS (
        -- Span rows only (a claim needs one), and only this sport's games.
        SELECT DISTINCT x.game_ticker
          FROM public.leg_span_claims((SELECT array_agg(g.game_ticker) FROM g)) x
         WHERE x.edge_claimed
    )
    SELECT jsonb_strip_nulls(g.card || jsonb_build_object(
               -- The game's own event (its ticker is the game's): its winner markets, Kalshi's own prices,
               -- each [the ticker after the event's, YES bid, YES ask], in the order the title names them
               -- (away first), anything the title does not name (a tie) last.
               'w', (SELECT jsonb_agg(jsonb_build_array(substr(b.market_ticker, length(b.event_ticker) + 2),
                                                        b.yes_bid::float8, b.yes_ask::float8)
                                      ORDER BY nullif(strpos(g.title, coalesce(b.subject, '')), 0) NULLS LAST,
                                               b.market_ticker)
                       FROM public.kalshi_board b
                      WHERE b.event_ticker = g.game_ticker AND b.stat = 'winner' AND b.line IS NULL
                        AND b.payout = 'binary' AND (NOT b.is_test OR p_tests)),
               -- The winner event takes YES only in a Kalshi combo.
               'wy', (SELECT CASE WHEN e.combo_yes_only THEN true END
                        FROM public.kalshi_board_events e WHERE e.event_ticker = g.game_ticker),
               'hc', CASE WHEN hc.game_ticker IS NOT NULL THEN true END))
      FROM g
      LEFT JOIN hc ON hc.game_ticker = g.game_ticker
     ORDER BY g.at, g.title;
$$;

-- ===================================================================== 3
-- db/076's helper with two span rows (the CLAIM and the NO-CLAIM cases).
CREATE OR REPLACE FUNCTION public.add_test_kalshi_board(p_marker text, p_starts_at timestamptz)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_tag  text := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
    v_game text;
    v_pts  text;
    v_lad  text;
BEGIN
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'add_test_kalshi_board: the owner and a verified test marker only';
    END IF;
    v_game := 'E2ETEST-' || v_tag;
    v_pts := 'E2ETEST-' || v_tag || 'PTS';
    v_lad := 'E2ETEST-' || v_tag || 'LAD';
    INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier, fee_regime,
                                            combo_eligible, collection_tickers, is_test)
    VALUES ('E2ETEST', 'E2E Test Series', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
            '{"fee_type": "quadratic", "fee_multiplier": "1"}'::jsonb, true, ARRAY['E2ECOLL-R'], true)
    ON CONFLICT (series_ticker) DO NOTHING;
    INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                            starts_at, combo_eligible, combo_yes_only, combo_collections,
                                            combo_size_max, combo_quoters, mutually_exclusive, is_test, fetched_at)
    VALUES (v_game, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, true,
            ARRAY['E2ECOLL-R'], 1, 0, true, true, now()),
           (v_pts, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, false, false,
            '{}', NULL, NULL, false, true, now()),
           (v_lad, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, false,
            ARRAY['E2ECOLL-R'], NULL, NULL, false, true, now());
    INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line,
                                     label, yes_bid, yes_ask, volume, open_interest, taker_fee_at_ask, team, is_test,
                                     fetched_at, last_changed_at)
    SELECT v.market_ticker, v.event_ticker, v.market_kind, v.variant, v.payout, v.subject, v.stat, v.line, v.label,
           v.yes_bid, v.yes_ask, v.volume, v.open_interest, v.fee, v.team, true, now(), now()
      FROM (VALUES
        (v_game || '-HOM', v_game, 'game_line', NULL::text, 'binary', 'Test Home', 'winner', NULL::numeric,
         'Test Home', 0.6100::numeric, 0.6300::numeric, 1200::numeric, 800::numeric, 0.016317::numeric, 'HOM'),
        (v_game || '-AWY', v_game, 'game_line', NULL, 'binary', 'Test Away', 'winner', NULL, 'Test Away',
         0.3700, 0.3900, 900, 700, 0.016653, 'AWY'),
        (v_pts || '-P1-10', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5500, 0.5800, 300, 200, 0.017052, 'HOM'),
        (v_pts || '-P1-20', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 19.5, 'Test Player: 20+',
         0.1500, 0.1700, 150, 100, 0.009877, 'HOM'),
        (v_pts || '-P1-10P', v_pts, 'prop', 'protected', 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5400, 0.5700, 50, 40, 0.017157, 'HOM'),
        (v_lad || '-P1', v_lad, 'prop', 'ladder', 'scalar', 'Test Player', 'points', NULL,
         'Test Player: points, $1 at 50', 0.3000, 0.3200, 20, 10, 0.015232, 'HOM')
      ) AS v (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line, label, yes_bid, yes_ask,
              volume, open_interest, fee, team);
    -- HOM: p 0.7 x 0.66 + 0.3 x 0.62 = 0.648 against 0.63 + 0.016317: +0.17c; at the low bound 0.659,
    -- 0.6473: +0.10c. Claimed. P1-10: p 0.7 x 0.62 + 0.3 x 0.565 = 0.6035 against 0.58 + 0.017052: +0.64c;
    -- at the low bound 0.58, 0.5755: -2.16c. Not claimed.
    INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, p_books_lo, p_books_hi,
                                      p_espn, inputs, source_at, computed_at)
    SELECT v.market_ticker, 'books', v.n_books, v.p_books, v.p_books_mult, v.p_lo, v.p_hi, NULL, v.inputs, now(), now()
      FROM (VALUES
        (v_game || '-HOM', 3::smallint, 0.660000::numeric, 0.659000::numeric, 0.659000::numeric, 0.665000::numeric,
         '{"m": "power", "b": {"draftkings": 0.66, "fanduel": 0.665, "betmgm": 0.655}}'::jsonb),
        (v_pts || '-P1-10', 2::smallint, 0.620000, 0.620000, 0.580000, 0.630000,
         '{"m": "power", "b": {"draftkings": 0.62, "fanduel": 0.62}}'::jsonb)
      ) AS v (market_ticker, n_books, p_books, p_books_mult, p_lo, p_hi, inputs);
    RETURN v_game;
END;
$$;

-- ===================================================================== 4
REVOKE ALL ON FUNCTION public.leg_span_claims(text[]) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_cards(text, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.leg_span_claims(text[]) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_cards(text, boolean) TO authenticated, valemont_readonly;
-- add_test_kalshi_board keeps db/071's grants (CREATE OR REPLACE): authenticated only.

INSERT INTO migration_log (name) VALUES ('082_board_cards')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 5
-- Self-test, run on paste, rolled back ('selftest-rollback'). The owner's
-- session is simulated with request.jwt claims and a throwaway marker, as in
-- db/076. Test rows only. A statement that calls a writing function stores
-- its result first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker text := 'selftest-082-' || md5(random()::text);
    v_game   text;
    v_pts    text;
    v_j      jsonb;
    v_n      int;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));

        -- ------------------------------------------------ 1. the helper: two span rows
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        v_pts := v_game || 'PTS';
        SELECT count(*) INTO v_n FROM public.leg_consensus
         WHERE market_ticker IN (v_game || '-HOM', v_pts || '-P1-10') AND p_books_lo IS NOT NULL;
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: the helper wrote % span rows', v_n; END IF;

        -- ------------------------------------------------ 2. the claims: HOM yes, P1-10 no; the same as db/080's
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the home moneyline does not claim';
        END IF;
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_pts || '-P1-10')
               IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the least favourable bound did not stop P1-10';
        END IF;
        IF (SELECT edge_yes FROM public.leg_consensus_for(ARRAY[v_game]) WHERE market_ticker = v_pts || '-P1-10')
               IS NULL OR (SELECT edge_yes FROM public.leg_consensus_for(ARRAY[v_game])
                            WHERE market_ticker = v_pts || '-P1-10') <= 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: P1-10 should pass the shipped method';
        END IF;
        SELECT count(*) INTO v_n
          FROM public.leg_span_claims(ARRAY[v_game]) s
          JOIN public.leg_consensus_for(ARRAY[v_game]) f ON f.market_ticker = s.market_ticker
         WHERE s.edge_claimed IS DISTINCT FROM f.edge_claimed;
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: % span rows disagree with leg_consensus_for', v_n; END IF;
        IF (SELECT count(*) FROM public.leg_span_claims(ARRAY[v_game])) <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: span claims priced a row with no span';
        END IF;

        -- ------------------------------------------------ 3. the card
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        -- Away first, as the title names them: [suffix, YES bid, YES ask].
        IF v_j IS NULL OR v_j->>'hc' IS DISTINCT FROM 'true' OR v_j->'w' IS DISTINCT FROM '[["AWY", 0.37, 0.39], ["HOM", 0.61, 0.63]]'::jsonb
           OR v_j->>'wy' IS DISTINCT FROM 'true'
           OR (v_j->>'l')::int <> 2 OR (v_j->>'p')::int <> 3 OR (v_j->>'s')::int <> 1 OR (v_j->>'c')::int <> 2
           OR v_j->>'test' <> 'true' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test card reads %', v_j;
        END IF;
        -- The flag reads span rows only: the same claim with the span gone is no claim and no flag.
        UPDATE public.leg_consensus SET p_books_lo = NULL, p_books_hi = NULL WHERE market_ticker = v_game || '-HOM';
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j ? 'hc' OR EXISTS (SELECT 1 FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a books row with no span flagged its game';
        END IF;
        UPDATE public.leg_consensus SET p_books_lo = 0.659, p_books_hi = 0.665 WHERE market_ticker = v_game || '-HOM';
        IF EXISTS (SELECT 1 FROM public.kalshi_board_cards('Basketball', false) x WHERE x->>'g' = v_game) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test card without p_tests';
        END IF;

        -- ------------------------------------------------ 4. stale Kalshi: no claim, no flag, in both functions
        UPDATE public.kalshi_board_events SET fetched_at = now() - interval '21 minutes' WHERE event_ticker = v_game;
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM false
           OR (SELECT edge_claimed FROM public.leg_consensus_for(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a stale Kalshi price claimed';
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j ? 'hc' THEN RAISE EXCEPTION 'SELFTEST FAILED: hc on a stale game'; END IF;
        UPDATE public.kalshi_board_events SET fetched_at = now() WHERE event_ticker = v_game;
        -- A stale book price: no claim either.
        UPDATE public.leg_consensus SET source_at = now() - interval '13 hours' WHERE market_ticker = v_game || '-HOM';
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a stale book price claimed';
        END IF;
        UPDATE public.leg_consensus SET source_at = now() WHERE market_ticker = v_game || '-HOM';
        -- The NO side claims too (HOM priced low): the flag is any side.
        UPDATE public.leg_consensus SET p_books = 0.40, p_books_lo = 0.39, p_books_hi = 0.41
         WHERE market_ticker = v_game || '-HOM';
        SELECT count(*) INTO v_n
          FROM public.leg_span_claims(ARRAY[v_game]) s
          JOIN public.leg_consensus_for(ARRAY[v_game]) f ON f.market_ticker = s.market_ticker
         WHERE s.edge_claimed IS DISTINCT FROM f.edge_claimed OR (s.market_ticker = v_game || '-HOM' AND NOT s.edge_claimed);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: the NO-side claim (% rows wrong)', v_n; END IF;
        -- NO positive under the shipped method, negative at the highest (0.60: blend 0.606, NO costs
        -- 0.39 + 0.016653): not claimed, in both functions.
        UPDATE public.leg_consensus SET p_books_hi = 0.60 WHERE market_ticker = v_game || '-HOM';
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM false
           OR (SELECT edge_claimed FROM public.leg_consensus_for(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM')
               IS DISTINCT FROM false
           OR (SELECT edge_no FROM public.leg_consensus_for(ARRAY[v_game]) WHERE market_ticker = v_game || '-HOM') <= 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the highest bound did not stop the NO side';
        END IF;
        -- Normalization as leg_consensus_for does it: mids 0.62 + 0.34 = 0.96, so HOM's Kalshi p is
        -- 0.645833. p_books 0.653: 0.4571 + 0.19375 = 0.65085 against 0.646317, claimed; unnormalized
        -- (0.62) it would be 0.6431, not claimed.
        UPDATE public.kalshi_board SET yes_bid = 0.33, yes_ask = 0.35 WHERE market_ticker = v_game || '-AWY';
        UPDATE public.leg_consensus SET p_books = 0.653, p_books_lo = 0.653, p_books_hi = 0.66
         WHERE market_ticker = v_game || '-HOM';
        SELECT count(*) INTO v_n
          FROM public.leg_span_claims(ARRAY[v_game]) s
          JOIN public.leg_consensus_for(ARRAY[v_game]) f ON f.market_ticker = s.market_ticker
         WHERE s.edge_claimed IS DISTINCT FROM f.edge_claimed OR (s.market_ticker = v_game || '-HOM' AND NOT s.edge_claimed);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: the normalized claim (% rows wrong)', v_n; END IF;

        -- ------------------------------------------------ 5. grants
        IF has_function_privilege('anon', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.leg_span_claims(text[])', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.leg_span_claims(text[])', 'EXECUTE')
           OR has_function_privilege('anon', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '082 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '082_board_cards';
-- (b) SELECT count(*), count(*) FILTER (WHERE x ? 'w'), count(*) FILTER (WHERE x ? 'hc'),
--            octet_length(jsonb_agg(x)::text) FROM kalshi_board_cards('Basketball', false) x;
-- (c) has_function_privilege for anon / authenticated / valemont_readonly on both readers.
