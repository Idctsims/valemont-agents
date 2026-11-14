-- 079_board_game_tabs.sql
--
-- Chat 8 Phase 3.5, Workstream B (owner, 2026-11-13: "take the per-tab
-- migration"). Paste into the Supabase SQL Editor AFTER db/078. Runs
-- top-to-bottom, no BEGIN/COMMIT.
--
-- WHY. The new /betting opens a game as its own page with three tabs (Game
-- lines, Players, Halves and quarters). One call for a whole NBA game is
-- ~106 KB with labels only (kalshi_board_game); the page needs each market's
-- subject, line, stat and team to draw ladders and player rows. With those,
-- measured on production (read-only, the largest NBA game on the board):
--   Nov 13, UTA at HOU: lines 47 KB, periods 35 KB, players 150 KB (783 markets)
--   Nov 14, CHI at MIN: lines 48 KB, periods 36 KB, players 108 KB (567 markets)
-- So the Players tab is asked for one stat at a time (p_stat). CHI at MIN by
-- stat: threes 21 KB, points 20 KB, rebounds 19 KB, points+rebounds+assists
-- 15 KB, assists 13 KB, every other stat under 6 KB.
--
-- WHAT. Read functions only. No table, column or trigger changes, and
-- kalshi_board_game is untouched (the current page and its specs keep
-- working until the new page replaces them).
--   1. kalshi_board_period(series): '1H' | '2H' | '1Q'..'4Q' | '1P'..'3P' or NULL.
--   2. kalshi_board_tab(series, kind): 'periods' | 'players' | 'lines'.
--   3. kalshi_board_game_tab(game, tab, tests, stat): one jsonb row per event
--      of that tab (and of that stat, when given), each market with its
--      ticker suffix, subject, line, stat, team, bid, ask, fee, variant,
--      scalar flag and packed consensus q.
--   4. kalshi_board_game_tabs(game, tests): the three tabs' market counts and
--      the Players tab's stats, most markets first.

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.kalshi_board_period(p_series text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    -- A period token closes the series ticker, after a letter, with at most one market word behind it.
    -- KXNBA1HSPREAD -> 1H; KXNHL2P -> 2P; KXNBA3PT and KXNBA3PTPROT -> NULL (threes, not a third period).
    SELECT (regexp_match(p_series, '[A-Z](1H|2H|[1-4]Q|[1-3]P)(?:SPREAD|TOTAL|TEAMTOTAL|WINNER|BTTS|SCORE)?$'))[1];
$$;

CREATE OR REPLACE FUNCTION public.kalshi_board_tab(p_series text, p_kind text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT CASE WHEN public.kalshi_board_period(p_series) IS NOT NULL THEN 'periods'
                WHEN p_kind = 'prop' THEN 'players'
                ELSE 'lines' END;
$$;

-- ===================================================================== 2
CREATE OR REPLACE FUNCTION public.kalshi_board_game_tab(p_game text, p_tab text, p_tests boolean DEFAULT false,
                                                        p_stat text DEFAULT NULL)
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
        -- kalshi_board_game's packing, unchanged (db/074): <tier><letter><p><edge>.
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
        SELECT b.market_ticker, e.event_ticker, e.series_ticker, s.title AS series_title, e.combo_eligible,
               e.combo_yes_only, e.combo_size_max, e.fetched_at, b.payout, b.market_kind, b.variant, b.label,
               b.subject, b.stat, b.line, b.team, b.yes_bid, b.yes_ask, b.taker_fee_at_ask, q.packed AS cq
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
          LEFT JOIN q ON q.market_ticker = b.market_ticker
         WHERE e.game_ticker = p_game AND (NOT b.is_test OR p_tests)
           AND public.kalshi_board_tab(e.series_ticker, b.market_kind) = p_tab
           AND (p_stat IS NULL OR b.stat = p_stat)
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'e', m.event_ticker, 'sr', min(m.series_ticker), 't', min(m.series_title),
               'pd', public.kalshi_board_period(min(m.series_ticker)),
               'c', CASE WHEN bool_or(m.combo_eligible AND m.payout = 'binary') THEN true END,
               'y', CASE WHEN bool_or(m.combo_yes_only AND m.payout = 'binary') THEN true END,
               'sm', max(m.combo_size_max) FILTER (WHERE m.payout = 'binary'),
               'asof', max(m.fetched_at),
               'm', jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'x', substr(m.market_ticker, length(m.event_ticker) + 2),
                        'sj', m.subject, 'ln', m.line, 'st', m.stat, 'tm', m.team,
                        -- The label only where the subject does not already say it.
                        'lb', CASE WHEN m.subject IS NULL OR m.label IS DISTINCT FROM m.subject THEN m.label END,
                        'b', m.yes_bid, 'a', m.yes_ask, 'f', m.taker_fee_at_ask, 'v', m.variant,
                        's', CASE WHEN m.payout = 'scalar' THEN true END, 'q', m.cq))
                    ORDER BY m.team NULLS LAST, m.subject NULLS LAST, m.line NULLS FIRST, m.label, m.market_ticker)))
      FROM m
     GROUP BY m.event_ticker
     ORDER BY min(m.series_ticker), m.event_ticker;
$$;

CREATE OR REPLACE FUNCTION public.kalshi_board_game_tabs(p_game text, p_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
               'lines',   count(*) FILTER (WHERE public.kalshi_board_tab(e.series_ticker, b.market_kind) = 'lines'),
               'players', count(*) FILTER (WHERE public.kalshi_board_tab(e.series_ticker, b.market_kind) = 'players'),
               'periods', count(*) FILTER (WHERE public.kalshi_board_tab(e.series_ticker, b.market_kind) = 'periods'),
               'asof', max(e.fetched_at),
               'stats', coalesce((SELECT jsonb_agg(jsonb_build_object('st', z.stat, 'n', z.n) ORDER BY z.n DESC, z.stat)
                                    FROM (SELECT b2.stat, count(*) AS n
                                            FROM public.kalshi_board b2
                                            JOIN public.kalshi_board_events e2 ON e2.event_ticker = b2.event_ticker
                                           WHERE e2.game_ticker = p_game AND (NOT b2.is_test OR p_tests)
                                             AND b2.stat IS NOT NULL
                                             AND public.kalshi_board_tab(e2.series_ticker, b2.market_kind) = 'players'
                                           GROUP BY b2.stat) z), '[]'::jsonb))
      FROM public.kalshi_board b
      JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
     WHERE e.game_ticker = p_game AND (NOT b.is_test OR p_tests);
$$;

-- ===================================================================== 3
REVOKE ALL ON FUNCTION public.kalshi_board_period(text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_tab(text, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_game_tab(text, text, boolean, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.kalshi_board_game_tabs(text, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.kalshi_board_period(text) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_tab(text, text) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_game_tab(text, text, boolean, text) TO authenticated, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.kalshi_board_game_tabs(text, boolean) TO authenticated, valemont_readonly;

-- ===================================================================== 4
INSERT INTO migration_log (name) VALUES ('079_board_game_tabs')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 5
-- Self-test, run on paste, rolled back ('selftest-rollback'). Rows use series
-- KXSTSEVNINE* (no real series has them). A statement that calls a writing
-- function stores its result first and reads back later (db/062's rule). No
-- INSERT ... VALUES tuple in this file except the log row.
DO $$
DECLARE
    v_t0   timestamptz := now() - interval '1 minute';
    v_soon timestamptz := now() + interval '2 hours';
    v_n    int;
    v_j    jsonb;
    v_m    jsonb;
    v_gt   text := 'KXSTSEVNINEGAME-26NOV13AAABBB';
    v_sp   text := 'KXSTSEVNINESPREAD-26NOV13AAABBB';
    v_pt   text := 'KXSTSEVNINE3PT-26NOV13AAABBB';
    v_hs   text := 'KXSTSEVNINE1HSPREAD-26NOV13AAABBB';
    v_q3   text := 'KXSTSEVNINE3Q-26NOV13AAABBB';
    v_ps   text := 'KXSTSEVNINEPTS-26NOV13AAABBB';
    v_tt   text := 'E2ESTSEVNINE-26NOV13CCCDDD';
    v_tg   text := 'E2ESTSEVNINEGAME-26NOV13CCCDDD';
    v_s    text;
BEGIN
    BEGIN
        -- ------------------------------------------------ 1. the classifier
        IF public.kalshi_board_period('KXNBA1HSPREAD') IS DISTINCT FROM '1H'
           OR public.kalshi_board_period('KXNBA4Q') IS DISTINCT FROM '4Q'
           OR public.kalshi_board_period('KXNHL2PTOTAL') IS DISTINCT FROM '2P'
           OR public.kalshi_board_period('KXLIGAMX1HBTTS') IS DISTINCT FROM '1H'
           OR public.kalshi_board_period('KXNBA1HTEAMTOTAL') IS DISTINCT FROM '1H'
           OR public.kalshi_board_period('KXNBA3PT') IS NOT NULL
           OR public.kalshi_board_period('KXNBA3PTPROT') IS NOT NULL
           OR public.kalshi_board_period('KXNBAGAME') IS NOT NULL
           OR public.kalshi_board_period('KXNBASPREAD') IS NOT NULL
           OR public.kalshi_board_period('KXNBA2D') IS NOT NULL
           OR public.kalshi_board_period('KXNBAESCALATOR3PT') IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_period';
        END IF;
        IF public.kalshi_board_tab('KXNBA1Q', 'game_line') <> 'periods'
           OR public.kalshi_board_tab('KXNBA3PT', 'prop') <> 'players'
           OR public.kalshi_board_tab('KXNBASPREAD', 'game_line') <> 'lines'
           OR public.kalshi_board_tab('KXNBAWINMARGIN', 'other') <> 'lines'
           OR public.kalshi_board_tab('KXNBA1HTOTAL', 'prop') <> 'periods' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_tab';
        END IF;

        -- ------------------------------------------------ 2. a game with all three tabs and a test row
        v_n := public.kalshi_board_series_upsert(
            (SELECT jsonb_agg(jsonb_build_object('series_ticker', t, 'title', 'Selftest ' || t, 'sport', 'Basketball',
                        'league', 'nba', 'category', 'Sports', 'tags', jsonb_build_array('Basketball'),
                        'fee_type', 'quadratic', 'fee_multiplier', '1',
                        'fee_regime', jsonb_build_object('fee_type', 'quadratic'),
                        'combo_eligible', false, 'collection_tickers', jsonb_build_array()))
               FROM unnest(ARRAY['KXSTSEVNINESPREAD', 'KXSTSEVNINE3PT', 'KXSTSEVNINE1HSPREAD', 'KXSTSEVNINE3Q',
                                 'KXSTSEVNINEPTS']) t));
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: series insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_events_upsert(
            (SELECT jsonb_agg(jsonb_build_object('event_ticker', e, 'series_ticker', split_part(e, '-', 1),
                        'game_ticker', v_gt, 'game_title', 'AAA vs BBB', 'milestone_start', v_soon))
               FROM unnest(ARRAY[v_sp, v_pt, v_hs, v_q3, v_ps]) e), v_t0);
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: event insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(
            jsonb_build_object('market_ticker', v_sp || '-BBB4', 'event_ticker', v_sp, 'market_kind', 'game_line',
                               'subject', 'BBB', 'stat', 'spread', 'line', 4.5, 'team', 'BBB',
                               'label', 'BBB wins by over 4.5 points', 'yes_bid', 0.40, 'yes_ask', 0.42,
                               'taker_fee_at_ask', 0.017052),
            jsonb_build_object('market_ticker', v_sp || '-AAA2', 'event_ticker', v_sp, 'market_kind', 'game_line',
                               'subject', 'AAA', 'stat', 'spread', 'line', 2.5, 'team', 'AAA',
                               'label', 'AAA wins by over 2.5 points', 'yes_bid', 0.30, 'yes_ask', 0.32),
            jsonb_build_object('market_ticker', v_sp || '-AAA6', 'event_ticker', v_sp, 'market_kind', 'game_line',
                               'subject', 'AAA', 'stat', 'spread', 'line', 6.5, 'team', 'AAA',
                               'label', 'AAA wins by over 6.5 points', 'yes_bid', 0.18, 'yes_ask', 0.20),
            jsonb_build_object('market_ticker', v_pt || '-P1-2', 'event_ticker', v_pt, 'market_kind', 'prop',
                               'subject', 'Player One', 'stat', 'threes', 'line', 1.5, 'team', 'AAA',
                               'label', 'Player One', 'yes_bid', 0.60, 'yes_ask', 0.62),
            jsonb_build_object('market_ticker', v_ps || '-P1-20', 'event_ticker', v_ps, 'market_kind', 'prop',
                               'subject', 'Player One', 'stat', 'points', 'line', 19.5, 'team', 'AAA',
                               'label', 'Player One', 'yes_bid', 0.50, 'yes_ask', 0.52),
            jsonb_build_object('market_ticker', v_ps || '-P1-25', 'event_ticker', v_ps, 'market_kind', 'prop',
                               'subject', 'Player One', 'stat', 'points', 'line', 24.5, 'team', 'AAA',
                               'label', 'Player One', 'yes_bid', 0.24, 'yes_ask', 0.26),
            jsonb_build_object('market_ticker', v_ps || '-P2-20', 'event_ticker', v_ps, 'market_kind', 'prop',
                               'subject', 'Player Two', 'stat', 'points', 'line', 19.5, 'team', 'BBB',
                               'label', 'Player Two', 'yes_bid', 0.30, 'yes_ask', 0.32),
            jsonb_build_object('market_ticker', v_hs || '-AAA1', 'event_ticker', v_hs, 'market_kind', 'game_line',
                               'subject', 'AAA', 'stat', 'spread', 'line', 1.5, 'team', 'AAA',
                               'label', 'AAA wins by over 1.5 points', 'yes_bid', 0.44, 'yes_ask', 0.46),
            jsonb_build_object('market_ticker', v_q3 || '-TIE', 'event_ticker', v_q3, 'market_kind', 'game_line',
                               'subject', 'Tie', 'stat', 'winner', 'label', 'Tie', 'yes_bid', 0.05, 'yes_ask', 0.07)),
            v_t0);
        IF v_n <> 9 THEN RAISE EXCEPTION 'SELFTEST FAILED: market insert wrote %', v_n; END IF;

        -- ------------------------------------------------ 3. counts, and the Players tab's stats
        v_j := public.kalshi_board_game_tabs(v_gt);
        IF (v_j->>'lines')::int <> 3 OR (v_j->>'players')::int <> 4 OR (v_j->>'periods')::int <> 2
           OR (v_j->>'asof')::timestamptz <> v_t0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: tab counts read %', v_j;
        END IF;
        -- Most markets first; no lines or periods stat (spread, winner) among them.
        IF (v_j->'stats')::text IS DISTINCT FROM '[{"n": 3, "st": "points"}, {"n": 1, "st": "threes"}]' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: stats read %', v_j->'stats';
        END IF;

        -- ------------------------------------------------ 4. each tab holds its own markets only
        IF (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'lines')) <> 1
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'players')) <> 2
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'players', false, 'threes')) <> 1
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'players', false, 'nonsense')) <> 0
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'lines', false, 'points')) <> 0
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'periods')) <> 2
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_gt, 'nonsense')) <> 0
           OR (SELECT count(*) FROM public.kalshi_board_game_tab('KXSTSEVNINEGAME-NOSUCH', 'lines')) <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: events per tab';
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game_tab(v_gt, 'lines') x;
        IF v_j->>'e' <> v_sp OR v_j->>'sr' <> 'KXSTSEVNINESPREAD' OR v_j ? 'pd' OR jsonb_array_length(v_j->'m') <> 3
           OR (v_j->>'asof')::timestamptz <> v_t0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the lines event reads %', v_j;
        END IF;
        -- Ordered by team, then subject, then line: AAA 2.5, AAA 6.5, BBB 4.5.
        IF (v_j->'m'->0->>'x') <> 'AAA2' OR (v_j->'m'->1->>'x') <> 'AAA6' OR (v_j->'m'->2->>'x') <> 'BBB4' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: market order reads %', v_j->'m';
        END IF;
        v_m := v_j->'m'->2;
        -- IS DISTINCT FROM throughout: a missing key must fail, not read as NULL and pass.
        IF v_m->>'sj' IS DISTINCT FROM 'BBB' OR (v_m->>'ln')::numeric IS DISTINCT FROM 4.5
           OR v_m->>'st' IS DISTINCT FROM 'spread' OR v_m->>'tm' IS DISTINCT FROM 'BBB'
           OR v_m->>'lb' IS DISTINCT FROM 'BBB wins by over 4.5 points' OR (v_m->>'b')::numeric IS DISTINCT FROM 0.40
           OR (v_m->>'a')::numeric IS DISTINCT FROM 0.42 OR (v_m->>'f')::numeric IS DISTINCT FROM 0.017052
           OR v_m ? 's' OR v_m ? 'v'
           OR v_m->>'q' IS NULL OR v_m->>'q' !~ '^k[a-p][0-9]{3}-?[0-9]+$' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a lines market reads %', v_m;
        END IF;
        -- One stat at a time: points is one event holding both players, and nothing else.
        SELECT jsonb_agg(x) INTO v_j FROM public.kalshi_board_game_tab(v_gt, 'players', false, 'points') x;
        IF jsonb_array_length(v_j) IS DISTINCT FROM 1 OR v_j->0->>'e' IS DISTINCT FROM v_ps
           OR jsonb_array_length(v_j->0->'m') IS DISTINCT FROM 3
           OR v_j->0->'m'->0->>'x' IS DISTINCT FROM 'P1-20'
           OR v_j->0->'m'->1->>'x' IS DISTINCT FROM 'P1-25'
           OR v_j->0->'m'->2->>'sj' IS DISTINCT FROM 'Player Two' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the points stat reads %', v_j;
        END IF;
        SELECT x INTO v_j FROM public.kalshi_board_game_tab(v_gt, 'players', false, 'threes') x;
        v_m := v_j->'m'->0;
        -- A label equal to the subject is not sent twice.
        IF v_j->>'sr' IS DISTINCT FROM 'KXSTSEVNINE3PT' OR v_j ? 'pd' OR v_m->>'sj' IS DISTINCT FROM 'Player One'
           OR v_m ? 'lb' OR v_m->>'st' IS DISTINCT FROM 'threes' OR v_m->>'x' IS DISTINCT FROM 'P1-2'
           OR v_m->>'tm' IS DISTINCT FROM 'AAA' OR (v_m->>'ln')::numeric IS DISTINCT FROM 1.5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the players event reads %', v_j;
        END IF;
        IF (SELECT array_agg(x->>'pd' ORDER BY x->>'sr') FROM public.kalshi_board_game_tab(v_gt, 'periods') x)
           IS DISTINCT FROM ARRAY['1H', '3Q'] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: periods read %',
                (SELECT jsonb_agg(x) FROM public.kalshi_board_game_tab(v_gt, 'periods') x);
        END IF;

        -- ------------------------------------------------ 5. consensus rides in q
        v_n := public.leg_consensus_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_sp || '-BBB4', 'tier', 'books', 'n_books', 3, 'p_books', 0.55, 'p_books_mult', 0.55,
            'inputs', '{"m": "power"}'::jsonb, 'source_at', now())), v_t0);
        SELECT m->>'q' INTO v_s FROM public.kalshi_board_game_tab(v_gt, 'lines') x,
               jsonb_array_elements(x->'m') m WHERE m->>'x' = 'BBB4';
        IF v_n <> 1 OR v_s IS NULL OR v_s !~ '^b3[a-p][0-9]{3}-?[0-9]+$' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a books market''s q reads % (wrote %)', v_s, v_n;
        END IF;

        -- ------------------------------------------------ 6. test rows only when asked
        v_n := public.kalshi_board_series_upsert(jsonb_build_array(jsonb_build_object(
            'series_ticker', 'E2ESTSEVNINE', 'title', 'Selftest E2E', 'sport', 'Basketball', 'league', 'nba',
            'category', 'Sports', 'tags', jsonb_build_array('Basketball'), 'fee_type', 'quadratic',
            'fee_multiplier', '1', 'fee_regime', jsonb_build_object('fee_type', 'quadratic'),
            'combo_eligible', false, 'collection_tickers', jsonb_build_array())), true);
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', v_tt, 'series_ticker', 'E2ESTSEVNINE', 'game_ticker', v_tg, 'game_title', 'CCC vs DDD',
            'milestone_start', v_soon)), v_t0, true);
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_tt || '-CCC', 'event_ticker', v_tt, 'market_kind', 'game_line', 'subject', 'CCC',
            'stat', 'winner', 'team', 'CCC', 'label', 'CCC', 'yes_bid', 0.50, 'yes_ask', 0.52),
            jsonb_build_object(
            'market_ticker', v_tt || '-P9-20', 'event_ticker', v_tt, 'market_kind', 'prop', 'subject', 'Player Nine',
            'stat', 'points', 'line', 19.5, 'team', 'CCC', 'label', 'Player Nine', 'yes_bid', 0.40,
            'yes_ask', 0.42)), v_t0, true);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: the test markets wrote %', v_n; END IF;
        v_j := public.kalshi_board_game_tabs(v_tg);
        IF (v_j->>'lines')::int <> 0 OR (v_j->>'players')::int <> 0 OR (v_j->'stats')::text IS DISTINCT FROM '[]'
           OR EXISTS (SELECT 1 FROM public.kalshi_board_game_tab(v_tg, 'lines'))
           OR EXISTS (SELECT 1 FROM public.kalshi_board_game_tab(v_tg, 'players')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test row is served without the flag: %', v_j;
        END IF;
        v_j := public.kalshi_board_game_tabs(v_tg, true);
        IF (v_j->>'lines')::int <> 1 OR (v_j->>'players')::int <> 1
           OR (v_j->'stats')::text IS DISTINCT FROM '[{"n": 1, "st": "points"}]'
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_tg, 'players', true, 'points')) <> 1
           OR (SELECT count(*) FROM public.kalshi_board_game_tab(v_tg, 'lines', true)) <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test row is not served with the flag: %', v_j;
        END IF;

        -- ------------------------------------------------ 7. objects
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '079_board_game_tabs') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_function_privilege('anon', 'public.kalshi_board_game_tab(text, text, boolean, text)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.kalshi_board_game_tabs(text, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.kalshi_board_tab(text, text)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.kalshi_board_period(text)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_game_tab(text, text, boolean, text)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_game_tabs(text, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_game_tab(text, text, boolean, text)',
                                         'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_game_tabs(text, boolean)', 'EXECUTE')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '079 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '079_board_game_tabs';
-- (b) SELECT public.kalshi_board_game_tabs('<an NBA game ticker>');
-- (c) each tab's size: SELECT octet_length(string_agg(x::text, '')) FROM kalshi_board_game_tab('<game>', 'lines') x;
--     and one stat's: ... FROM kalshi_board_game_tab('<game>', 'players', false, 'points') x;
-- (d) md5 of the four function bodies against the scratch copy's; the grants.
