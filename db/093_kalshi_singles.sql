-- db/093_kalshi_singles.sql
--
-- The Props tab's reader (owner, 2026-11-25; docs/props_tab.md). Read only: no
-- table, column, trigger or ledger row changes, and no existing function changes.
--
--   kalshi_singles(sport, kind, tier, q, sort, offset, limit, tests)  jsonb
--     Every binary single of one sport on the board, YES and NO sides, for games
--     not yet started, in one price tier, with fair p and the edge after the taker
--     fee. Each side is priced as leg_consensus_for prices it, but the arithmetic
--     is done inline (the event-normalized Kalshi mid, the 0.7 / 0.3 blend with an
--     outside line, kalshi_taker_fee's formula), so a page of 50 is answered
--     without pricing the board (Step 0: 100-160 ms on a local copy of the Nov 25
--     board; 32,930 sides equal to leg_consensus_for's p, edge and tier).
--
--     kind   'prop' | 'line' (every non-prop) | NULL (both). Applied AFTER the
--            normalization window, so an event's mids always sum over the whole event.
--     tier   by the side's ask: safer 0.60-0.85, mid [0.30, 0.60), lotto [0.05, 0.30),
--            low under 0.05 (owner, Nov 25). Over 0.85: no tier, never listed.
--     q      a case-insensitive search over the label, subject, stat and game title.
--     sort   'edge' (default): sides with an outside line first, then by edge after the
--            fee; on the kalshi tier that edge is the cost to trade, never a claim.
--            'mult': by the multiplier (1 / (ask + fee)), highest first.
--     The answer: {counts: {tier: n}, rows: [...]}; counts are of sides, never money.
--
-- The claim rule is unchanged and lives in leg_consensus_for alone; this reader
-- returns no claim flag. Test rows (E2E series) only with p_tests.
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

CREATE OR REPLACE FUNCTION public.kalshi_singles(p_sport text, p_kind text, p_tier text, p_q text,
                                                 p_sort text, p_offset int, p_limit int, p_tests boolean)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    -- t is read twice (the counts and the page), so Postgres materializes it: it
    -- carries only the ticker, the side and the numbers (a wide t spilled to disk on
    -- production, ~820 ms), and the words are joined back for the page's rows alone.
    WITH m AS (
        SELECT b.market_ticker, b.event_ticker, e.game_title, e.starts_at,
               b.market_kind, b.subject, b.stat, b.label, b.yes_bid, b.yes_ask,
               s.fee_multiplier AS mult, e.mutually_exclusive,
               (b.yes_bid + b.yes_ask) / 2 AS mid,
               sum((b.yes_bid + b.yes_ask) / 2) OVER w AS mid_sum,
               count(*) OVER w AS n_ev,
               count(b.yes_bid + b.yes_ask) OVER w AS n_quoted
          FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
          JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
         WHERE b.payout = 'binary' AND s.sport = p_sport AND (NOT b.is_test OR p_tests)
        WINDOW w AS (PARTITION BY b.event_ticker)
    ), k AS (
        -- leg_consensus_priced's normalization, unchanged.
        SELECT m.*, round(CASE WHEN coalesce(m.mutually_exclusive AND m.n_ev >= 2 AND m.n_quoted = m.n_ev
                                             AND m.mid_sum BETWEEN 0.95 AND 1.05, false)
                               THEN m.mid / m.mid_sum ELSE m.mid END, 6) AS pk
          FROM m
         WHERE m.starts_at > now()
           AND (CASE p_kind WHEN 'prop' THEN m.market_kind = 'prop'
                            WHEN 'line' THEN m.market_kind <> 'prop' ELSE true END)
           AND (p_q IS NULL OR concat_ws(' ', m.label, m.subject, m.stat, m.game_title) ILIKE '%' || p_q || '%')
    ), p AS (
        -- consensus_leg's blend, unchanged.
        SELECT k.market_ticker, k.starts_at, k.yes_bid, k.yes_ask, k.mult, c.tier AS ctier,
               round(CASE WHEN k.pk IS NULL THEN NULL
                          WHEN c.tier = 'books' THEN 0.7 * c.p_books + 0.3 * k.pk
                          WHEN c.tier = 'espn' THEN 0.7 * c.p_espn + 0.3 * k.pk
                          ELSE k.pk END, 6) AS pc
          FROM k
          LEFT JOIN public.leg_consensus c ON c.market_ticker = k.market_ticker
    ), t AS (
        -- Each side at its own ask, and its tier: all the counts need. The fee and
        -- the edge are worked out below for the asked tier only.
        SELECT p.market_ticker, p.starts_at, p.ctier, p.mult, x.side, x.ask, x.ps,
               CASE WHEN x.ask BETWEEN 0.60 AND 0.85 THEN 'safer'
                    WHEN x.ask >= 0.30 AND x.ask < 0.60 THEN 'mid'
                    WHEN x.ask >= 0.05 AND x.ask < 0.30 THEN 'lotto'
                    WHEN x.ask < 0.05 THEN 'low' END AS tier
          FROM p
         CROSS JOIN LATERAL (VALUES ('yes', p.yes_ask, p.pc), ('no', 1 - p.yes_bid, 1 - p.pc)) x (side, ask, ps)
         WHERE x.ask > 0 AND x.ask < 1 AND p.mult IS NOT NULL
    ), sd AS (
        -- kalshi_taker_fee's formula, unchanged.
        SELECT t.*, ceil(0.07 * t.mult * t.ask * (1 - t.ask) * 1000000) / 1000000 AS fee
          FROM t
         WHERE t.tier = p_tier
    ), ranked AS (
        -- The order, once: the page and the paging both read rn.
        SELECT sd.*, sd.ps - (sd.ask + sd.fee) AS edge, 1 / (sd.ask + sd.fee) AS mx,
               row_number() OVER (
                   ORDER BY CASE WHEN p_sort = 'mult' THEN 1 / (sd.ask + sd.fee) END DESC NULLS LAST,
                            (sd.ctier IS NOT NULL) DESC, sd.ps - (sd.ask + sd.fee) DESC NULLS LAST,
                            sd.starts_at, sd.market_ticker, sd.side) AS rn
          FROM sd
    ), pick AS (
        SELECT ranked.*, b.market_kind, b.label, b.subject, b.stat, b.line, b.team,
               e.game_title, e.game_ticker, e.sports_event_id, e.fetched_at, e.combo_eligible, e.combo_yes_only,
               b.yes_ask AS ya, 1 - b.yes_bid AS na, public.kalshi_board_period(e.series_ticker) AS per
          FROM ranked
          JOIN public.kalshi_board b ON b.market_ticker = ranked.market_ticker
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
         WHERE ranked.rn > greatest(coalesce(p_offset, 0), 0)
           AND ranked.rn <= greatest(coalesce(p_offset, 0), 0) + least(greatest(coalesce(p_limit, 50), 1), 200)
    )
    SELECT jsonb_build_object(
        'asof', extract(epoch FROM now())::bigint,
        'counts', coalesce((SELECT jsonb_object_agg(z.tier, z.n)
                              FROM (SELECT t.tier, count(*) AS n FROM t WHERE t.tier IS NOT NULL GROUP BY 1) z),
                           '{}'::jsonb),
        'rows', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                    'm', pick.market_ticker, 'side', pick.side, 'k', pick.market_kind,
                    'label', pick.label, 'who', pick.subject, 'stat', pick.stat, 'line', pick.line,
                    'team', pick.team, 'game', pick.game_title, 'gt', pick.game_ticker, 'g', pick.sports_event_id,
                    'per', pick.per, 'start', extract(epoch FROM pick.starts_at)::bigint,
                    'ask', pick.ask, 'fee', round(pick.fee, 6), 'p', pick.ps, 'edge', round(pick.edge, 4),
                    'mult', round(pick.mx, 3), 'tier', coalesce(pick.ctier, 'kalshi'),
                    'ya', pick.ya, 'na', pick.na,
                    'age', greatest(extract(epoch FROM now() - pick.fetched_at), 0)::int,
                    -- In a Kalshi combo: 'no' (the event is not combo-eligible), 'yes_only' (YES legs only).
                    'combo', CASE WHEN NOT coalesce(pick.combo_eligible, false) THEN 'no'
                                  WHEN pick.combo_yes_only THEN 'yes_only' END))
                    ORDER BY pick.rn)
                  FROM pick), '[]'::jsonb));
$$;

REVOKE ALL ON FUNCTION public.kalshi_singles(text, text, text, text, text, int, int, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.kalshi_singles(text, text, text, text, text, int, int, boolean)
    TO authenticated, valemont_readonly;

INSERT INTO migration_log (name) VALUES ('093_kalshi_singles')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== self-test
-- Run on paste, rolled back ('selftest-rollback'). Series KXSELFTEST093,
-- KXSELFTEST093PTS and KXSELFTEST093FG (no real series has them), and the test
-- series E2ESELFTEST093; sport 'Selftest093', so no real market is in scope. A
-- statement that calls a writing function stores its result first and reads back
-- in a later statement (db/062's rule).
--
-- The open game's sides, by ask (YES ask / NO ask = 1 - YES bid):
--   ML  (exclusive)       AAA .61/.63  yes .63 safer, no .39 mid
--                         BBB .35/.37  yes .37 mid,   no .65 safer   (books line)
--   EF  (exclusive, mids sum 0.90: never normalized)
--                         E   .44/.46  yes .46 mid,   no .56 mid
--                         F   .44/.46  yes .46 mid,   no .56 mid
--   PTS (not exclusive)   P10 .40/.42  yes .42 mid,   no .60 safer   (espn line)
--                         P20 .04/.05  yes .05 lotto, no .96 none
--                         P30 .02/.03  yes .03 low,   no .98 none
--   FG  (exclusive, a prop beside an 'other' market; mids 0.31 + 0.67 = 0.98)
--                         TP  .30/.32  yes .32 mid,   no .70 safer
--                         NONE .66/.68 yes .68 safer, no .34 mid
--   safer 5, mid 9, lotto 1, low 1. A started game (CCC) and a test row are never counted.
DO $$
DECLARE
    v_t0   timestamptz := now() - interval '1 minute';
    v_soon timestamptz := now() + interval '2 hours';
    v_gt   text := 'KXSELFTEST093GAME-26NOV25AAABBB';
    v_ml   text := 'KXSELFTEST093-26NOV25AAABBB';
    v_ef   text := 'KXSELFTEST093-26NOV25EEEFFF';
    v_pp   text := 'KXSELFTEST093PTS-26NOV25AAABBB';
    v_fg   text := 'KXSELFTEST093FG-26NOV25AAABBB';
    v_old  text := 'KXSELFTEST093-26NOV24CCCDDD';
    v_tst  text := 'E2ESELFTEST093-26NOV25AAABBB';
    v_all  jsonb := '{"safer": 5, "mid": 9, "lotto": 1, "low": 1}';
    v_n    int;
    v_j    jsonb;
    v_r    jsonb;
    v_bad  int;
BEGIN
    BEGIN
        v_n := public.kalshi_board_series_upsert((SELECT jsonb_agg(jsonb_build_object(
            'series_ticker', x, 'title', 'Selftest 093', 'sport', 'Selftest093', 'league', 'nba', 'category', 'Sports',
            'tags', '["Basketball"]'::jsonb, 'fee_type', 'quadratic', 'fee_multiplier', '1',
            'fee_regime', '{"fee_type": "quadratic"}'::jsonb, 'combo_eligible', false, 'collection_tickers', '[]'::jsonb))
            FROM unnest(ARRAY['KXSELFTEST093', 'KXSELFTEST093PTS', 'KXSELFTEST093FG']) x));
        v_n := public.kalshi_board_series_upsert('[{"series_ticker": "E2ESELFTEST093", "title": "Selftest 093 test",
            "sport": "Selftest093", "league": "nba", "category": "Sports", "tags": ["Basketball"],
            "fee_type": "quadratic", "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"},
            "combo_eligible": false, "collection_tickers": []}]'::jsonb, true);
        v_n := public.kalshi_board_events_upsert((SELECT jsonb_agg(jsonb_build_object(
            'event_ticker', z.e, 'series_ticker', z.sr, 'game_ticker', z.gt, 'game_title', z.ti,
            'milestone_start', z.st, 'mutually_exclusive', z.mx))
            FROM (VALUES (v_ml, 'KXSELFTEST093', v_gt, 'AAA vs BBB', v_soon, true),
                         (v_ef, 'KXSELFTEST093', v_gt, 'AAA vs BBB', v_soon, true),
                         (v_pp, 'KXSELFTEST093PTS', v_gt, 'AAA vs BBB', v_soon, false),
                         (v_fg, 'KXSELFTEST093FG', v_gt, 'AAA vs BBB', v_soon, true),
                         (v_old, 'KXSELFTEST093', 'KXSELFTEST093GAME-26NOV24CCCDDD', 'CCC vs DDD',
                          now() - interval '1 hour', false)) z (e, sr, gt, ti, st, mx)), v_t0);
        v_n := public.kalshi_board_events_upsert(jsonb_build_array(jsonb_build_object(
            'event_ticker', v_tst, 'series_ticker', 'E2ESELFTEST093', 'game_ticker', 'E2ESELFTEST093GAME-26NOV25AAABBB',
            'game_title', 'AAA vs BBB', 'milestone_start', v_soon, 'mutually_exclusive', false)), v_t0, true);
        v_n := public.kalshi_board_markets_upsert((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
            'market_ticker', z.mt, 'event_ticker', z.ev, 'market_kind', z.kd, 'subject', z.sb, 'stat', z.st,
            'line', z.ln, 'label', z.lb, 'yes_bid', z.yb, 'yes_ask', z.ya)))
            FROM (VALUES (v_ml || '-AAA', v_ml, 'game_line', 'AAA', NULL, NULL::numeric, 'AAA', 0.61, 0.63),
                         (v_ml || '-BBB', v_ml, 'game_line', 'BBB', NULL, NULL, 'BBB', 0.35, 0.37),
                         (v_ef || '-E', v_ef, 'game_line', 'E', NULL, NULL, 'E', 0.44, 0.46),
                         (v_ef || '-F', v_ef, 'game_line', 'F', NULL, NULL, 'F', 0.44, 0.46),
                         (v_pp || '-P1-10', v_pp, 'prop', 'Test Player', 'points', 9.5, 'Test Player: 10+', 0.40, 0.42),
                         (v_pp || '-P1-20', v_pp, 'prop', 'Test Player', 'points', 19.5, 'Test Player: 20+', 0.04, 0.05),
                         (v_pp || '-P1-30', v_pp, 'prop', 'Test Player', 'points', 29.5, 'Test Player: 30+', 0.02, 0.03),
                         (v_fg || '-TP', v_fg, 'prop', 'Test Player', NULL, NULL, 'Test Player', 0.30, 0.32),
                         (v_fg || '-NONE', v_fg, 'other', NULL, NULL, NULL, 'No goal', 0.66, 0.68),
                         (v_old || '-CCC', v_old, 'game_line', 'CCC', NULL, NULL, 'CCC', 0.50, 0.52))
                 z (mt, ev, kd, sb, st, ln, lb, yb, ya)), v_t0);
        IF v_n <> 10 THEN RAISE EXCEPTION 'SELFTEST FAILED: market insert wrote %', v_n; END IF;
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_tst || '-AAA', 'event_ticker', v_tst, 'market_kind', 'game_line', 'subject', 'AAA',
            'label', 'AAA', 'yes_bid', 0.61, 'yes_ask', 0.63)), v_t0, true);
        v_n := public.leg_consensus_upsert(jsonb_build_array(
            jsonb_build_object('market_ticker', v_ml || '-BBB', 'tier', 'books', 'n_books', 3, 'p_books', 0.30,
                               'p_books_mult', 0.30, 'p_books_lo', 0.29, 'p_books_hi', 0.31,
                               'inputs', '{"m": "power"}'::jsonb, 'source_at', now()),
            jsonb_build_object('market_ticker', v_pp || '-P1-10', 'tier', 'espn', 'n_books', 1, 'p_espn', 0.45,
                               'inputs', '{"m": "espn"}'::jsonb, 'source_at', now())), v_t0);

        -- ------------------------------------------------ 1. every side equals leg_consensus_for
        SELECT count(*) INTO v_bad
          FROM (SELECT r
                  FROM unnest(ARRAY['safer', 'mid', 'lotto', 'low']) tr
                 CROSS JOIN LATERAL jsonb_array_elements(
                       public.kalshi_singles('Selftest093', NULL, tr, NULL, 'edge', 0, 200, false)->'rows') r) s
          LEFT JOIN public.leg_consensus_for(ARRAY[v_gt]) e ON e.market_ticker = s.r->>'m'
         WHERE e.market_ticker IS NULL
            OR (s.r->>'p')::numeric IS DISTINCT FROM CASE s.r->>'side' WHEN 'yes' THEN e.p_consensus ELSE 1 - e.p_consensus END
            OR (s.r->>'edge')::numeric IS DISTINCT FROM round(CASE s.r->>'side' WHEN 'yes' THEN e.edge_yes ELSE e.edge_no END, 4)
            OR (s.r->>'fee')::numeric IS DISTINCT FROM CASE s.r->>'side' WHEN 'yes' THEN e.fee_yes ELSE e.fee_no END
            OR s.r->>'tier' IS DISTINCT FROM e.tier;
        IF v_bad <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: % sides differ from leg_consensus_for', v_bad; END IF;
        -- The fixture is priced as designed: the espn line blended, the books line, the 0.90 event not normalized.
        SELECT count(*) INTO v_n
          FROM public.leg_consensus_for(ARRAY[v_gt]) e
         WHERE (e.market_ticker = v_pp || '-P1-10' AND e.tier = 'espn' AND e.p_consensus = round(0.7 * 0.45 + 0.3 * 0.41, 6))
            OR (e.market_ticker = v_ml || '-BBB' AND e.tier = 'books')
            OR (e.market_ticker = v_ef || '-E' AND e.p_kalshi = 0.45 AND NOT e.normalized);
        IF v_n <> 3 THEN RAISE EXCEPTION 'SELFTEST FAILED: the fixture is not priced as designed (% of 3)', v_n; END IF;

        -- ------------------------------------------------ 2. tiers and counts; started games and test rows out
        v_j := public.kalshi_singles('Selftest093', NULL, 'safer', NULL, 'edge', 0, 50, false);
        IF v_j->'counts' IS DISTINCT FROM v_all THEN
            RAISE EXCEPTION 'SELFTEST FAILED: counts %', v_j->'counts';
        END IF;
        IF jsonb_array_length(v_j->'rows') <> 5 OR v_j->'rows'->0->>'m' <> v_ml || '-BBB'
           OR v_j->'rows'->0->>'side' <> 'no' OR v_j->'rows'->0->>'tier' <> 'books'
           OR v_j->'rows'->1->>'m' <> v_pp || '-P1-10' OR v_j->'rows'->1->>'tier' <> 'espn' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the outside sides are not first: %', v_j->'rows';
        END IF;
        -- Every outside side, then every kalshi side, each by edge, highest first.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'rows') WITH ORDINALITY a (x, i)
                     JOIN jsonb_array_elements(v_j->'rows') WITH ORDINALITY b (y, k) ON b.k = a.i + 1
                    WHERE (a.x->>'tier' = 'kalshi' AND b.y->>'tier' <> 'kalshi')
                       OR ((a.x->>'tier' = 'kalshi') = (b.y->>'tier' = 'kalshi')
                           AND (a.x->>'edge')::numeric < (b.y->>'edge')::numeric)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: not outside first, then by edge: %', v_j->'rows';
        END IF;
        v_j := public.kalshi_singles('Selftest093', NULL, 'mid', NULL, 'edge', 0, 50, true);
        IF (v_j->'counts'->>'mid')::int <> 10 OR (v_j->'counts'->>'safer')::int <> 6 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: with tests, counts %', v_j->'counts';
        END IF;

        -- ------------------------------------------------ 3. the multiplier sort, paging, the low tier
        v_j := public.kalshi_singles('Selftest093', NULL, 'mid', NULL, 'mult', 0, 50, false);
        IF jsonb_array_length(v_j->'rows') <> 9
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'rows') WITH ORDINALITY a (x, i)
                        JOIN jsonb_array_elements(v_j->'rows') WITH ORDINALITY b (y, k) ON b.k = a.i + 1
                       WHERE (a.x->>'mult')::numeric < (b.y->>'mult')::numeric) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: not by multiplier: %', v_j->'rows';
        END IF;
        v_r := public.kalshi_singles('Selftest093', NULL, 'mid', NULL, 'mult', 3, 2, false)->'rows';
        IF jsonb_array_length(v_r) <> 2 OR v_r->0 IS DISTINCT FROM v_j->'rows'->3 OR v_r->1 IS DISTINCT FROM v_j->'rows'->4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: paging %', v_r;
        END IF;
        v_r := public.kalshi_singles('Selftest093', NULL, 'low', NULL, 'edge', 0, 50, false)->'rows';
        IF jsonb_array_length(v_r) <> 1 OR v_r->0->>'m' <> v_pp || '-P1-30' OR v_r->0->>'side' <> 'yes' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the low tier %', v_r;
        END IF;

        -- ------------------------------------------------ 4. kind after the window, and the search
        v_j := public.kalshi_singles('Selftest093', 'prop', 'mid', NULL, 'edge', 0, 50, false);
        SELECT r INTO v_r FROM jsonb_array_elements(v_j->'rows') r WHERE r->>'m' = v_pp || '-P1-10';
        IF jsonb_array_length(v_j->'rows') <> 2 OR v_r->>'who' IS DISTINCT FROM 'Test Player'
           OR v_r->>'stat' IS DISTINCT FROM 'points' OR (v_r->>'line')::numeric IS DISTINCT FROM 9.5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: props %', v_j;
        END IF;
        -- The first-goal prop stays normalized over its whole event, though 'No goal' is not a prop.
        SELECT r INTO v_r FROM jsonb_array_elements(v_j->'rows') r WHERE r->>'m' = v_fg || '-TP';
        IF (v_r->>'p')::numeric IS DISTINCT FROM round(0.31 / 0.98, 6) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the prop was not normalized over its event: %', v_r;
        END IF;
        v_j := public.kalshi_singles('Selftest093', 'line', 'safer', NULL, 'edge', 0, 50, false);
        IF jsonb_array_length(v_j->'rows') <> 3
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'rows') r WHERE r->>'k' = 'prop') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: lines %', v_j;
        END IF;
        -- Both sides' prices ride on every row (the slip flips a leg in place).
        SELECT r INTO v_r FROM jsonb_array_elements(v_j->'rows') r WHERE r->>'m' = v_ml || '-AAA';
        IF (v_r->>'ya')::numeric IS DISTINCT FROM 0.63 OR (v_r->>'na')::numeric IS DISTINCT FROM 0.39
           OR v_r->>'combo' IS DISTINCT FROM 'no' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a row''s two prices or combo flag: %', v_r;
        END IF;
        v_j := public.kalshi_singles('Selftest093', NULL, 'mid', 'test player', 'edge', 0, 50, false);
        IF jsonb_array_length(v_j->'rows') <> 2
           OR v_j->'counts' IS DISTINCT FROM '{"safer": 2, "mid": 2, "lotto": 1, "low": 1}'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: search %', v_j;
        END IF;

        -- ------------------------------------------------ 5. access
        IF has_function_privilege('anon', 'public.kalshi_singles(text, text, text, text, text, int, int, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.kalshi_singles(text, text, text, text, text, int, int, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.kalshi_singles(text, text, text, text, text, int, int, boolean)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '093 self-test passed (rolled back)';
END
$$;
