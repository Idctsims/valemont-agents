-- 069_nfl_first_marker_closed.sql — two owner decisions (2026-11-05).
--
-- What it changes (no table, column or grant):
--   1. e2e_marker_ok() fails CLOSED: false, never NULL. db/051's body
--      compared the marker's hash with "(SELECT … LIMIT 1)", which is NULL
--      while e2e_markers is empty, so the whole AND was NULL, and every
--      caller written "IF NOT public.e2e_marker_ok(…) THEN RAISE" let the
--      call through. Production held 1 marker row (read Nov 5), so it was
--      never open there; this makes it hold by construction. Same name,
--      signature, SECURITY DEFINER and search_path; CREATE OR REPLACE keeps
--      its grants (service_role only; the add_test_* definers call it).
--   2. gameday_brief(): NFL games lead the slate on ANY night the NFL plays
--      (Thursday, Sunday, Monday, late-season Saturdays), not only on a
--      Sunday. Alabama is still always first. Otherwise db/068's text,
--      unchanged (generated from it with only that rule replaced). The web's
--      day order changes the same way (apps/web/src/lib/gameday/slate.ts).
--
-- Self-test (section 4) on paste, rolled back: the marker true and false
-- both ways, an EMPTY markers table answering false (not NULL), and the
-- brief on 2031 days (no real row there).

-- ===================================================================== 1
-- The marker check, fail-closed.

CREATE OR REPLACE FUNCTION public.e2e_marker_ok(p_marker text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT coalesce(
        p_marker IS NOT NULL AND auth.uid() IS NOT NULL AND public.is_owner()
        AND encode(sha256(convert_to(p_marker, 'UTF8')), 'hex')
            = (SELECT m.marker_sha256 FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1),
        false);
$$;

-- ===================================================================== 2
-- The Brief's slate: NFL first on any night it plays.

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

    -- The owner's five's national-TV games, NFL first on any night it plays (owner, Nov 5).
    v_room := 5 - (v_bama IS NOT NULL)::int - (v_f1 IS NOT NULL)::int - (v_golf IS NOT NULL)::int;
    SELECT array_agg(g.line ORDER BY g.ord) INTO v_games
      FROM (SELECT upper(e.league) || ': ' || public.gameday_title(e, true) || ', '
                   || CASE WHEN e.time_tbd THEN 'time TBD' ELSE public.gameday_at(e.starts_at, false) END
                   || coalesce(' on ' || e.broadcast[1], '') || '.' AS line,
                   row_number() OVER (ORDER BY (e.league = 'nfl') DESC,
                                               public.sports_tv_tier(e.broadcast),
                                               least(coalesce((e.participants->'home'->>'rank')::int, 999),
                                                     coalesce((e.participants->'away'->>'rank')::int, 999)),
                                               e.starts_at, l.sort, e.id) AS ord
              FROM public.sports_events e
              JOIN public.sports_leagues l ON l.code = e.league
             WHERE NOT e.is_test AND l.owner_five AND l.active AND e.kind = 'game' AND e.national_tv
               AND e.starts_at >= v_lo AND e.starts_at < v_hi
               AND public.gameday_day(e.starts_at, e.time_tbd) = p_day
               AND e.status NOT IN ('canceled', 'postponed')
               -- NULL-safe: a game with no team ids is not Alabama (and must not vanish).
               AND NOT coalesce(e.league = 'cfb'
                                AND '333' IN (e.participants->'home'->>'id', e.participants->'away'->>'id'), false)
             ORDER BY 2
             LIMIT greatest(v_room, 0)) g;

    v_lines := array_remove(ARRAY[v_bama], NULL) || coalesce(v_games, '{}'::text[])
               || array_remove(ARRAY[v_f1, v_golf], NULL);
    IF cardinality(v_lines) = 0 THEN
        RETURN NULL;  -- a dead night says nothing
    END IF;
    RETURN jsonb_build_object(
        'about', 'Tonight''s slate for the brief, already worded (at most 5 lines; Alabama first when they '
                 'play, NFL first on any night it plays). Use these lines as given.',
        'date', p_day,
        'lines', to_jsonb(v_lines[1:5]));
END;
$$;

-- ===================================================================== 3
INSERT INTO migration_log (name) VALUES ('069_nfl_first_marker_closed')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 4
-- Self-test, run on paste, rolled back ('selftest-rollback'). The empty
-- markers case disables e2e_markers' append-only trigger and deletes inside
-- this block; the rollback restores both (DDL is transactional). Real rows
-- use espn ids 999069xxx on days in 2031 (no real row there). Every
-- statement that calls a writing function stores its result first and reads
-- back in a later statement (db/062's rule).

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_tz     text := public.gameday_tz();
    v_marker text := 'selftest-069-' || gen_random_uuid();
    v_thu    date := date '2031-11-13';
    v_mon    date := date '2031-11-17';
    v_wed    date := date '2031-11-19';
    v_ok     boolean;
    v_b      boolean;
    v_n      integer;
    v_j      jsonb;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;

        -- --------------------------------------------------------- 1. the marker, no session
        v_b := public.e2e_marker_ok('anything');
        IF v_b IS NULL OR v_b THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no session must answer false, got %', v_b;
        END IF;

        -- --------------------------------------------------------- 2. the owner, a marker on file
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_b := public.e2e_marker_ok(v_marker);
        IF v_b IS NOT TRUE THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the right marker must answer true, got %', v_b;
        END IF;
        v_b := public.e2e_marker_ok('not-the-marker');
        IF v_b IS NULL OR v_b THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a wrong marker must answer false, got %', v_b;
        END IF;
        v_b := public.e2e_marker_ok(NULL);
        IF v_b IS NULL OR v_b THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a NULL marker must answer false, got %', v_b;
        END IF;
        -- A signed-in stranger with the right marker: false.
        PERFORM set_config('request.jwt.claims',
                           jsonb_build_object('sub', '00000000-0000-4000-8000-000000000069', 'role', 'authenticated')::text,
                           true);
        PERFORM set_config('request.jwt.claim.sub', '00000000-0000-4000-8000-000000000069', true);
        v_b := public.e2e_marker_ok(v_marker);
        IF v_b IS NULL OR v_b THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a non-owner with the right marker must answer false, got %', v_b;
        END IF;
        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);

        -- --------------------------------------------------------- 3. an EMPTY markers table
        ALTER TABLE public.e2e_markers DISABLE TRIGGER e2e_markers_immutable;
        DELETE FROM public.e2e_markers;
        ALTER TABLE public.e2e_markers ENABLE TRIGGER e2e_markers_immutable;
        SELECT count(*) INTO v_n FROM public.e2e_markers;
        IF v_n <> 0 THEN RAISE EXCEPTION 'SELFTEST FAILED: the markers table is not empty (%)', v_n; END IF;
        v_b := public.e2e_marker_ok(v_marker);
        IF v_b IS NULL OR v_b THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an empty markers table must answer false (fail closed), got %', v_b;
        END IF;
        -- And a marker-gated helper now refuses (db/051's would have let it through).
        v_ok := false;
        BEGIN
            PERFORM public.add_test_sports_event(v_marker, 'nfl', 'H', 'A', now());
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'add_test_sports_event: the owner and a verified test marker only%';
        END;
        IF NOT v_ok THEN
            RAISE EXCEPTION 'SELFTEST FAILED: add_test_sports_event ran with no marker on file';
        END IF;
        PERFORM set_config('request.jwt.claims', '', true);
        PERFORM set_config('request.jwt.claim.sub', '', true);

        -- --------------------------------------------------------- 4. grants unchanged
        IF has_function_privilege('anon', 'public.e2e_marker_ok(text)', 'EXECUTE')
           OR has_function_privilege('authenticated', 'public.e2e_marker_ok(text)', 'EXECUTE')
           OR has_function_privilege('valemont_readonly', 'public.e2e_marker_ok(text)', 'EXECUTE')
           OR NOT (SELECT prosecdef FROM pg_proc WHERE oid = 'public.e2e_marker_ok(text)'::regprocedure) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: e2e_marker_ok grants or SECURITY DEFINER changed';
        END IF;

        -- --------------------------------------------------------- 5. the brief: NFL first any night
        -- Thursday: an NFL game on ESPN+ (tier 3) leads a CFB game on ABC (tier 1).
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('cfb', 'game', '999069001', 'x', (v_thu + time '18:00') AT TIME ZONE v_tz, 'Thu CFB Home',
                'Thu CFB Away', NULL, ARRAY['ABC'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-1')),
               ('nfl', 'game', '999069002', 'x', (v_thu + time '19:15') AT TIME ZONE v_tz, 'Thu NFL Home',
                'Thu NFL Away', NULL, ARRAY['ESPN+'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-2'));
        v_j := public.gameday_brief(v_thu);
        IF v_j->'lines'->>0 <> 'NFL: Thu NFL Away at Thu NFL Home, 7:15 PM on ESPN+.'
           OR v_j->'lines'->>1 <> 'CFB: Thu CFB Away at Thu CFB Home, 6:00 PM on ABC.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Thursday must lead with the NFL: %', v_j->'lines';
        END IF;
        -- Monday: Alabama first, then the NFL, then the rest.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('cfb', 'game', '999069003', 'x', (v_mon + time '18:00') AT TIME ZONE v_tz, 'Mon Opp',
                'Alabama Crimson Tide', '{"home": {"id": "2"}, "away": {"id": "333"}}', ARRAY['ESPN'], true,
                'scheduled', 'STATUS_SCHEDULED', md5('069-3')),
               ('nba', 'game', '999069004', 'x', (v_mon + time '18:30') AT TIME ZONE v_tz, 'Mon NBA Home',
                'Mon NBA Away', NULL, ARRAY['ABC'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-4')),
               ('nfl', 'game', '999069005', 'x', (v_mon + time '19:15') AT TIME ZONE v_tz, 'Mon NFL Home',
                'Mon NFL Away', NULL, ARRAY['ESPN'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-5'));
        v_j := public.gameday_brief(v_mon);
        IF v_j->'lines'->>0 <> 'CFB: Alabama Crimson Tide at Mon Opp, 6:00 PM on ESPN.'
           OR v_j->'lines'->>1 <> 'NFL: Mon NFL Away at Mon NFL Home, 7:15 PM on ESPN.'
           OR v_j->'lines'->>2 <> 'NBA: Mon NBA Away at Mon NBA Home, 6:30 PM on ABC.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Monday must read Alabama, NFL, then the rest: %', v_j->'lines';
        END IF;
        -- Wednesday, no NFL: tiers decide as before.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('nba', 'game', '999069006', 'x', (v_wed + time '18:00') AT TIME ZONE v_tz, 'Wed NBA Home',
                'Wed NBA Away', NULL, ARRAY['ESPN'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-6')),
               ('cfb', 'game', '999069007', 'x', (v_wed + time '19:00') AT TIME ZONE v_tz, 'Wed CFB Home',
                'Wed CFB Away', NULL, ARRAY['ABC'], true, 'scheduled', 'STATUS_SCHEDULED', md5('069-7'));
        v_j := public.gameday_brief(v_wed);
        IF v_j->'lines'->>0 <> 'CFB: Wed CFB Away at Wed CFB Home, 7:00 PM on ABC.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: with no NFL the bigger network leads: %', v_j->'lines';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '069 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '069_nfl_first_marker_closed';
-- (b) SELECT prosecdef, has_function_privilege('anon', oid, 'EXECUTE'),
--            has_function_privilege('authenticated', oid, 'EXECUTE')
--       FROM pg_proc WHERE oid = 'public.e2e_marker_ok(text)'::regprocedure;     -- t, f, f
-- (c) SELECT position('coalesce' IN prosrc) > 0 FROM pg_proc
--      WHERE oid = 'public.e2e_marker_ok(text)'::regprocedure;                   -- t
-- (d) SELECT position('v_sunday' IN prosrc) = 0 FROM pg_proc
--      WHERE oid = 'public.gameday_brief(date)'::regprocedure;                   -- t
-- (e) SELECT count(*) FROM e2e_markers;                                          -- unchanged (1 on Nov 5)
