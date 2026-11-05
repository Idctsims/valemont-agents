-- 068_gameday.sql — Game Day (Chat 7 Phase 3; docs/gameday.md).
--
-- Read functions only, plus two marker-gated test helpers. No table, no
-- column, no trigger. Every reader is SECURITY INVOKER (RLS applies: the
-- owner reads, nobody else), STABLE, search_path ''.
--
-- What it adds:
--   1. Helpers: gameday_tz(); gameday_day(starts_at, time_tbd), the owner's
--      game day: a timed event belongs to the Chicago day that runs 05:00 to
--      05:00 (owner, Nov 4: 04:59 is still the night before); a TBD event to
--      ESPN's Eastern date (ESPN files it at midnight Eastern, which is 23:00
--      Chicago the evening BEFORE, so the 05:00 rule would put it a day early);
--      gameday_today(); sports_tv_tier(broadcast): 1 broadcast network (ABC,
--      CBS, NBC, FOX), 2 national cable, 3 anything else (streaming-only and
--      regional), 4 none (owner, Nov 5; the web twin is
--      apps/web/src/lib/gameday/tiers.ts and a fast test keeps them equal);
--      gameday_at, gameday_title, gameday_score (narrow live scores).
--   2. gameday_slate(day, all, tests): the page's rows, narrow columns. The
--      owner's five for the day and the 7 after it, plus what is live; with
--      all = true the other leagues for the day and live (never their week:
--      ~580 more rows a week). Sectioning and order are the page's
--      (apps/web/src/lib/gameday/slate.ts).
--   3. gameday_live(ids): the live poll. Only the asked ids (at most 60), only
--      what changes in play.
--   4. gameday_event(id): the detail page, one jsonb: the row, where to
--      watch, venue and neutral site, the Kalshi link state, an F1 weekend's
--      stored sessions, a golf tournament's latest standings.
--   5. ctx_sports_today(): the same keys as db/065 (counts, live, owner_five,
--      owner_five_more), now on the game day (05:00 to 05:00) with the time,
--      every broadcaster and the score, plus alabama_next, other_tonight and
--      this_week, so Wags answers "what's on tonight", "when does Bama play"
--      and "where can I watch X" from rows. Not a brief section.
--   6. gameday_brief(day) and ctx_sports_brief(): the Brief's slate, at most
--      5 lines, NULL on a dead night; registered as provider `sports_brief`
--      (sort 47, a brief section). Alabama when they play; then the owner's
--      five's national-TV games (NFL first on a Sunday) by tier, rank and
--      time; an F1 weekend's session times; a golf day's leader and the
--      followed players.
--   7. Test helpers (owner, the verified e2e marker, test rows only):
--      add_test_gameday_event, set_test_sports_live.
--
-- Self-test (section 9) on paste, rolled back; it uses days in 2031, where
-- no real row exists, so real data cannot move its answers.

-- ===================================================================== 1
-- Helpers. (Nothing below exists before this file; the DROPs only let the
-- file run again on a scratch database.)

DROP FUNCTION IF EXISTS public.gameday_slate(date, boolean, boolean);
DROP FUNCTION IF EXISTS public.gameday_live(bigint[]);

CREATE OR REPLACE FUNCTION public.gameday_tz()
RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago');
$$;

CREATE OR REPLACE FUNCTION public.gameday_day(p_starts timestamptz, p_tbd boolean)
RETURNS date
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE WHEN p_tbd THEN (p_starts AT TIME ZONE 'America/New_York')::date
                ELSE ((p_starts AT TIME ZONE public.gameday_tz()) - interval '5 hours')::date END;
$$;

CREATE OR REPLACE FUNCTION public.gameday_today()
RETURNS date
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT public.gameday_day(now(), false);
$$;

-- The web twin is apps/web/src/lib/gameday/tiers.ts (tests/test_gameday.py
-- compares the two lists). Names as ESPN stores them, compared upper-case.
CREATE OR REPLACE FUNCTION public.sports_tv_tier(p_broadcast text[])
RETURNS integer
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN p_broadcast IS NULL OR cardinality(p_broadcast) = 0 THEN 4
        WHEN EXISTS (SELECT 1 FROM unnest(p_broadcast) b
                      WHERE upper(btrim(b)) IN ('ABC', 'CBS', 'NBC', 'FOX')) THEN 1
        WHEN EXISTS (SELECT 1 FROM unnest(p_broadcast) b
                      WHERE upper(btrim(b)) IN ('ESPN', 'ESPN2', 'ESPNU', 'FS1', 'SEC NETWORK', 'BTN',
                                                'ACC NETWORK', 'CBSSN', 'CW', 'USA NET', 'TNT', 'TRUTV',
                                                'NFL NET', 'NBA TV', 'GOLF CHNL', 'TBS', 'FS2', 'NHL NET',
                                                'NHL NETWORK', 'ESPNEWS')) THEN 2
        ELSE 3
    END;
$$;

-- "2:30 PM" in the owner's time, or "TBD" when ESPN has no time (never the
-- stored placeholder).
CREATE OR REPLACE FUNCTION public.gameday_at(p_starts timestamptz, p_tbd boolean)
RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE WHEN p_tbd THEN 'TBD'
                ELSE to_char(p_starts AT TIME ZONE public.gameday_tz(), 'FMHH12:MI AM') END;
$$;

-- "Away at Home" ("vs" at a neutral site); the two names of a bout or match;
-- else the event's name. p_ranked puts "No. N " before a ranked team.
CREATE OR REPLACE FUNCTION public.gameday_title(e public.sports_events, p_ranked boolean)
RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN e.home IS NOT NULL AND e.away IS NOT NULL THEN
            CASE WHEN p_ranked AND e.participants->'away'->>'rank' IS NOT NULL
                 THEN 'No. ' || (e.participants->'away'->>'rank') || ' ' ELSE '' END
            || e.away || CASE WHEN e.neutral_site THEN ' vs ' ELSE ' at ' END
            || CASE WHEN p_ranked AND e.participants->'home'->>'rank' IS NOT NULL
                    THEN 'No. ' || (e.participants->'home'->>'rank') || ' ' ELSE '' END
            || e.home
        WHEN e.kind IN ('match', 'bout') AND jsonb_typeof(e.participants) = 'array' THEN
            (SELECT string_agg(x.p->>'name', ' vs ' ORDER BY x.o)
               FROM jsonb_array_elements(e.participants) WITH ORDINALITY AS x (p, o))
        ELSE e.event_name
    END;
$$;

-- The score a card needs: a game's or a match's as stored; a golf round's
-- leader and the followed players; an F1 session's leader.
CREATE OR REPLACE FUNCTION public.gameday_score(p_kind text, p_score jsonb)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT CASE
        WHEN p_score IS NULL THEN NULL
        WHEN p_kind = 'golf_round' THEN jsonb_strip_nulls(jsonb_build_object(
            'round', p_score->'round', 'leader', p_score->'leaders'->0, 'followed', p_score->'followed'))
        WHEN p_kind = 'race_session' THEN jsonb_strip_nulls(jsonb_build_object('leader', p_score->'results'->0))
        ELSE p_score
    END;
$$;

-- ===================================================================== 2
-- The page's rows.

-- One compact object per row (nulls and false left out, egress): id, league,
-- day, at (starts_at), tbd, title (non-games only: a game is away/home),
-- session, home, away, hr/ar (ranks), sec (an SEC team), bama (Alabama, ESPN
-- team 333), tv (broadcast), tier, status, detail, clock, score, test.
CREATE OR REPLACE FUNCTION public.gameday_slate(p_day date DEFAULT NULL, p_all boolean DEFAULT false,
                                                p_tests boolean DEFAULT false)
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH d AS (SELECT coalesce(p_day, public.gameday_today()) AS d, public.gameday_tz() AS tz),
    w AS (SELECT d.d, ((d.d - 1) + time '00:00') AT TIME ZONE d.tz AS lo,
                 ((d.d + 9) + time '00:00') AT TIME ZONE d.tz AS hi FROM d),
    ev AS (
        SELECT e, l.owner_five AS five, public.gameday_day(e.starts_at, e.time_tbd) AS gday
          FROM public.sports_events e
          JOIN public.sports_leagues l ON l.code = e.league
         CROSS JOIN w
         WHERE l.active AND (NOT e.is_test OR p_tests)
           AND ((e.status IN ('live', 'delayed')
                 AND e.starts_at > now() - CASE WHEN e.kind = 'golf_round' THEN interval '30 hours'
                                                ELSE interval '16 hours' END)
                OR (e.starts_at >= w.lo AND e.starts_at < w.hi))
    )
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'id', (ev.e).id, 'league', (ev.e).league, 'day', ev.gday, 'at', (ev.e).starts_at,
               'tbd', CASE WHEN (ev.e).time_tbd THEN true END,
               'title', CASE WHEN (ev.e).home IS NULL OR (ev.e).away IS NULL
                             THEN public.gameday_title(ev.e, false) END,
               'session', (ev.e).session, 'home', (ev.e).home, 'away', (ev.e).away,
               'neutral', CASE WHEN (ev.e).neutral_site THEN true END,
               'hr', ((ev.e).participants->'home'->>'rank')::integer,
               'ar', ((ev.e).participants->'away'->>'rank')::integer,
               'sec', CASE WHEN (ev.e).league = 'cfb'
                            AND '8' IN ((ev.e).participants->'home'->>'conference_id',
                                        (ev.e).participants->'away'->>'conference_id') THEN true END,
               'bama', CASE WHEN (ev.e).league = 'cfb'
                             AND '333' IN ((ev.e).participants->'home'->>'id',
                                           (ev.e).participants->'away'->>'id') THEN true END,
               'tv', CASE WHEN cardinality((ev.e).broadcast) > 0 THEN to_jsonb((ev.e).broadcast) END,
               'tier', public.sports_tv_tier((ev.e).broadcast),
               'status', (ev.e).status,
               'detail', CASE WHEN (ev.e).status <> 'scheduled' THEN (ev.e).status_detail END,
               'clock', CASE WHEN (ev.e).status IN ('live', 'delayed') THEN (ev.e).clock END,
               'score', CASE WHEN (ev.e).status IN ('live', 'delayed', 'final', 'suspended')
                             THEN public.gameday_score((ev.e).kind, (ev.e).score) END,
               'test', CASE WHEN (ev.e).is_test THEN true END))
      FROM ev CROSS JOIN w
     WHERE (ev.five OR p_all)
       AND (((ev.e).status IN ('live', 'delayed')
             AND (ev.e).starts_at > now() - CASE WHEN (ev.e).kind = 'golf_round' THEN interval '30 hours'
                                                 ELSE interval '16 hours' END)
            OR (ev.gday = w.d)
            OR (ev.five AND ev.gday BETWEEN w.d + 1 AND w.d + 7))
     ORDER BY (ev.e).starts_at, (ev.e).id
     LIMIT 800;
$$;

-- ===================================================================== 3
-- The live poll: only the ids on screen, only what moves in play.

-- The same keys as the slate's: id, status, detail, clock, score.
CREATE OR REPLACE FUNCTION public.gameday_live(p_ids bigint[])
RETURNS SETOF jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_strip_nulls(jsonb_build_object(
               'id', e.id, 'status', e.status,
               'detail', CASE WHEN e.status <> 'scheduled' THEN e.status_detail END,
               'clock', CASE WHEN e.status IN ('live', 'delayed') THEN e.clock END,
               'score', CASE WHEN e.status IN ('live', 'delayed', 'final', 'suspended')
                             THEN public.gameday_score(e.kind, e.score) END))
      FROM public.sports_events e
     WHERE e.id = ANY ((p_ids)[1:60]);
$$;

-- ===================================================================== 4
-- One event, for /gameday/[id].

CREATE OR REPLACE FUNCTION public.gameday_event(p_id bigint)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'id', e.id, 'league', e.league, 'league_label', l.label, 'kind', e.kind,
        'day', public.gameday_day(e.starts_at, e.time_tbd), 'starts_at', e.starts_at, 'time_tbd', e.time_tbd,
        'title', public.gameday_title(e, false), 'event_name', e.event_name, 'session', e.session,
        'home', e.home, 'away', e.away,
        'participants', CASE WHEN e.kind <> 'golf_round' THEN e.participants END,
        'broadcast', to_jsonb(e.broadcast), 'tv_tier', public.sports_tv_tier(e.broadcast),
        'venue', e.venue, 'neutral_site', e.neutral_site,
        'status', e.status, 'status_detail', e.status_detail, 'period', e.period, 'clock', e.clock,
        'score', CASE WHEN e.status IN ('live', 'delayed', 'final', 'suspended') THEN e.score END,
        'is_test', e.is_test,
        'kalshi', jsonb_build_object('state', e.link_state, 'reason', e.link_reason,
                                     'ticker', e.kalshi_event_ticker, 'series', l.kalshi_series),
        'sessions', CASE WHEN e.kind = 'race_session' AND e.parent_espn_id IS NOT NULL THEN (
            SELECT jsonb_agg(jsonb_build_object('id', s.id, 'session', s.session, 'starts_at', s.starts_at,
                                                'time_tbd', s.time_tbd, 'status', s.status)
                             ORDER BY s.starts_at, s.id)
              FROM public.sports_events s
             WHERE s.league = e.league AND s.parent_espn_id = e.parent_espn_id AND s.is_test = e.is_test) END,
        'standings', CASE WHEN e.kind = 'golf_round' AND e.parent_espn_id IS NOT NULL THEN (
            SELECT jsonb_build_object('session', s.session, 'status', s.status, 'score', s.score)
              FROM public.sports_events s
             WHERE s.league = e.league AND s.parent_espn_id = e.parent_espn_id AND s.is_test = e.is_test
               AND s.score IS NOT NULL
             ORDER BY s.starts_at DESC, s.id DESC
             LIMIT 1) END)
      FROM public.sports_events e
      JOIN public.sports_leagues l ON l.code = e.league
     WHERE e.id = p_id;
$$;

-- ===================================================================== 5
-- Wags' context: same keys as db/065, on the game day, plus three.

CREATE OR REPLACE FUNCTION public.ctx_sports_today()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    WITH z AS (SELECT public.gameday_tz() AS tz, public.gameday_today() AS d),
    ev AS (
        SELECT e.id, e.league, e.kind, e.status, e.status_detail, e.starts_at, e.time_tbd, e.broadcast,
               e.participants, l.owner_five, l.sort,
               public.gameday_day(e.starts_at, e.time_tbd) AS gday,
               public.sports_tv_tier(e.broadcast) AS tier,
               public.ctx_trim(public.gameday_title(e, true), 120) AS what,
               public.gameday_at(e.starts_at, e.time_tbd) AS at,
               CASE WHEN e.status IN ('live', 'delayed', 'final', 'suspended')
                    THEN public.gameday_score(e.kind, e.score) END AS score,
               coalesce(e.league = 'cfb'
                        AND '333' IN (e.participants->'home'->>'id', e.participants->'away'->>'id'), false) AS bama
          FROM public.sports_events e
          JOIN public.sports_leagues l ON l.code = e.league
         CROSS JOIN z
         WHERE NOT e.is_test AND l.active
           AND e.starts_at >= ((z.d - 1) + time '00:00') AT TIME ZONE z.tz
           AND e.starts_at < ((z.d + 22) + time '00:00') AT TIME ZONE z.tz
    ),
    tonight AS (SELECT ev.* FROM ev, z WHERE ev.gday = z.d)
    SELECT jsonb_build_object(
        'about', 'Sports from ESPN, in the owner''s time. The game day runs 05:00 to 05:00 Chicago, so a '
                 'late game is still tonight. counts: tonight by league. live: in play now. owner_five: '
                 'tonight''s CFB, NFL, NBA, F1 and golf. other_tonight: other leagues tonight on national TV. '
                 'this_week: the next 7 days of the five on national TV. alabama_next: Alabama''s next games. '
                 'at is the start time, TBD when ESPN has none; tv lists every broadcaster as ESPN gives it; '
                 'No. N is ESPN''s ranking.',
        'date', (SELECT d FROM z),
        'counts', coalesce((SELECT jsonb_object_agg(c.league, c.n)
                              FROM (SELECT league, count(*) AS n FROM tonight GROUP BY league) c), '{}'::jsonb),
        'live', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                              'league', x.league, 'what', x.what, 'detail', x.status_detail,
                              'score', x.score, 'tv', to_jsonb(x.broadcast))) ORDER BY x.sort, x.starts_at)
                            FROM (SELECT * FROM ev WHERE status IN ('live', 'delayed')
                                     AND starts_at > now() - CASE WHEN kind = 'golf_round'
                                                                  THEN interval '30 hours'
                                                                  ELSE interval '16 hours' END
                                   ORDER BY sort, starts_at LIMIT 15) x), '[]'::jsonb),
        'owner_five', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                    'league', x.league, 'what', x.what, 'at', x.at,
                                    'tv', to_jsonb(x.broadcast), 'status', x.status, 'score', x.score))
                                    ORDER BY x.bama DESC, x.starts_at, x.sort)
                                  FROM (SELECT * FROM tonight WHERE owner_five
                                         ORDER BY bama DESC, starts_at, sort LIMIT 30) x), '[]'::jsonb),
        'owner_five_more', greatest((SELECT count(*) FROM tonight WHERE owner_five) - 30, 0),
        'other_tonight', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                       'league', x.league, 'what', x.what, 'at', x.at,
                                       'tv', to_jsonb(x.broadcast), 'status', x.status, 'score', x.score))
                                       ORDER BY x.tier, x.starts_at)
                                     FROM (SELECT * FROM tonight WHERE NOT owner_five AND tier <= 2
                                            ORDER BY tier, starts_at LIMIT 12) x), '[]'::jsonb),
        'this_week', coalesce((SELECT jsonb_agg(jsonb_build_object(
                                   'league', x.league, 'what', x.what, 'day', to_char(x.gday, 'FMDy Mon FMDD'),
                                   'at', x.at, 'tv', to_jsonb(x.broadcast)) ORDER BY x.gday, x.starts_at)
                                 FROM (SELECT * FROM ev, z WHERE owner_five AND (tier <= 2 OR bama)
                                          AND gday BETWEEN z.d + 1 AND z.d + 7
                                        ORDER BY gday, bama DESC, tier, starts_at LIMIT 25) x), '[]'::jsonb),
        'alabama_next', coalesce((SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                      'what', x.what, 'day', to_char(x.gday, 'FMDy Mon FMDD'), 'at', x.at,
                                      'tv', to_jsonb(x.broadcast), 'status', x.status, 'score', x.score))
                                      ORDER BY x.starts_at)
                                    FROM (SELECT * FROM ev WHERE bama AND status NOT IN ('final', 'canceled')
                                             AND starts_at > now() - interval '6 hours'
                                           ORDER BY starts_at LIMIT 2) x), '[]'::jsonb)
    );
$$;

-- ===================================================================== 6
-- The Brief's slate.

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
    v_sunday boolean := extract(isodow FROM p_day) = 7;
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

    -- The owner's five's national-TV games, NFL first on a Sunday.
    v_room := 5 - (v_bama IS NOT NULL)::int - (v_f1 IS NOT NULL)::int - (v_golf IS NOT NULL)::int;
    SELECT array_agg(g.line ORDER BY g.ord) INTO v_games
      FROM (SELECT upper(e.league) || ': ' || public.gameday_title(e, true) || ', '
                   || CASE WHEN e.time_tbd THEN 'time TBD' ELSE public.gameday_at(e.starts_at, false) END
                   || coalesce(' on ' || e.broadcast[1], '') || '.' AS line,
                   row_number() OVER (ORDER BY (v_sunday AND e.league = 'nfl') DESC,
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
                 'play, NFL first on a Sunday). Use these lines as given.',
        'date', p_day,
        'lines', to_jsonb(v_lines[1:5]));
END;
$$;

CREATE OR REPLACE FUNCTION public.ctx_sports_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT public.gameday_brief(public.gameday_today());
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('sports_brief', 'Game Day', 'public.ctx_sports_brief()'::regprocedure, true, 47)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 7
-- Test helpers: the owner, the verified e2e marker (db/051), test rows only.

CREATE OR REPLACE FUNCTION public.add_test_gameday_event(
    p_marker text, p_league text, p_starts_at timestamptz,
    p_home text DEFAULT NULL, p_away text DEFAULT NULL, p_status text DEFAULT 'scheduled',
    p_broadcast text[] DEFAULT '{}', p_participants jsonb DEFAULT NULL, p_score jsonb DEFAULT NULL,
    p_time_tbd boolean DEFAULT false, p_event_name text DEFAULT NULL, p_session text DEFAULT NULL,
    p_parent text DEFAULT NULL, p_link_state text DEFAULT NULL, p_link_reason text DEFAULT NULL,
    p_venue text DEFAULT NULL, p_neutral boolean DEFAULT NULL, p_ticker text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_kind text;
    v_id   bigint;
BEGIN
    -- coalesce: db/051's e2e_marker_ok answers NULL (not false) while e2e_markers is empty.
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'add_test_gameday_event: the owner and a verified test marker only';
    END IF;
    SELECT kind INTO v_kind FROM public.sports_leagues WHERE code = p_league;
    IF v_kind IS NULL THEN
        RAISE EXCEPTION 'add_test_gameday_event: unknown league %', p_league;
    END IF;
    INSERT INTO public.sports_events (league, kind, espn_id, parent_espn_id, event_name, session, starts_at,
                                      time_tbd, home, away, participants, broadcast, national_tv, venue,
                                      neutral_site, status, espn_status, score, content_hash, is_test,
                                      link_state, link_reason, link_method, kalshi_event_ticker)
    VALUES (p_league, v_kind, 'e2e-' || gen_random_uuid(), p_parent,
            coalesce(p_event_name, p_away || ' at ' || p_home, 'Test event'), p_session, p_starts_at,
            p_time_tbd, p_home, p_away, p_participants, coalesce(p_broadcast, '{}'),
            coalesce(cardinality(p_broadcast), 0) > 0, p_venue, p_neutral, p_status,
            CASE p_status WHEN 'live' THEN 'STATUS_IN_PROGRESS' WHEN 'final' THEN 'STATUS_FINAL'
                          WHEN 'postponed' THEN 'STATUS_POSTPONED' WHEN 'canceled' THEN 'STATUS_CANCELED'
                          ELSE 'STATUS_SCHEDULED' END,
            p_score, md5(gen_random_uuid()::text), true,
            CASE WHEN p_ticker IS NOT NULL THEN 'linked' ELSE p_link_state END,
            CASE WHEN p_ticker IS NOT NULL THEN NULL ELSE p_link_reason END,
            CASE WHEN p_ticker IS NOT NULL THEN 'team_pair' END, p_ticker)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- A test row's live state, so e2e can watch a score change without a refresh.
CREATE OR REPLACE FUNCTION public.set_test_sports_live(p_marker text, p_id bigint, p_status text, p_score jsonb,
                                                       p_detail text DEFAULT NULL, p_clock text DEFAULT NULL,
                                                       p_period integer DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NOT coalesce(public.e2e_marker_ok(p_marker), false) THEN
        RAISE EXCEPTION 'set_test_sports_live: the owner and a verified test marker only';
    END IF;
    UPDATE public.sports_events
       SET status = p_status, score = p_score, status_detail = p_detail, clock = p_clock, period = p_period,
           espn_status = CASE p_status WHEN 'live' THEN 'STATUS_IN_PROGRESS' WHEN 'final' THEN 'STATUS_FINAL'
                                       ELSE 'STATUS_SCHEDULED' END,
           content_hash = md5(gen_random_uuid()::text), updated_at = now()
     WHERE id = p_id AND is_test;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'set_test_sports_live: % is not a test event', p_id;
    END IF;
END;
$$;

-- ===================================================================== 8
-- Access. Readers: the owner (RLS on sports_events) and valemont_readonly.
-- Test helpers: authenticated only (they check the owner and the marker).

REVOKE ALL ON FUNCTION public.gameday_tz(), public.gameday_day(timestamptz, boolean), public.gameday_today(),
    public.sports_tv_tier(text[]), public.gameday_at(timestamptz, boolean),
    public.gameday_title(public.sports_events, boolean), public.gameday_score(text, jsonb),
    public.gameday_slate(date, boolean, boolean), public.gameday_live(bigint[]), public.gameday_event(bigint),
    public.ctx_sports_today(), public.gameday_brief(date), public.ctx_sports_brief()
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.gameday_tz(), public.gameday_day(timestamptz, boolean), public.gameday_today(),
    public.sports_tv_tier(text[]), public.gameday_at(timestamptz, boolean),
    public.gameday_title(public.sports_events, boolean), public.gameday_score(text, jsonb),
    public.gameday_slate(date, boolean, boolean), public.gameday_live(bigint[]), public.gameday_event(bigint),
    public.ctx_sports_today(), public.gameday_brief(date), public.ctx_sports_brief()
    TO authenticated, valemont_readonly;

REVOKE ALL ON FUNCTION public.add_test_gameday_event(text, text, timestamptz, text, text, text, text[], jsonb, jsonb,
                                                     boolean, text, text, text, text, text, text, boolean, text),
                       public.set_test_sports_live(text, bigint, text, jsonb, text, text, integer)
    FROM PUBLIC, anon, valemont_readonly;
GRANT EXECUTE ON FUNCTION public.add_test_gameday_event(text, text, timestamptz, text, text, text, text[], jsonb,
                                                        jsonb, boolean, text, text, text, text, text, text, boolean,
                                                        text),
                          public.set_test_sports_live(text, bigint, text, jsonb, text, text, integer)
    TO authenticated;

-- ===================================================================== 9
INSERT INTO migration_log (name) VALUES ('068_gameday')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 10
-- Self-test, run on paste, rolled back ('selftest-rollback'). Real rows use
-- espn ids 999068xxx and days in 2031 (no real row there); the owner's
-- session is simulated with request.jwt claims, the marker is a throwaway
-- e2e_markers row. Every statement that calls a writing function stores its
-- result first and reads back in a later statement (db/062's rule).

DO $$
DECLARE
    v_owner  uuid := (SELECT owner_id FROM public.app_settings LIMIT 1);
    v_tz     text := public.gameday_tz();
    v_marker text := 'selftest-068-' || gen_random_uuid();
    v_sat    date := date '2031-11-08';   -- a Saturday
    v_sun    date := date '2031-11-09';   -- a Sunday
    v_dead   date := date '2031-11-12';   -- nothing stored
    v_ok     boolean;
    v_n      integer;
    v_j      jsonb;
    v_t      text;
    v_ids    bigint[];
    v_bama   bigint;
    v_late   bigint;
    v_early  bigint;
    v_tbd    bigint;
    v_nhl    bigint;
    v_week   bigint;
    v_qual   bigint;
    v_r2     bigint;
    v_test   bigint;
    v_row    record;
BEGIN
    BEGIN
        IF v_owner IS NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: app_settings has no owner';
        END IF;

        -- --------------------------------------------------------- 1. tiers
        IF public.sports_tv_tier(ARRAY['ABC']) <> 1 OR public.sports_tv_tier(ARRAY['Fox']) <> 1
           OR public.sports_tv_tier(ARRAY['ESPN+', 'CBS']) <> 1
           OR public.sports_tv_tier(ARRAY['SEC Network']) <> 2 OR public.sports_tv_tier(ARRAY['truTV']) <> 2
           OR public.sports_tv_tier(ARRAY['TBS']) <> 2 OR public.sports_tv_tier(ARRAY['FS2']) <> 2
           OR public.sports_tv_tier(ARRAY['NHL Net']) <> 2 OR public.sports_tv_tier(ARRAY['NHL Network']) <> 2
           OR public.sports_tv_tier(ARRAY['ESPNEWS']) <> 2
           OR public.sports_tv_tier(ARRAY['ESPN+']) <> 3 OR public.sports_tv_tier(ARRAY['MNMT']) <> 3
           OR public.sports_tv_tier(ARRAY[]::text[]) <> 4 OR public.sports_tv_tier(NULL) <> 4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sports_tv_tier';
        END IF;

        -- --------------------------------------------------------- 2. the game day
        IF public.gameday_day((v_sat + 1 + time '04:59') AT TIME ZONE v_tz, false) <> v_sat
           OR public.gameday_day((v_sat + 1 + time '05:00') AT TIME ZONE v_tz, false) <> v_sat + 1
           OR public.gameday_day(timestamptz '2031-11-08 05:00:00+00', true) <> v_sat THEN
            RAISE EXCEPTION 'SELFTEST FAILED: gameday_day boundaries';
        END IF;
        IF public.gameday_at(timestamptz '2031-11-08 20:30:00+00', false) <> '2:30 PM'
           OR public.gameday_at(timestamptz '2031-11-08 05:00:00+00', true) <> 'TBD' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: gameday_at: %', public.gameday_at(timestamptz '2031-11-08 20:30:00+00', false);
        END IF;

        -- --------------------------------------------------------- 3. rows
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, participants,
                                          broadcast, national_tv, neutral_site, status, espn_status, content_hash,
                                          link_state, link_reason)
        VALUES ('cfb', 'game', '999068001', 'Alabama Crimson Tide at LSU Tigers', (v_sat + time '14:30') AT TIME ZONE v_tz,
                'LSU Tigers', 'Alabama Crimson Tide',
                '{"home": {"id": "99", "rank": 14, "conference_id": "8"}, "away": {"id": "333", "rank": 5, "conference_id": "8"}}',
                ARRAY['ABC'], true, false, 'scheduled', 'STATUS_SCHEDULED', md5('068-1'),
                'not_linked', 'selftest reason')
        RETURNING id INTO v_bama;
        -- 04:59 the next morning: still Saturday night. 05:00: Sunday.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('nba', 'game', '999068002', 'Late at Night', (v_sat + 1 + time '04:59') AT TIME ZONE v_tz,
                'Night Home', 'Night Away', ARRAY['ESPN'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-2'))
        RETURNING id INTO v_late;
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('nfl', 'game', '999068003', 'Early at Sunday', (v_sat + 1 + time '05:00') AT TIME ZONE v_tz,
                'Sun Home', 'Sun Away', ARRAY['ESPN+'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-3'))
        RETURNING id INTO v_early;
        -- A TBD CFB game, filed at midnight Eastern Saturday (23:00 Chicago Friday).
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, time_tbd, home, away,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('cfb', 'game', '999068004', 'TBD game', timestamptz '2031-11-08 05:00:00+00', true, 'TBD Home',
                'TBD Away', '{}', false, 'scheduled', 'STATUS_SCHEDULED', md5('068-4'))
        RETURNING id INTO v_tbd;
        -- A national CFB game with no team ids (participants NULL): not Alabama, and still in the brief.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('cfb', 'game', '999068008', 'No ids', (v_sat + time '19:00') AT TIME ZONE v_tz, 'NoIds Home',
                'NoIds Away', ARRAY['FS1'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-8'));
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('nhl', 'game', '999068005', 'Hockey', (v_sat + time '18:00') AT TIME ZONE v_tz, 'H Home', 'H Away',
                ARRAY['TNT'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-5'))
        RETURNING id INTO v_nhl;
        -- Next week, the five: in the week; NHL next week: never.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('nfl', 'game', '999068006', 'Week game', (v_sat + 6 + time '12:00') AT TIME ZONE v_tz, 'W Home',
                'W Away', ARRAY['CBS'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-6')),
               ('nhl', 'game', '999068007', 'Week hockey', (v_sat + 6 + time '12:00') AT TIME ZONE v_tz, 'X Home',
                'X Away', ARRAY['TNT'], true, 'scheduled', 'STATUS_SCHEDULED', md5('068-7'));
        SELECT id INTO v_week FROM public.sports_events WHERE league = 'nfl' AND espn_id = '999068006';
        -- An F1 weekend: Qual Saturday, Race Sunday.
        INSERT INTO public.sports_events (league, kind, espn_id, parent_espn_id, event_name, session, starts_at,
                                          broadcast, national_tv, status, espn_status, content_hash)
        VALUES ('f1', 'race_session', '999068010', '999068009', 'Selftest GP', 'Qual',
                (v_sat + time '13:00') AT TIME ZONE v_tz, ARRAY['Apple TV'], true, 'scheduled', 'STATUS_SCHEDULED',
                md5('068-10')),
               ('f1', 'race_session', '999068011', '999068009', 'Selftest GP', 'Race',
                (v_sun + time '13:00') AT TIME ZONE v_tz, ARRAY['Apple TV'], true, 'scheduled', 'STATUS_SCHEDULED',
                md5('068-11'));
        SELECT id INTO v_qual FROM public.sports_events WHERE league = 'f1' AND espn_id = '999068010';
        -- Golf: R1 final with standings, R2 on Saturday.
        INSERT INTO public.sports_events (league, kind, espn_id, parent_espn_id, event_name, session, starts_at,
                                          time_tbd, broadcast, national_tv, status, espn_status, score, content_hash)
        VALUES ('pga', 'golf_round', '999068020-r1', '999068020', 'Selftest Open', 'R1',
                timestamptz '2031-11-07 05:00:00+00', true, ARRAY['Golf Chnl'], true, 'final', 'STATUS_FINAL',
                '{"round": 1, "leaders": [{"name": "Lead Er", "pos": "1", "to_par": "-9", "thru": 18}], "followed": [{"name": "Scottie Scheffler", "pos": "T5", "to_par": "-4", "thru": 18}]}',
                md5('068-20')),
               ('pga', 'golf_round', '999068020-r2', '999068020', 'Selftest Open', 'R2',
                timestamptz '2031-11-08 05:00:00+00', true, ARRAY['Golf Chnl'], true, 'scheduled', 'STATUS_SCHEDULED',
                NULL, md5('068-21'));
        SELECT id INTO v_r2 FROM public.sports_events WHERE league = 'pga' AND espn_id = '999068020-r2';

        -- --------------------------------------------------------- 4. the slate
        SELECT array_agg((s->>'id')::bigint) INTO v_ids FROM public.gameday_slate(v_sat, false, false) s;
        IF NOT (v_ids @> ARRAY[v_bama, v_late, v_tbd, v_week, v_qual, v_r2]) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the slate misses a row of the five: %', v_ids;
        END IF;
        IF v_ids && ARRAY[v_nhl] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: an NHL game is on the five-only slate';
        END IF;
        SELECT array_agg((s->>'id')::bigint) INTO v_ids FROM public.gameday_slate(v_sat, true, false) s;
        IF NOT (v_ids @> ARRAY[v_nhl]) OR v_ids && ARRAY[(SELECT id FROM public.sports_events
                                                         WHERE league = 'nhl' AND espn_id = '999068007')] THEN
            RAISE EXCEPTION 'SELFTEST FAILED: All sports must add today''s NHL game and never next week''s';
        END IF;
        SELECT s INTO v_j FROM public.gameday_slate(v_sat, false, false) s WHERE (s->>'id')::bigint = v_bama;
        IF (v_j->>'day')::date <> v_sat OR v_j ? 'title' OR (v_j->>'ar')::int <> 5 OR (v_j->>'hr')::int <> 14
           OR v_j->>'sec' <> 'true' OR v_j->>'bama' <> 'true' OR (v_j->>'tier')::int <> 1
           OR v_j->'tv' <> '["ABC"]'::jsonb OR v_j ? 'score' OR v_j ? 'test' OR v_j ? 'neutral' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the Alabama row reads %', v_j;
        END IF;
        IF (SELECT s->>'day' FROM public.gameday_slate(v_sat, false, false) s
             WHERE (s->>'id')::bigint = v_late)::date <> v_sat
           OR (SELECT s->>'day' FROM public.gameday_slate(v_sat, false, false) s
                WHERE (s->>'id')::bigint = v_early)::date <> v_sun
           OR (SELECT s->>'day' FROM public.gameday_slate(v_sat, false, false) s
                WHERE (s->>'id')::bigint = v_tbd)::date <> v_sat
           OR (SELECT s->>'tbd' FROM public.gameday_slate(v_sat, false, false) s
                WHERE (s->>'id')::bigint = v_tbd) <> 'true'
           OR (SELECT s->>'title' FROM public.gameday_slate(v_sat, false, false) s
                WHERE (s->>'id')::bigint = v_qual) <> 'Selftest GP' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: slate days (04:59, 05:00, TBD), the TBD flag, a session''s title';
        END IF;

        -- --------------------------------------------------------- 5. the live poll and the event
        UPDATE public.sports_events SET status = 'live', espn_status = 'STATUS_IN_PROGRESS', clock = '4:12', period = 2,
               score = '{"round": 2, "leaders": [{"name": "A"}, {"name": "B"}], "followed": []}'
         WHERE id = v_r2;
        SELECT count(*) INTO v_n FROM public.gameday_live(ARRAY[v_r2, v_bama]);
        IF v_n <> 2 THEN RAISE EXCEPTION 'SELFTEST FAILED: gameday_live returned % rows', v_n; END IF;
        SELECT l->'score' INTO v_j FROM public.gameday_live(ARRAY[v_r2]) l;
        IF v_j->'leader'->>'name' <> 'A' OR v_j ? 'leaders' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the live golf score is not trimmed: %', v_j;
        END IF;
        UPDATE public.sports_events SET status = 'scheduled', espn_status = 'STATUS_SCHEDULED', score = NULL,
               clock = NULL, period = NULL WHERE id = v_r2;

        v_j := public.gameday_event(v_bama);
        IF v_j->'kalshi'->>'state' <> 'not_linked' OR v_j->'kalshi'->>'reason' <> 'selftest reason'
           OR v_j->'kalshi'->>'series' <> 'KXNCAAFGAME' OR (v_j->>'neutral_site')::boolean IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'SELFTEST FAILED: gameday_event Kalshi/venue: %', v_j;
        END IF;
        v_j := public.gameday_event(v_qual);
        IF jsonb_array_length(v_j->'sessions') <> 2 OR v_j->'sessions'->1->>'session' <> 'Race' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the F1 weekend: %', v_j->'sessions';
        END IF;
        v_j := public.gameday_event(v_r2);
        IF v_j->'standings'->>'session' <> 'R1'
           OR v_j->'standings'->'score'->'followed'->0->>'name' <> 'Scottie Scheffler' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: golf standings: %', v_j->'standings';
        END IF;

        -- --------------------------------------------------------- 6. the brief
        v_j := public.gameday_brief(v_sat);
        v_t := v_j->'lines'->>0;
        IF v_t <> 'CFB: No. 5 Alabama Crimson Tide at No. 14 LSU Tigers, 2:30 PM on ABC.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: Alabama is not the first brief line: %', v_j;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_j->'lines') x
                        WHERE x = 'F1, Selftest GP: Qualifying today 1:00 PM; Race Sun 1:00 PM.')
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_j->'lines') x
                           WHERE x = 'Golf, Selftest Open: R2 today; Lead Er leads at -9 after R1; '
                                     'Scottie Scheffler T5 (-4, thru 18).')
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_j->'lines') x
                           WHERE x = 'NBA: Night Away at Night Home, 4:59 AM on ESPN.')
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_j->'lines') x
                           WHERE x = 'CFB: NoIds Away at NoIds Home, 7:00 PM on FS1.') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the Saturday brief lines: %', v_j->'lines';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_j->'lines') x WHERE x LIKE '%TBD Away%'
                                                                          OR x LIKE 'NHL%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a non-national or non-five game in the brief: %', v_j->'lines';
        END IF;
        -- Six national games on the Sunday: NFL leads, and the cap is 5.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        SELECT 'nba', 'game', '99906810' || g, 'NBA ' || g, (v_sun + time '12:00') AT TIME ZONE v_tz,
               'NBA Home ' || g, 'NBA Away ' || g, ARRAY['ABC'], true, 'scheduled', 'STATUS_SCHEDULED',
               md5('068-nba-' || g)
          FROM generate_series(1, 6) g;
        v_j := public.gameday_brief(v_sun);
        IF jsonb_array_length(v_j->'lines') <> 5 OR v_j->'lines'->>0 NOT LIKE 'NFL: Sun Away at Sun Home%'
           OR v_j->'lines'->>4 <> 'F1, Selftest GP: Race today 1:00 PM.' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the Sunday brief (NFL first, 5 lines): %', v_j->'lines';
        END IF;
        IF public.gameday_brief(v_dead) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a dead night must say nothing';
        END IF;
        -- A night with only a regional game is a dead night for the brief.
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, broadcast,
                                          national_tv, status, espn_status, content_hash)
        VALUES ('nba', 'game', '999068030', 'Regional', (v_dead + time '19:00') AT TIME ZONE v_tz, 'R Home',
                'R Away', ARRAY['MNMT'], false, 'scheduled', 'STATUS_SCHEDULED', md5('068-30'));
        IF public.gameday_brief(v_dead) IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a regional-only night must say nothing: %', public.gameday_brief(v_dead);
        END IF;

        -- --------------------------------------------------------- 7. Wags' context (today, real data)
        v_j := public.ctx_sports_today();
        IF NOT (v_j ?& ARRAY['counts', 'live', 'owner_five', 'owner_five_more', 'other_tonight', 'this_week',
                             'alabama_next']) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: ctx_sports_today keys: %', (SELECT array_agg(k) FROM jsonb_object_keys(v_j) k);
        END IF;

        -- --------------------------------------------------------- 8. test helpers
        v_ok := false;
        BEGIN
            PERFORM public.add_test_gameday_event(v_marker, 'nfl', now(), 'H', 'A');
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'add_test_gameday_event: the owner and a verified test marker only%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: add_test_gameday_event ran with no session'; END IF;

        PERFORM set_config('request.jwt.claims', jsonb_build_object('sub', v_owner, 'role', 'authenticated')::text, true);
        PERFORM set_config('request.jwt.claim.sub', v_owner::text, true);
        INSERT INTO public.e2e_markers (marker_sha256) VALUES (encode(sha256(convert_to(v_marker, 'UTF8')), 'hex'));
        v_test := public.add_test_gameday_event(v_marker, 'cfb', (v_sat + time '11:00') AT TIME ZONE v_tz,
                                                'Test Home', 'Test Away', 'scheduled', ARRAY['CBS'],
                                                '{"home": {"id": "333"}, "away": {"id": "1"}}'::jsonb,
                                                p_link_state => 'not_listed', p_link_reason => 'selftest');
        SELECT is_test, national_tv, link_state INTO v_row FROM public.sports_events WHERE id = v_test;
        IF NOT v_row.is_test OR NOT v_row.national_tv OR v_row.link_state <> 'not_listed' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the test event reads %', v_row;
        END IF;
        IF EXISTS (SELECT 1 FROM public.gameday_slate(v_sat, false, false) s WHERE (s->>'id')::bigint = v_test)
           OR NOT EXISTS (SELECT 1 FROM public.gameday_slate(v_sat, false, true) s
                           WHERE (s->>'id')::bigint = v_test AND s->>'test' = 'true') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a test row shows without p_tests, or not with it';
        END IF;
        IF public.gameday_brief(v_sat)->>'lines' LIKE '%Test Away%' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the brief read a test row';
        END IF;
        PERFORM public.set_test_sports_live(v_marker, v_test, 'live', '{"home": 7, "away": 3}', 'Q2 4:12', '4:12', 2);
        SELECT status, score INTO v_row FROM public.sports_events WHERE id = v_test;
        IF v_row.status <> 'live' OR v_row.score <> '{"home": 7, "away": 3}'::jsonb THEN
            RAISE EXCEPTION 'SELFTEST FAILED: set_test_sports_live: %', v_row;
        END IF;
        v_ok := false;
        BEGIN
            PERFORM public.set_test_sports_live(v_marker, v_bama, 'final', '{"home": 1, "away": 0}');
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'set_test_sports_live: % is not a test event%';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: set_test_sports_live changed a real row'; END IF;

        -- --------------------------------------------------------- 9. grants and the provider
        IF has_function_privilege('anon', 'public.gameday_slate(date, boolean, boolean)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.gameday_live(bigint[])', 'EXECUTE')
           OR has_function_privilege('anon', 'public.gameday_event(bigint)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.ctx_sports_brief()', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.gameday_slate(date, boolean, boolean)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.gameday_brief(date)', 'EXECUTE')
           OR NOT has_function_privilege('valemont_readonly', 'public.ctx_sports_brief()', 'EXECUTE')
           OR NOT has_function_privilege('authenticated', 'public.gameday_live(bigint[])', 'EXECUTE')
           OR has_function_privilege('valemont_readonly',
                  'public.set_test_sports_live(text, bigint, text, jsonb, text, text, integer)', 'EXECUTE')
           OR has_function_privilege('anon',
                  'public.add_test_gameday_event(text, text, timestamptz, text, text, text, text[], jsonb, jsonb, '
                  'boolean, text, text, text, text, text, text, boolean, text)', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: function grants';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.context_providers
                        WHERE key = 'sports_brief' AND brief_section AND enabled AND sort = 47)
           OR NOT EXISTS (SELECT 1 FROM public.context_providers
                           WHERE key = 'sports_today' AND NOT brief_section AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: providers (sports_brief a brief section, sports_today not)';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '068 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '068_gameday';
-- (b) SELECT key, label, brief_section, enabled, sort FROM context_providers
--      WHERE key IN ('sports_today', 'sports_brief') ORDER BY sort;
-- (c) SELECT p.proname, p.prosecdef, has_function_privilege('anon', p.oid, 'EXECUTE') AS anon,
--            has_function_privilege('authenticated', p.oid, 'EXECUTE') AS auth,
--            has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS ro
--       FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--      WHERE n.nspname = 'public' AND (p.proname LIKE 'gameday%' OR p.proname IN ('sports_tv_tier',
--            'ctx_sports_today', 'ctx_sports_brief', 'add_test_gameday_event', 'set_test_sports_live'))
--      ORDER BY 1;    -- readers: prosecdef f, anon f, auth t, ro t; the two test helpers: prosecdef t, ro f
-- (d) SELECT ctx_sports_brief();                          -- tonight's lines, or NULL on a dead night
-- (e) SELECT octet_length(json_agg(s)::text) FROM gameday_slate() s;   -- bytes of the page's default read
