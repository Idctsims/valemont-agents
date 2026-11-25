-- db/095_parlay_leg_game_ticker.sql
--
-- The Parlays page's live count for games with no ESPN link (owner, Nov 25):
-- most soccer, from Kalshi's own live data (/live_data, keyed by the game's
-- milestone, looked up by the game's event ticker). The page needs each such
-- leg's Kalshi game ticker; parlay_day did not send it.
--
--   parlay_day(day, tests)  db/091's text, plus `gk` on a leg whose game has no
--     Game Day link (the same legs that carry `gt`, the game's title). A linked
--     leg is unchanged (it has `g`). Nothing else changes: same signature,
--     columns, rows and grants.
--
-- Read only. Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls
-- itself back.

CREATE OR REPLACE FUNCTION public.parlay_day(p_day date, p_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH bounds AS (
        SELECT (p_day::timestamp AT TIME ZONE 'America/Chicago') AS lo,
               ((p_day + 1)::timestamp AT TIME ZONE 'America/Chicago') AS hi
    ), ag AS (
        SELECT a.id, a.slug FROM public.agents a WHERE public.parlay_agent_ok(a.slug, a.is_test, p_tests)
    ), tk AS (
        SELECT c.id, c.committed_at, c.payload AS p, r.outcome, r.pnl, r.detail AS rd
          FROM public.commitments c
          JOIN ag ON ag.id = c.agent_id
          CROSS JOIN bounds
          LEFT JOIN public.resolutions r ON r.commitment_id = c.id
         WHERE c.mode = 'paper' AND c.committed_at >= bounds.lo AND c.committed_at < bounds.hi
    ), lg AS (
        SELECT tk.id, x.i, x.l,
               tk.p->'leg_reasons'->>((x.i - 1)::int) AS reason,
               d.market_kind, d.subject, d.stat, d.line, d.team, d.period, d.game_title,
               coalesce(d.game_ticker, x.l->>'game_ticker') AS gt,
               (SELECT (y->>'side_value')::numeric FROM jsonb_array_elements(tk.rd->'legs') y
                 WHERE y->>'ticker' = x.l->>'market_ticker' LIMIT 1) AS res,
               (tk.p->'kickoffs'->>(x.l->>'market_ticker'))::timestamptz AS kickoff
          FROM tk
          CROSS JOIN LATERAL jsonb_array_elements(tk.p->'legs') WITH ORDINALITY AS x (l, i)
          LEFT JOIN public.parlay_leg_display d ON d.market_ticker = x.l->>'market_ticker'
    ), gl AS (
        -- The leg's game on Game Day: by the permanent link on sports_events, else
        -- the board's (while it holds the game).
        SELECT g.gt, (
            SELECT se.id FROM (
                SELECT s1.id FROM public.sports_events s1
                 WHERE s1.kalshi_event_ticker = g.gt AND (NOT s1.is_test OR p_tests)
                UNION ALL
                SELECT s2.id FROM public.kalshi_board_events e
                  JOIN public.sports_events s2 ON s2.id = e.sports_event_id
                 WHERE e.event_ticker = g.gt AND (NOT s2.is_test OR p_tests)
            ) se LIMIT 1) AS sid
          FROM (SELECT DISTINCT gt FROM lg WHERE gt IS NOT NULL) g
    ), gk AS (
        -- Each linked game's two sides as Kalshi codes them (ESPN's code, or its alias,
        -- db/066), so a leg's team says home or away.
        SELECT se.id AS sid,
               coalesce(ah.kalshi_code, se.participants->'home'->>'abbr') AS hk,
               coalesce(aa.kalshi_code, se.participants->'away'->>'abbr') AS ak
          FROM public.sports_events se
          LEFT JOIN public.sports_team_aliases ah
                 ON ah.league = se.league AND ah.espn_code = se.participants->'home'->>'abbr'
          LEFT JOIN public.sports_team_aliases aa
                 ON aa.league = se.league AND aa.espn_code = se.participants->'away'->>'abbr'
         WHERE se.id IN (SELECT sid FROM gl WHERE sid IS NOT NULL)
    ), legs_j AS (
        SELECT lg.id,
               jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'side', lg.l->>'side',
                   'label', coalesce(substring(lg.reason FROM '^(?:YES|NO) (.+): ask [0-9]'), lg.l->>'market_ticker'),
                   'kind', lg.market_kind, 'who', lg.subject, 'stat', lg.stat, 'line', lg.line,
                   'team', lg.team, 'per', lg.period,
                   'gt', CASE WHEN gl.sid IS NULL THEN lg.game_title END,
                   -- db/095: an unlinked leg's Kalshi game ticker, for Kalshi's live data (the page).
                   'gk', CASE WHEN gl.sid IS NULL THEN lg.gt END,
                   'g', gl.sid,
                   'sd', CASE WHEN lg.team IS NULL THEN NULL WHEN lg.team = gk.hk THEN 'home'
                              WHEN lg.team = gk.ak THEN 'away' END,
                   'start', extract(epoch FROM lg.kickoff)::bigint,
                   'res', lg.res)) ORDER BY lg.i) AS legs
          FROM lg LEFT JOIN gl ON gl.gt = lg.gt LEFT JOIN gk ON gk.sid = gl.sid
         GROUP BY lg.id
    ), games AS (
        SELECT jsonb_object_agg(se.id::text, jsonb_strip_nulls(jsonb_build_object(
                   'lg', se.league, 'st', se.status, 'per', se.period, 'clk', se.clock,
                   'sc', se.score, 'start', extract(epoch FROM se.starts_at)::bigint,
                   'home', public.parlay_team(se.participants->'home'),
                   'away', public.parlay_team(se.participants->'away')))) AS j
          FROM public.sports_events se
         WHERE se.id IN (SELECT sid FROM gl WHERE sid IS NOT NULL)
    ), cards AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'id', tk.id,
                   'tier', tk.p->>'tier', 'scope', tk.p->>'scope', 'sport', tk.p->>'sport',
                   'rv', coalesce(tk.p->>'rule_version', 'P2'),
                   'slot', public.parlay_slot_of(tk.committed_at),
                   'at', extract(epoch FROM tk.committed_at)::bigint,
                   'stake', round((tk.p->>'capital_at_risk')::numeric, 2),
                   'pays', coalesce((tk.p->>'contracts')::numeric, 100),
                   'outcome', tk.outcome, 'pnl', round(tk.pnl, 4),
                   'paid', round((tk.rd->>'proceeds')::numeric, 2),
                   'fill', tk.p->>'fill_note', 'combo', tk.p->>'combo_status',
                   'legs', legs_j.legs)) ORDER BY tk.committed_at, tk.id) AS j
          FROM tk LEFT JOIN legs_j ON legs_j.id = tk.id
    ), results AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'slot', CASE WHEN e.detail->>'slot' LIKE '% am' THEN '10:45'
                                WHEN e.detail->>'slot' LIKE '% pm' THEN '16:45'
                                ELSE public.parlay_slot_of(e.at) END,
                   'tier', coalesce(e.detail->>'tier', replace(ag.slug, 'parlay_', '')),
                   'scope', e.detail->>'scope', 'sport', e.detail->>'sport',
                   'rule', CASE WHEN e.detail ? 'refused' THEN 'refused at commit'
                                ELSE public.parlay_rule_name(e.detail->>'binding') END,
                   'raw', CASE WHEN e.detail ? 'refused' THEN e.detail->>'refused'
                               WHEN e.detail->>'binding' NOT LIKE 'rules 1-4: universe%'
                               THEN e.detail->>'binding' END,
                   'rv', e.detail->>'rule_version')) ORDER BY e.id) AS j
          FROM public.events e
          JOIN ag ON ag.id = e.agent_id
          CROSS JOIN bounds
         WHERE e.at >= bounds.lo AND e.at < bounds.hi AND e.kind = 'gate_evaluated'
           AND (e.detail ? 'binding' OR e.detail ? 'refused')
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'day', p_day,
        'asof', extract(epoch FROM now())::bigint,
        'enabled', EXISTS (SELECT 1 FROM public.agents a
                            WHERE a.slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto') AND a.enabled),
        'prev', (SELECT max((c.committed_at AT TIME ZONE 'America/Chicago')::date)
                   FROM public.commitments c JOIN ag ON ag.id = c.agent_id CROSS JOIN bounds
                  WHERE c.committed_at < bounds.lo),
        'next', (SELECT min((c.committed_at AT TIME ZONE 'America/Chicago')::date)
                   FROM public.commitments c JOIN ag ON ag.id = c.agent_id CROSS JOIN bounds
                  WHERE c.committed_at >= bounds.hi),
        'tickets', coalesce((SELECT j FROM cards), '[]'::jsonb),
        'games', coalesce((SELECT j FROM games), '{}'::jsonb),
        'results', coalesce((SELECT j FROM results), '[]'::jsonb)));
$$;

-- db/091's grants, restated (CREATE OR REPLACE keeps them; stated so the file stands alone).
REVOKE ALL ON FUNCTION public.parlay_day(date, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.parlay_day(date, boolean) TO authenticated, valemont_readonly;

INSERT INTO migration_log (name) VALUES ('095_parlay_leg_game_ticker')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== self-test
-- Run on paste, rolled back ('selftest-rollback'). A test ticket for the
-- _e2e_parlays agent with two legs: one on a game Game Day links (the e2e board,
-- db/083's add_test_kalshi_board) and one on a game it does not. The owner's
-- session is simulated as in db/091. A statement that calls a writing function
-- stores its result first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker text := 'selftest-095-' || md5(random()::text);
    v_game   text;
    v_agent  smallint := (SELECT id FROM public.agents WHERE slug = '_e2e_parlays');
    v_run    bigint;
    v_c      bigint;
    v_free   text := 'KXSELFTEST095GAME-26NOV25AAABBB';
    v_today  date := (now() AT TIME ZONE 'America/Chicago')::date;
    v_j      jsonb;
    v_k      jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL OR v_agent IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no owner or no _e2e_parlays agent';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');

        INSERT INTO public.runs (agent_id, status) VALUES (v_agent, 'ok') RETURNING id INTO v_run;
        INSERT INTO public.commitments (agent_id, run_id, kind, thesis, payload, resolves_after, closes_at)
        VALUES (v_agent, v_run, 'event_contract', 'selftest 095',
                jsonb_build_object(
                    'tier', 'safer', 'scope', 'sport', 'sport', 'Soccer', 'rule_version', 'P2.1',
                    'capital_at_risk', '40.00', 'contracts', 100,
                    'leg_reasons', jsonb_build_array(
                        'YES Test Home wins (Test Away vs Test Home): ask 63¢, mid 0.620',
                        'NO Tie (AAA vs BBB): ask 70¢, mid 0.700'),
                    'legs', jsonb_build_array(
                        jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes', 'event_ticker', v_game,
                                           'game_ticker', v_game, 'ask', '0.63'),
                        jsonb_build_object('market_ticker', v_free || '-TIE', 'side', 'no', 'event_ticker', v_free,
                                           'game_ticker', v_free, 'ask', '0.70')),
                    'kickoffs', jsonb_build_object(v_game || '-HOM', now() + interval '2 hours',
                                                   v_free || '-TIE', now() + interval '2 hours')),
                now() + interval '3 hours', now() + interval '2 hours')
        RETURNING id INTO v_c;

        v_j := public.parlay_day(v_today, true);
        SELECT x INTO v_k FROM jsonb_array_elements(v_j->'tickets') x WHERE (x->>'id')::bigint = v_c;
        IF v_k IS NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: the test ticket is not on its day'; END IF;
        -- The linked leg: its Game Day id, no game ticker, no title.
        IF NOT (v_k->'legs'->0 ? 'g') OR v_k->'legs'->0 ? 'gk' OR v_k->'legs'->0 ? 'gt' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the linked leg reads %', v_k->'legs'->0;
        END IF;
        -- The unlinked leg: its game ticker, and no Game Day id.
        IF v_k->'legs'->1->>'gk' IS DISTINCT FROM v_free OR v_k->'legs'->1 ? 'g' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the unlinked leg reads %', v_k->'legs'->1;
        END IF;
        -- db/091's fields are unchanged.
        IF v_k->>'tier' <> 'safer' OR (v_k->>'stake')::numeric <> 40 OR jsonb_array_length(v_k->'legs') <> 2
           OR v_k->'legs'->1->>'side' <> 'no' OR v_k->'legs'->1->>'label' <> 'Tie (AAA vs BBB)' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the card reads %', v_k;
        END IF;
        -- Without p_tests: none of it.
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(public.parlay_day(v_today, false)->'tickets') x
                    WHERE (x->>'id')::bigint = v_c) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test ticket without p_tests';
        END IF;
        IF has_function_privilege('anon', 'public.parlay_day(date, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.parlay_day(date, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.parlay_day(date, boolean)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '095 self-test passed (rolled back)';
END
$$;
