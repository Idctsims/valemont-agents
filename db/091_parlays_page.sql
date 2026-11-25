-- db/091_parlays_page.sql
--
-- Chat 8 Phase 4.5: the Parlays page, the Record, the brief line and Wags
-- (docs/parlays_page.md; owner, 2026-11-24). Nothing here changes a rule, a
-- price, a payload, a score or a ledger row. Everything reads the parlay
-- agents' commitments as committed.
--
--   parlay_leg_display  APP TABLE, write-once: one row per parlay leg's
--     market, copied from the board (player, stat, line, team, period, game)
--     while the board still holds it (it prunes 6 h after a game starts), so
--     a settled ticket still names its legs and the page can count them live.
--     Written only by parlay_leg_display_capture() (the worker's
--     parlay_legs_capture job, every 15 minutes, and once below).
--   parlay_hit_pushes   APPEND-ONLY, TRUNCATE-guarded: one row per ticket
--     pushed as a hit, so a hit is pushed once (the worker writes it).
--
-- Readers (SECURITY INVOKER; the owner reads the ledger by db/020's policies):
--   parlay_day(day, tests)       the Chicago day's tickets as cards, their
--                                games, and the slot results (empty, refused)
--   parlay_ticket(id, tests)     one ticket's Details, the selection log as recorded
--   parlay_record(tests)         per agent x cohort (rule version) x scope, and by
--                                sport: never a total across tiers or cohorts
--   ctx_parlays()                Wags (provider `parlays`, not a brief section)
--   ctx_parlays_brief()          the brief (provider `parlays_brief`), lines worded here
--
-- Test rows: the `_e2e_parlays` agent (is_test), whose tickets are written by
-- the worker's scripts.seed_e2e_parlays through core/ledger.py. A reader shows
-- them only with p_tests (the web passes it for a verified e2e request).
--
-- Paste-by-hand: no BEGIN/COMMIT; the self-test at the end rolls itself back.

-- ===================================================================== 1. helpers

CREATE OR REPLACE FUNCTION public.parlay_agent_ok(p_slug text, p_is_test boolean, p_tests boolean)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT (p_slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto') AND NOT p_is_test)
        OR (p_tests AND p_slug = '_e2e_parlays' AND p_is_test);
$$;

-- An empty slot's binding rule, as recorded (consensus/parlay_ticket.py), in a
-- few plain words. The raw text is always shown beside it.
CREATE OR REPLACE FUNCTION public.parlay_rule_name(p_binding text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT CASE
        WHEN p_binding IS NULL THEN NULL
        WHEN p_binding LIKE 'rules 1-4: universe%' THEN 'no eligible market'
        WHEN p_binding ~ '^rules 1-4: [0-9]+ candidates' THEN 'too few candidates'
        WHEN p_binding LIKE 'rules 1-4: ask_band%' THEN 'ask band'
        WHEN p_binding LIKE 'rules 1-4: fresh%' OR p_binding = 'fresh' THEN 'no fresh prices'
        WHEN p_binding LIKE 'rules 1-4: %' THEN replace(split_part(p_binding, ': ', 2), '_', ' ')
        WHEN p_binding LIKE 'window%' THEN 'price window'
        ELSE split_part(p_binding, ':', 1)
    END;
$$;

-- The slot a commitment or a result belongs to: the stamped times are 10:45
-- and 16:45 Chicago (preregistration §2).
CREATE OR REPLACE FUNCTION public.parlay_slot_of(p_at timestamptz)
RETURNS text
LANGUAGE sql
STABLE
SET search_path = ''
AS $$
    SELECT CASE WHEN extract(hour FROM p_at AT TIME ZONE 'America/Chicago') < 13 THEN '10:45' ELSE '16:45' END;
$$;

-- A side of a linked ESPN game as the page draws it: the short name, city,
-- nickname, logo key and colour (db/083's fields), nothing else.
CREATE OR REPLACE FUNCTION public.parlay_team(p_team jsonb)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT CASE WHEN p_team IS NULL OR jsonb_typeof(p_team) <> 'object' THEN NULL
                ELSE jsonb_strip_nulls(jsonb_build_object(
                         'id', p_team->>'id',
                         'abbr', p_team->>'abbr', 'loc', p_team->>'loc', 'nick', p_team->>'nick',
                         'name', CASE WHEN p_team ? 'loc' THEN NULL ELSE p_team->>'name' END,
                         'logo', p_team->>'logo', 'color', p_team->>'color')) END;
$$;

-- ===================================================================== 2. parlay_leg_display

CREATE TABLE IF NOT EXISTS parlay_leg_display (
    id             BIGSERIAL    PRIMARY KEY,
    market_ticker  TEXT         NOT NULL UNIQUE CHECK (char_length(market_ticker) BETWEEN 1 AND 160),
    series_ticker  TEXT         NOT NULL,
    event_ticker   TEXT         NOT NULL,
    game_ticker    TEXT,
    market_kind    TEXT         NOT NULL,
    subject        TEXT,
    stat           TEXT,
    line           NUMERIC,
    team           TEXT,
    period         TEXT,
    label          TEXT,
    game_title     TEXT,
    starts_at      TIMESTAMPTZ,
    is_test        BOOLEAN      NOT NULL DEFAULT false,
    captured_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS parlay_leg_display_immutable ON parlay_leg_display;
CREATE TRIGGER parlay_leg_display_immutable
    BEFORE UPDATE OR DELETE ON parlay_leg_display
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS parlay_leg_display_no_truncate ON parlay_leg_display;
CREATE TRIGGER parlay_leg_display_no_truncate
    BEFORE TRUNCATE ON parlay_leg_display
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

ALTER TABLE parlay_leg_display ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON parlay_leg_display FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE parlay_leg_display_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON parlay_leg_display TO authenticated, valemont_readonly;
DROP POLICY IF EXISTS parlay_leg_display_owner_read ON parlay_leg_display;
CREATE POLICY parlay_leg_display_owner_read ON parlay_leg_display FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

-- Copies every recent parlay leg's market from the board, once. Returns the
-- rows written. Worker only (no API role runs it).
CREATE OR REPLACE FUNCTION public.parlay_leg_display_capture(p_since interval DEFAULT interval '3 days')
RETURNS integer
LANGUAGE sql
VOLATILE
SET search_path = ''
AS $$
    WITH want AS (
        SELECT DISTINCT l.subject AS market_ticker
          FROM public.legs l
          JOIN public.commitments c ON c.id = l.commitment_id
          JOIN public.agents a ON a.id = c.agent_id
         WHERE a.slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto', '_e2e_parlays')
           AND c.committed_at > now() - p_since
    ), ins AS (
        INSERT INTO public.parlay_leg_display (market_ticker, series_ticker, event_ticker, game_ticker, market_kind,
                                               subject, stat, line, team, period, label, game_title, starts_at, is_test)
        SELECT b.market_ticker, e.series_ticker, b.event_ticker, e.game_ticker, b.market_kind,
               b.subject, b.stat, b.line, b.team, public.kalshi_board_period(e.series_ticker), b.label,
               e.game_title, e.starts_at, b.is_test
          FROM want w
          JOIN public.kalshi_board b ON b.market_ticker = w.market_ticker
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
         WHERE NOT EXISTS (SELECT 1 FROM public.parlay_leg_display d WHERE d.market_ticker = w.market_ticker)
        ON CONFLICT (market_ticker) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::int FROM ins;
$$;
REVOKE ALL ON FUNCTION public.parlay_leg_display_capture(interval) FROM PUBLIC, anon, authenticated;

-- ===================================================================== 3. parlay_hit_pushes

CREATE TABLE IF NOT EXISTS parlay_hit_pushes (
    id               BIGSERIAL    PRIMARY KEY,
    commitment_id    BIGINT       NOT NULL UNIQUE REFERENCES commitments (id),
    notification_id  BIGINT,
    is_test          BOOLEAN      NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS parlay_hit_pushes_immutable ON parlay_hit_pushes;
CREATE TRIGGER parlay_hit_pushes_immutable
    BEFORE UPDATE OR DELETE ON parlay_hit_pushes
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS parlay_hit_pushes_no_truncate ON parlay_hit_pushes;
CREATE TRIGGER parlay_hit_pushes_no_truncate
    BEFORE TRUNCATE ON parlay_hit_pushes
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

ALTER TABLE parlay_hit_pushes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON parlay_hit_pushes FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE parlay_hit_pushes_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON parlay_hit_pushes TO authenticated, valemont_readonly;
DROP POLICY IF EXISTS parlay_hit_pushes_owner_read ON parlay_hit_pushes;
CREATE POLICY parlay_hit_pushes_owner_read ON parlay_hit_pushes FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

-- ===================================================================== 4. the e2e agent

INSERT INTO agents (slug, display_name, domain, enabled, is_test)
VALUES ('_e2e_parlays', 'Parlays (e2e fixtures)', 'kalshi', false, true)
ON CONFLICT (slug) DO NOTHING;

-- ===================================================================== 5. parlay_day

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

-- ===================================================================== 6. parlay_ticket

CREATE OR REPLACE FUNCTION public.parlay_close_ages(p_detail jsonb)
RETURNS numeric[]
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    -- A close's leg ages in minutes (kickoff minus the candle's end), median
    -- and maximum: the recorded fields when present (P2.1, and P2 from Nov 24),
    -- else read from the legs' frozen kickoff and candle_end. Nothing is re-scored.
    SELECT CASE
        WHEN p_detail ? 'age_min_median' THEN
            ARRAY[(p_detail->>'age_min_median')::numeric, (p_detail->>'age_min_max')::numeric]
        ELSE (SELECT ARRAY[round(percentile_cont(0.5) WITHIN GROUP (ORDER BY g)::numeric, 1),
                           round(max(g)::numeric, 1)]
                FROM (SELECT extract(epoch FROM ((x->>'kickoff')::timestamptz
                                                 - (x->>'candle_end')::timestamptz)) / 60 AS g
                        FROM jsonb_array_elements(coalesce(p_detail->'legs', '[]'::jsonb)) x
                       WHERE x ? 'kickoff' AND x ? 'candle_end') q
               HAVING count(*) > 0)
    END;
$$;

CREATE OR REPLACE FUNCTION public.parlay_ticket(p_id bigint, p_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'id', c.id,
        'tier', c.payload->>'tier', 'scope', c.payload->>'scope', 'sport', c.payload->>'sport',
        'rv', coalesce(c.payload->>'rule_version', 'P2'),
        'cohort', CASE coalesce(c.payload->>'rule_version', 'P2') WHEN 'P2' THEN 1 WHEN 'P2.1' THEN 2 END,
        'search', c.payload->>'search',
        'slot', public.parlay_slot_of(c.committed_at),
        'at', extract(epoch FROM c.committed_at)::bigint,
        'closes', extract(epoch FROM c.closes_at)::bigint,
        'mode', c.mode,
        'stake', round((c.payload->>'capital_at_risk')::numeric, 2),
        'pays', coalesce((c.payload->>'contracts')::numeric, 100),
        'outcome', r.outcome, 'pnl', round(r.pnl, 4), 'paid', round((r.detail->>'proceeds')::numeric, 2),
        'scored', round((c.payload->>'scored_price')::numeric, 6),
        'fee', (c.payload->>'taker_fee')::numeric,
        'maker', round((c.payload->>'maker_fee_assumed')::numeric, 6),
        'fair', round((c.payload->>'fair_p')::numeric, 6),
        'fair_se', round((c.payload->>'fair_se')::numeric, 6),
        'naive', round((c.payload->>'naive_p')::numeric, 6),
        'rel', round((c.payload->>'rel_cost_over_fair')::numeric, 6),
        'pairs', (c.payload->>'pairs')::int, 'measured', (c.payload->>'measured_pairs')::int,
        'prior', (c.payload->>'prior_pairs')::int,
        'edge_legs', jsonb_array_length(coalesce(c.payload->'edge_legs', '[]'::jsonb)),
        'combo', c.payload->>'combo_status', 'combo_reason', c.payload->>'combo_reason',
        'fill', c.payload->>'fill_note',
        'eq', c.payload->>'expected_quote_note',
        'rho', c.payload->>'rho_version',
        -- Each leg's market and side, for "Copy legs to the slip" (the owner builds it on Kalshi by hand).
        'legs', (SELECT jsonb_agg(jsonb_build_object(
                         'm', x.l->>'market_ticker', 'side', x.l->>'side', 'e', x.l->>'event_ticker',
                         'label', substring(c.payload->'leg_reasons'->>((x.i - 1)::int) FROM '^(?:YES|NO) (.+): ask [0-9]'))
                         ORDER BY x.i)
                   FROM jsonb_array_elements(c.payload->'legs') WITH ORDINALITY AS x (l, i)),
        'reasons', c.payload->'leg_reasons',
        'close', CASE WHEN s.id IS NULL THEN NULL ELSE jsonb_strip_nulls(jsonb_build_object(
                     'status', s.status, 'clv', s.clv, 'clv_pct', s.clv_pct, 'reason', s.reason,
                     'age_med', (public.parlay_close_ages(s.detail))[1],
                     'age_max', (public.parlay_close_ages(s.detail))[2],
                     'label', coalesce(s.detail->>'label', 'CLV (proxy)'))) END))
      FROM public.commitments c
      JOIN public.agents a ON a.id = c.agent_id
      LEFT JOIN public.resolutions r ON r.commitment_id = c.id
      LEFT JOIN public.closing_snapshots s ON s.commitment_id = c.id
     WHERE c.id = p_id AND public.parlay_agent_ok(a.slug, a.is_test, p_tests);
$$;

-- ===================================================================== 7. parlay_record

CREATE OR REPLACE FUNCTION public.parlay_record(p_tests boolean DEFAULT false)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH ag AS (
        SELECT a.id, a.slug FROM public.agents a WHERE public.parlay_agent_ok(a.slug, a.is_test, p_tests)
    ), t AS (
        SELECT c.id,
               c.payload->>'tier' AS tier,
               coalesce(c.payload->>'scope', 'sport') AS scope,
               coalesce(c.payload->>'sport', '') AS sport,
               coalesce(c.payload->>'rule_version', 'P2') AS rv,
               (c.committed_at AT TIME ZONE 'America/Chicago')::date AS d,
               (c.payload->>'capital_at_risk')::numeric AS stake,
               r.outcome, r.pnl, (r.detail->>'proceeds')::numeric AS paid,
               s.status AS cs, s.clv, s.clv_pct, s.detail AS sd
          FROM public.commitments c
          JOIN ag ON ag.id = c.agent_id
          LEFT JOIN public.resolutions r ON r.commitment_id = c.id
          LEFT JOIN public.closing_snapshots s ON s.commitment_id = c.id
         WHERE c.mode = 'paper'
    ), u AS (  -- each ticket once for its agent (k = '') and once for its sport
        SELECT t.*, '' AS k FROM t
        UNION ALL
        SELECT t.*, t.sport FROM t WHERE t.scope = 'sport'
    ), m AS (
        SELECT tier, scope, rv, k, avg(pnl) AS mr, count(pnl) AS n
          FROM u WHERE pnl IS NOT NULL GROUP BY 1, 2, 3, 4
    ), daily AS (  -- clustered by slate day: each day's sum of residuals
        SELECT u.tier, u.scope, u.rv, u.k, u.d, sum(u.pnl - m.mr) AS e
          FROM u JOIN m USING (tier, scope, rv, k)
         WHERE u.pnl IS NOT NULL
         GROUP BY 1, 2, 3, 4, 5
    ), se AS (
        SELECT daily.tier, daily.scope, daily.rv, daily.k, count(*) AS g,
               CASE WHEN count(*) >= 2
                    THEN sqrt(sum(daily.e * daily.e) * count(*) / (count(*) - 1)) / max(m.n) END AS se
          FROM daily JOIN m USING (tier, scope, rv, k)
         GROUP BY 1, 2, 3, 4
    ), ages AS (
        SELECT u.tier, u.scope, u.rv, u.k,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q.g)::numeric, 1) AS med,
               round(max(q.g)::numeric, 1) AS mx
          FROM u
          CROSS JOIN LATERAL (
              SELECT (u.sd->>'age_min_median')::numeric AS g WHERE u.sd ? 'age_min_median'
              UNION ALL
              SELECT extract(epoch FROM ((x->>'kickoff')::timestamptz - (x->>'candle_end')::timestamptz)) / 60
                FROM jsonb_array_elements(coalesce(u.sd->'legs', '[]'::jsonb)) x
               WHERE NOT (u.sd ? 'age_min_median') AND x ? 'kickoff' AND x ? 'candle_end'
          ) q
         WHERE u.cs = 'captured'
         GROUP BY 1, 2, 3, 4
    ), maxage AS (
        SELECT u.tier, u.scope, u.rv, u.k, max(coalesce((u.sd->>'age_min_max')::numeric,
                   (public.parlay_close_ages(u.sd))[2])) AS mx
          FROM u WHERE u.cs = 'captured' GROUP BY 1, 2, 3, 4
    ), agg AS (
        SELECT u.tier, u.scope, u.rv, u.k,
               count(*) AS tickets,
               count(*) FILTER (WHERE u.outcome IS NULL) AS open,
               count(u.outcome) AS settled,
               count(*) FILTER (WHERE u.outcome = 'hit') AS hit,
               count(*) FILTER (WHERE u.outcome = 'partial') AS partial,
               round(avg(u.pnl), 4) AS r_mean,
               round(sum(u.stake) FILTER (WHERE u.outcome IS NOT NULL), 2) AS staked,
               round(sum(u.paid) FILTER (WHERE u.outcome IS NOT NULL), 2) AS paid,
               count(*) FILTER (WHERE u.cs = 'captured') AS clv_n,
               round(avg(u.clv) FILTER (WHERE u.cs = 'captured'), 4) AS clv_mean,
               round(avg(u.clv_pct) FILTER (WHERE u.cs = 'captured'), 4) AS clv_pct_mean,
               count(*) FILTER (WHERE u.cs = 'missed') AS missed
          FROM u GROUP BY 1, 2, 3, 4
    ), empt AS (  -- empty slots by rule, per agent and cohort; sports with no eligible market left out
        SELECT x.tier, x.scope, x.rv, jsonb_object_agg(x.rule, x.n) AS j
          FROM (SELECT coalesce(e.detail->>'tier', replace(ag.slug, 'parlay_', '')) AS tier,
                       coalesce(e.detail->>'scope', 'sport') AS scope,
                       coalesce(e.detail->>'rule_version', 'unrecorded') AS rv,
                       CASE WHEN e.detail ? 'refused' THEN 'refused at commit'
                            ELSE public.parlay_rule_name(e.detail->>'binding') END AS rule,
                       count(*) AS n
                  FROM public.events e JOIN ag ON ag.id = e.agent_id
                 WHERE e.kind = 'gate_evaluated' AND (e.detail ? 'binding' OR e.detail ? 'refused')
                 GROUP BY 1, 2, 3, 4) x
         WHERE x.rule <> 'no eligible market'
         GROUP BY 1, 2, 3
    ), rows_j AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'tier', agg.tier, 'scope', agg.scope, 'rv', agg.rv,
                   'sport', nullif(agg.k, ''),
                   'tickets', agg.tickets, 'open', agg.open, 'settled', agg.settled,
                   'hit', agg.hit, 'partial', agg.partial,
                   'r_mean', agg.r_mean, 'r_se', round(se.se, 4), 'days', se.g,
                   'staked', agg.staked, 'paid', agg.paid, 'pl', agg.paid - agg.staked,
                   'clv_n', agg.clv_n, 'clv_mean', agg.clv_mean, 'clv_pct_mean', agg.clv_pct_mean,
                   'age_med', ages.med, 'age_max', greatest(ages.mx, maxage.mx), 'missed', agg.missed,
                   'empty', CASE WHEN agg.k = '' THEN empt.j END))
                   ORDER BY CASE agg.tier WHEN 'safer' THEN 1 WHEN 'mid' THEN 2 ELSE 3 END,
                            agg.scope DESC, agg.rv, agg.k) AS j
          FROM agg
          LEFT JOIN se USING (tier, scope, rv, k)
          LEFT JOIN ages USING (tier, scope, rv, k)
          LEFT JOIN maxage USING (tier, scope, rv, k)
          LEFT JOIN empt ON empt.tier = agg.tier AND empt.scope = agg.scope AND empt.rv = agg.rv
    )
    SELECT jsonb_build_object(
        'asof', extract(epoch FROM now())::bigint,
        'cohorts', (SELECT jsonb_agg(v ORDER BY v)
                      FROM (SELECT 'P2' AS v UNION SELECT 'P2.1' UNION SELECT DISTINCT rv FROM t) c),
        'rows', coalesce((SELECT j FROM rows_j), '[]'::jsonb),
        'unattributed_empty', (SELECT jsonb_object_agg(tier || ' ' || scope, j) FROM empt WHERE rv = 'unrecorded'));
$$;

-- ===================================================================== 8. Wags and the brief

CREATE OR REPLACE FUNCTION public.ctx_parlays()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        SELECT (now() AT TIME ZONE 'America/Chicago')::date AS today
    ), ag AS (
        SELECT a.id, a.slug FROM public.agents a WHERE public.parlay_agent_ok(a.slug, a.is_test, false)
    ), tk AS (
        SELECT c.id, c.committed_at, c.payload AS p, r.outcome, r.detail AS rd,
               (c.committed_at AT TIME ZONE 'America/Chicago')::date AS day
          FROM public.commitments c
          JOIN ag ON ag.id = c.agent_id
          CROSS JOIN d
          LEFT JOIN public.resolutions r ON r.commitment_id = c.id
         WHERE c.mode = 'paper'
           AND c.committed_at >= ((d.today - 1)::timestamp AT TIME ZONE 'America/Chicago')
    ), one AS (
        SELECT tk.day, jsonb_strip_nulls(jsonb_build_object(
                   'id', tk.id, 'tier', tk.p->>'tier',
                   'sport', coalesce(tk.p->>'sport', 'cross-sport'),
                   'slot', public.parlay_slot_of(tk.committed_at),
                   'stake_usd', round((tk.p->>'capital_at_risk')::numeric, 2),
                   'pays_usd', coalesce((tk.p->>'contracts')::numeric, 100),
                   'multiplier', round(coalesce((tk.p->>'contracts')::numeric, 100)
                                       / (tk.p->>'capital_at_risk')::numeric, 2),
                   'outcome', coalesce(tk.outcome, 'open'),
                   'fill_note', tk.p->>'fill_note',
                   -- Today's Safer and Mid legs in words; a Lotto's 11-20 legs are counted only (Wags' snapshot stays small).
                   'legs', CASE WHEN tk.day = (SELECT today FROM d) AND jsonb_array_length(tk.p->'legs') <= 10 THEN (
                       SELECT jsonb_agg(substring(x FROM '^((?:YES|NO) .+): ask [0-9]') ORDER BY i)
                         FROM jsonb_array_elements_text(tk.p->'leg_reasons') WITH ORDINALITY AS y (x, i)) END,
                   'n_legs', jsonb_array_length(tk.p->'legs'))) AS j
          FROM tk
    ), em AS (
        SELECT coalesce(e.detail->>'tier', replace(ag.slug, 'parlay_', '')) AS tier,
               coalesce(e.detail->>'sport', 'all') AS sport,
               CASE WHEN e.detail ? 'refused' THEN 'refused at commit'
                    ELSE public.parlay_rule_name(e.detail->>'binding') END AS rule
          FROM public.events e JOIN ag ON ag.id = e.agent_id CROSS JOIN d
         WHERE e.kind = 'gate_evaluated' AND (e.detail ? 'binding' OR e.detail ? 'refused')
           AND e.at >= (d.today::timestamp AT TIME ZONE 'America/Chicago')
    ), rec AS (
        SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                   'tier', x->>'tier', 'scope', x->>'scope', 'rule_version', x->>'rv',
                   'settled', x->'settled', 'hit', x->'hit', 'open', x->'open',
                   'return_per_usd_staked', x->'r_mean', 'se', x->'r_se',
                   'paper_pl_usd', x->'pl', 'staked_usd', x->'staked',
                   'clv_proxy_mean', x->'clv_mean', 'close_age_min_median', x->'age_med',
                   'close_age_min_max', x->'age_max'))) AS j
          FROM jsonb_array_elements(public.parlay_record(false)->'rows') x
         WHERE NOT (x ? 'sport')
    )
    SELECT jsonb_build_object(
        'about', 'The Parlay Generator''s PAPER tickets (nothing is bet): three agents, Safer (3-5 legs), Mid '
                 || '(6-10) and Lotto (11-20, plus a cross-sport Lotto marked extrapolated fill), at the 10:45 and '
                 || '16:45 Chicago slots. Each ticket stakes the paper amount shown to win $100. Report each tier '
                 || 'on its own and never add tiers or rule versions (cohorts) together. CLV (proxy) is not profit '
                 || 'and is always given with its close ages. An empty slot has a binding rule.',
        'today', (SELECT today FROM d),
        'today_tickets', coalesce((SELECT jsonb_agg(j ORDER BY j->>'slot', j->>'tier')
                                     FROM one WHERE day = (SELECT today FROM d)), '[]'::jsonb),
        'yesterday_tickets', coalesce((SELECT jsonb_agg(j ORDER BY j->>'slot', j->>'tier')
                                         FROM one WHERE day < (SELECT today FROM d)), '[]'::jsonb),
        'today_empty_slots', coalesce((SELECT jsonb_agg(jsonb_build_object('tier', tier, 'rule', rule, 'n', n,
                                                                           'sports', sports) ORDER BY tier, n DESC)
                                         FROM (SELECT tier, rule, count(*) AS n,
                                                      string_agg(DISTINCT sport, ', ') AS sports
                                                 FROM em WHERE rule <> 'no eligible market' GROUP BY 1, 2) x),
                                      '[]'::jsonb),
        'record_by_agent', coalesce((SELECT j FROM rec), '[]'::jsonb));
$$;

CREATE OR REPLACE FUNCTION public.parlay_usd(p_amount numeric)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    -- "+$52.70" / "−$41.50" (a real minus), for the brief's lines.
    SELECT CASE WHEN p_amount < 0 THEN '−' ELSE '+' END || '$' || to_char(abs(p_amount), 'FM999,990.00');
$$;

CREATE OR REPLACE FUNCTION public.ctx_parlays_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        SELECT (now() AT TIME ZONE 'America/Chicago')::date - 1 AS y
    ), ag AS (
        SELECT a.id, a.slug FROM public.agents a WHERE public.parlay_agent_ok(a.slug, a.is_test, false)
    ), b AS (
        SELECT (d.y::timestamp AT TIME ZONE 'America/Chicago') AS lo,
               ((d.y + 1)::timestamp AT TIME ZONE 'America/Chicago') AS hi
          FROM d
    ), tiers AS (
        SELECT * FROM (VALUES (1, 'safer', 'sport', 'Safer'), (2, 'mid', 'sport', 'Mid'),
                              (3, 'lotto', 'sport', 'Lotto'), (4, 'lotto', 'cross_sport', 'Lotto cross-sport'))
               AS v (o, tier, scope, name)
    ), made AS (
        SELECT c.payload->>'tier' AS tier, coalesce(c.payload->>'scope', 'sport') AS scope, count(*) AS n
          FROM public.commitments c JOIN ag ON ag.id = c.agent_id CROSS JOIN b
         WHERE c.mode = 'paper' AND c.committed_at >= b.lo AND c.committed_at < b.hi
         GROUP BY 1, 2
    ), settled AS (
        SELECT c.payload->>'tier' AS tier, coalesce(c.payload->>'scope', 'sport') AS scope,
               count(*) FILTER (WHERE r.outcome = 'hit') AS hit,
               count(*) FILTER (WHERE r.outcome = 'miss') AS miss,
               count(*) FILTER (WHERE r.outcome = 'partial') AS part,
               sum((r.detail->>'proceeds')::numeric - (c.payload->>'capital_at_risk')::numeric) AS pl
          FROM public.resolutions r
          JOIN public.commitments c ON c.id = r.commitment_id
          JOIN ag ON ag.id = c.agent_id
         WHERE c.mode = 'paper' AND r.resolved_at > now() - interval '24 hours'
         GROUP BY 1, 2
    ), ev AS (
        SELECT e.at, coalesce(e.detail->>'tier', replace(ag.slug, 'parlay_', '')) AS tier,
               public.parlay_slot_of(e.at) AS slot,
               CASE WHEN e.detail ? 'refused' THEN 'refused at commit'
                    WHEN e.detail ? 'binding' THEN public.parlay_rule_name(e.detail->>'binding') END AS rule
          FROM public.events e JOIN ag ON ag.id = e.agent_id CROSS JOIN b
         WHERE e.kind = 'gate_evaluated' AND e.at >= b.lo AND e.at < b.hi
    ), rules AS (
        SELECT tier, string_agg(rule || ' ' || n, ', ' ORDER BY n DESC, rule) AS txt
          FROM (SELECT tier, rule, count(*) AS n FROM ev
                 WHERE rule IS NOT NULL AND rule <> 'no eligible market' GROUP BY 1, 2) x
         GROUP BY 1
    ), made_line AS (
        SELECT 'Parlays (paper), yesterday: '
               || string_agg(t.name || ' ' || m.n || CASE WHEN m.n = 1 THEN ' ticket' ELSE ' tickets' END,
                             ', ' ORDER BY t.o) || '.' AS txt
          FROM tiers t JOIN made m ON m.tier = t.tier AND m.scope = t.scope
    ), settled_line AS (
        SELECT 'Settled since yesterday''s brief: '
               || string_agg(t.name || ' '
                             || concat_ws(', ',
                                    CASE WHEN s.hit > 0 THEN s.hit || ' hit' END,
                                    CASE WHEN s.part > 0 THEN s.part || ' partial' END,
                                    CASE WHEN s.miss > 0 THEN s.miss || ' missed' END)
                             || ' (' || public.parlay_usd(s.pl) || ' paper)', '; ' ORDER BY t.o)
               || '.' AS txt
          FROM tiers t JOIN settled s ON s.tier = t.tier AND s.scope = t.scope
    ), empty_line AS (
        SELECT 'Parlays (paper): no ticket yesterday. '
               || CASE WHEN count(DISTINCT ev.slot) = 2 THEN 'Both slots ran and every tier was empty. '
                       ELSE 'Only the ' || to_char(min(ev.at) AT TIME ZONE 'America/Chicago', 'FMHH12:MI AM')
                            || ' slot ran, and every tier was empty. ' END
               || coalesce((SELECT string_agg(initcap(r.tier) || ': ' || r.txt, '. '
                                              ORDER BY CASE r.tier WHEN 'safer' THEN 1 WHEN 'mid' THEN 2 ELSE 3 END)
                              FROM rules r) || '.', '') AS txt
          FROM ev
    ), last_slot AS (
        SELECT max(e.at) AS at FROM public.events e JOIN ag ON ag.id = e.agent_id WHERE e.kind = 'gate_evaluated'
    )
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM public.agents a
                          WHERE a.slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto') AND a.enabled) THEN NULL
        ELSE jsonb_build_object(
            'date', (SELECT y FROM d) + 1,
            'about', 'The Parlay Generator''s PAPER tickets, already worded: yesterday''s slots and what settled '
                     || 'in the last 24 hours. Use these lines as given. Each tier''s figure is its own: never add '
                     || 'tiers together or give a total.',
            'lines', to_jsonb(array_remove(ARRAY[
                CASE
                    WHEN NOT EXISTS (SELECT 1 FROM ev) THEN
                        'Parlays (paper): no slot ran yesterday. '
                        || coalesce('The last was '
                                    || to_char((SELECT at FROM last_slot) AT TIME ZONE 'America/Chicago',
                                               'Dy Mon FMDD, FMHH12:MI AM') || '.',
                                    'No slot has run yet.')
                    WHEN EXISTS (SELECT 1 FROM made) THEN (SELECT txt FROM made_line)
                    ELSE (SELECT txt FROM empty_line)
                END,
                (SELECT txt FROM settled_line)], NULL)))
    END;
$$;

-- ===================================================================== 9. grants and providers

REVOKE ALL ON FUNCTION public.parlay_agent_ok(text, boolean, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_rule_name(text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_slot_of(timestamptz) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_close_ages(jsonb) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_usd(numeric) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_team(jsonb) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_day(date, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_ticket(bigint, boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.parlay_record(boolean) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_parlays() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_parlays_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.parlay_agent_ok(text, boolean, boolean), public.parlay_rule_name(text),
    public.parlay_slot_of(timestamptz), public.parlay_close_ages(jsonb), public.parlay_usd(numeric), public.parlay_team(jsonb),
    public.parlay_day(date, boolean), public.parlay_ticket(bigint, boolean), public.parlay_record(boolean),
    public.ctx_parlays(), public.ctx_parlays_brief()
    TO authenticated, valemont_readonly;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('parlays', 'Parlays', 'public.ctx_parlays()'::regprocedure, false, 52),
       ('parlays_brief', 'Parlays', 'public.ctx_parlays_brief()'::regprocedure, true, 52)
ON CONFLICT (key) DO NOTHING;

-- The legs still on the board, captured now (older legs keep their frozen label only).
SELECT public.parlay_leg_display_capture(interval '30 days');

INSERT INTO migration_log (name) VALUES ('091_parlays_page')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 10. self-test
-- Run on paste, rolled back ('selftest-rollback'). The owner's session is
-- simulated as in db/083, with a throwaway marker, for the e2e board helper.
-- Test rows only. A statement that calls a writing function stores its
-- result first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker text := 'selftest-091-' || md5(random()::text);
    v_game   text;
    v_agent  smallint := (SELECT id FROM public.agents WHERE slug = '_e2e_parlays');
    v_run    bigint;
    v_c      bigint;
    v_sid    bigint;
    v_today  date := (now() AT TIME ZONE 'America/Chicago')::date;
    v_j      jsonb;
    v_k      jsonb;
    v_n      int;
BEGIN
    BEGIN
        IF v_owner IS NULL OR v_agent IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no owner or no _e2e_parlays agent';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        SELECT sports_event_id INTO v_sid FROM public.kalshi_board_events WHERE event_ticker = v_game;

        -- ------------------------------------------------ 1. a test ticket and two slot results
        INSERT INTO public.runs (agent_id, status) VALUES (v_agent, 'ok') RETURNING id INTO v_run;
        INSERT INTO public.commitments (agent_id, run_id, kind, thesis, payload, resolves_after, closes_at)
        VALUES (v_agent, v_run, 'event_contract', 'selftest 091',
                jsonb_build_object(
                    'tier', 'safer', 'scope', 'sport', 'sport', 'Basketball', 'rule_version', 'P2.1',
                    'search', 'greedy', 'capital_at_risk', '41.34', 'contracts', 100,
                    'scored_price', '0.3966', 'taker_fee', '0.0168', 'maker_fee_assumed', '0.0084',
                    'fair_p', 0.3862, 'naive_p', '0.3841', 'rel_cost_over_fair', 0.0254,
                    'pairs', 1, 'measured_pairs', 1, 'prior_pairs', 0, 'edge_legs', '[]'::jsonb,
                    'combo_status', 'fits', 'expected_quote_note', 'pending (M1)', 'rho_version', '.4',
                    'leg_reasons', jsonb_build_array(
                        'YES Test Home wins (Test Away vs Test Home): ask 63¢, mid 0.620, ask÷mid 1.016, ranked 1 of 2; kalshi consensus p 0.648; 0 ranked higher were not taken',
                        'NO Test Player: 10+ pts (Test Away vs Test Home): ask 45¢, mid 0.435, ask÷mid 1.034, ranked 2 of 2; kalshi consensus p 0.435; 0 ranked higher were not taken'),
                    'legs', jsonb_build_array(
                        jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes', 'event_ticker', v_game,
                                           'game_ticker', v_game, 'ask', '0.63'),
                        jsonb_build_object('market_ticker', v_game || 'PTS-P1-10', 'side', 'no',
                                           'event_ticker', v_game || 'PTS', 'game_ticker', v_game, 'ask', '0.45')),
                    'kickoffs', jsonb_build_object(v_game || '-HOM', now() + interval '2 hours',
                                                   v_game || 'PTS-P1-10', now() + interval '2 hours')),
                now() + interval '3 hours', now() + interval '2 hours')
        RETURNING id INTO v_c;
        INSERT INTO public.legs (commitment_id, leg_index, subject, market, line, direction, size)
        VALUES (v_c, 0, v_game || '-HOM', 'E2ETEST', 0.63, 'yes', 100),
               (v_c, 1, v_game || 'PTS-P1-10', 'E2ETEST', 0.45, 'no', 100);
        INSERT INTO public.events (agent_id, run_id, kind, message, detail)
        VALUES (v_agent, v_run, 'gate_evaluated', 'empty: unmodeled pair',
                jsonb_build_object('slot', v_today || ' pm', 'tier', 'safer', 'scope', 'sport', 'sport', 'E2E Football',
                                   'rule_version', 'P2.1', 'binding', 'unmodeled pair')),
               (v_agent, v_run, 'gate_evaluated', 'empty: rules 1-4: universe',
                jsonb_build_object('slot', v_today || ' pm', 'tier', 'safer', 'scope', 'sport', 'sport', 'E2E Chess',
                                   'rule_version', 'P2.1', 'binding', 'rules 1-4: universe'));

        -- ------------------------------------------------ 2. the capture, once
        v_n := public.parlay_leg_display_capture();
        IF v_n < 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: the capture wrote % rows', v_n; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.parlay_leg_display
                        WHERE market_ticker = v_game || 'PTS-P1-10' AND subject = 'Test Player' AND stat = 'points'
                          AND line = 9.5 AND team = 'HOM' AND period IS NULL AND is_test AND game_ticker = v_game) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the prop leg''s display row';
        END IF;
        v_n := public.parlay_leg_display_capture();
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: a second capture wrote % rows', v_n; END IF;
        BEGIN
            UPDATE public.parlay_leg_display SET line = 1 WHERE market_ticker = v_game || '-HOM';
            RAISE EXCEPTION 'SELFTEST FAILED: a display row was updated';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM LIKE 'SELFTEST FAILED%' THEN RAISE; END IF;
        END;

        -- ------------------------------------------------ 3. the day
        v_j := public.parlay_day(v_today, true);
        SELECT x INTO v_k FROM jsonb_array_elements(v_j->'tickets') x WHERE (x->>'id')::bigint = v_c;
        IF v_k IS NULL THEN RAISE EXCEPTION 'SELFTEST FAILED: the test ticket is not on its day'; END IF;
        IF (v_k->>'stake')::numeric <> 41.34 OR (v_k->>'pays')::numeric <> 100 OR v_k->>'tier' <> 'safer'
           OR v_k->>'rv' <> 'P2.1' OR jsonb_array_length(v_k->'legs') <> 2 OR v_k ? 'outcome' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the card reads %', v_k;
        END IF;
        IF v_k->'legs'->1->>'label' <> 'Test Player: 10+ pts (Test Away vs Test Home)'
           OR v_k->'legs'->1->>'who' <> 'Test Player' OR v_k->'legs'->1->>'side' <> 'no'
           OR (v_k->'legs'->1->>'line')::numeric <> 9.5 OR (v_k->'legs'->1->>'g')::bigint <> v_sid
           OR v_k->'legs'->1->>'sd' <> 'home' OR v_k->'legs'->0->>'sd' <> 'home'
           OR v_k->'legs'->0->>'label' <> 'Test Home wins (Test Away vs Test Home)' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the legs read %', v_k->'legs';
        END IF;
        IF NOT (v_j->'games' ? v_sid::text) THEN RAISE EXCEPTION 'SELFTEST FAILED: the game is missing'; END IF;
        IF v_j->'games'->(v_sid::text)->'home'
               IS DISTINCT FROM '{"id": "9002", "abbr": "HOM", "loc": "Hometown", "nick": "Home", "color": "c53030"}'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the game''s home side reads %', v_j->'games'->(v_sid::text)->'home';
        END IF;
        SELECT count(*) INTO v_n FROM jsonb_array_elements(v_j->'results') x
         WHERE x->>'sport' IN ('E2E Football', 'E2E Chess') AND x->>'slot' = '16:45';
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 2 slot results', v_n; END IF;
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'results') x
                        WHERE x->>'sport' = 'E2E Football' AND x->>'rule' = 'unmodeled pair'
                          AND x->>'raw' = 'unmodeled pair') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the empty slot''s rule';
        END IF;
        -- Without p_tests: none of it.
        v_j := public.parlay_day(v_today, false);
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'tickets') x WHERE (x->>'id')::bigint = v_c) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test ticket without p_tests';
        END IF;

        -- ------------------------------------------------ 4. Details and the record
        v_j := public.parlay_ticket(v_c, true);
        IF v_j IS NULL OR v_j->>'cohort' <> '2' OR jsonb_array_length(v_j->'reasons') <> 2
           OR v_j->'reasons'->>0 NOT LIKE 'YES Test Home wins%ask 63¢%' OR (v_j->>'fair')::numeric <> 0.3862
           OR v_j ? 'close' OR v_j->>'mode' <> 'paper' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Details read %', v_j;
        END IF;
        IF public.parlay_ticket(v_c, false) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Details of a test ticket without p_tests';
        END IF;
        v_j := public.parlay_record(true);
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'rows') x
                        WHERE x->>'tier' = 'safer' AND x->>'scope' = 'sport' AND x->>'rv' = 'P2.1'
                          AND NOT (x ? 'sport') AND (x->>'open')::int >= 1
                          AND (x->'empty'->>'unmodeled pair')::int >= 1 AND NOT (x->'empty' ? 'no eligible market')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the record reads %', v_j;
        END IF;
        IF NOT (v_j->'cohorts' @> '["P2", "P2.1"]'::jsonb) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the cohorts read %', v_j->'cohorts';
        END IF;

        -- ------------------------------------------------ 5. Wags and the brief never see a test ticket
        v_j := public.ctx_parlays();
        IF v_j->>'about' IS NULL
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_j->'today_tickets') x WHERE (x->>'id')::bigint = v_c) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_parlays reads %', v_j;
        END IF;
        v_j := public.ctx_parlays_brief();
        IF EXISTS (SELECT 1 FROM public.agents WHERE slug LIKE 'parlay\_%' AND enabled AND NOT is_test) THEN
            IF v_j IS NULL OR jsonb_array_length(v_j->'lines') < 1 OR v_j->>'lines' LIKE '%Test %' THEN
                RAISE EXCEPTION 'SELFTEST FAILED: the brief reads %', v_j;
            END IF;
        ELSIF v_j IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a brief line with every parlay agent disabled';
        END IF;

        -- ------------------------------------------------ 6. the helpers
        IF public.parlay_rule_name('rules 1-4: universe') <> 'no eligible market'
           OR public.parlay_rule_name('rules 1-4: 3 candidates < 11') <> 'too few candidates'
           OR public.parlay_rule_name('window: 3 candidates < 4, the fewest legs that reach the window') <> 'price window'
           OR public.parlay_rule_name('rules 1-4: ask_band') <> 'ask band'
           OR public.parlay_rule_name('3 per game') <> '3 per game'
           OR public.parlay_rule_name('fresh') <> 'no fresh prices'
           OR public.parlay_usd(-41.5) <> '−$41.50' OR public.parlay_usd(52.7) <> '+$52.70'
           OR (public.parlay_close_ages('{"legs": [{"kickoff": "2026-11-23T20:00:00+00:00", "candle_end": "2026-11-23T19:56:00+00:00"},
                                                    {"kickoff": "2026-11-23T20:00:00+00:00", "candle_end": "2026-11-23T20:00:00+00:00"},
                                                    {"kickoff": "2026-11-23T20:00:00+00:00", "candle_end": "2026-11-23T19:59:00+00:00"}]}'))
              IS DISTINCT FROM ARRAY[1.0, 4.0]::numeric[]
           OR (public.parlay_close_ages('{"age_min_median": 2, "age_min_max": 7}')) IS DISTINCT FROM ARRAY[2, 7]::numeric[]
           OR public.parlay_slot_of('2026-11-24 16:45:09+00') <> '10:45'
           OR public.parlay_slot_of('2026-11-24 22:45:09+00') <> '16:45' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a helper';
        END IF;

        -- ------------------------------------------------ 7. parlay_hit_pushes: once, never changed
        INSERT INTO public.parlay_hit_pushes (commitment_id, is_test) VALUES (v_c, true);
        v_n := 0;
        BEGIN
            INSERT INTO public.parlay_hit_pushes (commitment_id, is_test) VALUES (v_c, true);
        EXCEPTION WHEN unique_violation THEN v_n := v_n + 1;
        END;
        BEGIN
            UPDATE public.parlay_hit_pushes SET notification_id = 1 WHERE commitment_id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            DELETE FROM public.parlay_hit_pushes WHERE commitment_id = v_c;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.parlay_hit_pushes;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        BEGIN
            TRUNCATE public.parlay_leg_display;
        EXCEPTION WHEN raise_exception THEN v_n := v_n + 1;
        END;
        IF v_n <> 5 THEN RAISE EXCEPTION 'SELFTEST FAILED: % of 5 refusals', v_n; END IF;

        -- ------------------------------------------------ 8. access
        IF has_table_privilege('anon', 'public.parlay_leg_display', 'SELECT')
           OR has_table_privilege('authenticated', 'public.parlay_leg_display', 'INSERT')
           OR has_table_privilege('authenticated', 'public.parlay_hit_pushes', 'INSERT')
           OR NOT has_table_privilege('valemont_readonly', 'public.parlay_hit_pushes', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.parlay_leg_display', 'SELECT')
           OR has_function_privilege('authenticated', 'public.parlay_leg_display_capture(interval)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.parlay_leg_display_capture(interval)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.parlay_day(date, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.parlay_day(date, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.ctx_parlays()', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants';
        END IF;
        IF (SELECT count(*) FROM public.context_providers WHERE key IN ('parlays', 'parlays_brief')) <> 2
           OR (SELECT brief_section FROM public.context_providers WHERE key = 'parlays')
           OR NOT (SELECT brief_section FROM public.context_providers WHERE key = 'parlays_brief') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the providers';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '091 self-test passed (rolled back)';
END
$$;
