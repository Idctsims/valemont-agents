-- 065_sports_events.sql — the sports data layer (Chat 7 Phase 1;
-- docs/sports_data.md).
--
-- What it adds:
--   1. sports_leagues: the league registry the worker reads each schedule
--      run: code, sport, ESPN path and params, fetch mode (per_day: ESPN
--      refuses a date range for team sports; range), kind, owner_five, sort,
--      active. 14 leagues seeded; the owner's five (cfb, nfl, nba, f1, pga)
--      first.
--   2. sports_events: one row per game, race session, golf round, bout or
--      tennis match. UNIQUE (league, espn_id); (league, kind) must match the
--      registry (composite foreign key). An APP table, not a ledger table:
--      the worker upserts it in place, writing a row only when its
--      content_hash changes, and never touches kalshi_event_ticker (Phase 2)
--      or a test row. A test row is exactly one whose espn_id starts 'e2e-'
--      (CHECK), written only by add_test_sports_event (the verified e2e
--      marker).
--   3. Indexes for "today by league" (league, starts_at) and "live now"
--      (starts_at WHERE status IN ('live', 'delayed')).
--   4. ctx_sports_today(): registered as context provider `sports_today`
--      (sort 46), NOT a brief section (Phase 3 makes the Brief's slate).
--   5. Access: RLS on both tables; the owner reads (SELECT only); the worker
--      writes through DATABASE_URL; anon nothing; valemont_readonly reads.
--
-- No trigger. A self-test runs on paste (section 7), rolled back, and raises
-- on any wrong result, so a bad paste applies nothing.

-- ===================================================================== 1
-- The league registry.

CREATE TABLE IF NOT EXISTS sports_leagues (
    code         TEXT     PRIMARY KEY CONSTRAINT sports_leagues_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,15}$'),
    label        TEXT     NOT NULL CHECK (char_length(label) BETWEEN 1 AND 60),
    sport        TEXT     NOT NULL CHECK (sport IN ('football', 'basketball', 'hockey', 'baseball', 'soccer',
                                                    'tennis', 'golf', 'racing', 'mma')),
    espn_path    TEXT     NOT NULL UNIQUE CONSTRAINT sports_leagues_path_format
                                          CHECK (espn_path ~ '^[a-z]+/[a-z0-9.-]+$'),
    espn_params  JSONB    NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(espn_params) = 'object'),
    fetch_mode   TEXT     NOT NULL CHECK (fetch_mode IN ('per_day', 'range')),
    kind         TEXT     NOT NULL CHECK (kind IN ('game', 'race_session', 'golf_round', 'bout', 'match')),
    owner_five   BOOLEAN  NOT NULL DEFAULT false,
    sort         INTEGER  NOT NULL,
    active       BOOLEAN  NOT NULL DEFAULT true,
    CONSTRAINT sports_leagues_code_kind UNIQUE (code, kind)
);

INSERT INTO sports_leagues (code, label, sport, espn_path, espn_params, fetch_mode, kind, owner_five, sort)
SELECT v.code, v.label, v.sport, v.espn_path, v.espn_params::jsonb, v.fetch_mode, v.kind, v.owner_five, v.sort
  FROM (VALUES
    ('cfb',  'College Football',          'football',   'football/college-football',          '{"groups": "80", "limit": "500"}', 'per_day', 'game',         true,  10),
    ('nfl',  'NFL',                       'football',   'football/nfl',                       '{}',                               'per_day', 'game',         true,  20),
    ('nba',  'NBA',                       'basketball', 'basketball/nba',                     '{}',                               'per_day', 'game',         true,  30),
    ('f1',   'Formula 1',                 'racing',     'racing/f1',                          '{}',                               'range',   'race_session', true,  40),
    ('pga',  'PGA Tour',                  'golf',       'golf/pga',                           '{}',                               'range',   'golf_round',   true,  50),
    ('mcbb', 'Men''s College Basketball', 'basketball', 'basketball/mens-college-basketball', '{"groups": "50", "limit": "500"}', 'per_day', 'game',         false, 60),
    ('nhl',  'NHL',                       'hockey',     'hockey/nhl',                         '{}',                               'per_day', 'game',         false, 70),
    ('mlb',  'MLB',                       'baseball',   'baseball/mlb',                       '{}',                               'per_day', 'game',         false, 80),
    ('epl',  'Premier League',            'soccer',     'soccer/eng.1',                       '{}',                               'per_day', 'game',         false, 90),
    ('mls',  'MLS',                       'soccer',     'soccer/usa.1',                       '{}',                               'per_day', 'game',         false, 100),
    ('ucl',  'Champions League',          'soccer',     'soccer/uefa.champions',              '{}',                               'per_day', 'game',         false, 110),
    ('atp',  'ATP',                       'tennis',     'tennis/atp',                         '{}',                               'range',   'match',        false, 120),
    ('wta',  'WTA',                       'tennis',     'tennis/wta',                         '{}',                               'range',   'match',        false, 130),
    ('ufc',  'UFC',                       'mma',        'mma/ufc',                            '{}',                               'range',   'bout',         false, 140)
  ) AS v (code, label, sport, espn_path, espn_params, fetch_mode, kind, owner_five, sort)
ON CONFLICT (code) DO NOTHING;

-- ===================================================================== 2
-- The events.

CREATE TABLE IF NOT EXISTS sports_events (
    id                   BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league               TEXT         NOT NULL,
    kind                 TEXT         NOT NULL,
    espn_id              TEXT         NOT NULL CONSTRAINT sports_events_espn_id_format
                                               CHECK (espn_id ~ '^[A-Za-z0-9-]{1,48}$'),
    parent_espn_id       TEXT         CONSTRAINT sports_events_parent_format
                                      CHECK (parent_espn_id IS NULL OR parent_espn_id ~ '^[A-Za-z0-9-]{1,48}$'),
    event_name           TEXT         NOT NULL CHECK (char_length(event_name) BETWEEN 1 AND 200),
    session              TEXT         CHECK (session IS NULL OR char_length(session) BETWEEN 1 AND 60),
    starts_at            TIMESTAMPTZ  NOT NULL,
    time_tbd             BOOLEAN      NOT NULL DEFAULT false,
    home                 TEXT         CHECK (home IS NULL OR char_length(home) BETWEEN 1 AND 120),
    away                 TEXT         CHECK (away IS NULL OR char_length(away) BETWEEN 1 AND 120),
    participants         JSONB        CHECK (participants IS NULL OR jsonb_typeof(participants) IN ('object', 'array')),
    broadcast            TEXT[]       NOT NULL DEFAULT '{}',
    national_tv          BOOLEAN      NOT NULL DEFAULT false,
    venue                TEXT         CHECK (venue IS NULL OR char_length(venue) <= 200),
    status               TEXT         NOT NULL CONSTRAINT sports_events_status_check
                                      CHECK (status IN ('scheduled', 'live', 'delayed', 'suspended', 'final',
                                                        'postponed', 'canceled')),
    espn_status          TEXT         NOT NULL CHECK (espn_status ~ '^[A-Z0-9_]{1,60}$'),
    status_detail        TEXT         CHECK (status_detail IS NULL OR char_length(status_detail) <= 120),
    period               INTEGER      CHECK (period IS NULL OR period >= 0),
    clock                TEXT         CHECK (clock IS NULL OR char_length(clock) <= 30),
    score                JSONB        CHECK (score IS NULL OR jsonb_typeof(score) = 'object'),
    kalshi_event_ticker  TEXT         CHECK (kalshi_event_ticker IS NULL
                                             OR kalshi_event_ticker ~ '^[A-Z0-9][A-Z0-9._-]{1,80}$'),
    content_hash         TEXT         NOT NULL CHECK (content_hash ~ '^[0-9a-f]{32,64}$'),
    is_test              BOOLEAN      NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT sports_events_league_espn_id UNIQUE (league, espn_id),
    CONSTRAINT sports_events_league_kind FOREIGN KEY (league, kind) REFERENCES sports_leagues (code, kind),
    -- A test row is exactly one with an e2e- id: a real ESPN id never starts so.
    CONSTRAINT sports_events_test_ids CHECK ((espn_id LIKE 'e2e-%') = is_test),
    CONSTRAINT sports_events_game_teams CHECK (kind <> 'game' OR (home IS NOT NULL AND away IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS sports_events_league_starts ON sports_events (league, starts_at);
CREATE INDEX IF NOT EXISTS sports_events_live ON sports_events (starts_at) WHERE status IN ('live', 'delayed');

COMMENT ON TABLE sports_events IS
    'ESPN schedule, status, score and broadcasters (docs/sports_data.md). Worker upserts by (league, espn_id), '
    'only when content_hash changes; never touches kalshi_event_ticker or a test row.';

-- ===================================================================== 3
-- Test rows (e2e). Owner only, the verified e2e marker (db/051 e2e_marker_ok).

CREATE OR REPLACE FUNCTION public.add_test_sports_event(p_marker text, p_league text, p_home text, p_away text,
                                                        p_starts_at timestamptz, p_status text DEFAULT 'scheduled')
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_kind text;
    v_id   bigint;
BEGIN
    IF NOT public.e2e_marker_ok(p_marker) THEN
        RAISE EXCEPTION 'add_test_sports_event: the owner and a verified test marker only';
    END IF;
    SELECT kind INTO v_kind FROM public.sports_leagues WHERE code = p_league;
    IF v_kind IS NULL THEN
        RAISE EXCEPTION 'add_test_sports_event: unknown league %', p_league;
    END IF;
    INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                      espn_status, broadcast, national_tv, content_hash, is_test)
    VALUES (p_league, v_kind, 'e2e-' || gen_random_uuid(),
            coalesce(p_away || ' at ' || p_home, p_home, p_away, 'Test event'),
            p_starts_at, p_home, p_away, p_status,
            CASE p_status WHEN 'live' THEN 'STATUS_IN_PROGRESS' WHEN 'final' THEN 'STATUS_FINAL'
                          ELSE 'STATUS_SCHEDULED' END,
            ARRAY['Test TV'], true, md5(gen_random_uuid()::text), true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 4
-- Context provider: today's events (America/Chicago), not a brief section.

CREATE OR REPLACE FUNCTION public.ctx_sports_today()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (
        SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago') AS tz,
               public.owner_today() AS d
    ),
    ev AS (
        SELECT e.league, e.kind, e.status, e.status_detail, e.score, e.broadcast, e.time_tbd, e.starts_at,
               l.owner_five, l.sort,
               CASE WHEN e.home IS NOT NULL AND e.away IS NOT NULL THEN e.away || ' at ' || e.home
                    WHEN jsonb_typeof(e.participants) = 'array'
                        THEN (SELECT string_agg(p->>'name', ' vs ') FROM jsonb_array_elements(e.participants) p)
                             || ' · ' || e.event_name
                    ELSE e.event_name || coalesce(' · ' || e.session, '')
               END AS what,
               CASE WHEN e.time_tbd THEN 'TBD' ELSE to_char(e.starts_at AT TIME ZONE z.tz, 'HH24:MI') END AS at
          FROM public.sports_events e
          JOIN public.sports_leagues l ON l.code = e.league
         CROSS JOIN z
         WHERE NOT e.is_test AND l.active
           AND ((e.starts_at AT TIME ZONE z.tz)::date = z.d
                OR (e.status IN ('live', 'delayed') AND e.starts_at > now() - interval '1 day'))
    )
    SELECT jsonb_build_object(
        'about', 'Sports today (from ESPN): counts by league, what is live now, and the owner''s five '
                 '(CFB, NFL, NBA, F1, golf) today in the owner''s local time; tv lists the broadcasters '
                 'as ESPN gives them. Game Day ordering comes later.',
        'date', (SELECT d FROM z),
        'counts', coalesce((SELECT jsonb_object_agg(league, n) FROM (SELECT league, count(*) AS n FROM ev
                                                                     GROUP BY league) c), '{}'::jsonb),
        'live', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                              'league', x.league, 'what', public.ctx_trim(x.what, 120),
                              'detail', x.status_detail, 'score', x.score)) ORDER BY x.sort, x.starts_at)
                            FROM (SELECT * FROM ev WHERE status IN ('live', 'delayed')
                                   ORDER BY sort, starts_at LIMIT 15) x), '[]'::jsonb),
        'owner_five', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                    'league', x.league, 'what', public.ctx_trim(x.what, 120), 'at', x.at,
                                    'tv', to_jsonb(x.broadcast[1:2]), 'status', x.status)
                                    ORDER BY x.starts_at, x.sort)
                                  FROM (SELECT * FROM ev WHERE owner_five
                                         ORDER BY starts_at, sort LIMIT 30) x), '[]'::jsonb),
        'owner_five_more', greatest((SELECT count(*) FROM ev WHERE owner_five) - 30, 0)
    );
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('sports_today', 'Sports today', 'public.ctx_sports_today()'::regprocedure, false, 46)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 5
-- Access.

ALTER TABLE sports_leagues ENABLE ROW LEVEL SECURITY;
ALTER TABLE sports_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON sports_leagues, sports_events FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE sports_events_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON sports_leagues, sports_events TO authenticated;
DROP POLICY IF EXISTS sports_leagues_owner_read ON sports_leagues;
CREATE POLICY sports_leagues_owner_read ON sports_leagues FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS sports_events_owner_read ON sports_events;
CREATE POLICY sports_events_owner_read ON sports_events FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON sports_leagues, sports_events TO valemont_readonly;

REVOKE ALL ON FUNCTION public.add_test_sports_event(text, text, text, text, timestamptz, text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.ctx_sports_today() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_sports_event(text, text, text, text, timestamptz, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.ctx_sports_today() TO authenticated, valemont_readonly;

-- ===================================================================== 6
INSERT INTO migration_log (name) VALUES ('065_sports_events')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 7
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with its SQLSTATE.
-- The owner's session is simulated with request.jwt claims; the marker is a
-- throwaway e2e_markers row. Real rows use ids from 999000000 up (none real
-- yet: the worker has never written this table), rolled back with the rest.
-- Every statement that calls a writing function stores its result first and
-- reads back in a later statement (db/062's rule).

DO $$
DECLARE
    v_owner   uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_marker  text := 'selftest-065-' || gen_random_uuid();
    v_tz      text := coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago');
    v_noon    timestamptz;
    v_ok      boolean;
    v_n       int;
    v_before  int;
    v_id      bigint;
    v_ctx     jsonb;
    v_row     record;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;
        v_noon := (public.owner_today() + time '12:00') AT TIME ZONE v_tz;

        -- ---------------------------------------------------- 1. the registry
        SELECT count(*), count(*) FILTER (WHERE owner_five) INTO v_n, v_before FROM public.sports_leagues;
        IF v_n <> 14 OR v_before <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % leagues, % owner_five (want 14, 5)', v_n, v_before;
        END IF;
        IF (SELECT string_agg(code, ',' ORDER BY sort) FROM public.sports_leagues WHERE owner_five)
           <> 'cfb,nfl,nba,f1,pga' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the owner''s five are not cfb, nfl, nba, f1, pga in order';
        END IF;

        -- ---------------------------------------------------- 2. a real row, and the refusals
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                          espn_status, broadcast, national_tv, content_hash)
        VALUES ('nfl', 'game', '999000001', 'Selftest Away at Selftest Home', v_noon, 'Selftest Home',
                'Selftest Away', 'scheduled', 'STATUS_SCHEDULED', ARRAY['CBS'], true, md5('065-1'));

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                              espn_status, content_hash)
            VALUES ('nfl', 'game', '999000001', 'x', v_noon, 'a', 'b', 'scheduled', 'STATUS_SCHEDULED', md5('x'));
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a duplicate (league, espn_id) was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, status, espn_status,
                                              content_hash)
            VALUES ('nfl', 'bout', '999000002', 'x', v_noon, 'scheduled', 'STATUS_SCHEDULED', md5('x'));
        EXCEPTION WHEN foreign_key_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a bout in the nfl was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                              espn_status, content_hash)
            VALUES ('nfl', 'game', '999000003', 'x', v_noon, 'a', 'b', 'halftime', 'STATUS_HALFTIME', md5('x'));
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: status halftime was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                              espn_status, content_hash)
            VALUES ('nfl', 'game', 'e2e-999000004', 'x', v_noon, 'a', 'b', 'scheduled', 'STATUS_SCHEDULED',
                    md5('x'));
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a real row with an e2e- id was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, status,
                                              espn_status, content_hash)
            VALUES ('nfl', 'game', '999000005', 'x', v_noon, 'a', 'scheduled', 'STATUS_SCHEDULED', md5('x'));
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a game with no away team was accepted'; END IF;

        -- A non-game kind needs no teams.
        INSERT INTO public.sports_events (league, kind, espn_id, parent_espn_id, event_name, session, starts_at,
                                          time_tbd, participants, status, espn_status, content_hash)
        VALUES ('pga', 'golf_round', '999000006-r1', '999000006', 'Selftest Open', 'R1', v_noon, true,
                '[{"name": "A"}, {"name": "B"}]'::jsonb, 'live', 'STATUS_IN_PROGRESS', md5('065-6'));

        -- ---------------------------------------------------- 3. ctx_sports_today
        v_ctx := public.ctx_sports_today();
        IF coalesce((v_ctx->'counts'->>'nfl')::int, 0) < 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_sports_today does not count today''s nfl row: %', v_ctx->'counts';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_ctx->'live') x
                        WHERE x->>'league' = 'pga' AND x->>'what' LIKE 'A vs B · Selftest Open%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the live golf round is not under live: %', v_ctx->'live';
        END IF;
        v_before := (v_ctx->'counts'->>'nfl')::int;

        -- ---------------------------------------------------- 4. add_test_sports_event
        v_ok := false;
        BEGIN
            PERFORM public.add_test_sports_event(v_marker, 'nfl', 'H', 'A', v_noon);
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'add_test_sports_event: the owner and a verified test marker only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: add_test_sports_event ran with no session'; END IF;

        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_id := public.add_test_sports_event(v_marker, 'nfl', 'Test Home', 'Test Away', v_noon);
        SELECT is_test, espn_id LIKE 'e2e-%' AS e2e, kind, event_name INTO v_row
          FROM public.sports_events WHERE id = v_id;
        IF v_row IS NULL OR NOT v_row.is_test OR NOT v_row.e2e OR v_row.kind <> 'game'
           OR v_row.event_name <> 'Test Away at Test Home' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test event reads %', v_row;
        END IF;
        v_ctx := public.ctx_sports_today();
        IF (v_ctx->'counts'->>'nfl')::int <> v_before THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_sports_today counted a test row';
        END IF;

        -- ---------------------------------------------------- 5. grants
        IF has_table_privilege('anon', 'public.sports_events', 'SELECT,INSERT,UPDATE,DELETE')
           OR has_table_privilege('anon', 'public.sports_leagues', 'SELECT,INSERT,UPDATE,DELETE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: anon holds a privilege';
        END IF;
        IF NOT has_table_privilege('authenticated', 'public.sports_events', 'SELECT')
           OR has_table_privilege('authenticated', 'public.sports_events', 'INSERT,UPDATE,DELETE,TRUNCATE')
           OR has_table_privilege('authenticated', 'public.sports_leagues', 'INSERT,UPDATE,DELETE,TRUNCATE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: authenticated is not select-only';
        END IF;
        IF NOT has_table_privilege('valemont_readonly', 'public.sports_events', 'SELECT')
           OR NOT has_table_privilege('valemont_readonly', 'public.sports_leagues', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: valemont_readonly cannot read';
        END IF;
        IF has_function_privilege('anon', 'public.ctx_sports_today()', 'EXECUTE')
           OR has_function_privilege('valemont_readonly',
                                     'public.add_test_sports_event(text, text, text, text, timestamptz, text)',
                                     'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.ctx_sports_today()', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: function grants';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers
                        WHERE key = 'sports_today' AND NOT brief_section AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sports_today is not an enabled, non-brief provider';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '065 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '065_sports_events';
-- (b) SELECT code, kind, fetch_mode, owner_five, sort FROM sports_leagues ORDER BY sort;
-- (c) SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname),
--            has_table_privilege('anon', c.oid, 'SELECT'), has_table_privilege('authenticated', c.oid, 'SELECT'),
--            has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE'),
--            has_table_privilege('valemont_readonly', c.oid, 'SELECT')
--       FROM pg_class c WHERE c.oid IN ('public.sports_leagues'::regclass, 'public.sports_events'::regclass);
-- (d) SELECT indexname FROM pg_indexes WHERE tablename = 'sports_events' ORDER BY 1;
-- (e) SELECT key, brief_section, enabled, sort FROM context_providers WHERE key = 'sports_today';
-- (f) SELECT count(*) FROM sports_events;   -- 0 until the worker deploys
