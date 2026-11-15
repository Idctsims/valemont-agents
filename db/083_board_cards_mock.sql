-- 083_board_cards_mock.sql
--
-- Chat 8 Phase 3.5, Workstream B (owner, 2026-11-15, PR #125: "match the
-- mock on all of them"). Paste into the Supabase SQL Editor AFTER db/082
-- (db/081 belongs to the Wags session and is independent of this file).
-- Runs top-to-bottom, no BEGIN/COMMIT.
--
-- WHY. The approved mock's board card carries, per game: each team's city,
-- nickname and logo, Kalshi's price to win as the big number, the books'
-- consensus as a team-colour bar, the spread and total at the books' line,
-- and the market count. db/082's card carried the prices and the claim flag
-- only. The owner's Nov 15 rule (no consensus pass over whole games) holds:
-- nothing here runs leg_consensus_for; each game's winner markets are priced
-- with consensus_leg's blend inline, and the spread and total are picked by
-- whether a books (or ESPN) row exists for a rung, never priced.
--
-- WHAT. Read functions and the e2e test helper. No table, column or trigger.
--   1. kalshi_board_cards(sport, tests), replaced (same signature):
--        g, t, at, lg, test, hc, wy   as db/082
--        asof the board's latest fetch, on the FIRST card only (was every card)
--        n    every market of the game (binary and scalar; the mock's "583 markets")
--        w    the winner markets [ticker suffix, YES bid, YES ask, p], Kalshi's
--             order (away first as the title names them), p the consensus
--             (0.7 x outside + 0.3 x the event-normalized Kalshi mid, else
--             the mid: leg_consensus_for's p_consensus, to 3 decimals)
--        wn   books at the line when any winner market has a books row;
--        we   true when the winner's outside price is ESPN's only
--        tm   the linked ESPN game's two teams, in w's order (Kalshi's: away
--             first unless the link says home and away are swapped), each
--             [city, nickname, ESPN abbreviation, ESPN id, colour, 'a'|'h'];
--             absent for a game with no ESPN link
--        at   the start, epoch seconds
--        sp   the main spread: [market ticker with the game's date-and-teams part as '~', team, line, YES bid, YES ask,
--             1 when its event takes YES only in a combo]
--        to   the main total: [market ticker as sp's, line, YES bid, YES ask, yes-only]
--      The main line is the rung with a books row (most books first), else an
--      ESPN row, else the rung asked nearest 50c; Game lines tab only (never a
--      half or quarter), binary only.
--      Dropped from db/082's card: l, p, o, s, c (the page shows n only).
--   2. add_test_kalshi_board: db/082's rows, plus a linked test ESPN game
--      (Test Away / Test Home, city, nickname, colours, no logo), a spread
--      event (books row on HOM -6.5, the main line though -3.5 is nearer 50c)
--      and a total event (no books row: 220.5, the rung nearest 50c).
--
-- Also replaces leg_span_claims (db/082), same rule, the pricing arithmetic
-- inline (section 0).
--
-- Measured before handover on a local PG 17 copy of production's board
-- (Nov 15 ~20:50 UTC: 37,601 markets, 269 span rows; the 97 linked ESPN rows
-- copied as valemont_readonly, with city, nickname, colour and logo added as
-- the new worker stores them), JIT off, three runs each, kalshi_board_games
-- against this kalshi_board_cards:
--   Basketball (213 games, 73 linked)  25 ms, 37.3 KB  ->  89-91 ms, 52.9 KB
--   Football (14)                      24 ms,  3.0 KB  ->  76-79 ms,  4.8 KB
--   Hockey (20)                         9 ms,  3.5 KB  ->  44 ms,     4.6 KB
--   Soccer (53)                        10 ms,  8.8 KB  ->  49-51 ms, 13.2 KB
--   Tennis (110)                        9 ms, 17.9 KB  ->  48-49 ms, 17.8 KB
-- db/082's reader on the same copy: Basketball 54 ms, Football 128 ms.
-- At this morning's 234 Basketball games the payload scales to ~58 KB.
-- Every card's p equals leg_consensus_for's p_consensus to 3 places (835
-- winner markets, 5 sports, 0 differences); leg_span_claims agrees with
-- leg_consensus_for on all 269 span rows (as copied and all prices fresh).
-- Self-test passes; 22 mutations, 22 red.

-- ===================================================================== 0
-- leg_span_claims (db/082), the same rule, with consensus_leg's and
-- kalshi_taker_fee's arithmetic written inline: three function calls per span
-- row were most of a card read (Football, 234 span rows: ~100 of ~215 ms).
-- The self-test compares every claim with leg_consensus_for's.
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
        -- leg_consensus_for's normalization window (the event's binary markets), per span row (db/082).
        SELECT t.*, round(CASE WHEN coalesce(t.mutually_exclusive AND ev.n_ev >= 2 AND ev.n_quoted = ev.n_ev
                                             AND ev.mid_sum BETWEEN 0.95 AND 1.05, false)
                               THEN t.mid / ev.mid_sum ELSE t.mid END, 6) AS pk,
               1 - t.yes_bid AS no_ask
          FROM t
         CROSS JOIN LATERAL (
                SELECT sum((b.yes_bid + b.yes_ask) / 2) AS mid_sum, count(*) AS n_ev,
                       count(b.yes_bid + b.yes_ask) AS n_quoted
                  FROM public.kalshi_board b
                 WHERE b.event_ticker = t.event_ticker AND b.payout = 'binary') ev
    ),
    q AS (
        -- consensus_leg at the shipped p, the lowest (YES) and the highest (NO): p = 0.7 x outside + 0.3 x
        -- Kalshi (NULL with no Kalshi mid); the taker fee kalshi_taker_fee's, at the ask and at 1 - the bid.
        SELECT kk.market_ticker, kk.game_ticker, kk.kalshi_at, kk.source_at, kk.yes_ask, kk.no_ask,
               round(0.7 * kk.p_books + 0.3 * kk.pk, 6) AS p,
               round(0.7 * kk.p_books_lo + 0.3 * kk.pk, 6) AS p_lo,
               round(0.7 * kk.p_books_hi + 0.3 * kk.pk, 6) AS p_hi,
               CASE WHEN kk.yes_ask IS NULL OR kk.fee_multiplier IS NULL OR kk.yes_ask <= 0 OR kk.yes_ask >= 1 THEN NULL
                    ELSE ceil(0.07 * kk.fee_multiplier * kk.yes_ask * (1 - kk.yes_ask) * 1000000) / 1000000 END AS fy,
               CASE WHEN kk.no_ask IS NULL OR kk.fee_multiplier IS NULL OR kk.no_ask <= 0 OR kk.no_ask >= 1 THEN NULL
                    ELSE ceil(0.07 * kk.fee_multiplier * kk.no_ask * (1 - kk.no_ask) * 1000000) / 1000000 END AS fn
          FROM kk
    )
    SELECT q.market_ticker, q.game_ticker,
           -- db/080's claim, with tested = true (every row here carries a span); never NULL.
           coalesce(NOT (q.kalshi_at < now() - interval '20 minutes')
                    AND NOT coalesce(q.source_at < now() - interval '12 hours', false)
                    AND ((coalesce(q.p - (q.yes_ask + q.fy), -1) > 0 AND coalesce(q.p_lo - (q.yes_ask + q.fy), -1) > 0)
                         OR (coalesce((1 - q.p) - (q.no_ask + q.fn), -1) > 0
                             AND coalesce((1 - q.p_hi) - (q.no_ask + q.fn), -1) > 0)), false)
      FROM q;
$$;

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.kalshi_board_cards(p_sport text, p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH g AS (
        SELECT e.game_ticker, substr(e.game_ticker, strpos(e.game_ticker, '-') + 1) AS tail, min(e.game_title) AS title, min(e.starts_at) AS at, min(s.league) AS lg,
               count(*) AS n, date_trunc('second', max(e.fetched_at)) AS asof, bool_or(b.is_test) AS test
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE (NOT b.is_test OR p_tests) AND s.sport = p_sport AND e.starts_at > now() - interval '6 hours'
         GROUP BY e.game_ticker
    ),
    hc AS (
        -- Span rows only (a claim needs one), and only this sport's games (db/082).
        SELECT DISTINCT x.game_ticker
          FROM public.leg_span_claims((SELECT array_agg(g.game_ticker) FROM g)) x
         WHERE x.edge_claimed
    ),
    ml AS (
        -- The main spread and total of every game in one pass: the books' rung, else ESPN's, else the one
        -- asked nearest 50c. Game lines only: kalshi_board_tab(series, kind) = 'lines' is exactly no period
        -- token in the series and not a prop (db/079), written so the period test runs once per event.
        SELECT DISTINCT ON (e2.game_ticker, b.stat) e2.game_ticker, b.stat, b.market_ticker,
               coalesce(b.team, b.subject) AS who, b.line, b.yes_bid, b.yes_ask, e2.combo_yes_only
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e2 ON e2.event_ticker = b.event_ticker
          JOIN g ON g.game_ticker = e2.game_ticker
          LEFT JOIN public.leg_consensus c ON c.market_ticker = b.market_ticker AND c.tier IN ('books', 'espn')
         WHERE b.stat IN ('spread', 'total') AND b.line IS NOT NULL AND b.payout = 'binary'
           AND (NOT b.is_test OR p_tests) AND b.market_kind <> 'prop'
           AND public.kalshi_board_period(e2.series_ticker) IS NULL
         ORDER BY e2.game_ticker, b.stat, (c.tier IS NULL), (c.tier = 'espn'), c.n_books DESC NULLS LAST,
                  abs(coalesce(b.yes_ask, 2) - 0.5), b.market_ticker
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               -- The title only where no ESPN teams say it.
               'g', g.game_ticker, 't', CASE WHEN tm.tm IS NULL THEN g.title END,
               -- The start as epoch seconds (an ISO string is 27 characters).
               'at', floor(extract(epoch FROM g.at))::bigint, 'lg', g.lg, 'n', g.n,
               -- The board's latest fetch, on the first card only (the page shows one source time).
               'asof', CASE WHEN row_number() OVER (ORDER BY g.at, g.title) = 1 THEN max(g.asof) OVER () END,
               'test', CASE WHEN g.test THEN true END,
               'w', w.w, 'wn', w.wn, 'we', w.we,
               'wy', CASE WHEN ge.combo_yes_only THEN true END,
               'tm', tm.tm, 'hs', tm.hs,
               'sp', CASE WHEN sp.market_ticker IS NOT NULL THEN
                          jsonb_build_array(replace(sp.market_ticker, '-' || g.tail || '-', '-~-'), sp.who, sp.line::float8, sp.yes_bid::float8,
                                            sp.yes_ask::float8, CASE WHEN sp.combo_yes_only THEN 1 ELSE 0 END) END,
               'to', CASE WHEN tt.market_ticker IS NOT NULL THEN
                          jsonb_build_array(replace(tt.market_ticker, '-' || g.tail || '-', '-~-'), tt.line::float8, tt.yes_bid::float8, tt.yes_ask::float8,
                                            CASE WHEN tt.combo_yes_only THEN 1 ELSE 0 END) END,
               'hc', CASE WHEN hc.game_ticker IS NOT NULL THEN true END))
      FROM g
      LEFT JOIN public.kalshi_board_events ge ON ge.event_ticker = g.game_ticker
      -- The winner markets of the game's own event, each with its consensus p (leg_consensus_for's blend).
      LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_array(substr(x.market_ticker, length(x.event_ticker) + 2),
                                               x.yes_bid::float8, x.yes_ask::float8,
                                               -- consensus_leg rounds to 6 places first; so does this.
                                               round(round(CASE WHEN x.p_out IS NULL THEN x.pk
                                                                ELSE 0.7 * x.p_out + 0.3 * x.pk END, 6), 3)::float8)
                             ORDER BY x.pos NULLS LAST, x.market_ticker) AS w,
                   max(x.n_books) FILTER (WHERE x.tier = 'books') AS wn,
                   CASE WHEN bool_or(x.tier = 'espn') AND NOT coalesce(bool_or(x.tier = 'books'), false)
                        THEN true END AS we
              FROM (SELECT b.market_ticker, b.event_ticker, b.yes_bid, b.yes_ask,
                           nullif(strpos(g.title, coalesce(b.subject, '')), 0) AS pos,
                           c.tier, c.n_books,
                           CASE c.tier WHEN 'books' THEN c.p_books WHEN 'espn' THEN c.p_espn END AS p_out,
                           round(CASE WHEN coalesce(ge.mutually_exclusive AND ev.n_ev >= 2 AND ev.n_quoted = ev.n_ev
                                                    AND ev.mid_sum BETWEEN 0.95 AND 1.05, false)
                                      THEN ((b.yes_bid + b.yes_ask) / 2) / ev.mid_sum
                                      ELSE (b.yes_bid + b.yes_ask) / 2 END, 6) AS pk
                      FROM public.kalshi_board b
                      LEFT JOIN public.leg_consensus c ON c.market_ticker = b.market_ticker
                     CROSS JOIN (SELECT sum((b2.yes_bid + b2.yes_ask) / 2) AS mid_sum, count(*) AS n_ev,
                                        count(b2.yes_bid + b2.yes_ask) AS n_quoted
                                   FROM public.kalshi_board b2
                                  WHERE b2.event_ticker = g.game_ticker AND b2.payout = 'binary') ev
                     WHERE b.event_ticker = g.game_ticker AND b.stat = 'winner' AND b.line IS NULL
                       AND b.payout = 'binary' AND (NOT b.is_test OR p_tests)) x) w ON true
      -- The linked ESPN game's teams, in Kalshi's order (away first unless the link says swapped), each
      -- [city (else the full name), nickname, logo key, colour]. The logo key is ESPN's logo path folded to
      -- "<folder>/<file>" ("nba/det", "ncaa/254"): the web adds the host, 500 and .png back.
      LEFT JOIN LATERAL (
            SELECT CASE WHEN se.home_away_swapped THEN jsonb_build_array(h.t, a.t)
                        ELSE jsonb_build_array(a.t, h.t) END AS tm,
                   CASE WHEN se.home_away_swapped THEN true END AS hs
              FROM public.sports_events se
             CROSS JOIN LATERAL (SELECT jsonb_build_array(
                       coalesce(se.participants->'away'->>'loc', se.away), se.participants->'away'->>'nick',
                       (regexp_match(se.participants->'away'->>'logo', '^([a-z]+)/500/(?:scoreboard/)?([a-z0-9-]+)\.png$'))[1]
                       || '/' || (regexp_match(se.participants->'away'->>'logo', '^([a-z]+)/500/(?:scoreboard/)?([a-z0-9-]+)\.png$'))[2],
                       se.participants->'away'->>'color') AS t) a
             CROSS JOIN LATERAL (SELECT jsonb_build_array(
                       coalesce(se.participants->'home'->>'loc', se.home), se.participants->'home'->>'nick',
                       (regexp_match(se.participants->'home'->>'logo', '^([a-z]+)/500/(?:scoreboard/)?([a-z0-9-]+)\.png$'))[1]
                       || '/' || (regexp_match(se.participants->'home'->>'logo', '^([a-z]+)/500/(?:scoreboard/)?([a-z0-9-]+)\.png$'))[2],
                       se.participants->'home'->>'color') AS t) h
             WHERE se.id = ge.sports_event_id AND se.kind = 'game'
               AND jsonb_typeof(se.participants) = 'object') tm ON true
      LEFT JOIN ml sp ON sp.game_ticker = g.game_ticker AND sp.stat = 'spread'
      LEFT JOIN ml tt ON tt.game_ticker = g.game_ticker AND tt.stat = 'total'
      LEFT JOIN hc ON hc.game_ticker = g.game_ticker
     ORDER BY g.at, g.title;
$$;

-- ===================================================================== 2
-- db/082's helper, plus a linked test ESPN game, a spread event and a total event.
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
    v_spr  text;
    v_tot  text;
    v_sid  bigint;
BEGIN
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'add_test_kalshi_board: the owner and a verified test marker only';
    END IF;
    v_game := 'E2ETEST-' || v_tag;
    v_pts := 'E2ETEST-' || v_tag || 'PTS';
    v_lad := 'E2ETEST-' || v_tag || 'LAD';
    v_spr := 'E2ETEST-' || v_tag || 'SPR';
    v_tot := 'E2ETEST-' || v_tag || 'TOT';
    INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier, fee_regime,
                                            combo_eligible, collection_tickers, is_test)
    VALUES ('E2ETEST', 'E2E Test Series', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
            '{"fee_type": "quadratic", "fee_multiplier": "1"}'::jsonb, true, ARRAY['E2ECOLL-R'], true)
    ON CONFLICT (series_ticker) DO NOTHING;
    -- The linked ESPN game (a test row: an e2e- id). No logo: the card shows the city and nickname.
    INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants, status,
                                      espn_status, broadcast, national_tv, content_hash, is_test)
    VALUES ('nba', 'game', 'e2e-' || lower(v_tag), 'Test Away at Test Home', p_starts_at, 'Test Home', 'Test Away',
            '{"away": {"id": "9001", "name": "Test Away", "abbr": "AWY", "loc": "Testville", "nick": "Away",
                       "color": "2b6cb0", "alt": "ffffff"},
              "home": {"id": "9002", "name": "Test Home", "abbr": "HOM", "loc": "Hometown", "nick": "Home",
                       "color": "c53030", "alt": "000000"}}'::jsonb,
            'scheduled', 'STATUS_SCHEDULED', '{}', false, md5(v_tag), true)
    RETURNING id INTO v_sid;
    INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                            starts_at, sports_event_id, combo_eligible, combo_yes_only,
                                            combo_collections, combo_size_max, combo_quoters, mutually_exclusive,
                                            is_test, fetched_at)
    VALUES (v_game, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, v_sid, true, true,
            ARRAY['E2ECOLL-R'], 1, 0, true, true, now()),
           (v_pts, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, NULL, false, false,
            '{}', NULL, NULL, false, true, now()),
           (v_lad, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, NULL, true, false,
            ARRAY['E2ECOLL-R'], NULL, NULL, false, true, now()),
           (v_spr, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, NULL, true, false,
            ARRAY['E2ECOLL-R'], 1, NULL, false, true, now()),
           (v_tot, 'E2ETEST', v_game, 'Test Away vs Test Home', p_starts_at, p_starts_at, NULL, true, false,
            ARRAY['E2ECOLL-R'], 1, NULL, false, true, now());
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
         'Test Player: points, $1 at 50', 0.3000, 0.3200, 20, 10, 0.015232, 'HOM'),
        (v_spr || '-HOM3', v_spr, 'game_line', NULL, 'binary', 'Test Home', 'spread', 3.5,
         'Test Home wins by over 3.5 points', 0.5300, 0.5500, 400, 300, 0.017325, 'HOM'),
        (v_spr || '-HOM6', v_spr, 'game_line', NULL, 'binary', 'Test Home', 'spread', 6.5,
         'Test Home wins by over 6.5 points', 0.3800, 0.4000, 400, 300, 0.0168, 'HOM'),
        (v_spr || '-AWY2', v_spr, 'game_line', NULL, 'binary', 'Test Away', 'spread', 2.5,
         'Test Away wins by over 2.5 points', 0.2800, 0.3000, 200, 100, 0.0147, 'AWY'),
        (v_tot || '-220', v_tot, 'game_line', NULL, 'binary', NULL, 'total', 220.5,
         'Over 220.5 points scored', 0.4600, 0.4800, 300, 200, 0.017472, NULL),
        (v_tot || '-224', v_tot, 'game_line', NULL, 'binary', NULL, 'total', 224.5,
         'Over 224.5 points scored', 0.2800, 0.3000, 300, 200, 0.0147, NULL)
      ) AS v (market_ticker, event_ticker, market_kind, variant, payout, subject, stat, line, label, yes_bid, yes_ask,
              volume, open_interest, fee, team);
    -- HOM: claims (db/082). P1-10: positive under the shipped method, fails the least favourable.
    -- HOM6: the books' spread line, no span (never claims).
    INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, p_books_lo, p_books_hi,
                                      p_espn, inputs, source_at, computed_at)
    SELECT v.market_ticker, 'books', v.n_books, v.p_books, v.p_books_mult, v.p_lo, v.p_hi, NULL, v.inputs, now(), now()
      FROM (VALUES
        (v_game || '-HOM', 3::smallint, 0.660000::numeric, 0.659000::numeric, 0.659000::numeric, 0.665000::numeric,
         '{"m": "power", "b": {"draftkings": 0.66, "fanduel": 0.665, "betmgm": 0.655}}'::jsonb),
        (v_pts || '-P1-10', 2::smallint, 0.620000, 0.620000, 0.580000, 0.630000,
         '{"m": "power", "b": {"draftkings": 0.62, "fanduel": 0.62}}'::jsonb),
        (v_spr || '-HOM6', 4::smallint, 0.410000, 0.410000, NULL, NULL,
         '{"m": "power", "b": {"draftkings": 0.41, "fanduel": 0.41, "betmgm": 0.40, "caesars": 0.42}}'::jsonb)
      ) AS v (market_ticker, n_books, p_books, p_books_mult, p_lo, p_hi, inputs);
    RETURN v_game;
END;
$$;

-- ===================================================================== 3
REVOKE ALL ON FUNCTION public.kalshi_board_cards(text, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.kalshi_board_cards(text, boolean) TO authenticated, valemont_readonly;
-- add_test_kalshi_board keeps db/071's grants (CREATE OR REPLACE): authenticated only.

INSERT INTO migration_log (name) VALUES ('083_board_cards_mock')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 4
-- Self-test, run on paste, rolled back ('selftest-rollback'). The owner's
-- session is simulated with request.jwt claims and a throwaway marker, as in
-- db/076. Test rows only. A statement that calls a writing function stores
-- its result first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker text := 'selftest-083-' || md5(random()::text);
    v_game   text;
    v_j      jsonb;
    v_n      int;
    v_sid    bigint;
    v_pts    text;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));

        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        v_pts := v_game || 'PTS';

        -- ------------------------------------------------ 1. the card
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j IS NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: no test card'; END IF;
        -- Away first; p: AWY has no outside row (its normalized mid 0.38), HOM 0.7 x 0.66 + 0.3 x 0.62 = 0.648.
        IF v_j->'w' IS DISTINCT FROM '[["AWY", 0.37, 0.39, 0.38], ["HOM", 0.61, 0.63, 0.648]]'::jsonb
           OR (v_j->>'wn')::int IS DISTINCT FROM 3 OR v_j ? 'we' OR v_j->>'wy' IS DISTINCT FROM 'true'
           OR v_j->>'hc' IS DISTINCT FROM 'true' OR (v_j->>'n')::int IS DISTINCT FROM 11
           OR v_j ? 'l' OR v_j ? 'c' OR v_j->>'test' IS DISTINCT FROM 'true' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the card reads %', v_j;
        END IF;
        -- One source time for the board: the first card carries it, no other.
        IF (SELECT count(*) FROM public.kalshi_board_cards('Basketball', true) x WHERE x ? 'asof') <> 1
           OR NOT (SELECT x ? 'asof' FROM public.kalshi_board_cards('Basketball', true) x LIMIT 1) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the board''s source time is not on the first card only';
        END IF;
        -- The bar's p is leg_consensus_for's p_consensus, to 3 decimals, for every winner market.
        SELECT count(*) INTO v_n
          FROM jsonb_array_elements(v_j->'w') w
          JOIN public.leg_consensus_for(ARRAY[v_game]) f ON f.market_ticker = v_game || '-' || (w->>0)
         WHERE round(f.p_consensus, 3) IS DISTINCT FROM (w->>3)::numeric;
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: % winner p differ from leg_consensus_for', v_n; END IF;
        -- Teams in Kalshi's order, from the linked ESPN game.
        -- No logo on the test teams; no title (the teams say it); not swapped.
        IF v_j->'tm' IS DISTINCT FROM '[["Testville", "Away", null, "2b6cb0"], ["Hometown", "Home", null, "c53030"]]'::jsonb
           OR v_j ? 't' OR v_j ? 'hs' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the teams read % (card %)', v_j->'tm', v_j;
        END IF;
        -- The spread at the books' line (-6.5, though -3.5 is nearer 50c); the total nearest 50c (220.5).
        IF v_j->'sp' IS DISTINCT FROM jsonb_build_array(v_game || 'SPR-HOM6', 'HOM', 6.5, 0.38, 0.40, 0)
           OR v_j->'to' IS DISTINCT FROM jsonb_build_array(v_game || 'TOT-220', 220.5, 0.46, 0.48, 0) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the main lines read % / %', v_j->'sp', v_j->'to';
        END IF;

        -- ------------------------------------------------ 1b. db/082's claim cases, on the inlined leg_span_claims
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

        -- The fee decides: P1-10's low bound 0.60 blends to 0.5895, under 0.58 + 0.017052 (no claim);
        -- without the fee it would pass.
        UPDATE public.leg_consensus SET p_books_lo = 0.60 WHERE market_ticker = v_pts || '-P1-10';
        IF (SELECT edge_claimed FROM public.leg_span_claims(ARRAY[v_game]) WHERE market_ticker = v_pts || '-P1-10')
               IS DISTINCT FROM false
           OR (SELECT edge_claimed FROM public.leg_consensus_for(ARRAY[v_game]) WHERE market_ticker = v_pts || '-P1-10')
               IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the taker fee did not stop P1-10';
        END IF;
        UPDATE public.leg_consensus SET p_books_lo = 0.58 WHERE market_ticker = v_pts || '-P1-10';

        -- ------------------------------------------------ 1c. stale Kalshi: no claim, no flag, in both functions
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

        -- ------------------------------------------------ 2. the main line: espn after books, nearest 50c last
        UPDATE public.leg_consensus SET tier = 'espn', n_books = 1, p_espn = 0.41, p_books = NULL, p_books_mult = NULL
         WHERE market_ticker = v_game || 'SPR-HOM6';
        SELECT x->'sp'->0 INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j #>> '{}' IS DISTINCT FROM v_game || 'SPR-HOM6' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an ESPN rung is not the main line: %', v_j;
        END IF;
        -- Books beat ESPN, even with fewer books than ESPN's row counts.
        INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, inputs, source_at,
                                          computed_at)
        SELECT v_game || 'SPR-AWY2', 'books', 2, 0.29, 0.29, '{"m": "power"}'::jsonb, now(), now();
        SELECT x->'sp'->0 INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j #>> '{}' IS DISTINCT FROM v_game || 'SPR-AWY2' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an ESPN rung beat a books rung: %', v_j;
        END IF;
        DELETE FROM public.leg_consensus WHERE market_ticker IN (v_game || 'SPR-HOM6', v_game || 'SPR-AWY2');
        SELECT x->'sp'->0 INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j #>> '{}' IS DISTINCT FROM v_game || 'SPR-HOM3' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: with no outside row the main spread is %', v_j;
        END IF;
        -- A half's spread never stands in for the game's, even nearer 50c.
        INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier,
                                                fee_regime, combo_eligible, collection_tickers, is_test)
        SELECT 'E2ETEST1HSPREAD', 'E2E 1H spread', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
               '{"fee_type": "quadratic"}'::jsonb, false, '{}', true;
        INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                                starts_at, combo_eligible, combo_yes_only, combo_collections,
                                                mutually_exclusive, is_test, fetched_at)
        SELECT 'E2ETEST1HSPREAD-' || substr(v_game, 9), 'E2ETEST1HSPREAD', v_game, 'Test Away vs Test Home',
               now() + interval '2 hours', now() + interval '2 hours', false, false, '{}', false, true, now();
        INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, payout, subject, stat, line, label,
                                         yes_bid, yes_ask, team, is_test, fetched_at, last_changed_at)
        SELECT 'E2ETEST1HSPREAD-' || substr(v_game, 9) || '-HOM1', 'E2ETEST1HSPREAD-' || substr(v_game, 9),
               'game_line', 'binary', 'Test Home', 'spread', 1.5, 'Test Home wins the half by over 1.5', 0.49, 0.50,
               'HOM', true, now(), now();
        SELECT x->'sp'->0 INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j #>> '{}' IS DISTINCT FROM v_game || 'SPR-HOM3' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a half''s spread became the main spread: %', v_j;
        END IF;
        -- A spread event named as Kalshi names them (series-<date and teams>): its ticker goes out with '~',
        -- and a rung with more books wins.
        INSERT INTO public.kalshi_series_board (series_ticker, title, sport, tags, fee_type, fee_multiplier,
                                                fee_regime, combo_eligible, collection_tickers, is_test)
        SELECT 'E2ETESTSPREAD', 'E2E spread', 'Basketball', ARRAY['Basketball'], 'quadratic', 1,
               '{"fee_type": "quadratic"}'::jsonb, false, '{}', true;
        INSERT INTO public.kalshi_board_events (event_ticker, series_ticker, game_ticker, game_title, milestone_start,
                                                starts_at, combo_eligible, combo_yes_only, combo_collections,
                                                mutually_exclusive, is_test, fetched_at)
        SELECT 'E2ETESTSPREAD-' || substr(v_game, 9), 'E2ETESTSPREAD', v_game, 'Test Away vs Test Home',
               now() + interval '2 hours', now() + interval '2 hours', false, false, '{}', false, true, now();
        INSERT INTO public.kalshi_board (market_ticker, event_ticker, market_kind, payout, subject, stat, line, label,
                                         yes_bid, yes_ask, team, is_test, fetched_at, last_changed_at)
        SELECT 'E2ETESTSPREAD-' || substr(v_game, 9) || '-HOM9', 'E2ETESTSPREAD-' || substr(v_game, 9), 'game_line',
               'binary', 'Test Home', 'spread', 9.5, 'Test Home wins by over 9.5', 0.20, 0.22, 'HOM', true, now(), now();
        INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, inputs, source_at,
                                          computed_at)
        SELECT 'E2ETESTSPREAD-' || substr(v_game, 9) || '-HOM9', 'books', 6, 0.21, 0.21, '{"m": "power"}'::jsonb,
               now(), now();
        SELECT x->'sp' INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j IS DISTINCT FROM '["E2ETESTSPREAD-~-HOM9", "HOM", 9.5, 0.2, 0.22, 0]'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a Kalshi-named spread reads %', v_j;
        END IF;

        -- ------------------------------------------------ 3. teams: swapped, no participants, no link
        -- A swap is set only on a linked row (db/066), at insert: a second test game, linked and swapped.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          status, espn_status, content_hash, is_test, kalshi_event_ticker,
                                          link_state, link_method, home_away_swapped)
        SELECT 'nba', 'game', 'e2e-swap-' || lower(substr(v_game, 9)), 'Swap', now() + interval '2 hours',
               'Test Home', 'Test Away', participants, 'scheduled', 'STATUS_SCHEDULED', md5(v_game), true, v_game,
               'linked', 'team_pair', true
          FROM public.sports_events WHERE id = (SELECT sports_event_id FROM public.kalshi_board_events
                                                 WHERE event_ticker = v_game)
        RETURNING id INTO v_sid;
        UPDATE public.kalshi_board_events SET sports_event_id = v_sid WHERE event_ticker = v_game;
        SELECT x->'tm' INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j->0->>0 IS DISTINCT FROM 'Hometown' OR v_j->1->>0 IS DISTINCT FROM 'Testville'
           OR (SELECT x->>'hs' FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game)
              IS DISTINCT FROM 'true' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a swapped link reads %', v_j;
        END IF;
        SELECT sports_event_id INTO v_sid FROM public.kalshi_board_events WHERE event_ticker = v_game;
        UPDATE public.kalshi_board_events
           SET sports_event_id = (SELECT id FROM public.sports_events WHERE espn_id = 'e2e-' || lower(substr(v_game, 9)))
         WHERE event_ticker = v_game;
        SELECT sports_event_id INTO v_sid FROM public.kalshi_board_events WHERE event_ticker = v_game;
        UPDATE public.sports_events SET participants = NULL WHERE id = v_sid;
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j ? 'tm' THEN RAISE EXCEPTION 'SELFTEST FAILED: teams with no participants: %', v_j->'tm'; END IF;
        UPDATE public.sports_events
           SET participants = '{"away": {"id": "9001", "name": "Test Away"}, "home": {"id": "9002", "name": "Test Home"}}'
         WHERE id = v_sid;
        SELECT x->'tm' INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        -- Before the worker stores a city, the full name stands in for it.
        IF v_j->0->>0 IS DISTINCT FROM 'Test Away' OR v_j->0->>1 IS NOT NULL OR v_j->0->>3 IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a team without a city reads %', v_j;
        END IF;
        -- ESPN's logo path folds to "<folder>/<file>"; anything else is no logo.
        UPDATE public.sports_events
           SET participants = '{"away": {"id": "8", "name": "A", "logo": "nba/500/scoreboard/det.png"},
                                "home": {"id": "254", "name": "H", "logo": "ncaa/500/254.png"}}'
         WHERE id = v_sid;
        SELECT x->'tm' INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j->0->>2 IS DISTINCT FROM 'nba/det' OR v_j->1->>2 IS DISTINCT FROM 'ncaa/254' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: logo keys read %', v_j;
        END IF;
        UPDATE public.sports_events
           SET participants = '{"away": {"id": "8", "name": "A", "logo": "../x.png"}, "home": {"id": "2", "name": "H"}}'
         WHERE id = v_sid;
        SELECT x->'tm' INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j->0->>2 IS NOT NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: a bad logo path became %', v_j; END IF;
        UPDATE public.kalshi_board_events SET sports_event_id = NULL WHERE event_ticker = v_game;
        SELECT x INTO v_j FROM public.kalshi_board_cards('Basketball', true) x WHERE x->>'g' = v_game;
        IF v_j ? 'tm' OR NOT v_j ? 'w' OR v_j->>'t' IS DISTINCT FROM 'Test Away vs Test Home' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unlinked game reads %', v_j;
        END IF;

        -- ------------------------------------------------ 4. test rows only with the flag; grants
        IF EXISTS (SELECT 1 FROM public.kalshi_board_cards('Basketball', false) x WHERE x->>'g' = v_game) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test card without p_tests';
        END IF;
        IF has_function_privilege('anon', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_board_cards(text, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '083 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '083_board_cards_mock';
-- (b) SELECT count(*), count(*) FILTER (WHERE x ? 'tm'), count(*) FILTER (WHERE x ? 'sp'),
--            octet_length(jsonb_agg(x)::text) FROM kalshi_board_cards('Basketball', false) x;
-- (c) has_function_privilege for anon / authenticated / valemont_readonly on kalshi_board_cards.
