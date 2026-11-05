-- 066_kalshi_mapping.sql — ESPN events linked to their Kalshi events (Chat 7
-- Phase 2; docs/kalshi_mapping.md, decided from docs/kalshi_mapping_probe.md).
--
-- What it adds:
--   1. sports_leagues: kalshi_series (the league's Kalshi series, verified by
--      the worker at run time), kalshi_map (how a league's events are matched:
--      team_pair, player_pair, by_date, or off = out of scope) and days_ahead
--      (the schedule pull's horizon: 14 for NFL and CFB, 7 for the rest).
--   2. sports_team_aliases: ESPN team code -> Kalshi team code, EXCEPTIONS
--      ONLY (no row = the same code). Seeded from the probe's matched games:
--      NFL 2, NBA 6, CFB 14, NCAAB 57 (79 rows), plus 12 FOR REVIEW from the
--      Phase 2 dry run (NCAAB 4, EPL 5, MLS 3): 91 rows. App table: the owner
--      reads, the worker reads, only a migration writes; anon nothing;
--      readonly reads.
--   3. sports_events: neutral_site (ESPN's flag; NULL where ESPN does not say,
--      e.g. soccer) and the link columns: link_state (linked | not_listed |
--      not_linked | out_of_scope; NULL = never evaluated), link_reason,
--      link_method, linked_at, link_state_at, home_away_swapped, link_conflict
--      and link_conflict_at. CHECKs tie them together (NULL-safe).
--   4. sports_events_kalshi_ticker: one game, match, bout or race per Kalshi
--      event (a golf tournament's rounds share theirs).
--   5. sports_events_link_guard (trigger, BEFORE INSERT / UPDATE, fires only
--      when a link column changes): THE NO-RELINK RULE. A row once linked
--      keeps its ticker, method and home/away flag for good: never relinked,
--      never unlinked. Stamps linked_at, link_state_at and link_conflict_at
--      from the database clock; a client value for them is ignored.
--
-- The worker (sports/kalshi_map.py) writes the link columns through
-- DATABASE_URL; ESPN's upsert (db/065) never touches them, and the trigger's
-- WHEN clause keeps it out of that path entirely.
--
-- A self-test runs on paste (section 8), rolled back, and raises on any wrong
-- result, so a bad paste applies nothing.

-- ===================================================================== 1
-- The registry: each league's Kalshi series, match method and pull horizon.

ALTER TABLE sports_leagues
    ADD COLUMN IF NOT EXISTS kalshi_series TEXT
        CONSTRAINT sports_leagues_kalshi_series_format CHECK (kalshi_series IS NULL OR kalshi_series ~ '^KX[A-Z0-9]{2,30}$'),
    ADD COLUMN IF NOT EXISTS kalshi_map TEXT NOT NULL DEFAULT 'off'
        CONSTRAINT sports_leagues_kalshi_map_check CHECK (kalshi_map IN ('team_pair', 'player_pair', 'by_date', 'off')),
    ADD COLUMN IF NOT EXISTS days_ahead INTEGER NOT NULL DEFAULT 7
        CONSTRAINT sports_leagues_days_ahead_check CHECK (days_ahead BETWEEN 1 AND 21);

ALTER TABLE sports_leagues DROP CONSTRAINT IF EXISTS sports_leagues_kalshi_mapped_has_series;
ALTER TABLE sports_leagues ADD CONSTRAINT sports_leagues_kalshi_mapped_has_series
    CHECK (kalshi_map = 'off' OR kalshi_series IS NOT NULL);

-- KALSHI REGISTRY BEGIN (scripts/kalshi_map_dry_run.py reads this block)
UPDATE sports_leagues l
   SET kalshi_series = v.kalshi_series, kalshi_map = v.kalshi_map, days_ahead = v.days_ahead
  FROM (VALUES
    ('cfb',  'KXNCAAFGAME',  'team_pair',   14),
    ('nfl',  'KXNFLGAME',    'team_pair',   14),
    ('nba',  'KXNBAGAME',    'team_pair',   7),
    ('f1',   'KXF1RACE',     'by_date',     7),
    ('pga',  'KXPGATOUR',    'by_date',     7),
    ('mcbb', 'KXNCAAMBGAME', 'team_pair',   7),
    ('nhl',  'KXNHLGAME',    'team_pair',   7),
    ('mlb',  'KXMLBGAME',    'off',         7),
    ('epl',  'KXEPLGAME',    'team_pair',   7),
    ('mls',  'KXMLSGAME',    'team_pair',   7),
    ('ucl',  'KXUCLGAME',    'team_pair',   7),
    ('atp',  'KXATPMATCH',   'player_pair', 7),
    ('wta',  'KXWTAMATCH',   'player_pair', 7),
    ('ufc',  'KXUFCFIGHT',   'player_pair', 7)
  ) AS v (code, kalshi_series, kalshi_map, days_ahead)
 WHERE l.code = v.code;
-- KALSHI REGISTRY END

COMMENT ON COLUMN sports_leagues.kalshi_map IS
    'How kalshi_map links this league''s events: team_pair (the unordered code pair, ±1 ET day), player_pair '
    '(the two names, ±1 ET day), by_date (F1 race / golf tournament by date), off (out of scope; MLB is '
    'off-season). docs/kalshi_mapping.md.';

-- ===================================================================== 2
-- Team code exceptions. No row = ESPN's code is Kalshi's.

CREATE TABLE IF NOT EXISTS sports_team_aliases (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league       TEXT         NOT NULL REFERENCES sports_leagues (code),
    espn_code    TEXT         NOT NULL CONSTRAINT sports_team_aliases_espn_format
                                       CHECK (espn_code ~ '^[A-Za-z0-9&''.-]{1,12}$'),
    kalshi_code  TEXT         NOT NULL CONSTRAINT sports_team_aliases_kalshi_format
                                       CHECK (kalshi_code ~ '^[A-Z0-9]{1,12}$'),
    note         TEXT         CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT sports_team_aliases_league_espn UNIQUE (league, espn_code),
    CONSTRAINT sports_team_aliases_league_kalshi UNIQUE (league, kalshi_code),
    CONSTRAINT sports_team_aliases_not_identity CHECK (upper(espn_code) <> kalshi_code)
);

COMMENT ON TABLE sports_team_aliases IS
    'ESPN team code -> Kalshi team code, exceptions only (no row = identity). Seeded by db/066 from the '
    'mapping probe (docs/kalshi_mapping_probe.md §4); changed only by a migration.';

-- Every tuple is (league, espn_code, kalshi_code, note): 4 values for the 4
-- target columns (checked mechanically before handover).
-- ALIASES BEGIN (scripts/kalshi_map_dry_run.py reads this block)
INSERT INTO sports_team_aliases (league, espn_code, kalshi_code, note)
SELECT v.league, v.espn_code, v.kalshi_code, v.note
  FROM (VALUES
    -- NFL (2 of 30 compared; PIT and TEN read from settled events: identity)
    ('nfl',  'JAX',  'JAC',   'Jacksonville Jaguars'),
    ('nfl',  'WSH',  'WAS',   'Washington Commanders'),
    -- NBA (6 of 30)
    ('nba',  'WSH',  'WAS',   'Washington Wizards'),
    ('nba',  'NY',   'NYK',   'New York Knicks'),
    ('nba',  'NO',   'NOP',   'New Orleans Pelicans'),
    ('nba',  'UTAH', 'UTA',   'Utah Jazz'),
    ('nba',  'SA',   'SAS',   'San Antonio Spurs'),
    ('nba',  'GS',   'GSW',   'Golden State Warriors'),
    -- CFB (14 of 129)
    ('cfb',  'MIZ',  'MIZZ',  'Missouri Tigers'),
    ('cfb',  'NCSU', 'NCST',  'NC State Wolfpack'),
    ('cfb',  'CCU',  'CCAR',  'Coastal Carolina Chanticleers'),
    ('cfb',  'SC',   'SCAR',  'South Carolina Gamecocks'),
    ('cfb',  'TA&M', 'TXAM',  'Texas A&M Aggies'),
    ('cfb',  'MTSU', 'MTU',   'Middle Tennessee Blue Raiders'),
    ('cfb',  'CLT',  'CHAR',  'Charlotte 49ers'),
    ('cfb',  'MOST', 'MOSU',  'Missouri State Bears'),
    ('cfb',  'UL',   'ULL',   'Louisiana Ragin'' Cajuns'),
    ('cfb',  'BOIS', 'BSU',   'Boise State Broncos'),
    ('cfb',  'JXST', 'JVST',  'Jacksonville State Gamecocks'),
    ('cfb',  'NU',   'NW',    'Northwestern Wildcats'),
    ('cfb',  'OU',   'OKLA',  'Oklahoma Sooners'),
    ('cfb',  'M-OH', 'MOH',   'Miami (OH) RedHawks'),
    -- NCAAB (57 of 312; the probe's 8 home/away swaps are not aliases)
    ('mcbb', 'HPU',  'HP',    'High Point Panthers'),
    ('mcbb', 'CSUN', 'CSN',   'Cal State Northridge Matadors'),
    ('mcbb', 'MCN',  'MCNS',  'McNeese Cowboys'),
    ('mcbb', 'IU',   'IND',   'Indiana Hoosiers'),
    ('mcbb', 'UIW',  'IW',    'Incarnate Word Cardinals'),
    ('mcbb', 'CHST', 'CHS',   'Chicago State Cougars'),
    ('mcbb', 'UTC',  'CHAT',  'Chattanooga Mocs'),
    ('mcbb', 'CLT',  'CHAR',  'Charlotte 49ers'),
    ('mcbb', 'UML',  'MASSL', 'UMass Lowell River Hawks'),
    ('mcbb', 'MERC', 'MHU',   'Mercyhurst Lakers'),
    ('mcbb', 'SPU',  'SPC',   'Saint Peter''s Peacocks'),
    ('mcbb', 'BUF',  'BUFF',  'Buffalo Bulls'),
    ('mcbb', 'STO',  'STNH',  'Stonehill Skyhawks'),
    ('mcbb', 'LUC',  'LCHI',  'Loyola Chicago Ramblers'),
    ('mcbb', 'WGA',  'UWGA',  'West Georgia Wolves'),
    ('mcbb', 'GWEB', 'WEBB',  'Gardner-Webb Runnin'' Bulldogs'),
    ('mcbb', 'JXST', 'JVST',  'Jacksonville State Gamecocks'),
    ('mcbb', 'CCU',  'CCAR',  'Coastal Carolina Chanticleers'),
    ('mcbb', 'JAX',  'JAC',   'Jacksonville Dolphins'),
    ('mcbb', 'LEM',  'LMC',   'Le Moyne Dolphins'),
    ('mcbb', 'BCU',  'COOK',  'Bethune-Cookman Wildcats'),
    ('mcbb', 'TOW',  'TOWS',  'Towson Tigers'),
    ('mcbb', 'UAPB', 'ARPB',  'Arkansas-Pine Bluff Golden Lions'),
    ('mcbb', 'UWF',  'UWFL',  'West Florida Argonauts'),
    ('mcbb', 'VAL',  'VALP',  'Valparaiso Beacons'),
    ('mcbb', 'NHVN', 'NHC',   'New Haven Chargers'),
    ('mcbb', 'L-MD', 'LMD',   'Loyola Maryland Greyhounds'),
    ('mcbb', 'NCSU', 'NCST',  'NC State Wolfpack'),
    ('mcbb', 'APSU', 'PEAY',  'Austin Peay Governors'),
    ('mcbb', 'PRES', 'PRE',   'Presbyterian Blue Hose'),
    ('mcbb', 'TA&M', 'TXAM',  'Texas A&M Aggies'),
    ('mcbb', 'NU',   'NW',    'Northwestern Wildcats'),
    ('mcbb', 'STBK', 'STON',  'Stony Brook Seawolves'),
    ('mcbb', 'STMN', 'UST',   'St. Thomas Tommies'),
    ('mcbb', 'DETM', 'DET',   'Detroit Mercy Titans'),
    ('mcbb', 'OU',   'OKLA',  'Oklahoma Sooners'),
    ('mcbb', 'UL',   'ULL',   'Louisiana Ragin'' Cajuns'),
    ('mcbb', 'KC',   'UMKC',  'Kansas City Roos'),
    ('mcbb', 'MTSU', 'MTU',   'Middle Tennessee Blue Raiders'),
    ('mcbb', 'OMA',  'NEOM',  'Omaha Mavericks'),
    ('mcbb', 'TAR',  'TARL',  'Tarleton State Texans'),
    ('mcbb', 'ACU',  'AC',    'Abilene Christian Wildcats'),
    ('mcbb', 'CSUF', 'CSF',   'Cal State Fullerton Titans'),
    ('mcbb', 'M-OH', 'MOH',   'Miami (OH) RedHawks'),
    ('mcbb', 'RGV',  'UTRGV', 'UT Rio Grande Valley Vaqueros'),
    ('mcbb', 'NIA',  'NIAG',  'Niagara Purple Eagles'),
    ('mcbb', 'SBU',  'SBON',  'St. Bonaventure Bonnies'),
    ('mcbb', 'UCR',  'UCRV',  'UC Riverside Highlanders'),
    ('mcbb', 'MUR',  'MURR',  'Murray State Racers'),
    ('mcbb', 'GCU',  'GC',    'Grand Canyon Lopes'),
    ('mcbb', 'SC',   'SCAR',  'South Carolina Gamecocks'),
    ('mcbb', 'CAM',  'CAMP',  'Campbell Fighting Camels'),
    ('mcbb', 'BOIS', 'BSU',   'Boise State Broncos'),
    ('mcbb', 'BTLR', 'BUT',   'Butler Bulldogs'),
    ('mcbb', 'AF',   'AFA',   'Air Force Falcons'),
    ('mcbb', 'CSUB', 'CSB',   'Cal State Bakersfield Roadrunners'),
    ('mcbb', 'W&M',  'WM',    'William & Mary Tribe'),
    -- FOR REVIEW: 12 more, from the Phase 2 dry run (2026-11-05 02:11 UTC),
    -- each an ESPN game in the window beside an unclaimed Kalshi event on the
    -- same date with the other team's code equal (docs/kalshi_mapping.md §5).
    -- NCAAB: the probe's §5 matcher misses, now with both sides' codes.
    ('mcbb', 'UALB', 'ALBY',  'UAlbany Great Danes (ALBYMAN, MRMKALBY)'),
    ('mcbb', 'UPST', 'SCUS',  'South Carolina Upstate Spartans (SCSTSCUS)'),
    ('mcbb', 'MIZ',  'MIZZ',  'Missouri Tigers (MIZZSLU; cfb has the same alias)'),
    ('mcbb', 'LR',   'UALR',  'Little Rock Trojans (SELAUALR)'),
    -- EPL
    ('epl',  'MNC',  'MCI',   'Manchester City (NFOMCI)'),
    ('epl',  'BHA',  'BRI',   'Brighton & Hove Albion (BRIBRE)'),
    ('epl',  'LIV',  'LFC',   'Liverpool (CRYLFC)'),
    ('epl',  'CHE',  'CFC',   'Chelsea (SUNCFC)'),
    ('epl',  'MAN',  'MUN',   'Manchester United (MUNAVL)'),
    -- MLS
    ('mls',  'DC',   'DCU',   'D.C. United (DCUNYC)'),
    ('mls',  'RBNY', 'NYRB',  'Red Bull New York (NYRBATL)'),
    ('mls',  'LA',   'LAG',   'LA Galaxy (RSLLAG)')
  ) AS v (league, espn_code, kalshi_code, note)
ON CONFLICT (league, espn_code) DO NOTHING;
-- ALIASES END

-- ===================================================================== 3
-- The link columns, and ESPN's neutral-site flag.

ALTER TABLE sports_events
    ADD COLUMN IF NOT EXISTS neutral_site       BOOLEAN,
    ADD COLUMN IF NOT EXISTS link_state         TEXT
        CONSTRAINT sports_events_link_state_check
        CHECK (link_state IS NULL OR link_state IN ('linked', 'not_listed', 'not_linked', 'out_of_scope')),
    ADD COLUMN IF NOT EXISTS link_reason        TEXT
        CONSTRAINT sports_events_link_reason_length CHECK (link_reason IS NULL OR char_length(link_reason) BETWEEN 1 AND 300),
    ADD COLUMN IF NOT EXISTS link_method        TEXT
        CONSTRAINT sports_events_link_method_check
        CHECK (link_method IS NULL OR link_method IN ('team_pair', 'player_pair', 'by_date')),
    ADD COLUMN IF NOT EXISTS linked_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS link_state_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS home_away_swapped  BOOLEAN,
    ADD COLUMN IF NOT EXISTS link_conflict      TEXT
        CONSTRAINT sports_events_link_conflict_format
        CHECK (link_conflict IS NULL OR link_conflict ~ '^[A-Z0-9][A-Z0-9._-]{1,80}$'),
    ADD COLUMN IF NOT EXISTS link_conflict_at   TIMESTAMPTZ;

-- NULL-safe throughout (db/031's lesson: a CHECK that evaluates to NULL passes).
ALTER TABLE sports_events DROP CONSTRAINT IF EXISTS sports_events_linked_has_ticker;
ALTER TABLE sports_events ADD CONSTRAINT sports_events_linked_has_ticker
    CHECK ((link_state IS NOT DISTINCT FROM 'linked') = (kalshi_event_ticker IS NOT NULL));
ALTER TABLE sports_events DROP CONSTRAINT IF EXISTS sports_events_linked_has_method;
ALTER TABLE sports_events ADD CONSTRAINT sports_events_linked_has_method
    CHECK ((link_state IS NOT DISTINCT FROM 'linked') = (link_method IS NOT NULL));
ALTER TABLE sports_events DROP CONSTRAINT IF EXISTS sports_events_unlinked_has_reason;
ALTER TABLE sports_events ADD CONSTRAINT sports_events_unlinked_has_reason
    CHECK (link_state IS NULL OR link_state = 'linked' OR link_reason IS NOT NULL);
ALTER TABLE sports_events DROP CONSTRAINT IF EXISTS sports_events_swap_only_linked;
ALTER TABLE sports_events ADD CONSTRAINT sports_events_swap_only_linked
    CHECK (home_away_swapped IS NULL OR link_state IS NOT DISTINCT FROM 'linked');
ALTER TABLE sports_events DROP CONSTRAINT IF EXISTS sports_events_conflict_only_linked;
ALTER TABLE sports_events ADD CONSTRAINT sports_events_conflict_only_linked
    CHECK (link_conflict IS NULL
           OR (link_state IS NOT DISTINCT FROM 'linked' AND link_conflict IS DISTINCT FROM kalshi_event_ticker));

-- ===================================================================== 4
-- One game, match, bout or race per Kalshi event. A golf tournament's
-- rounds all carry the tournament's event, so golf is exempt.

CREATE UNIQUE INDEX IF NOT EXISTS sports_events_kalshi_ticker ON sports_events (kalshi_event_ticker)
    WHERE kalshi_event_ticker IS NOT NULL AND kind <> 'golf_round';

COMMENT ON COLUMN sports_events.link_state IS
    'kalshi_map (docs/kalshi_mapping.md): linked | not_listed (starts after the series'' latest listed event) | '
    'not_linked (inside the window, no unique match; link_reason says why) | out_of_scope. NULL: never evaluated.';
COMMENT ON COLUMN sports_events.link_conflict IS
    'A linked row whose later run found a DIFFERENT unique Kalshi candidate: the candidate, flagged; the link '
    'itself never changes (sports_events_link_guard).';

-- ===================================================================== 5
-- The no-relink rule, and the link timestamps.

CREATE OR REPLACE FUNCTION public.sports_events_link_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.linked_at := CASE WHEN NEW.kalshi_event_ticker IS NULL THEN NULL ELSE now() END;
        NEW.link_state_at := CASE WHEN NEW.link_state IS NULL THEN NULL ELSE now() END;
        NEW.link_conflict_at := CASE WHEN NEW.link_conflict IS NULL THEN NULL ELSE now() END;
        RETURN NEW;
    END IF;

    IF OLD.kalshi_event_ticker IS NOT NULL THEN
        IF NEW.kalshi_event_ticker IS DISTINCT FROM OLD.kalshi_event_ticker THEN
            RAISE EXCEPTION 'sports_events_link_guard: event % is linked to %; never relinked or unlinked',
                OLD.id, OLD.kalshi_event_ticker;
        END IF;
        IF (NEW.link_method, NEW.home_away_swapped) IS DISTINCT FROM (OLD.link_method, OLD.home_away_swapped) THEN
            RAISE EXCEPTION 'sports_events_link_guard: event % is linked; its method and home/away flag are frozen',
                OLD.id;
        END IF;
    END IF;

    -- The database's clock, never the client's.
    NEW.linked_at := CASE WHEN OLD.kalshi_event_ticker IS NULL AND NEW.kalshi_event_ticker IS NOT NULL THEN now()
                          ELSE OLD.linked_at END;
    NEW.link_state_at := CASE WHEN NEW.link_state IS DISTINCT FROM OLD.link_state THEN now()
                              ELSE OLD.link_state_at END;
    NEW.link_conflict_at := CASE WHEN NEW.link_conflict IS NULL THEN NULL
                                 WHEN NEW.link_conflict IS DISTINCT FROM OLD.link_conflict THEN now()
                                 ELSE OLD.link_conflict_at END;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sports_events_link_insert ON sports_events;
CREATE TRIGGER sports_events_link_insert
    BEFORE INSERT ON sports_events
    FOR EACH ROW
    WHEN (NEW.link_state IS NOT NULL OR NEW.kalshi_event_ticker IS NOT NULL OR NEW.link_conflict IS NOT NULL
          OR NEW.linked_at IS NOT NULL OR NEW.link_state_at IS NOT NULL OR NEW.link_conflict_at IS NOT NULL)
    EXECUTE FUNCTION public.sports_events_link_guard();

-- Fires only when a link column changes, so ESPN's upsert never reaches it.
DROP TRIGGER IF EXISTS sports_events_link_guard ON sports_events;
CREATE TRIGGER sports_events_link_guard
    BEFORE UPDATE ON sports_events
    FOR EACH ROW
    WHEN ((OLD.kalshi_event_ticker, OLD.link_state, OLD.link_method, OLD.home_away_swapped, OLD.link_conflict,
           OLD.linked_at, OLD.link_state_at, OLD.link_conflict_at)
          IS DISTINCT FROM
          (NEW.kalshi_event_ticker, NEW.link_state, NEW.link_method, NEW.home_away_swapped, NEW.link_conflict,
           NEW.linked_at, NEW.link_state_at, NEW.link_conflict_at))
    EXECUTE FUNCTION public.sports_events_link_guard();

REVOKE ALL ON FUNCTION public.sports_events_link_guard() FROM PUBLIC, anon, authenticated;

-- ===================================================================== 6
-- Access: the aliases are read by the owner and the read-only role only.

ALTER TABLE sports_team_aliases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON sports_team_aliases FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE sports_team_aliases_id_seq FROM anon, authenticated, PUBLIC;
GRANT SELECT ON sports_team_aliases TO authenticated;
DROP POLICY IF EXISTS sports_team_aliases_owner_read ON sports_team_aliases;
CREATE POLICY sports_team_aliases_owner_read ON sports_team_aliases FOR SELECT TO authenticated
    USING ((SELECT public.is_owner()));
GRANT SELECT ON sports_team_aliases TO valemont_readonly;

-- ===================================================================== 7
INSERT INTO migration_log (name) VALUES ('066_kalshi_mapping')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 8
-- Self-test, run on paste, rolled back ('selftest-rollback'). Each expected
-- refusal runs in its own nested block and counts only with its SQLSTATE
-- (and, for the guard, its message). Rows use real-looking ids from
-- 999066000 up and tickers under KXSELFTEST, rolled back with the rest. A
-- write and its read-back are always separate statements (db/062's rule).

DO $$
DECLARE
    v_ok      boolean;
    v_n       int;
    v_a       bigint;
    v_b       bigint;
    v_g1      bigint;
    v_row     record;
BEGIN
    BEGIN
        -- ---------------------------------------------------- 1. the registry
        SELECT count(*) INTO v_n FROM public.sports_leagues WHERE kalshi_series IS NOT NULL;
        IF v_n <> 14 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % leagues carry a Kalshi series (want 14)', v_n;
        END IF;
        IF (SELECT string_agg(code, ',' ORDER BY code) FROM public.sports_leagues WHERE kalshi_map = 'off') <> 'mlb' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: only mlb should be out of scope';
        END IF;
        IF (SELECT string_agg(code || ':' || kalshi_map, ',' ORDER BY code) FROM public.sports_leagues
             WHERE kalshi_map IN ('player_pair', 'by_date')) <> 'atp:player_pair,f1:by_date,pga:by_date,ufc:player_pair,wta:player_pair' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: player_pair / by_date leagues are wrong';
        END IF;
        IF (SELECT string_agg(code, ',' ORDER BY code) FROM public.sports_leagues WHERE days_ahead = 14) <> 'cfb,nfl'
           OR EXISTS (SELECT 1 FROM public.sports_leagues WHERE days_ahead NOT IN (7, 14)) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: days_ahead should be 14 for cfb and nfl, 7 for the rest';
        END IF;
        IF (SELECT kalshi_series FROM public.sports_leagues WHERE code = 'nba') <> 'KXNBAGAME' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: nba is not KXNBAGAME';
        END IF;

        -- ---------------------------------------------------- 2. the aliases
        IF (SELECT string_agg(league || ' ' || n, ',' ORDER BY league)
              FROM (SELECT league, count(*) AS n FROM public.sports_team_aliases GROUP BY league) c)
           <> 'cfb 14,epl 5,mcbb 61,mls 3,nba 6,nfl 2' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: alias counts are not cfb 14, epl 5, mcbb 61, mls 3, nba 6, nfl 2';
        END IF;
        IF (SELECT kalshi_code FROM public.sports_team_aliases WHERE league = 'nfl' AND espn_code = 'JAX') <> 'JAC'
           OR (SELECT kalshi_code FROM public.sports_team_aliases WHERE league = 'cfb' AND espn_code = 'TA&M') <> 'TXAM' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: alias lookups (nfl JAX, cfb TA&M)';
        END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_team_aliases (league, espn_code, kalshi_code) VALUES ('nfl', 'JAX', 'JAX2');
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a second alias for nfl JAX was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_team_aliases (league, espn_code, kalshi_code) VALUES ('nhl', 'BOS', 'BOS');
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an identity alias was accepted'; END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_team_aliases (league, espn_code, kalshi_code) VALUES ('nfl', 'XYZ', 'WAS');
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: two ESPN codes to one Kalshi code were accepted'; END IF;

        -- ---------------------------------------------------- 3. a game, evaluated, then linked
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                          espn_status, content_hash)
        VALUES ('nfl', 'game', '999066001', 'Selftest Away at Selftest Home', now() + interval '2 days',
                'Selftest Home', 'Selftest Away', 'scheduled', 'STATUS_SCHEDULED', md5('066-1'))
        RETURNING id INTO v_a;
        SELECT link_state, linked_at, link_state_at INTO v_row FROM public.sports_events WHERE id = v_a;
        IF v_row.link_state IS NOT NULL OR v_row.linked_at IS NOT NULL OR v_row.link_state_at IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a new row is not unevaluated: %', v_row;
        END IF;

        UPDATE public.sports_events SET link_state = 'not_listed', link_reason = 'selftest: beyond the horizon',
               link_state_at = '2000-01-01' WHERE id = v_a;
        SELECT link_state, linked_at, link_state_at INTO v_row FROM public.sports_events WHERE id = v_a;
        IF v_row.link_state <> 'not_listed' OR v_row.linked_at IS NOT NULL
           OR v_row.link_state_at IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: not_listed should stamp link_state_at with now(): %', v_row;
        END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET link_state = 'not_linked', link_reason = NULL WHERE id = v_a;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: not_linked with no reason was accepted'; END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET link_state = 'linked', link_method = 'team_pair' WHERE id = v_a;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: linked with no ticker was accepted'; END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET link_state = 'linked', kalshi_event_ticker = 'KXSELFTEST-26NOV08AWYHOM'
             WHERE id = v_a;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: linked with no method was accepted'; END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET home_away_swapped = true WHERE id = v_a;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: home_away_swapped on an unlinked row was accepted'; END IF;

        UPDATE public.sports_events
           SET link_state = 'linked', link_reason = NULL, link_method = 'team_pair',
               kalshi_event_ticker = 'KXSELFTEST-26NOV08AWYHOM', home_away_swapped = false,
               linked_at = '2000-01-01'
         WHERE id = v_a;
        SELECT link_state, kalshi_event_ticker, linked_at, link_state_at INTO v_row
          FROM public.sports_events WHERE id = v_a;
        IF v_row.link_state <> 'linked' OR v_row.kalshi_event_ticker <> 'KXSELFTEST-26NOV08AWYHOM'
           OR v_row.linked_at IS DISTINCT FROM now() OR v_row.link_state_at IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the link did not stamp linked_at / link_state_at from now(): %', v_row;
        END IF;

        -- ---------------------------------------------------- 4. the no-relink rule
        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET kalshi_event_ticker = 'KXSELFTEST-26NOV09AWYHOM' WHERE id = v_a;
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'sports_events_link_guard: event % is linked to %; never relinked or unlinked';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a relink was accepted'; END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET kalshi_event_ticker = NULL, link_state = 'not_linked',
                   link_reason = 'selftest', link_method = NULL, home_away_swapped = NULL WHERE id = v_a;
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'sports_events_link_guard: event % is linked to %; never relinked or unlinked';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: an unlink was accepted'; END IF;

        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET home_away_swapped = true WHERE id = v_a;
        EXCEPTION WHEN raise_exception THEN
            v_ok := SQLERRM LIKE 'sports_events_link_guard: event % is linked; its method and home/away flag are frozen';
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a linked row''s home/away flag changed'; END IF;

        -- A client's linked_at is ignored, and ESPN's own update never reaches the guard.
        UPDATE public.sports_events SET linked_at = '2000-01-01', status = 'live', espn_status = 'STATUS_IN_PROGRESS'
         WHERE id = v_a;
        SELECT status, linked_at, kalshi_event_ticker INTO v_row FROM public.sports_events WHERE id = v_a;
        IF v_row.status <> 'live' OR v_row.linked_at IS DISTINCT FROM now()
           OR v_row.kalshi_event_ticker <> 'KXSELFTEST-26NOV08AWYHOM' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a status update disturbed the link: %', v_row;
        END IF;

        -- ---------------------------------------------------- 5. the conflict flag
        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET link_conflict = 'KXSELFTEST-26NOV08AWYHOM' WHERE id = v_a;
        EXCEPTION WHEN check_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a conflict equal to the link was accepted'; END IF;

        UPDATE public.sports_events SET link_conflict = 'KXSELFTEST-26NOV09AWYHOM', link_conflict_at = '2000-01-01'
         WHERE id = v_a;
        SELECT link_conflict, link_conflict_at, kalshi_event_ticker INTO v_row FROM public.sports_events WHERE id = v_a;
        IF v_row.link_conflict <> 'KXSELFTEST-26NOV09AWYHOM' OR v_row.link_conflict_at IS DISTINCT FROM now()
           OR v_row.kalshi_event_ticker <> 'KXSELFTEST-26NOV08AWYHOM' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the conflict flag: %', v_row;
        END IF;

        -- ---------------------------------------------------- 6. one game per Kalshi event (golf exempt)
        INSERT INTO public.sports_events (league, kind, espn_id, event_name, starts_at, home, away, status,
                                          espn_status, content_hash)
        VALUES ('nfl', 'game', '999066002', 'Selftest B at Selftest C', now() + interval '2 days',
                'Selftest C', 'Selftest B', 'scheduled', 'STATUS_SCHEDULED', md5('066-2'))
        RETURNING id INTO v_b;
        v_ok := false;
        BEGIN
            UPDATE public.sports_events SET link_state = 'linked', link_method = 'team_pair',
                   kalshi_event_ticker = 'KXSELFTEST-26NOV08AWYHOM' WHERE id = v_b;
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: two games linked to one Kalshi event'; END IF;

        INSERT INTO public.sports_events (league, kind, espn_id, parent_espn_id, event_name, session, starts_at,
                                          time_tbd, status, espn_status, content_hash, link_state, link_method,
                                          kalshi_event_ticker, linked_at)
        VALUES ('pga', 'golf_round', '999066003-r1', '999066003', 'Selftest Open', 'R1', now() + interval '1 day',
                true, 'scheduled', 'STATUS_SCHEDULED', md5('066-3'), 'linked', 'by_date', 'KXSELFTEST-OPEN26',
                '2000-01-01'),
               ('pga', 'golf_round', '999066003-r2', '999066003', 'Selftest Open', 'R2', now() + interval '2 days',
                true, 'scheduled', 'STATUS_SCHEDULED', md5('066-4'), 'linked', 'by_date', 'KXSELFTEST-OPEN26',
                NULL);
        SELECT count(*), count(*) FILTER (WHERE linked_at = now() AND link_state_at = now()) INTO v_n, v_g1
          FROM public.sports_events WHERE parent_espn_id = '999066003' AND league = 'pga';
        IF v_n <> 2 OR v_g1 <> 2 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: two golf rounds sharing one event, stamped on insert (% rows, % stamped)',
                v_n, v_g1;
        END IF;

        -- ---------------------------------------------------- 7. neutral_site, grants
        UPDATE public.sports_events SET neutral_site = true WHERE id = v_b;
        IF NOT (SELECT neutral_site FROM public.sports_events WHERE id = v_b) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: neutral_site';
        END IF;
        IF has_table_privilege('anon', 'public.sports_team_aliases', 'SELECT,INSERT,UPDATE,DELETE')
           OR NOT has_table_privilege('authenticated', 'public.sports_team_aliases', 'SELECT')
           OR has_table_privilege('authenticated', 'public.sports_team_aliases', 'INSERT,UPDATE,DELETE,TRUNCATE')
           OR NOT has_table_privilege('valemont_readonly', 'public.sports_team_aliases', 'SELECT') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sports_team_aliases grants';
        END IF;
        IF has_function_privilege('authenticated', 'public.sports_events_link_guard()', 'EXECUTE')
           OR has_function_privilege('anon', 'public.sports_events_link_guard()', 'EXECUTE') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the guard function is executable by a client role';
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.sports_team_aliases'::regclass)
           OR (SELECT count(*) FROM pg_policies WHERE tablename = 'sports_team_aliases') <> 1 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: sports_team_aliases RLS / policy';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '066 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '066_kalshi_mapping';
-- (b) SELECT code, kalshi_series, kalshi_map, days_ahead FROM sports_leagues ORDER BY sort;
-- (c) SELECT league, count(*) FROM sports_team_aliases GROUP BY league ORDER BY league;
--      -- cfb 14, epl 5, mcbb 61, mls 3, nba 6, nfl 2 (91)
-- (d) SELECT c.relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname),
--            has_table_privilege('anon', c.oid, 'SELECT'), has_table_privilege('authenticated', c.oid, 'SELECT'),
--            has_table_privilege('authenticated', c.oid, 'INSERT,UPDATE,DELETE'),
--            has_table_privilege('valemont_readonly', c.oid, 'SELECT')
--       FROM pg_class c WHERE c.oid = 'public.sports_team_aliases'::regclass;          -- t, 1, f, t, f, t
-- (e) SELECT column_name, data_type FROM information_schema.columns
--      WHERE table_name = 'sports_events' AND (column_name LIKE 'link%' OR column_name IN
--            ('linked_at', 'home_away_swapped', 'neutral_site')) ORDER BY 1;          -- 9 columns
-- (f) SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = 'public.sports_events'::regclass
--      AND NOT tgisinternal ORDER BY 1;                                                  -- link_guard, link_insert: O
-- (g) SELECT indexname FROM pg_indexes WHERE tablename = 'sports_events' AND indexname = 'sports_events_kalshi_ticker';
-- (h) SELECT count(*) FILTER (WHERE link_state IS NOT NULL), count(*) FILTER (WHERE kalshi_event_ticker IS NOT NULL)
--       FROM sports_events;                                                              -- 0, 0 until kalshi_map deploys
