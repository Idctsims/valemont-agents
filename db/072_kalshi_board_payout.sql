-- 072_kalshi_board_payout.sql: scalar markets on the Kalshi board (Chat 8
-- Phase 1 fixes; owner decisions, Nov 7; docs/kalshi_board.md §3).
--
-- Kalshi lists ladder and escalator player markets (KXNFLLADDERREC,
-- KXNFLESCALATORRECYDS, KXNBAESCALATORPTS, ...). They are SCALAR: YES pays a
-- fraction of $1.00 that grows with the stat (a ladder linearly per unit, an
-- escalator on a cubic schedule), capped at $1.00 at custom_strike.scalar_cap.
-- There is no hit or miss, so they are never parlay legs, and their YES price
-- is not a probability.
--
-- Kalshi has no field that says so: market_type reads 'binary' on every
-- ladder and escalator (and settlement_bounds_type 'default',
-- notional_value_dollars 1.0000), read Nov 7. The worker therefore sets
-- payout from custom_strike: 'scalar' when scalar_cap is present (owner's
-- fallback rule), never from the series name.
--
-- What this file does:
--   1. kalshi_board.payout text NOT NULL DEFAULT 'binary',
--      CHECK (payout IN ('binary', 'scalar')).
--   2. kalshi_board_variant_check: NULL | protected | ladder | escalator.
--   3. kalshi_board_scalar_shape: payout = 'scalar' exactly when variant is
--      ladder or escalator, and a scalar row has no line (the cap rides in
--      the label: "Emeka Egbuka: receptions, $1 at 20").
--   4. kalshi_board_markets_upsert writes payout (absent = 'binary', so the
--      deployed worker keeps writing unchanged until the new one ships).
--   5. v_kalshi_board gains payout (last column).
--   6. The readers: kalshi_board_sports / _games count binary lines, props,
--      other and combo only, and scalar apart ('scalar' / 's'); the combo
--      filter takes binary markets only; kalshi_board_game marks a scalar
--      market ('s': true) and an event's combo flags come from its binary
--      markets only (a scalar event shows no combo chip).
--   7. ctx_kalshi_board: binary counts by sport and kind, scalar counts by
--      sport apart; combo and top_tonight binary only.
--   8. add_test_kalshi_board: the test game gains a ladder event (one scalar
--      market, its event combo-eligible, to prove the chip stays off).
-- Then the migration_log row and the rolled-back self-test.
--
-- Backward-compatible with the deployed worker (it sends no payout: every
-- row 'binary', its ladder rows variant NULL, which the shape CHECK allows).
-- PASTE BEFORE the worker that writes payout = 'scalar' deploys.

-- ===================================================================== 1-3
ALTER TABLE public.kalshi_board
    ADD COLUMN IF NOT EXISTS payout text NOT NULL DEFAULT 'binary'
        CONSTRAINT kalshi_board_payout_check CHECK (payout IN ('binary', 'scalar'));

ALTER TABLE public.kalshi_board DROP CONSTRAINT IF EXISTS kalshi_board_variant_check;
ALTER TABLE public.kalshi_board ADD CONSTRAINT kalshi_board_variant_check
    CHECK (variant IS NULL OR variant IN ('protected', 'ladder', 'escalator'));

ALTER TABLE public.kalshi_board DROP CONSTRAINT IF EXISTS kalshi_board_scalar_shape;
ALTER TABLE public.kalshi_board ADD CONSTRAINT kalshi_board_scalar_shape
    CHECK ((payout = 'scalar') = coalesce(variant IN ('ladder', 'escalator'), false)
           AND (payout = 'binary' OR line IS NULL));

COMMENT ON COLUMN public.kalshi_board.payout IS
    'binary (YES pays $1 or $0; a parlay leg) or scalar (ladder/escalator: YES pays a fraction of $1 that grows '
    'with the stat; never a leg). Set by the worker from custom_strike.scalar_cap: Kalshi''s market_type says '
    'binary on both (Nov 7). db/072.';

-- ===================================================================== 4
CREATE OR REPLACE FUNCTION public.kalshi_board_markets_upsert(p_rows jsonb, p_at timestamptz,
                                                              p_is_test boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH w AS (
        INSERT INTO public.kalshi_board AS b
               (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line, label, yes_bid,
                yes_ask, volume, open_interest, taker_fee_at_ask, is_test, fetched_at, last_changed_at)
        SELECT r.market_ticker, r.event_ticker, r.market_kind, r.variant, coalesce(r.payout, 'binary'), r.subject,
               r.stat, r.line, r.label, r.yes_bid, r.yes_ask, r.volume, r.open_interest, r.taker_fee_at_ask,
               p_is_test, p_at, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   market_ticker text, event_ticker text, market_kind text, variant text, payout text, subject text,
                   stat text, line numeric, label text, yes_bid numeric, yes_ask numeric, volume numeric,
                   open_interest numeric, taker_fee_at_ask numeric)
        ON CONFLICT (market_ticker) DO UPDATE SET
            event_ticker = EXCLUDED.event_ticker, market_kind = EXCLUDED.market_kind, variant = EXCLUDED.variant,
            payout = EXCLUDED.payout, subject = EXCLUDED.subject, stat = EXCLUDED.stat, line = EXCLUDED.line,
            label = EXCLUDED.label, yes_bid = EXCLUDED.yes_bid, yes_ask = EXCLUDED.yes_ask,
            volume = EXCLUDED.volume, open_interest = EXCLUDED.open_interest,
            taker_fee_at_ask = EXCLUDED.taker_fee_at_ask,
            fetched_at = greatest(b.fetched_at, EXCLUDED.fetched_at),
            last_changed_at = CASE WHEN (b.yes_bid, b.yes_ask) IS DISTINCT FROM (EXCLUDED.yes_bid, EXCLUDED.yes_ask)
                                   THEN greatest(b.last_changed_at, EXCLUDED.fetched_at)
                                   ELSE b.last_changed_at END
         WHERE b.is_test = p_is_test
           AND (b.event_ticker, b.market_kind, b.variant, b.payout, b.subject, b.stat, b.line, b.label, b.yes_bid,
                b.yes_ask, b.volume, b.open_interest, b.taker_fee_at_ask)
               IS DISTINCT FROM
               (EXCLUDED.event_ticker, EXCLUDED.market_kind, EXCLUDED.variant, EXCLUDED.payout, EXCLUDED.subject,
                EXCLUDED.stat, EXCLUDED.line, EXCLUDED.label, EXCLUDED.yes_bid, EXCLUDED.yes_ask, EXCLUDED.volume,
                EXCLUDED.open_interest, EXCLUDED.taker_fee_at_ask)
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- ===================================================================== 5
-- db/071's columns unchanged and in order; payout appended (CREATE OR
-- REPLACE VIEW may only add columns at the end). Grants and security_invoker
-- carry over.
CREATE OR REPLACE VIEW v_kalshi_board WITH (security_invoker = true) AS
SELECT b.market_ticker, b.event_ticker, e.series_ticker, e.game_ticker, e.game_title, e.starts_at,
       e.sports_event_id, s.sport, s.league, b.market_kind, b.variant, b.subject, b.stat, b.line, b.label,
       b.yes_bid, b.yes_ask,
       CASE WHEN b.yes_ask IS NULL THEN NULL ELSE 1 - b.yes_ask END AS no_bid,
       CASE WHEN b.yes_bid IS NULL THEN NULL ELSE 1 - b.yes_bid END AS no_ask,
       b.volume, b.open_interest, b.taker_fee_at_ask, s.fee_type, s.fee_multiplier,
       e.combo_eligible, e.combo_yes_only, e.combo_collections, e.combo_size_max, e.combo_quoters,
       e.close_time, e.expected_expiration_time,
       b.fetched_at, b.last_changed_at, e.fetched_at AS event_fetched_at, b.is_test,
       b.payout
  FROM kalshi_board b
  JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
  JOIN kalshi_series_board s ON s.series_ticker = e.series_ticker;

-- ===================================================================== 6
-- Sport chips: lines, props and combo count binary markets; scalar apart.
CREATE OR REPLACE FUNCTION public.kalshi_board_sports(p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'sport', s.sport, 'games', count(DISTINCT e.game_ticker), 'markets', count(*),
               'lines', count(*) FILTER (WHERE b.market_kind = 'game_line' AND b.payout = 'binary'),
               'props', count(*) FILTER (WHERE b.market_kind = 'prop' AND b.payout = 'binary'),
               'scalar', nullif(count(*) FILTER (WHERE b.payout = 'scalar'), 0),
               'combo', count(*) FILTER (WHERE e.combo_eligible AND b.payout = 'binary')))
      FROM public.kalshi_board b
      JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
      JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
     WHERE (NOT b.is_test OR p_tests) AND e.starts_at > now() - interval '6 hours'
     GROUP BY s.sport
     ORDER BY count(*) DESC, s.sport;
$$;

-- One sport's games, collapsed: binary counts per kind, scalar apart ('s'),
-- binary combo-eligible ('c'). The combo filter keeps binary markets only.
CREATE OR REPLACE FUNCTION public.kalshi_board_games(p_sport text, p_kind text DEFAULT NULL,
                                                     p_combo_only boolean DEFAULT false,
                                                     p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'g', e.game_ticker, 't', min(e.game_title), 'at', min(e.starts_at), 'lg', min(s.league),
               'se', min(e.sports_event_id),
               'l', nullif(count(*) FILTER (WHERE b.market_kind = 'game_line' AND b.payout = 'binary'), 0),
               'p', nullif(count(*) FILTER (WHERE b.market_kind = 'prop' AND b.payout = 'binary'), 0),
               'o', nullif(count(*) FILTER (WHERE b.market_kind = 'other' AND b.payout = 'binary'), 0),
               's', nullif(count(*) FILTER (WHERE b.payout = 'scalar'), 0),
               'c', nullif(count(*) FILTER (WHERE e.combo_eligible AND b.payout = 'binary'), 0),
               'asof', max(e.fetched_at),
               'test', CASE WHEN bool_or(b.is_test) THEN true END))
      FROM public.kalshi_board b
      JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
      JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
     WHERE (NOT b.is_test OR p_tests) AND s.sport = p_sport AND e.starts_at > now() - interval '6 hours'
       AND (p_kind IS NULL OR b.market_kind = p_kind)
       AND (NOT p_combo_only OR (e.combo_eligible AND b.payout = 'binary'))
     GROUP BY e.game_ticker
     ORDER BY min(e.starts_at), min(e.game_title);
$$;

-- One game's markets, one object per event. A scalar market carries 's':
-- true; the event's combo flags (c, y, sm) come from its binary markets only.
CREATE OR REPLACE FUNCTION public.kalshi_board_game(p_game text, p_kind text DEFAULT NULL,
                                                    p_combo_only boolean DEFAULT false,
                                                    p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH m AS (
        SELECT e.event_ticker, s.title AS series_title, e.combo_eligible, e.combo_yes_only, e.combo_size_max,
               e.fetched_at, b.payout,
               b.market_kind, b.variant, b.label, b.subject, b.line, b.yes_bid, b.yes_ask, b.taker_fee_at_ask,
               CASE b.market_kind WHEN 'game_line' THEN 0 WHEN 'prop' THEN 1 ELSE 2 END AS k_order
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
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
                        's', CASE WHEN m.payout = 'scalar' THEN true END))
                    ORDER BY m.k_order, m.subject NULLS LAST, m.line NULLS FIRST, m.label)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.k_order), min(m.series_title), m.event_ticker;
$$;

-- ===================================================================== 7
CREATE OR REPLACE FUNCTION public.ctx_kalshi_board()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago') AS tz),
    b AS (
        SELECT s.sport, b.market_kind, b.payout, b.label, b.yes_bid, b.yes_ask, b.volume, e.combo_eligible,
               e.game_title, e.starts_at, e.event_ticker
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE NOT b.is_test AND e.starts_at > now()
    ),
    bin AS (SELECT * FROM b WHERE payout = 'binary')
    SELECT jsonb_build_object(
        'about', 'The Kalshi board: every open Kalshi sports market for the next ~36 h (game lines, props, '
                 'other), from Kalshi''s public data, refreshed through the day. Binary markets pay $1 or $0, '
                 'so their YES price in dollars is a probability. Scalar markets (ladders and escalators) pay a '
                 'fraction of $1 that grows with a player''s stat: their price is not a probability, and they '
                 'are never parlay legs; they are counted apart. A board, not advice: nothing here is a pick, '
                 'and nothing is ever bet automatically.',
        'as_of', (SELECT max(fetched_at) FROM public.kalshi_board_events WHERE NOT is_test),
        'counts', coalesce((SELECT jsonb_object_agg(sport, kinds) FROM (
                      SELECT sport, jsonb_object_agg(market_kind, n) AS kinds
                        FROM (SELECT sport, market_kind, count(*) AS n FROM bin GROUP BY sport, market_kind) k
                       GROUP BY sport) x), '{}'::jsonb),
        'scalar', coalesce((SELECT jsonb_object_agg(sport, n) FROM (
                      SELECT sport, count(*) AS n FROM b WHERE payout = 'scalar' GROUP BY sport) x), '{}'::jsonb),
        'combo_eligible', jsonb_build_object(
            'markets', (SELECT count(*) FROM bin WHERE combo_eligible),
            'events', (SELECT count(DISTINCT event_ticker) FROM bin WHERE combo_eligible)),
        'top_tonight', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                    'market', public.ctx_trim(t.label, 100), 'game', public.ctx_trim(t.game_title, 100),
                                    'sport', t.sport, 'kind', t.market_kind,
                                    'at', to_char(t.starts_at AT TIME ZONE z.tz, 'HH24:MI'),
                                    'yes_bid', t.yes_bid, 'yes_ask', t.yes_ask, 'volume', t.volume))
                                    ORDER BY t.volume DESC NULLS LAST)
                                  FROM (SELECT * FROM bin WHERE starts_at < now() + interval '12 hours'
                                         ORDER BY volume DESC NULLS LAST LIMIT 10) t CROSS JOIN z), '[]'::jsonb)
    );
$$;

-- ===================================================================== 8
-- db/071's test game (series E2ETEST, sport 'Basketball') plus a ladder event:
-- one scalar market, the event combo-eligible (the chip must still not show).
-- 6 markets: 2 game lines, 3 binary props, 1 scalar prop.
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
                                            combo_size_max, combo_quoters, is_test, fetched_at)
    VALUES (v_game, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, true,
            ARRAY['E2ECOLL-R'], 1, 0, true, now()),
           (v_pts, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, false, false,
            '{}', NULL, NULL, true, now()),
           (v_lad, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, true, false,
            ARRAY['E2ECOLL-R'], NULL, NULL, true, now());
    INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line,
                                     label, yes_bid, yes_ask, volume, open_interest, taker_fee_at_ask, is_test,
                                     fetched_at, last_changed_at)
    SELECT v.market_ticker, v.event_ticker, v.market_kind, v.variant, v.payout, v.subject, v.stat, v.line, v.label,
           v.yes_bid, v.yes_ask, v.volume, v.open_interest, v.fee, true, now(), now()
      FROM (VALUES
        (v_game || '-HOM', v_game, 'game_line', NULL::text, 'binary', 'Test Home', 'winner', NULL::numeric,
         'Test Home', 0.6100::numeric, 0.6300::numeric, 1200::numeric, 800::numeric, 0.016317::numeric),
        (v_game || '-AWY', v_game, 'game_line', NULL, 'binary', 'Test Away', 'winner', NULL, 'Test Away',
         0.3700, 0.3900, 900, 700, 0.016653),
        (v_pts || '-P1-10', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5500, 0.5800, 300, 200, 0.017052),
        (v_pts || '-P1-20', v_pts, 'prop', NULL, 'binary', 'Test Player', 'points', 19.5, 'Test Player: 20+',
         0.1500, 0.1700, 150, 100, 0.009877),
        (v_pts || '-P1-10P', v_pts, 'prop', 'protected', 'binary', 'Test Player', 'points', 9.5, 'Test Player: 10+',
         0.5400, 0.5700, 50, 40, 0.017157),
        (v_lad || '-P1', v_lad, 'prop', 'ladder', 'scalar', 'Test Player', 'points', NULL,
         'Test Player: points, $1 at 50', 0.3000, 0.3200, 20, 10, 0.015232)
      ) AS v (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line, label, yes_bid, yes_ask,
              volume, open_interest, fee);
    RETURN v_game;
END;
$$;

-- Grants: CREATE OR REPLACE keeps db/071's on every function and the view.

-- ===================================================================== 9
INSERT INTO migration_log (name) VALUES ('072_kalshi_board_payout')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 10
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with its SQLSTATE.
-- Real rows use series KXSELFTEST072 (no real series has it). Every statement
-- that calls a writing function stores its result first and reads back in a
-- later statement (db/062's rule); every INSERT tuple was checked against its
-- column list before handover (db/054's rule).
DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-072-' || gen_random_uuid();
    v_t0      timestamptz := now() - interval '1 minute';
    v_t1      timestamptz := now();
    v_soon    timestamptz := now() + interval '2 hours';
    v_ok      boolean;
    v_c       text;
    v_n       int;
    v_j       jsonb;
    v_game    text;
    v_row     record;
    v_ev      text := 'KXSELFTEST072-26NOV08AAABBB';
    v_gt      text := 'KXSELFTEST072GAME-26NOV08AAABBB';
    v_series  jsonb := '[{"series_ticker": "KXSELFTEST072", "title": "Selftest Series 072", "sport": "Basketball",
                          "league": "nba", "category": "Sports", "tags": ["Basketball"], "fee_type": "quadratic",
                          "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": true,
                          "collection_tickers": ["KXMVESELFTEST-R"]}]';
    v_events  jsonb;
    v_bin     jsonb;
    v_sca     jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        v_events := jsonb_build_array(jsonb_build_object(
            'event_ticker', v_ev, 'series_ticker', 'KXSELFTEST072', 'game_ticker', v_gt, 'game_title', 'AAA vs BBB',
            'milestone_start', v_soon, 'close_time', v_soon + interval '2 days', 'combo_eligible', true,
            'combo_yes_only', false, 'combo_collections', jsonb_build_array('KXMVESELFTEST-R'),
            'combo_size_max', 1));
        -- A binary prop with NO payout key (what the deployed worker sends).
        v_bin := jsonb_build_array(jsonb_build_object(
            'market_ticker', v_ev || '-P1-10', 'event_ticker', v_ev, 'market_kind', 'prop', 'subject', 'Player One',
            'stat', 'points', 'line', 9.5, 'label', 'Player One: 10+', 'yes_bid', 0.40, 'yes_ask', 0.42,
            'volume', 100, 'open_interest', 50, 'taker_fee_at_ask', 0.017052));
        v_sca := jsonb_build_array(jsonb_build_object(
            'market_ticker', v_ev || '-P1LAD', 'event_ticker', v_ev, 'market_kind', 'prop', 'variant', 'ladder',
            'payout', 'scalar', 'subject', 'Player One', 'stat', 'points', 'label', 'Player One: points, $1 at 50',
            'yes_bid', 0.30, 'yes_ask', 0.32, 'volume', 20, 'open_interest', 10, 'taker_fee_at_ask', 0.015232));

        v_n := public.kalshi_board_series_upsert(v_series);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: series insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_events_upsert(v_events, v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: event insert wrote %', v_n; END IF;

        -- ------------------------------------------------ 1. a binary prop still writes
        v_n := public.kalshi_board_markets_upsert(v_bin, v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the binary prop wrote %', v_n; END IF;
        SELECT payout, variant, line INTO v_row FROM public.kalshi_board WHERE market_ticker = v_ev || '-P1-10';
        IF v_row.payout IS DISTINCT FROM 'binary' OR v_row.variant IS NOT NULL OR v_row.line <> 9.5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the binary prop reads %', v_row;
        END IF;
        v_n := public.kalshi_board_markets_upsert(jsonb_set(v_bin, '{0,payout}', '"binary"'), v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: an explicit binary rewrote an unchanged row (%)', v_n; END IF;

        -- ------------------------------------------------ 2. a scalar row round-trips
        v_n := public.kalshi_board_markets_upsert(v_sca, v_t0);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the scalar row wrote %', v_n; END IF;
        SELECT payout, variant, subject, stat, line, label INTO v_row
          FROM public.kalshi_board WHERE market_ticker = v_ev || '-P1LAD';
        IF v_row.payout IS DISTINCT FROM 'scalar' OR v_row.variant IS DISTINCT FROM 'ladder'
           OR v_row.subject IS DISTINCT FROM 'Player One' OR v_row.stat IS DISTINCT FROM 'points'
           OR v_row.line IS NOT NULL OR v_row.label IS DISTINCT FROM 'Player One: points, $1 at 50' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the scalar row reads %', v_row;
        END IF;
        v_n := public.kalshi_board_markets_upsert(v_sca, v_t1);
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: an unchanged scalar row was rewritten (%)', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(jsonb_set(jsonb_set(v_sca, '{0,variant}', '"escalator"'),
                                                            '{0,yes_ask}', '0.33'), v_t1);
        SELECT variant, yes_ask, last_changed_at INTO v_row FROM public.kalshi_board WHERE market_ticker = v_ev || '-P1LAD';
        IF v_n <> 1 OR v_row.variant IS DISTINCT FROM 'escalator' OR v_row.yes_ask <> 0.33
           OR v_row.last_changed_at <> v_t1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an escalator update read % (wrote %)', v_row, v_n;
        END IF;
        SELECT payout INTO v_row FROM public.v_kalshi_board WHERE market_ticker = v_ev || '-P1LAD';
        IF v_row.payout IS DISTINCT FROM 'scalar' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: v_kalshi_board payout reads %', v_row;
        END IF;

        -- ------------------------------------------------ 3. refusals
        -- Each counts only when the named constraint is the one that refused.
        v_c := NULL;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_set(v_bin, '{0,payout}', '"range"'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'kalshi_board_payout_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: payout range was not refused by the payout CHECK (%)', v_c;
        END IF;
        -- A binary row: only the variant CHECK can refuse it (the shape allows it).
        v_c := NULL;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_set(v_bin, '{0,variant}', '"spiral"'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'kalshi_board_variant_check' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: variant spiral was not refused by the variant CHECK (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_set(v_sca, '{0,line}', '50'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'kalshi_board_scalar_shape' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a scalar row with a line was not refused by the shape CHECK (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_set(v_sca, '{0,variant}', 'null'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'kalshi_board_scalar_shape' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a scalar row with no ladder/escalator variant was not refused by the shape CHECK (%)', v_c;
        END IF;
        v_c := NULL;
        BEGIN
            PERFORM public.kalshi_board_markets_upsert(jsonb_set(v_bin, '{0,variant}', '"ladder"'), v_t1);
        EXCEPTION WHEN check_violation THEN GET STACKED DIAGNOSTICS v_c = CONSTRAINT_NAME;
        END;
        IF v_c IS DISTINCT FROM 'kalshi_board_scalar_shape' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a binary ladder was not refused by the shape CHECK (%)', v_c;
        END IF;
        -- A protected binary prop still writes (db/071's one variant).
        v_n := public.kalshi_board_markets_upsert(jsonb_set(jsonb_set(v_bin, '{0,variant}', '"protected"'),
                                                            '{0,market_ticker}', to_jsonb(v_ev || '-P1-10P')), v_t1);
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: a protected binary prop wrote %', v_n; END IF;
        DELETE FROM public.kalshi_board WHERE market_ticker = v_ev || '-P1-10P';
        v_ok := false;
        BEGIN
            INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, payout, fetched_at,
                                             last_changed_at)
            VALUES (v_ev || '-NULLPAY', v_ev, 'prop', NULL, v_t1, v_t1);
        EXCEPTION WHEN not_null_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a NULL payout was accepted'; END IF;

        -- ------------------------------------------------ 4. the readers
        SELECT x INTO v_j FROM public.kalshi_board_games('Basketball') x WHERE x->>'g' = v_gt;
        IF (v_j->>'p')::int IS DISTINCT FROM 1 OR (v_j->>'s')::int IS DISTINCT FROM 1
           OR (v_j->>'c')::int IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_games reads %', v_j;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_games('Basketball', NULL, true) x WHERE x->>'g' = v_gt;
        IF v_j->'s' IS NOT NULL OR (v_j->>'c')::int IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the combo filter kept a scalar market: %', v_j;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x;
        IF (v_j->>'c')::boolean IS DISTINCT FROM true
           OR (SELECT count(*) FROM jsonb_array_elements(v_j->'m') m WHERE (m->>'s')::boolean) <> 1
           OR (SELECT count(*) FROM jsonb_array_elements(v_j->'m') m WHERE m ? 's') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_game reads %', v_j;
        END IF;
        -- An event of scalar markets only: no combo flags, although combo-eligible.
        DELETE FROM public.kalshi_board WHERE market_ticker = v_ev || '-P1-10';
        SELECT x INTO v_j FROM public.kalshi_board_game(v_gt) x;
        IF v_j ? 'c' OR v_j ? 'y' OR v_j ? 'sm' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a scalar-only event shows combo flags: %', v_j;
        END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_sports() x
         WHERE x->>'sport' = 'Basketball' AND (x->>'scalar')::int >= 1;
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_sports has no scalar count'; END IF;
        v_j := public.ctx_kalshi_board();
        IF NOT (v_j ? 'scalar') OR coalesce((v_j->'scalar'->>'Basketball')::int, 0) < 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_kalshi_board scalar reads %', v_j->'scalar';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'top_tonight') x WHERE x->>'market' LIKE '%$1 at%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_kalshi_board top_tonight shows a scalar market';
        END IF;

        -- ------------------------------------------------ 5. the test helper
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, v_soon);
        SELECT count(*) FILTER (WHERE b.payout = 'binary') AS bin, count(*) FILTER (WHERE b.payout = 'scalar') AS sca
          INTO v_row
          FROM public.kalshi_board b JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
         WHERE e.game_ticker = v_game AND b.is_test;
        IF v_row.bin <> 5 OR v_row.sca <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test game has % (binary, scalar) markets', v_row;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_games('Basketball', NULL, false, true) x WHERE x->>'g' = v_game;
        IF (v_j->>'l')::int <> 2 OR (v_j->>'p')::int <> 3 OR (v_j->>'s')::int <> 1 OR (v_j->>'c')::int <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test game reads %', v_j;
        END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_game(v_game, NULL, false, true) x
         WHERE x->>'e' LIKE '%LAD' AND NOT (x ? 'c');
        IF v_n <> 1 THEN RAISE EXCEPTION 'SELFTEST FAILED: the test ladder event shows a combo chip'; END IF;

        -- ------------------------------------------------ 6. objects
        IF (SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.kalshi_board'::regclass AND contype = 'c')
           <> 14 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board does not have 14 CHECK constraints';
        END IF;
        IF has_table_privilege('anon', 'public.v_kalshi_board', 'SELECT')
           OR NOT has_table_privilege('authenticated', 'public.v_kalshi_board', 'SELECT')
           OR has_table_privilege('authenticated', 'public.v_kalshi_board', 'INSERT,UPDATE,DELETE')
           OR NOT has_table_privilege('valemont_readonly', 'public.v_kalshi_board', 'SELECT')
           OR has_function_privilege('authenticated', 'public.kalshi_board_markets_upsert(jsonb, timestamptz, boolean)',
                                     'EXECUTE')
           OR has_function_privilege('anon', 'public.ctx_kalshi_board()', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_games(text, text, boolean, boolean)',
                                         'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants changed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '072_kalshi_board_payout') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '072 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '072_kalshi_board_payout';
-- (b) SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conrelid = 'kalshi_board'::regclass
--        AND conname IN ('kalshi_board_payout_check', 'kalshi_board_variant_check', 'kalshi_board_scalar_shape');
-- (c) SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--      WHERE table_name = 'kalshi_board' AND column_name = 'payout';     -- text, NO, 'binary'::text
-- (d) SELECT payout, count(*) FROM kalshi_board GROUP BY 1;            -- all binary until the new worker
-- (e) SELECT md5(prosrc) FROM pg_proc WHERE proname IN ('kalshi_board_markets_upsert', 'kalshi_board_sports',
--            'kalshi_board_games', 'kalshi_board_game', 'ctx_kalshi_board', 'add_test_kalshi_board');
-- (f) after the new worker's first full pull:
--     SELECT payout, variant, count(*) FROM kalshi_board WHERE NOT is_test GROUP BY 1, 2 ORDER BY 1, 2;
