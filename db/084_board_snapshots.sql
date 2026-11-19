-- 084_board_snapshots.sql
--
-- Chat 8 Phase 3.5, Workstream A, Step 1, M5 (owner, 2026-11-19: option B,
-- the worker job; docs/research/m5_step0.md). Paste into the Supabase SQL
-- Editor. Runs top-to-bottom, no BEGIN/COMMIT.
--
-- WHY. M5 (docs/betting_science_audit.md §6, frozen) simulates ticket rules
-- on 14 days of board snapshots taken at 10:30 and 16:30 Chicago. The board
-- is pruned and keeps no history, so a snapshot not taken is lost for good,
-- and none can be backfilled (earlier days are gone; anything from
-- 2026-07-01 is the reserve anyway). The worker takes them; the copy runs
-- inside Postgres, so collecting costs no egress.
--
-- WHAT. Research app tables (not ledger, not money: no `mode`), owner
-- read-only, worker-written, pruned after the export:
--   1. research_board_snapshots         one row per snapshot (a real one at
--                                       most once per Chicago date and slot);
--                                       its counts are written once, at the end
--   2. research_board_snapshot_events   per snapshot, each combo-eligible,
--                                       not-started event with a binary market:
--                                       game, series, sport, minutes to start,
--                                       the leg cap (NULL = absent, db/071),
--                                       YES-only, mutually exclusive, the
--                                       Kalshi price's age (seconds)
--   3. research_board_snapshot_legs     each binary market of those events:
--                                       the ticker after the event's (a prop's
--                                       player is the tail before its last '-':
--                                       1:1 with the board's subject on all
--                                       2,093 player-events of Nov 19), kind,
--                                       stat, team, line, YES bid and ask, the
--                                       fee at each side's ask, fair p and its
--                                       tier (leg_consensus_for). Prices and p
--                                       in 1/10,000 (smallint; rounded). The
--                                       Kalshi price's age is the event's
--                                       (db/074: one read per event)
--   4. research_board_eligibility       D13: per snapshot, sport and band of
--                                       hours to start (0, 3, 6, 12, 24+),
--                                       events and markets, all and eligible,
--                                       over every not-started event with a
--                                       binary market
--   5. research_board_snapshot_take(slot, tests, max)  the worker's call.
--      Refuses a real slot already taken and stops after `max` (28) real
--      snapshots: the 14-day window ends by itself.
--   6. research_board_snapshot_prune(tests_only)  the only way rows leave.
--      After the export: prune(false), then a later migration drops the tables.
--   A guard trigger: no UPDATE (except the header's counts, once), no DELETE
--   outside the prune, no TRUNCATE.
--
-- Cost (measured on a full board copy, 2026-11-19; docs/research/m5_step0.md):
-- see the handover. Neither function is executable by anon, authenticated or
-- valemont_readonly; the worker connects as postgres.

-- ===================================================================== 1
CREATE TABLE public.research_board_snapshots (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slot       text NOT NULL CONSTRAINT research_board_snapshots_slot CHECK (slot IN ('am', 'pm')),
    slot_date  date NOT NULL,
    taken_at   timestamptz NOT NULL DEFAULT now(),
    board_at   timestamptz,
    events     integer,
    legs       integer,
    bytes      bigint,
    ms         integer,
    is_test    boolean NOT NULL DEFAULT false,
    CONSTRAINT research_board_snapshots_counts CHECK (
        (events IS NULL) = (legs IS NULL) AND (legs IS NULL) = (bytes IS NULL) AND (bytes IS NULL) = (ms IS NULL))
);
CREATE UNIQUE INDEX research_board_snapshots_one_per_slot
    ON public.research_board_snapshots (slot_date, slot) WHERE NOT is_test;
COMMENT ON TABLE public.research_board_snapshots IS
    'M5 (db/084): one board snapshot at 10:30 or 16:30 Chicago; a real slot at most once; counts written once. Research, pruned after the export.';

CREATE TABLE public.research_board_snapshot_events (
    snapshot_id        integer NOT NULL REFERENCES public.research_board_snapshots (id) ON DELETE CASCADE,
    idx                smallint NOT NULL CONSTRAINT research_board_snapshot_events_idx CHECK (idx > 0),
    event_ticker       text NOT NULL,
    game_ticker        text NOT NULL,
    series_ticker      text NOT NULL,
    sport              text NOT NULL,
    starts_in_min      integer NOT NULL CONSTRAINT research_board_snapshot_events_not_started CHECK (starts_in_min > 0),
    combo_size_max     smallint,
    combo_yes_only     boolean NOT NULL,
    mutually_exclusive boolean,
    kalshi_age_s       integer,
    PRIMARY KEY (snapshot_id, idx)
);
COMMENT ON TABLE public.research_board_snapshot_events IS
    'M5 (db/084): the combo-eligible, not-started events of a snapshot; starts_in_min from taken_at; combo_size_max NULL = absent.';

CREATE TABLE public.research_board_snapshot_legs (
    snapshot_id  integer NOT NULL REFERENCES public.research_board_snapshots (id) ON DELETE CASCADE,
    event_idx    smallint NOT NULL,
    tail         text NOT NULL,
    kind         "char" NOT NULL CONSTRAINT research_board_snapshot_legs_kind CHECK (kind IN ('g', 'p', 'o')),
    stat         text,
    team         text,
    line         real,
    yes_bid      smallint,
    yes_ask      smallint,
    fee_yes      smallint,
    fee_no       smallint,
    p            smallint,
    tier         "char" NOT NULL CONSTRAINT research_board_snapshot_legs_tier CHECK (tier IN ('k', 'e', 'b')),
    CONSTRAINT research_board_snapshot_legs_prices CHECK (
        coalesce(yes_bid BETWEEN 0 AND 10000, true) AND coalesce(yes_ask BETWEEN 0 AND 10000, true)
        AND coalesce(fee_yes BETWEEN 0 AND 10000, true) AND coalesce(fee_no BETWEEN 0 AND 10000, true)
        AND coalesce(p BETWEEN 0 AND 10000, true))
) WITH (fillfactor = 100);
CREATE INDEX research_board_snapshot_legs_snapshot ON public.research_board_snapshot_legs USING brin (snapshot_id);
COMMENT ON TABLE public.research_board_snapshot_legs IS
    'M5 (db/084): a snapshot''s binary markets. market ticker = event_ticker || ''-'' || tail ('''' = the event''s own ticker, db/075). kind g game line, p prop, o other. Prices, fees and p in 1/10,000 (rounded); p and tier (k kalshi, e espn, b books) from leg_consensus_for.';

CREATE TABLE public.research_board_eligibility (
    snapshot_id      integer NOT NULL REFERENCES public.research_board_snapshots (id) ON DELETE CASCADE,
    sport            text NOT NULL,
    hours_band       smallint NOT NULL CONSTRAINT research_board_eligibility_band CHECK (hours_band IN (0, 3, 6, 12, 24)),
    events           integer NOT NULL,
    eligible_events  integer NOT NULL,
    markets          integer NOT NULL,
    eligible_markets integer NOT NULL,
    PRIMARY KEY (snapshot_id, sport, hours_band)
);
COMMENT ON TABLE public.research_board_eligibility IS
    'M5 / D13 (db/084): per snapshot, not-started events with a binary market by sport and hours to start (band lower bound: [0,3), [3,6), [6,12), [12,24), 24+), all and combo-eligible.';

-- ===================================================================== 2
CREATE OR REPLACE FUNCTION public.research_board_snapshot_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '%: no TRUNCATE (research_board_snapshot_prune deletes)', TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('valemont.research_board_prune', true), '') <> 'on' THEN
            RAISE EXCEPTION '%: rows leave only through research_board_snapshot_prune', TG_TABLE_NAME;
        END IF;
        RETURN OLD;
    END IF;
    IF TG_TABLE_NAME = 'research_board_snapshots' THEN
        IF OLD.legs IS NULL AND NEW.legs IS NOT NULL
           AND NEW.id = OLD.id AND NEW.slot = OLD.slot AND NEW.slot_date = OLD.slot_date
           AND NEW.taken_at = OLD.taken_at AND NEW.is_test = OLD.is_test
           AND NEW.board_at IS NOT DISTINCT FROM OLD.board_at THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '%: a snapshot is written once', TG_TABLE_NAME;
END;
$$;
REVOKE ALL ON FUNCTION public.research_board_snapshot_guard() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER research_board_snapshots_guard BEFORE UPDATE OR DELETE ON public.research_board_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_snapshot_events_guard BEFORE UPDATE OR DELETE ON public.research_board_snapshot_events
    FOR EACH ROW EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_snapshot_legs_guard BEFORE UPDATE OR DELETE ON public.research_board_snapshot_legs
    FOR EACH ROW EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_eligibility_guard BEFORE UPDATE OR DELETE ON public.research_board_eligibility
    FOR EACH ROW EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_snapshots_no_truncate BEFORE TRUNCATE ON public.research_board_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_snapshot_events_no_truncate BEFORE TRUNCATE ON public.research_board_snapshot_events
    FOR EACH STATEMENT EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_snapshot_legs_no_truncate BEFORE TRUNCATE ON public.research_board_snapshot_legs
    FOR EACH STATEMENT EXECUTE FUNCTION public.research_board_snapshot_guard();
CREATE TRIGGER research_board_eligibility_no_truncate BEFORE TRUNCATE ON public.research_board_eligibility
    FOR EACH STATEMENT EXECUTE FUNCTION public.research_board_snapshot_guard();

-- ===================================================================== 3
CREATE OR REPLACE FUNCTION public.research_board_snapshot_take(p_slot text, p_tests boolean DEFAULT false,
                                                              p_max integer DEFAULT 28)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_t0       timestamptz := clock_timestamp();
    v_now      timestamptz := now();
    v_date     date := (now() AT TIME ZONE 'America/Chicago')::date;
    v_id       integer;
    v_games    text[];
    v_board_at timestamptz;
    v_events   integer;
    v_legs     integer;
    v_bands    integer;
    v_bytes    bigint;
    v_ms       integer;
BEGIN
    IF p_slot IS NULL OR p_slot NOT IN ('am', 'pm') THEN
        RAISE EXCEPTION 'research_board_snapshot_take: the slot is am or pm, not %', p_slot;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('research_board_snapshot'));
    IF NOT p_tests THEN
        IF EXISTS (SELECT 1 FROM public.research_board_snapshots
                    WHERE NOT is_test AND slot_date = v_date AND slot = p_slot) THEN
            RETURN jsonb_build_object('skipped', 'taken', 'slot_date', v_date, 'slot', p_slot);
        END IF;
        IF (SELECT count(*) FROM public.research_board_snapshots WHERE NOT is_test) >= p_max THEN
            RETURN jsonb_build_object('skipped', 'window complete', 'snapshots', p_max);
        END IF;
    END IF;

    SELECT max(b.fetched_at) INTO v_board_at FROM public.kalshi_board b WHERE b.is_test = p_tests;
    INSERT INTO public.research_board_snapshots (slot, slot_date, board_at, is_test)
    VALUES (p_slot, v_date, v_board_at, p_tests)
    RETURNING id INTO v_id;

    INSERT INTO public.research_board_snapshot_events (snapshot_id, idx, event_ticker, game_ticker, series_ticker,
                                                       sport, starts_in_min, combo_size_max, combo_yes_only,
                                                       mutually_exclusive, kalshi_age_s)
    SELECT v_id, (row_number() OVER (ORDER BY e.event_ticker))::smallint, e.event_ticker, e.game_ticker,
           e.series_ticker, s.sport, ceil(extract(epoch FROM e.starts_at - v_now) / 60)::integer,
           e.combo_size_max, e.combo_yes_only, e.mutually_exclusive,
           (SELECT floor(extract(epoch FROM v_now - max(b.fetched_at)))::integer
              FROM public.kalshi_board b WHERE b.event_ticker = e.event_ticker)
      FROM public.kalshi_board_events e
      JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
     WHERE e.is_test = p_tests AND e.combo_eligible AND e.starts_at > v_now
       AND EXISTS (SELECT 1 FROM public.kalshi_board b WHERE b.event_ticker = e.event_ticker AND b.payout = 'binary');
    GET DIAGNOSTICS v_events = ROW_COUNT;

    SELECT array_agg(DISTINCT ev.game_ticker) INTO v_games
      FROM public.research_board_snapshot_events ev WHERE ev.snapshot_id = v_id;

    INSERT INTO public.research_board_snapshot_legs (snapshot_id, event_idx, tail, kind, stat, team, line,
                                                     yes_bid, yes_ask, fee_yes, fee_no, p, tier)
    SELECT v_id, ev.idx,
           CASE WHEN f.market_ticker = f.event_ticker THEN '' ELSE substr(f.market_ticker, length(f.event_ticker) + 2) END,
           (CASE f.market_kind WHEN 'game_line' THEN 'g' WHEN 'prop' THEN 'p' ELSE 'o' END)::"char",
           f.stat, b.team, f.line::real,
           round(f.yes_bid * 10000)::smallint, round(f.yes_ask * 10000)::smallint,
           round(f.fee_yes * 10000)::smallint, round(f.fee_no * 10000)::smallint,
           round(f.p_consensus * 10000)::smallint, left(f.tier, 1)::"char"
      FROM public.leg_consensus_for(coalesce(v_games, '{}'::text[])) f
      JOIN public.research_board_snapshot_events ev ON ev.snapshot_id = v_id AND ev.event_ticker = f.event_ticker
      JOIN public.kalshi_board b ON b.market_ticker = f.market_ticker AND b.payout = 'binary'
     WHERE f.is_test = p_tests;
    GET DIAGNOSTICS v_legs = ROW_COUNT;

    -- D13: every not-started event with a binary market, eligible or not.
    INSERT INTO public.research_board_eligibility (snapshot_id, sport, hours_band, events, eligible_events,
                                                   markets, eligible_markets)
    SELECT v_id, x.sport, x.band, count(*)::integer, (count(*) FILTER (WHERE x.combo_eligible))::integer,
           sum(x.n)::integer, coalesce(sum(x.n) FILTER (WHERE x.combo_eligible), 0)::integer
      FROM (SELECT s.sport, e.combo_eligible, m.n,
                   (CASE WHEN e.starts_at < v_now + interval '3 hours' THEN 0
                         WHEN e.starts_at < v_now + interval '6 hours' THEN 3
                         WHEN e.starts_at < v_now + interval '12 hours' THEN 6
                         WHEN e.starts_at < v_now + interval '24 hours' THEN 12
                         ELSE 24 END)::smallint AS band
              FROM public.kalshi_board_events e
              JOIN public.kalshi_series_board s ON s.series_ticker = e.series_ticker
              CROSS JOIN LATERAL (SELECT count(*) AS n FROM public.kalshi_board b
                                   WHERE b.event_ticker = e.event_ticker AND b.payout = 'binary') m
             WHERE e.is_test = p_tests AND e.starts_at > v_now AND m.n > 0) x
     GROUP BY x.sport, x.band;
    GET DIAGNOSTICS v_bands = ROW_COUNT;

    SELECT coalesce((SELECT sum(pg_column_size(l.*)) FROM public.research_board_snapshot_legs l WHERE l.snapshot_id = v_id), 0)
         + coalesce((SELECT sum(pg_column_size(ev.*)) FROM public.research_board_snapshot_events ev WHERE ev.snapshot_id = v_id), 0)
         + coalesce((SELECT sum(pg_column_size(g.*)) FROM public.research_board_eligibility g WHERE g.snapshot_id = v_id), 0)
      INTO v_bytes;
    v_ms := (extract(epoch FROM clock_timestamp() - v_t0) * 1000)::integer;
    UPDATE public.research_board_snapshots
       SET events = v_events, legs = v_legs, bytes = v_bytes, ms = v_ms
     WHERE id = v_id;
    RETURN jsonb_build_object('snapshot_id', v_id, 'slot', p_slot, 'slot_date', v_date, 'events', v_events,
                              'legs', v_legs, 'bands', v_bands, 'bytes', v_bytes, 'ms', v_ms, 'board_at', v_board_at,
                              'is_test', p_tests);
END;
$$;
REVOKE ALL ON FUNCTION public.research_board_snapshot_take(text, boolean, integer) FROM PUBLIC, anon, authenticated;
COMMENT ON FUNCTION public.research_board_snapshot_take(text, boolean, integer) IS
    'M5 (db/084): the worker''s 10:30 / 16:30 Chicago snapshot, copied inside Postgres. A real slot once; none after p_max real snapshots.';

CREATE OR REPLACE FUNCTION public.research_board_snapshot_prune(p_tests_only boolean DEFAULT true)
RETURNS integer
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_n integer;
BEGIN
    PERFORM set_config('valemont.research_board_prune', 'on', true);
    DELETE FROM public.research_board_snapshots WHERE is_test OR NOT p_tests_only;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    PERFORM set_config('valemont.research_board_prune', '', true);
    RETURN v_n;
END;
$$;
REVOKE ALL ON FUNCTION public.research_board_snapshot_prune(boolean) FROM PUBLIC, anon, authenticated;
COMMENT ON FUNCTION public.research_board_snapshot_prune(boolean) IS
    'M5 (db/084): the only way snapshot rows leave. Test snapshots by default; false deletes every snapshot (after the export).';

-- ===================================================================== 4
-- RLS: the owner reads; nobody but the worker (postgres) writes.
ALTER TABLE public.research_board_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_board_snapshot_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_board_snapshot_legs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_board_eligibility ENABLE ROW LEVEL SECURITY;
CREATE POLICY research_board_snapshots_owner_read ON public.research_board_snapshots
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
CREATE POLICY research_board_snapshot_events_owner_read ON public.research_board_snapshot_events
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
CREATE POLICY research_board_snapshot_legs_owner_read ON public.research_board_snapshot_legs
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
CREATE POLICY research_board_eligibility_owner_read ON public.research_board_eligibility
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
REVOKE ALL ON TABLE public.research_board_snapshots, public.research_board_snapshot_events,
                    public.research_board_snapshot_legs, public.research_board_eligibility
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.research_board_snapshots, public.research_board_snapshot_events,
                      public.research_board_snapshot_legs, public.research_board_eligibility
    TO authenticated, valemont_readonly;
-- Supabase's default privileges give authenticated USAGE/UPDATE on every new sequence: not this one.
REVOKE ALL ON SEQUENCE public.research_board_snapshots_id_seq FROM PUBLIC, anon, authenticated;

INSERT INTO migration_log (name) VALUES ('084_board_snapshots')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 5
-- Self-test, run on paste, rolled back ('selftest-rollback'). Test rows only:
-- the e2e board (add_test_kalshi_board, the owner simulated as in db/082).
-- The real board is never snapshotted here. A statement that calls a writing
-- function stores its result first and reads back later (db/062's rule).
DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker text := 'selftest-084-' || md5(random()::text);
    v_game   text;
    v_game2  text;
    v_j      jsonb;
    v_id     integer;
    v_n      integer;
    v_m      integer;
    v_ok     boolean;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_game := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        -- a second test game that started an hour ago: none of it may be snapshotted
        PERFORM public.add_test_kalshi_board(v_marker, now() - interval '1 hour');
        -- a third, whose props event is combo-eligible, so a prop leg is snapshotted (the first's is not)
        v_game2 := public.add_test_kalshi_board(v_marker, now() + interval '2 hours');
        UPDATE public.kalshi_board_events SET combo_eligible = true, combo_collections = ARRAY['E2ECOLL-R']
         WHERE event_ticker = v_game2 || 'PTS' AND is_test;

        -- ------------------------------------------------ 1. a test snapshot
        v_j := public.research_board_snapshot_take('am', true);
        v_id := (v_j->>'snapshot_id')::integer;
        IF v_id IS NULL OR (v_j->>'is_test')::boolean IS NOT TRUE THEN
            RAISE EXCEPTION 'SELFTEST FAILED: take returned %', v_j;
        END IF;
        SELECT count(*) INTO v_n FROM public.kalshi_board_events e
         WHERE e.is_test AND e.combo_eligible AND e.starts_at > now()
           AND EXISTS (SELECT 1 FROM public.kalshi_board b WHERE b.event_ticker = e.event_ticker AND b.payout = 'binary');
        SELECT count(*) INTO v_m FROM public.research_board_snapshot_events WHERE snapshot_id = v_id;
        IF v_n = 0 OR v_m <> v_n OR (v_j->>'events')::integer <> v_n THEN
            RAISE EXCEPTION 'SELFTEST FAILED: events % stored, % expected (%)', v_m, v_n, v_j;
        END IF;
        IF EXISTS (SELECT 1 FROM public.research_board_snapshot_events ev
                     JOIN public.kalshi_board_events e ON e.event_ticker = ev.event_ticker
                    WHERE ev.snapshot_id = v_id AND NOT e.combo_eligible) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a not-eligible event was snapshotted';
        END IF;
        IF EXISTS (SELECT 1 FROM public.research_board_snapshot_events ev
                    WHERE ev.snapshot_id = v_id AND ev.starts_in_min NOT BETWEEN 110 AND 121) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: minutes to start';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.research_board_snapshot_events ev
                        WHERE ev.snapshot_id = v_id AND ev.event_ticker = v_game AND ev.combo_yes_only) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the game event or its YES-only flag';
        END IF;

        SELECT count(*) INTO v_n FROM public.kalshi_board b
          JOIN public.kalshi_board_events e ON e.event_ticker = b.event_ticker
         WHERE b.is_test AND b.payout = 'binary' AND e.combo_eligible AND e.starts_at > now();
        SELECT count(*) INTO v_m FROM public.research_board_snapshot_legs WHERE snapshot_id = v_id;
        IF v_n = 0 OR v_m <> v_n OR (v_j->>'legs')::integer <> v_n THEN
            RAISE EXCEPTION 'SELFTEST FAILED: legs % stored, % expected', v_m, v_n;
        END IF;
        -- every leg agrees with leg_consensus_for and the board, to 1/10,000
        SELECT count(*) INTO v_n
          FROM public.research_board_snapshot_legs l
          JOIN public.research_board_snapshot_events ev ON ev.snapshot_id = l.snapshot_id AND ev.idx = l.event_idx
          JOIN public.leg_consensus_for(ARRAY[v_game, v_game2]) f
            ON f.market_ticker = CASE WHEN l.tail = '' THEN ev.event_ticker ELSE ev.event_ticker || '-' || l.tail END
          JOIN public.kalshi_board b ON b.market_ticker = f.market_ticker
         WHERE l.snapshot_id = v_id
           AND l.p IS NOT DISTINCT FROM round(f.p_consensus * 10000)::smallint
           AND l.yes_bid IS NOT DISTINCT FROM round(f.yes_bid * 10000)::smallint
           AND l.yes_ask IS NOT DISTINCT FROM round(f.yes_ask * 10000)::smallint
           AND l.fee_yes IS NOT DISTINCT FROM round(f.fee_yes * 10000)::smallint
           AND l.fee_no IS NOT DISTINCT FROM round(f.fee_no * 10000)::smallint
           AND l.tier = left(f.tier, 1)::"char"
           AND l.kind = (CASE f.market_kind WHEN 'game_line' THEN 'g' WHEN 'prop' THEN 'p' ELSE 'o' END)::"char"
           AND l.line IS NOT DISTINCT FROM f.line::real
           AND l.stat IS NOT DISTINCT FROM f.stat
           AND l.team IS NOT DISTINCT FROM b.team;
        IF v_n <> v_m THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of % legs agree with leg_consensus_for', v_n, v_m;
        END IF;
        IF EXISTS (SELECT 1 FROM public.research_board_snapshot_events ev
                     JOIN public.leg_consensus_for(ARRAY[v_game, v_game2]) f ON f.event_ticker = ev.event_ticker
                    WHERE ev.snapshot_id = v_id
                      AND ev.kalshi_age_s IS DISTINCT FROM floor(extract(epoch FROM now() - f.kalshi_at))::integer) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an event''s price age differs from leg_consensus_for''s kalshi_at';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.research_board_snapshot_legs WHERE snapshot_id = v_id AND kind = 'p')
           OR NOT EXISTS (SELECT 1 FROM public.research_board_snapshot_legs WHERE snapshot_id = v_id AND kind = 'g') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a prop and a game line are both snapshotted';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.research_board_snapshot_legs WHERE snapshot_id = v_id AND tier = 'b') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no books-tier leg (the helper writes one)';
        END IF;

        -- D13: the not-eligible event is counted there, not in the legs
        SELECT sum(events), sum(eligible_events) INTO v_n, v_m FROM public.research_board_eligibility WHERE snapshot_id = v_id;
        IF v_n IS DISTINCT FROM (SELECT count(*)::integer FROM public.kalshi_board_events e
                                  WHERE e.is_test AND e.starts_at > now()
                                    AND EXISTS (SELECT 1 FROM public.kalshi_board b
                                                 WHERE b.event_ticker = e.event_ticker AND b.payout = 'binary'))
           OR v_m IS DISTINCT FROM (v_j->>'events')::integer OR v_n <= v_m THEN
            RAISE EXCEPTION 'SELFTEST FAILED: eligibility % events, % eligible', v_n, v_m;
        END IF;
        IF EXISTS (SELECT 1 FROM public.research_board_eligibility WHERE snapshot_id = v_id AND hours_band <> 0) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a 2-hour event outside band 0';
        END IF;
        IF (SELECT legs IS NULL OR bytes <= 0 OR ms IS NULL OR board_at IS NULL FROM public.research_board_snapshots WHERE id = v_id) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the header counts';
        END IF;

        -- ------------------------------------------------ 2. written once
        v_ok := false;
        BEGIN
            UPDATE public.research_board_snapshot_legs SET p = 1 WHERE snapshot_id = v_id;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%written once%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a leg was updated'; END IF;
        v_ok := false;
        BEGIN
            UPDATE public.research_board_snapshots SET legs = 0 WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%written once%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: the counts were rewritten'; END IF;
        v_ok := false;
        BEGIN
            DELETE FROM public.research_board_snapshots WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%only through research_board_snapshot_prune%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a snapshot was deleted outside the prune'; END IF;
        v_ok := false;
        BEGIN
            TRUNCATE public.research_board_snapshots, public.research_board_snapshot_events,
                     public.research_board_snapshot_legs, public.research_board_eligibility;
        EXCEPTION WHEN raise_exception THEN v_ok := SQLERRM LIKE '%no TRUNCATE%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: TRUNCATE'; END IF;

        -- ------------------------------------------------ 3. a real slot once; the window ends by itself
        INSERT INTO public.research_board_snapshots (slot, slot_date, is_test)
        VALUES ('pm', (now() AT TIME ZONE 'America/Chicago')::date, false);
        v_j := public.research_board_snapshot_take('pm', false);
        IF v_j->>'skipped' IS DISTINCT FROM 'taken' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a real slot taken twice (%)', v_j;
        END IF;
        v_j := public.research_board_snapshot_take('am', false, 1);
        IF v_j->>'skipped' IS DISTINCT FROM 'window complete' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the window did not end (%)', v_j;
        END IF;
        v_ok := false;
        BEGIN
            v_j := public.research_board_snapshot_take('noon', true);
        EXCEPTION WHEN raise_exception THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an unknown slot'; END IF;

        -- ------------------------------------------------ 4. the prune
        v_n := public.research_board_snapshot_prune();
        IF v_n <> 1 OR EXISTS (SELECT 1 FROM public.research_board_snapshot_legs WHERE snapshot_id = v_id)
           OR EXISTS (SELECT 1 FROM public.research_board_eligibility WHERE snapshot_id = v_id)
           OR NOT EXISTS (SELECT 1 FROM public.research_board_snapshots WHERE NOT is_test) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: prune(tests) removed % (the real row must stay)', v_n;
        END IF;
        v_n := public.research_board_snapshot_prune(false);
        IF v_n <> 1 OR EXISTS (SELECT 1 FROM public.research_board_snapshots) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: prune(all) removed %', v_n;
        END IF;
        IF coalesce(current_setting('valemont.research_board_prune', true), '') = 'on' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the prune flag outlived the prune';
        END IF;

        -- ------------------------------------------------ 5. RLS and grants
        IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname IN ('research_board_snapshots', 'research_board_snapshot_events',
                          'research_board_snapshot_legs', 'research_board_eligibility') AND NOT c.relrowsecurity)
           OR (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename LIKE 'research_board_%'
                  AND cmd = 'SELECT') <> 4
           OR (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename LIKE 'research_board_%') <> 4
           OR has_table_privilege('anon', 'public.research_board_snapshot_legs', 'SELECT')
           OR has_table_privilege('authenticated', 'public.research_board_snapshot_legs', 'INSERT')
           OR has_table_privilege('authenticated', 'public.research_board_snapshots', 'UPDATE')
           OR has_table_privilege('authenticated', 'public.research_board_eligibility', 'DELETE')
           OR NOT has_table_privilege('authenticated', 'public.research_board_snapshot_legs', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.research_board_snapshot_legs', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.research_board_snapshot_events', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.research_board_snapshots', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.research_board_eligibility', 'SELECT')
           OR has_sequence_privilege('authenticated', 'public.research_board_snapshots_id_seq', 'USAGE')
           OR has_sequence_privilege('authenticated', 'public.research_board_snapshots_id_seq', 'UPDATE')
           OR has_function_privilege('anon', 'public.research_board_snapshot_take(text, boolean, integer)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.research_board_snapshot_take(text, boolean, integer)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.research_board_snapshot_take(text, boolean, integer)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.research_board_snapshot_prune(boolean)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.research_board_snapshot_prune(boolean)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: RLS or grants';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '084 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '084_board_snapshots';
-- (b) SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
--       FROM pg_class c WHERE c.relname LIKE 'research_board_%' AND c.relkind = 'r';
-- (c) has_table_privilege / has_function_privilege for anon, authenticated, valemont_readonly as in step 5.
-- (d) SELECT count(*) FROM research_board_snapshots;   -- 0 until the worker's first 10:30 or 16:30 run
