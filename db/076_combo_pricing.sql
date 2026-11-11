-- 076_combo_pricing.sql: same-game correlation, the combo pricer's tables
-- (owner, Nov 11; Chat 8 Phase 3; docs/correlation.md).
--
-- 1. kalshi_board.team: the team a market belongs to (a prop's player's
--    team, a team market's team), as the game's own code (BOS, TOR). The
--    worker reads it from custom_strike: a prop's `<sport>_team` id equals
--    the id on that team's GAME market, whose ticker ends in the code
--    (Step 0, Nov 11: NBA 4,527/4,527 prop markets, NFL 3,752/3,764 with the
--    12 misses all "No Touchdown", NHL 1,062/1,068 with the 6 all "no goal").
--    NULL when Kalshi gives no team. The upsert never clears a known team
--    (coalesce), so a refresh that cannot resolve one leaves it as it is,
--    and the deployed worker, which sends no team, writes nothing new.
--    v_kalshi_board appends `team` (CREATE OR REPLACE VIEW adds at the end).
-- 2. combo_requests: the owner's "price these legs" (an APP table). Legs are
--    a JSON array of {market_ticker, side}, 2 to 25 of them. An insert is
--    always `queued` and queues ONE job_queue row `price_combo` (the Arm Me
--    pattern: the web enqueues, the worker answers on its 10 s poll). Only
--    the worker settles a request, once: answered (with its quote), refused
--    (an impossible combo, with the reason) or failed (with the reason).
--    is_test is derived: every leg a test board market (the E2E series) is a
--    test request; test and real legs never mix.
-- 3. combo_quotes: one per answered request, written by the worker while
--    the request is still queued, never changed: the naive product, the
--    correlated fair p (with its Monte Carlo standard error when k >= 3 in
--    a game), the per-game blocks, Kalshi's naive cost, the combo status, the
--    sha256 of the rho table it used, and how many pairs were unmodeled or
--    priced from a prior/borrowed row (the sheet's "prior" chip).
-- 4. combo_prune(cutoff, tests_only): requests (and their quotes, by
--    cascade) past 7 days. The worker's daily job; no API role runs it.
-- 5. add_test_kalshi_board: db/073's, with `team` on its rows.
--
-- Read-only to Kalshi, nothing here bets (CLAUDE.md §1). Paste-by-hand, top
-- to bottom, no BEGIN/COMMIT (§5). The self-test at the end runs every
-- trigger and function this file creates or replaces and rolls back.
--
-- Prerequisites: db/020 (job_queue, is_owner), db/051 (e2e_markers,
-- e2e_marker_ok), db/069, db/071-db/075.

-- ===================================================================== 1
ALTER TABLE kalshi_board ADD COLUMN IF NOT EXISTS team text;
ALTER TABLE kalshi_board DROP CONSTRAINT IF EXISTS kalshi_board_team_shape;
ALTER TABLE kalshi_board ADD CONSTRAINT kalshi_board_team_shape
    CHECK (team IS NULL OR team ~ '^[A-Z0-9]{1,6}$');
COMMENT ON COLUMN public.kalshi_board.team IS
    'The team this market belongs to, as the game''s code (BOS): from custom_strike''s <sport>_team id matched '
    'to the id on that team''s GAME market (Step 0, Nov 11). NULL when Kalshi gives none. Never cleared by the '
    'upsert. db/076.';

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
                yes_ask, volume, open_interest, taker_fee_at_ask, team, is_test, fetched_at, last_changed_at)
        SELECT r.market_ticker, r.event_ticker, r.market_kind, r.variant, coalesce(r.payout, 'binary'), r.subject,
               r.stat, r.line, r.label, r.yes_bid, r.yes_ask, r.volume, r.open_interest, r.taker_fee_at_ask, r.team,
               p_is_test, p_at, p_at
          FROM jsonb_to_recordset(p_rows) AS r (
                   market_ticker text, event_ticker text, market_kind text, variant text, payout text, subject text,
                   stat text, line numeric, label text, yes_bid numeric, yes_ask numeric, volume numeric,
                   open_interest numeric, taker_fee_at_ask numeric, team text)
        ON CONFLICT (market_ticker) DO UPDATE SET
            event_ticker = EXCLUDED.event_ticker, market_kind = EXCLUDED.market_kind, variant = EXCLUDED.variant,
            payout = EXCLUDED.payout, subject = EXCLUDED.subject, stat = EXCLUDED.stat, line = EXCLUDED.line,
            label = EXCLUDED.label, yes_bid = EXCLUDED.yes_bid, yes_ask = EXCLUDED.yes_ask,
            volume = EXCLUDED.volume, open_interest = EXCLUDED.open_interest,
            taker_fee_at_ask = EXCLUDED.taker_fee_at_ask,
            team = coalesce(EXCLUDED.team, b.team),
            fetched_at = greatest(b.fetched_at, EXCLUDED.fetched_at),
            last_changed_at = CASE WHEN (b.yes_bid, b.yes_ask) IS DISTINCT FROM (EXCLUDED.yes_bid, EXCLUDED.yes_ask)
                                   THEN greatest(b.last_changed_at, EXCLUDED.fetched_at)
                                   ELSE b.last_changed_at END
         WHERE b.is_test = p_is_test
           AND (b.event_ticker, b.market_kind, b.variant, b.payout, b.subject, b.stat, b.line, b.label, b.yes_bid,
                b.yes_ask, b.volume, b.open_interest, b.taker_fee_at_ask, b.team)
               IS DISTINCT FROM
               (EXCLUDED.event_ticker, EXCLUDED.market_kind, EXCLUDED.variant, EXCLUDED.payout, EXCLUDED.subject,
                EXCLUDED.stat, EXCLUDED.line, EXCLUDED.label, EXCLUDED.yes_bid, EXCLUDED.yes_ask, EXCLUDED.volume,
                EXCLUDED.open_interest, EXCLUDED.taker_fee_at_ask, coalesce(EXCLUDED.team, b.team))
        RETURNING 1
    )
    SELECT count(*)::integer FROM w;
$$;

-- db/072's columns unchanged and in order; team appended. Grants and
-- security_invoker carry over.
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
       b.payout,
       b.team
  FROM kalshi_board b
  JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
  JOIN kalshi_series_board s ON s.series_ticker = e.series_ticker;

-- ===================================================================== 2
CREATE TABLE IF NOT EXISTS combo_requests (
    id           BIGSERIAL    PRIMARY KEY,
    owner_id     UUID         NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE RESTRICT,
    legs         JSONB        NOT NULL,
    status       TEXT         NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'answered', 'refused', 'failed')),
    reason       TEXT         CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 500),
    is_test      BOOLEAN      NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    answered_at  TIMESTAMPTZ,
    CONSTRAINT combo_requests_legs_shape
        CHECK (jsonb_typeof(legs) = 'array' AND jsonb_array_length(legs) BETWEEN 2 AND 25),
    CONSTRAINT combo_requests_settled CHECK ((status = 'queued') = (answered_at IS NULL)),
    CONSTRAINT combo_requests_reason CHECK ((status IN ('refused', 'failed')) = (reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS combo_requests_created ON combo_requests (created_at);
CREATE INDEX IF NOT EXISTS combo_requests_owner ON combo_requests (owner_id, created_at DESC);
COMMENT ON TABLE public.combo_requests IS
    'The owner''s "price these legs" (Chat 8 Phase 3). Always inserted queued; the worker settles it once '
    '(answered with a combo_quotes row, refused or failed with a reason). Pruned after 7 days. db/076.';

CREATE OR REPLACE FUNCTION public.combo_requests_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_leg   jsonb;
    v_n     int;
    v_test  int;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.status := 'queued';
        NEW.reason := NULL;
        NEW.answered_at := NULL;
        IF jsonb_typeof(NEW.legs) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'combo_requests: legs must be a JSON array';
        END IF;
        v_n := jsonb_array_length(NEW.legs);
        IF v_n < 2 OR v_n > 25 THEN
            RAISE EXCEPTION 'combo_requests: 2 to 25 legs (got %)', v_n;
        END IF;
        FOR v_leg IN SELECT x FROM jsonb_array_elements(NEW.legs) AS t (x) LOOP
            -- The type first, alone: jsonb_object_keys raises on a non-object.
            IF jsonb_typeof(v_leg) IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION 'combo_requests: each leg is {"market_ticker": "<KX…>", "side": "yes"|"no"} (got %)',
                    left(v_leg::text, 120);
            END IF;
            IF (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(v_leg) AS o (k))
                  IS DISTINCT FROM ARRAY['market_ticker', 'side']
               OR jsonb_typeof(v_leg -> 'market_ticker') IS DISTINCT FROM 'string'
               OR NOT (v_leg ->> 'market_ticker') ~ '^[A-Z0-9][A-Z0-9._-]{0,99}$'
               OR (v_leg ->> 'side') IS NULL OR (v_leg ->> 'side') NOT IN ('yes', 'no') THEN
                RAISE EXCEPTION 'combo_requests: each leg is {"market_ticker": "<KX…>", "side": "yes"|"no"} (got %)',
                    left(v_leg::text, 120);
            END IF;
        END LOOP;
        -- A test request is one whose every leg is a test board market.
        SELECT count(*) INTO v_test
          FROM jsonb_array_elements(NEW.legs) AS t (x)
          JOIN public.kalshi_board b ON b.market_ticker = t.x ->> 'market_ticker'
         WHERE b.is_test;
        IF v_test > 0 AND v_test < v_n THEN
            RAISE EXCEPTION 'combo_requests: a request never mixes test and real legs';
        END IF;
        NEW.is_test := (v_test = v_n);
        RETURN NEW;
    END IF;

    -- UPDATE: the worker settles a queued request, once.
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.legs IS DISTINCT FROM OLD.legs OR NEW.is_test IS DISTINCT FROM OLD.is_test
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'combo_requests: a request''s legs and identity never change';
    END IF;
    IF OLD.status <> 'queued' OR NEW.status NOT IN ('answered', 'refused', 'failed') THEN
        RAISE EXCEPTION 'combo_requests: a request settles once, from queued (% -> %)', OLD.status, NEW.status;
    END IF;
    NEW.answered_at := now();
    IF NEW.status = 'answered' THEN
        IF NEW.reason IS NOT NULL THEN
            RAISE EXCEPTION 'combo_requests: an answered request carries no reason';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.combo_quotes q WHERE q.request_id = NEW.id) THEN
            RAISE EXCEPTION 'combo_requests: answered needs its quote first';
        END IF;
    ELSE
        IF NEW.reason IS NULL OR btrim(NEW.reason) = '' THEN
            RAISE EXCEPTION 'combo_requests: % needs a reason', NEW.status;
        END IF;
        IF EXISTS (SELECT 1 FROM public.combo_quotes q WHERE q.request_id = NEW.id) THEN
            RAISE EXCEPTION 'combo_requests: a quoted request is answered, never %', NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- After an insert: ONE price_combo job. Runs as the inserting role (the
-- owner's job_queue insert policy admits a fresh queued job).
CREATE OR REPLACE FUNCTION public.combo_requests_queue()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.job_queue (kind, payload) VALUES ('price_combo', jsonb_build_object('request_id', NEW.id));
    RETURN NULL;
END;
$$;

-- ===================================================================== 3
-- combo_quotes comes before the triggers are attached: the requests guard
-- reads it, and it must exist before any statement fires that guard.
CREATE TABLE IF NOT EXISTS combo_quotes (
    request_id         BIGINT            PRIMARY KEY REFERENCES combo_requests (id) ON DELETE CASCADE,
    naive_p            DOUBLE PRECISION  NOT NULL CHECK (naive_p >= 0 AND naive_p <= 1),
    fair_p             DOUBLE PRECISION  NOT NULL CHECK (fair_p >= 0 AND fair_p <= 1),
    se                 DOUBLE PRECISION  CHECK (se IS NULL OR se >= 0),
    blocks             JSONB             NOT NULL
                                         CHECK (jsonb_typeof(blocks) = 'array' AND jsonb_array_length(blocks) >= 1),
    kalshi_naive_cost  NUMERIC(12, 8)    CHECK (kalshi_naive_cost IS NULL OR kalshi_naive_cost > 0),
    combo_status       TEXT              NOT NULL CHECK (combo_status IN ('fits', 'build_manually')),
    combo_reason       TEXT              CHECK (combo_reason IS NULL OR length(combo_reason) BETWEEN 1 AND 300),
    rho_version        TEXT              NOT NULL CHECK (rho_version ~ '^[0-9a-f]{64}$'),
    unmodeled_pairs    INTEGER           NOT NULL CHECK (unmodeled_pairs >= 0),
    prior_pairs        INTEGER           NOT NULL CHECK (prior_pairs >= 0),
    compute_ms         INTEGER           NOT NULL CHECK (compute_ms >= 0),
    is_test            BOOLEAN           NOT NULL DEFAULT false,
    computed_at        TIMESTAMPTZ       NOT NULL DEFAULT now(),
    CONSTRAINT combo_quotes_reason CHECK ((combo_status = 'fits') = (combo_reason IS NULL))
);
COMMENT ON TABLE public.combo_quotes IS
    'One quote per answered combo_requests row, written by the worker while the request is queued, never '
    'changed; deleted only with its request (the 7-day prune). fair_p is the Gaussian-copula joint, product over '
    'games; se is the Monte Carlo standard error when a game block has 3+ legs. db/076.';

CREATE OR REPLACE FUNCTION public.combo_quotes_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_status text;
    v_test   boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'combo_quotes: a quote never changes';
    END IF;
    SELECT r.status, r.is_test INTO v_status, v_test
      FROM public.combo_requests r WHERE r.id = NEW.request_id FOR UPDATE;
    IF v_status IS DISTINCT FROM 'queued' THEN
        RAISE EXCEPTION 'combo_quotes: request % is not queued (%)', NEW.request_id, coalesce(v_status, 'missing');
    END IF;
    NEW.is_test := v_test;
    NEW.computed_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS combo_quotes_guard ON combo_quotes;
CREATE TRIGGER combo_quotes_guard
    BEFORE INSERT OR UPDATE ON combo_quotes
    FOR EACH ROW EXECUTE FUNCTION public.combo_quotes_guard();
DROP TRIGGER IF EXISTS combo_requests_guard ON combo_requests;
CREATE TRIGGER combo_requests_guard
    BEFORE INSERT OR UPDATE ON combo_requests
    FOR EACH ROW EXECUTE FUNCTION public.combo_requests_guard();
DROP TRIGGER IF EXISTS combo_requests_queue ON combo_requests;
CREATE TRIGGER combo_requests_queue
    AFTER INSERT ON combo_requests
    FOR EACH ROW EXECUTE FUNCTION public.combo_requests_queue();

-- ===================================================================== 4
CREATE OR REPLACE FUNCTION public.combo_prune(p_cutoff timestamptz DEFAULT now() - interval '7 days',
                                              p_tests_only boolean DEFAULT false)
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (
        DELETE FROM public.combo_requests r
         WHERE r.created_at < p_cutoff AND (r.is_test OR NOT p_tests_only)
        RETURNING 1
    )
    SELECT count(*)::integer FROM d;
$$;

-- ===================================================================== 5
-- db/073's helper with `team` on its rows (HOM, AWY, and the player's HOM).
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
    INSERT INTO public.leg_consensus (market_ticker, tier, n_books, p_books, p_books_mult, p_espn, inputs, source_at,
                                      computed_at)
    VALUES (v_game || '-HOM', 'books', 3, 0.660000, 0.659000, NULL,
            '{"m": "power", "b": {"draftkings": 0.66, "fanduel": 0.665, "betmgm": 0.655}}'::jsonb, now(), now());
    RETURN v_game;
END;
$$;

-- ===================================================================== 6
ALTER TABLE combo_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE combo_quotes ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON combo_requests, combo_quotes FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE combo_requests_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON combo_requests TO authenticated;
GRANT USAGE ON SEQUENCE combo_requests_id_seq TO authenticated;
GRANT SELECT ON combo_quotes TO authenticated;
GRANT SELECT ON combo_requests, combo_quotes TO valemont_readonly;

DROP POLICY IF EXISTS combo_requests_owner_read ON combo_requests;
CREATE POLICY combo_requests_owner_read ON combo_requests FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS combo_requests_owner_insert ON combo_requests;
CREATE POLICY combo_requests_owner_insert ON combo_requests FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner()) AND owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS combo_quotes_owner_read ON combo_quotes;
CREATE POLICY combo_quotes_owner_read ON combo_quotes FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));

REVOKE ALL ON FUNCTION public.combo_requests_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.combo_quotes_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.combo_requests_queue() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.combo_prune(timestamptz, boolean) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.add_test_kalshi_board(text, timestamptz) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_kalshi_board(text, timestamptz) TO authenticated;

INSERT INTO migration_log (name) VALUES ('076_combo_pricing')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 7
-- Self-test, run on paste, rolled back ('selftest-rollback'). Expected
-- refusals run in nested blocks and count only with the expected message or
-- constraint. The owner's session is simulated with request.jwt claims; the
-- marker is a throwaway e2e_markers row; every row it writes is a test row
-- or rolled back. Counts of real rows are compared as before/after deltas.

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-076-' || md5(random()::text);
    v_game    text;
    v_pts     text;
    v_legs    jsonb;
    v_id      bigint;
    v_id2     bigint;
    v_n       int;
    v_real    bigint;
    v_team    text;
    v_rho     text := repeat('ab', 32);
    v_bad     text[];
    v_case    text;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));

        -- ------------------------------------------------ 1. the test board and team
        BEGIN
            PERFORM public.add_test_kalshi_board('not-the-marker', now() + interval '2 hours');
            RAISE EXCEPTION 'SELFTEST FAILED: add_test_kalshi_board took a wrong marker';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'add_test_kalshi_board: the owner%' THEN RAISE; END IF;
        END;
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        v_pts := v_game || 'PTS';
        SELECT team INTO v_team FROM public.kalshi_board WHERE market_ticker = v_pts || '-P1-10';
        IF v_team IS DISTINCT FROM 'HOM'
           OR (SELECT team FROM public.v_kalshi_board WHERE market_ticker = v_game || '-AWY') IS DISTINCT FROM 'AWY' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test board''s team (got %)', v_team;
        END IF;
        -- The same row with no team: nothing written, the team kept.
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_pts || '-P1-10', 'event_ticker', v_pts, 'market_kind', 'prop', 'payout', 'binary',
            'subject', 'Test Player', 'stat', 'points', 'line', 9.5, 'label', 'Test Player: 10+', 'yes_bid', 0.55,
            'yes_ask', 0.58, 'volume', 300, 'open_interest', 200, 'taker_fee_at_ask', 0.017052)), now(), true);
        IF v_n <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an unchanged row with no team was written (%)', v_n;
        END IF;
        IF (SELECT team FROM public.kalshi_board WHERE market_ticker = v_pts || '-P1-10') IS DISTINCT FROM 'HOM' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an upsert with no team cleared it';
        END IF;
        -- A changed price with no team (the deployed worker's shape): written, the team kept.
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_pts || '-P1-10', 'event_ticker', v_pts, 'market_kind', 'prop', 'payout', 'binary',
            'subject', 'Test Player', 'stat', 'points', 'line', 9.5, 'label', 'Test Player: 10+', 'yes_bid', 0.56,
            'yes_ask', 0.58, 'volume', 300, 'open_interest', 200, 'taker_fee_at_ask', 0.017052)), now(), true);
        IF v_n <> 1 OR (SELECT team FROM public.kalshi_board WHERE market_ticker = v_pts || '-P1-10')
                       IS DISTINCT FROM 'HOM' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a price change with no team (% written) cleared the team', v_n;
        END IF;
        -- A new team alone (every other field as stored) is written.
        v_n := public.kalshi_board_markets_upsert(jsonb_build_array(jsonb_build_object(
            'market_ticker', v_pts || '-P1-10', 'event_ticker', v_pts, 'market_kind', 'prop', 'payout', 'binary',
            'subject', 'Test Player', 'stat', 'points', 'line', 9.5, 'label', 'Test Player: 10+', 'yes_bid', 0.56,
            'yes_ask', 0.58, 'volume', 300, 'open_interest', 200, 'taker_fee_at_ask', 0.017052, 'team', 'AWY')),
            now(), true);
        IF v_n <> 1 OR (SELECT team FROM public.kalshi_board WHERE market_ticker = v_pts || '-P1-10') <> 'AWY' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a changed team was not written (%)', v_n;
        END IF;
        BEGIN
            UPDATE public.kalshi_board SET team = 'hom' WHERE market_ticker = v_pts || '-P1-10';
            RAISE EXCEPTION 'SELFTEST FAILED: a lower-case team was accepted';
        EXCEPTION WHEN check_violation THEN
            IF SQLERRM NOT LIKE '%kalshi_board_team_shape%' THEN RAISE; END IF;
        END;
        UPDATE public.kalshi_board SET team = 'HOM' WHERE market_ticker = v_pts || '-P1-10';

        -- ------------------------------------------------ 2. a request, its job
        SELECT count(*) INTO v_real FROM public.combo_requests WHERE NOT is_test;
        v_legs := jsonb_build_array(jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes'),
                                    jsonb_build_object('market_ticker', v_pts || '-P1-10', 'side', 'no'));
        INSERT INTO public.combo_requests (legs, status, reason, answered_at, created_at)
        VALUES (v_legs, 'answered', 'smuggled', now(), now() - interval '30 days')
        RETURNING id INTO v_id;
        IF (SELECT status FROM public.combo_requests WHERE id = v_id) <> 'queued'
           OR (SELECT reason FROM public.combo_requests WHERE id = v_id) IS NOT NULL
           OR (SELECT answered_at FROM public.combo_requests WHERE id = v_id) IS NOT NULL
           OR (SELECT created_at FROM public.combo_requests WHERE id = v_id) <> now()
           OR NOT (SELECT is_test FROM public.combo_requests WHERE id = v_id)
           OR (SELECT owner_id FROM public.combo_requests WHERE id = v_id) <> v_owner THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an insert is not forced to a fresh queued test request';
        END IF;
        SELECT count(*) INTO v_n FROM public.job_queue
         WHERE kind = 'price_combo' AND payload = jsonb_build_object('request_id', v_id) AND status = 'queued';
        IF v_n <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % price_combo jobs for the request', v_n;
        END IF;

        -- ------------------------------------------------ 3. malformed legs
        FOREACH v_case IN ARRAY ARRAY[
            '{"market_ticker": "KXA-1", "side": "yes"}',
            '[{"market_ticker": "KXA-1", "side": "yes"}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, {"market_ticker": "KXA-2", "side": "YES"}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, {"market_ticker": "kxa-2", "side": "no"}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, {"market_ticker": 7, "side": "no"}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, {"market_ticker": "KXA-2", "side": "no", "x": 1}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, {"market_ticker": "KXA-2"}]',
            '[{"market_ticker": "KXA-1", "side": "yes"}, "KXA-2"]'
        ] LOOP
            BEGIN
                INSERT INTO public.combo_requests (legs) VALUES (v_case::jsonb);
                RAISE EXCEPTION 'SELFTEST FAILED: malformed legs accepted: %', v_case;
            EXCEPTION WHEN raise_exception THEN
                IF SQLERRM NOT LIKE 'combo_requests: %' THEN RAISE; END IF;
            END;
        END LOOP;
        SELECT jsonb_agg(jsonb_build_object('market_ticker', 'KXA-' || g, 'side', 'yes')) INTO v_legs
          FROM generate_series(1, 26) g;
        BEGIN
            INSERT INTO public.combo_requests (legs) VALUES (v_legs);
            RAISE EXCEPTION 'SELFTEST FAILED: 26 legs accepted';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: 2 to 25 legs%' THEN RAISE; END IF;
        END;
        BEGIN
            INSERT INTO public.combo_requests (legs) VALUES (jsonb_build_array(
                jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes'),
                jsonb_build_object('market_ticker', 'KXNOTONBOARD-1', 'side', 'yes')));
            RAISE EXCEPTION 'SELFTEST FAILED: test and real legs mixed';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: a request never mixes%' THEN RAISE; END IF;
        END;

        -- ------------------------------------------------ 4. settling once
        BEGIN
            UPDATE public.combo_requests SET status = 'answered' WHERE id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: answered without a quote';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: answered needs its quote first%' THEN RAISE; END IF;
        END;
        BEGIN
            UPDATE public.combo_requests SET status = 'refused' WHERE id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: refused with no reason';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: refused needs a reason%' THEN RAISE; END IF;
        END;
        BEGIN
            UPDATE public.combo_requests SET legs = '[]' WHERE id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: legs changed';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: a request''s legs%' THEN RAISE; END IF;
        END;
        -- The quote's CHECKs.
        FOREACH v_case IN ARRAY ARRAY['fair', 'rho', 'reason', 'blocks'] LOOP
            BEGIN
                INSERT INTO public.combo_quotes (request_id, naive_p, fair_p, se, blocks, kalshi_naive_cost,
                                                 combo_status, combo_reason, rho_version, unmodeled_pairs,
                                                 prior_pairs, compute_ms)
                VALUES (v_id, 0.2, CASE WHEN v_case = 'fair' THEN 1.5 ELSE 0.25 END, NULL,
                        CASE WHEN v_case = 'blocks' THEN '[]'::jsonb ELSE '[{"game": "x"}]'::jsonb END, 0.21,
                        'fits', CASE WHEN v_case = 'reason' THEN 'why' END,
                        CASE WHEN v_case = 'rho' THEN 'abc' ELSE v_rho END, 0, 0, 12);
                RAISE EXCEPTION 'SELFTEST FAILED: a bad quote (%) accepted', v_case;
            EXCEPTION WHEN check_violation THEN
                NULL;
            END;
        END LOOP;
        INSERT INTO public.combo_quotes (request_id, naive_p, fair_p, se, blocks, kalshi_naive_cost, combo_status,
                                         combo_reason, rho_version, unmodeled_pairs, prior_pairs, compute_ms,
                                         is_test, computed_at)
        VALUES (v_id, 0.2, 0.25, NULL, '[{"game": "x"}]', 0.21, 'build_manually', 'max 1 leg per event', v_rho,
                0, 1, 12, false, now() - interval '1 day');
        IF NOT (SELECT is_test FROM public.combo_quotes WHERE request_id = v_id)
           OR (SELECT computed_at FROM public.combo_quotes WHERE request_id = v_id) <> now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the quote''s is_test and computed_at are not the guard''s';
        END IF;
        BEGIN
            UPDATE public.combo_requests SET status = 'failed', reason = 'x' WHERE id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: a quoted request failed';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: a quoted request is answered%' THEN RAISE; END IF;
        END;
        UPDATE public.combo_requests SET status = 'answered' WHERE id = v_id;
        IF (SELECT answered_at FROM public.combo_requests WHERE id = v_id) IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: answered_at not set';
        END IF;
        BEGIN
            UPDATE public.combo_requests SET status = 'refused', reason = 'again' WHERE id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: settled twice';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_requests: a request settles once%' THEN RAISE; END IF;
        END;
        BEGIN
            UPDATE public.combo_quotes SET fair_p = 0.3 WHERE request_id = v_id;
            RAISE EXCEPTION 'SELFTEST FAILED: a quote changed';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_quotes: a quote never changes%' THEN RAISE; END IF;
        END;
        -- A second request, refused with a reason; a quote for it afterwards is refused.
        INSERT INTO public.combo_requests (legs) VALUES (jsonb_build_array(
            jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'yes'),
            jsonb_build_object('market_ticker', v_game || '-HOM', 'side', 'no')))
        RETURNING id INTO v_id2;
        UPDATE public.combo_requests SET status = 'refused', reason = 'both sides of one market' WHERE id = v_id2;
        BEGIN
            INSERT INTO public.combo_quotes (request_id, naive_p, fair_p, blocks, combo_status, rho_version,
                                             unmodeled_pairs, prior_pairs, compute_ms)
            VALUES (v_id2, 0.2, 0.2, '[{"game": "x"}]', 'fits', v_rho, 0, 0, 1);
            RAISE EXCEPTION 'SELFTEST FAILED: a quote for a settled request';
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'combo_quotes: request % is not queued%' THEN RAISE; END IF;
        END;

        -- ------------------------------------------------ 5. the prune
        -- A real request of the self-test's own (legs on no board row), so the
        -- tests-only prune has a real row to keep.
        INSERT INTO public.combo_requests (legs) VALUES (
            '[{"market_ticker": "KXSELFTEST076-A", "side": "yes"}, {"market_ticker": "KXSELFTEST076-B", "side": "no"}]');
        IF (SELECT is_test FROM public.combo_requests ORDER BY id DESC LIMIT 1) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: legs on no board row made a test request';
        END IF;
        v_real := v_real + 1;
        -- The 7-day cutoff keeps today's requests.
        v_n := public.combo_prune(now() - interval '7 days', true);
        IF NOT EXISTS (SELECT 1 FROM public.combo_requests WHERE id = v_id) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the 7-day prune took a fresh request';
        END IF;
        v_n := public.combo_prune(now() + interval '1 hour', true);
        IF v_n < 2 OR EXISTS (SELECT 1 FROM public.combo_requests WHERE id IN (v_id, v_id2))
           OR EXISTS (SELECT 1 FROM public.combo_quotes WHERE request_id = v_id)
           OR (SELECT count(*) FROM public.combo_requests WHERE NOT is_test) <> v_real THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the tests-only prune (% deleted)', v_n;
        END IF;

        -- ------------------------------------------------ 6. objects and grants
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '076_combo_pricing') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;
        IF has_table_privilege('anon', 'public.combo_requests', 'SELECT')
           OR has_table_privilege('anon', 'public.combo_quotes', 'SELECT')
           OR NOT has_table_privilege('authenticated', 'public.combo_requests', 'SELECT,INSERT')
           OR has_table_privilege('authenticated', 'public.combo_requests', 'UPDATE')
           OR has_table_privilege('authenticated', 'public.combo_requests', 'DELETE')
           OR has_table_privilege('authenticated', 'public.combo_requests', 'TRUNCATE')
           OR NOT has_table_privilege('authenticated', 'public.combo_quotes', 'SELECT')
           OR has_table_privilege('authenticated', 'public.combo_quotes', 'INSERT')
           OR has_table_privilege('authenticated', 'public.combo_quotes', 'UPDATE')
           OR has_table_privilege('authenticated', 'public.combo_quotes', 'DELETE')
           OR NOT has_table_privilege('valemont_readonly', 'public.combo_requests', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.combo_quotes', 'SELECT')
           OR has_function_privilege('authenticated', 'public.combo_prune(timestamptz, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.combo_prune(timestamptz, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.add_test_kalshi_board(text, timestamptz)', 'EXECUTE')
           OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.combo_requests'::regclass)
           OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.combo_quotes'::regclass)
        THEN
            RAISE EXCEPTION 'SELFTEST FAILED: grants or RLS';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '076 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '076_combo_pricing';
-- (b) SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--      WHERE conrelid IN ('public.kalshi_board'::regclass, 'public.combo_requests'::regclass,
--                         'public.combo_quotes'::regclass) AND conname LIKE ANY (ARRAY['%team%', 'combo%']);
-- (c) SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('combo_requests', 'combo_quotes');
-- (d) SELECT count(*), count(team) FROM kalshi_board;          -- team NULL everywhere until the new worker
-- (e) SELECT count(*) FROM combo_requests; SELECT count(*) FROM combo_quotes;      -- 0, 0
-- (f) md5 of the five function bodies against the scratch copy's (CRs removed).
