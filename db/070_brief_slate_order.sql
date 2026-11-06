-- 070_brief_slate_order.sql — the Brief's Game Day order (owner, 2026-11-06).
--
-- What it changes (no table, column or grant): gameday_brief() only.
-- db/069's text, unchanged except the national-TV games block:
--
--   1. WHO GETS A LINE: db/069's order (NFL first, then the TV tier, then the
--      better rank, then the start time), with one rule ahead of it: the
--      night's best national-TV NBA game (lowest tier, then earliest) always
--      gets a line. Nov 6: PHI at CLE and POR at LAL were national (Prime
--      Video, tier 3), and five CFB games on tiers 1–2 filled the 4 lines
--      golf left, so no NBA line was written.
--   2. THE ORDER THE LINES READ: the NFL first on any night it plays (db/069),
--      then the tier, then the START TIME within a tier. Rank still decides
--      who gets in, never the order: Nov 6's stored slate read 7:00 (Fox),
--      9:15 (No. 22 Arizona, ESPN), 6:00 (ESPN), 6:30 (ESPN2).
--
-- Alabama first, F1 and golf after the games, at most 5 lines, unchanged.
-- The web's Game Day page orders by league (slate.ts DAY_ORDER), not by this
-- function, and is unchanged.
--
-- Self-test (section 3) on paste, rolled back: a Friday and a Sunday in 2031
-- (no real row there).

-- ===================================================================== 1
CREATE OR REPLACE FUNCTION public.gameday_brief(p_day date)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_tz     text := public.gameday_tz();
    v_lo     timestamptz := ((p_day - 1) + time '00:00') AT TIME ZONE v_tz;
    v_hi     timestamptz := ((p_day + 4) + time '00:00') AT TIME ZONE v_tz;
    v_bama   text;
    v_f1     text;
    v_golf   text;
    v_games  text[];
    v_room   integer;
    v_parent text;
    v_name   text;
    v_round  text;
    v_score  jsonb;
    v_lines  text[];
BEGIN
    -- Alabama, whenever they play.
    SELECT 'CFB: ' || public.gameday_title(e, true) || ', '
           || CASE WHEN e.time_tbd THEN 'time TBD' ELSE public.gameday_at(e.starts_at, false) END
           || coalesce(' on ' || e.broadcast[1], '') || '.'
      INTO v_bama
      FROM public.sports_events e
     WHERE NOT e.is_test AND e.league = 'cfb' AND e.kind = 'game'
       AND e.starts_at >= v_lo AND e.starts_at < v_hi
       AND public.gameday_day(e.starts_at, e.time_tbd) = p_day
       AND '333' IN (e.participants->'home'->>'id', e.participants->'away'->>'id')
       AND e.status NOT IN ('canceled', 'postponed')
     ORDER BY e.starts_at
     LIMIT 1;

    -- A race weekend: today's and the rest of the weekend's stored sessions.
    SELECT e.parent_espn_id, e.event_name INTO v_parent, v_name
      FROM public.sports_events e
     WHERE NOT e.is_test AND e.league = 'f1' AND e.starts_at >= v_lo AND e.starts_at < v_hi
       AND public.gameday_day(e.starts_at, e.time_tbd) = p_day AND e.status NOT IN ('canceled', 'postponed')
     ORDER BY e.starts_at
     LIMIT 1;
    IF v_parent IS NOT NULL THEN
        SELECT 'F1, ' || v_name || ': ' || string_agg(
                   CASE s.session WHEN 'Qual' THEN 'Qualifying' ELSE s.session END || ' '
                   || CASE WHEN public.gameday_day(s.starts_at, s.time_tbd) = p_day THEN 'today'
                           ELSE to_char(public.gameday_day(s.starts_at, s.time_tbd), 'FMDy') END
                   || ' ' || public.gameday_at(s.starts_at, s.time_tbd), '; ' ORDER BY s.starts_at) || '.'
          INTO v_f1
          FROM public.sports_events s
         WHERE NOT s.is_test AND s.league = 'f1' AND s.parent_espn_id = v_parent
           AND public.gameday_day(s.starts_at, s.time_tbd) >= p_day
           AND s.status NOT IN ('canceled', 'postponed');
    END IF;

    -- A golf day: the round, the leader after the last scored round, the followed players.
    v_parent := NULL;
    SELECT e.parent_espn_id, e.event_name, e.session INTO v_parent, v_name, v_round
      FROM public.sports_events e
     WHERE NOT e.is_test AND e.league = 'pga' AND e.kind = 'golf_round'
       AND e.starts_at >= v_lo AND e.starts_at < v_hi
       AND public.gameday_day(e.starts_at, e.time_tbd) = p_day AND e.status NOT IN ('canceled', 'postponed')
     ORDER BY e.starts_at
     LIMIT 1;
    IF v_parent IS NOT NULL THEN
        SELECT s.score INTO v_score
          FROM public.sports_events s
         WHERE NOT s.is_test AND s.league = 'pga' AND s.parent_espn_id = v_parent AND s.score IS NOT NULL
         ORDER BY s.starts_at DESC, s.id DESC
         LIMIT 1;
        v_golf := 'Golf, ' || v_name || ': ' || coalesce(v_round, 'round') || ' today';
        IF v_score->'leaders'->0->>'name' IS NOT NULL THEN
            v_golf := v_golf || '; ' || (v_score->'leaders'->0->>'name') || ' leads at '
                      || coalesce(v_score->'leaders'->0->>'to_par', '?')
                      || coalesce(' after R' || (v_score->>'round'), '');
        END IF;
        SELECT v_golf || coalesce('; ' || string_agg(
                   (f->>'name') || ' ' || coalesce(f->>'pos', '?') || coalesce(' (' || (f->>'to_par')
                   || coalesce(', thru ' || (f->>'thru'), '') || ')', ''), '; ' ORDER BY o), '')
          INTO v_golf
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(v_score->'followed') = 'array'
                                         THEN v_score->'followed' ELSE '[]'::jsonb END) WITH ORDINALITY AS x (f, o);
        v_golf := v_golf || '.';
    END IF;

    -- The owner's five's national-TV games (owner, Nov 6, db/070).
    -- Who gets a line: the night's best national NBA game always; then the NFL
    -- on any night it plays (Nov 5), the tier, the better rank, the start time.
    -- The order they read: the NFL first, then the tier, then the start time.
    v_room := 5 - (v_bama IS NOT NULL)::int - (v_f1 IS NOT NULL)::int - (v_golf IS NOT NULL)::int;
    SELECT array_agg(g.line ORDER BY g.nfl DESC, g.tier, g.starts_at, g.lsort, g.id) INTO v_games
      FROM (SELECT c.line, c.nfl, c.tier, c.starts_at, c.lsort, c.id
              FROM (SELECT upper(e.league) || ': ' || public.gameday_title(e, true) || ', '
                           || CASE WHEN e.time_tbd THEN 'time TBD' ELSE public.gameday_at(e.starts_at, false) END
                           || coalesce(' on ' || e.broadcast[1], '') || '.' AS line,
                           (e.league = 'nfl') AS nfl,
                           public.sports_tv_tier(e.broadcast) AS tier,
                           least(coalesce((e.participants->'home'->>'rank')::int, 999),
                                 coalesce((e.participants->'away'->>'rank')::int, 999)) AS best_rank,
                           e.starts_at, l.sort AS lsort, e.id,
                           (e.league = 'nba'
                            AND row_number() OVER (PARTITION BY e.league
                                                   ORDER BY public.sports_tv_tier(e.broadcast), e.starts_at, e.id) = 1
                           ) AS nba_top
                      FROM public.sports_events e
                      JOIN public.sports_leagues l ON l.code = e.league
                     WHERE NOT e.is_test AND l.owner_five AND l.active AND e.kind = 'game' AND e.national_tv
                       AND e.starts_at >= v_lo AND e.starts_at < v_hi
                       AND public.gameday_day(e.starts_at, e.time_tbd) = p_day
                       AND e.status NOT IN ('canceled', 'postponed')
                       -- NULL-safe: a game with no team ids is not Alabama (and must not vanish).
                       AND NOT coalesce(e.league = 'cfb'
                                        AND '333' IN (e.participants->'home'->>'id', e.participants->'away'->>'id'),
                                        false)) c
             ORDER BY c.nba_top DESC, c.nfl DESC, c.tier, c.best_rank, c.starts_at, c.lsort, c.id
             LIMIT greatest(v_room, 0)) g;

    v_lines := array_remove(ARRAY[v_bama], NULL) || coalesce(v_games, '{}'::text[])
               || array_remove(ARRAY[v_f1, v_golf], NULL);
    IF cardinality(v_lines) = 0 THEN
        RETURN NULL;  -- a dead night says nothing
    END IF;
    RETURN jsonb_build_object(
        'about', 'Tonight''s slate for the brief, already worded (at most 5 lines; Alabama first when they '
                 'play, NFL first on any night it plays, then by network and start time; a national NBA game '
                 'always has a line). Use these lines as given.',
        'date', p_day,
        'lines', to_jsonb(v_lines[1:5]));
END;
$$;

-- ===================================================================== 2
INSERT INTO migration_log (name) VALUES ('070_brief_slate_order')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 3
-- Self-test, run on paste, rolled back ('selftest-rollback'). Real-kind rows
-- (the brief ignores test rows) with espn ids 999070xxx on days in 2031,
-- where no real row exists. Each gameday_brief() call only reads.

DO $$
DECLARE
    v_tz  text := public.gameday_tz();
    v_fri date := date '2031-11-14';
    v_sun date := date '2031-11-16';
    v_j   jsonb;
    v_want jsonb;
BEGIN
    BEGIN
        -- ------------------------------------------------------- 1. a Friday like Nov 6
        -- Room 5. The best national NBA game (6:30, tier 3) gets in; the 8:00
        -- CBSSN game and the second NBA game do not; the 5:00 regional NBA
        -- game is not national. Lines read tier, then start time.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('cfb', 'game', '999070001', 'x', (v_fri + time '19:00') AT TIME ZONE v_tz, 'Fox Home', 'Fox Away',
                NULL, ARRAY['Fox'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-1')),
               ('cfb', 'game', '999070002', 'x', (v_fri + time '21:15') AT TIME ZONE v_tz, 'Rk Home', 'Rk Away',
                '{"home": {"id": "901", "rank": 22}, "away": {"id": "902"}}', ARRAY['ESPN'], true,
                'scheduled', 'STATUS_SCHEDULED', md5('070-2')),
               ('cfb', 'game', '999070003', 'x', (v_fri + time '18:00') AT TIME ZONE v_tz, 'Early Home', 'Early Away',
                NULL, ARRAY['ESPN'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-3')),
               ('cfb', 'game', '999070004', 'x', (v_fri + time '18:30') AT TIME ZONE v_tz, 'Mid Home', 'Mid Away',
                NULL, ARRAY['ESPN2'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-4')),
               ('cfb', 'game', '999070005', 'x', (v_fri + time '20:00') AT TIME ZONE v_tz, 'Late Home', 'Late Away',
                NULL, ARRAY['CBSSN'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-5')),
               ('nba', 'game', '999070006', 'x', (v_fri + time '18:30') AT TIME ZONE v_tz, 'Nba1 Home', 'Nba1 Away',
                NULL, ARRAY['Prime Video'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-6')),
               ('nba', 'game', '999070007', 'x', (v_fri + time '21:00') AT TIME ZONE v_tz, 'Nba2 Home', 'Nba2 Away',
                NULL, ARRAY['Prime Video'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-7')),
               ('nba', 'game', '999070008', 'x', (v_fri + time '17:00') AT TIME ZONE v_tz, 'Reg Home', 'Reg Away',
                NULL, ARRAY['MNMT'], false, 'scheduled', 'STATUS_SCHEDULED', md5('070-8'));
        v_j := public.gameday_brief(v_fri);
        v_want := jsonb_build_array(
            'CFB: Fox Away at Fox Home, 7:00 PM on Fox.',
            'CFB: Early Away at Early Home, 6:00 PM on ESPN.',
            'CFB: Mid Away at Mid Home, 6:30 PM on ESPN2.',
            'CFB: Rk Away at No. 22 Rk Home, 9:15 PM on ESPN.',
            'NBA: Nba1 Away at Nba1 Home, 6:30 PM on Prime Video.');
        IF v_j->'lines' IS DISTINCT FROM v_want THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Friday: want %, got %', v_want, v_j->'lines';
        END IF;

        -- ------------------------------------------------------- 2. a Sunday full of NFL
        -- Six national NFL games (ESPN, tier 2) and one NBA game (ABC, tier 1): the NBA game still gets a
        -- line, and reads after the NFL (NFL first on any night it plays),
        -- though it starts first and is on a tier-1 network.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        SELECT 'nfl', 'game', '99907010' || i, 'x', (v_sun + time '12:00' + make_interval(mins => 5 * i)) AT TIME ZONE v_tz,
               'N' || i || ' Home', 'N' || i || ' Away', NULL, ARRAY['ESPN'], true, 'scheduled', 'STATUS_SCHEDULED',
               md5('070-1' || i)
          FROM generate_series(0, 5) AS i;
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('nba', 'game', '999070020', 'x', (v_sun + time '11:00') AT TIME ZONE v_tz, 'Sun Home', 'Sun Away',
                NULL, ARRAY['ABC'], true, 'scheduled', 'STATUS_SCHEDULED', md5('070-20'));
        v_j := public.gameday_brief(v_sun);
        v_want := jsonb_build_array(
            'NFL: N0 Away at N0 Home, 12:00 PM on ESPN.',
            'NFL: N1 Away at N1 Home, 12:05 PM on ESPN.',
            'NFL: N2 Away at N2 Home, 12:10 PM on ESPN.',
            'NFL: N3 Away at N3 Home, 12:15 PM on ESPN.',
            'NBA: Sun Away at Sun Home, 11:00 AM on ABC.');
        IF v_j->'lines' IS DISTINCT FROM v_want THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Sunday: want %, got %', v_want, v_j->'lines';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '070 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '070_brief_slate_order';
-- (b) SELECT position('nba_top' IN prosrc) > 0, prosecdef FROM pg_proc
--      WHERE oid = 'public.gameday_brief(date)'::regprocedure;                  -- t, f
-- (c) SELECT has_function_privilege('anon', 'public.gameday_brief(date)', 'EXECUTE'),
--            has_function_privilege('authenticated', 'public.gameday_brief(date)', 'EXECUTE'),
--            has_function_privilege('valemont_readonly', 'public.gameday_brief(date)', 'EXECUTE');  -- f, t, t
-- (d) SELECT jsonb_array_elements_text(gameday_brief(date '2026-11-06')->'lines');
--      -- Nov 6 would read: Fox 7:00; ESPN 6:00; No. 22 Arizona ESPN 9:15;
--      -- PHI at CLE 6:30 Prime Video; golf (the room was 4).
