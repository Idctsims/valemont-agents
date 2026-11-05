-- 067_cfb_alias_indiana.sql — one team alias from the Nov 5 alias sweep
-- (Chat 7 Phase 2; docs/kalshi_mapping.md, "The 12 aliases FOR REVIEW").
--
-- What it adds: one sports_team_aliases row, cfb IU -> IND (Indiana Hoosiers).
-- Rows only: no table, function or trigger changes.
--
-- Evidence (read 2026-11-05, ~20:20 UTC; the sweep's method is the FOR
-- REVIEW one: a not_linked row beside an UNCLAIMED open Kalshi event in the
-- same series on ESPN's US-Eastern date, one team's code equal):
--   ESPN sports_events 10190, cfb, USC Trojans @ Indiana Hoosiers,
--   2026-11-14 20:30 UTC (Nov 14 ET), codes USC / IU, not_linked
--   ("no KXNCAAFGAME event for USC/IU within ±1 ET day").
--   Kalshi KXNCAAFGAME-26NOV14USCIND, "USC vs Indiana", sides USC and IND
--   (market yes_sub_title "Indiana"), unclaimed, day offset 0.
--   NCAAB already carries the same alias (mcbb IU -> IND, db/066).
-- The sweep covered all 56 not_linked team_pair rows (cfb 1, mcbb 55); the
-- 55 mcbb rows have no unclaimed Kalshi event on their dates (non-Division I
-- opponents), so this is the only alias it proposes.
--
-- After the paste, the next kalshi_map run links 10190 (not a relink: the
-- row was never linked). Every tuple is (league, espn_code, kalshi_code,
-- note): 4 values for the 4 target columns (checked mechanically before
-- handover). Idempotent: a second paste inserts nothing.

-- ALIASES BEGIN
INSERT INTO sports_team_aliases (league, espn_code, kalshi_code, note)
SELECT v.league, v.espn_code, v.kalshi_code, v.note
  FROM (VALUES
    ('cfb',  'IU',   'IND',   'Indiana Hoosiers (USCIND; mcbb has the same alias)')
  ) AS v (league, espn_code, kalshi_code, note)
 WHERE NOT EXISTS (SELECT 1 FROM sports_team_aliases a
                    WHERE a.league = v.league AND (a.espn_code = v.espn_code OR a.kalshi_code = v.kalshi_code));
-- ALIASES END

INSERT INTO migration_log (name) VALUES ('067_cfb_alias_indiana')
ON CONFLICT (name) DO NOTHING;

-- Self-test, rolled back (the db/032 pattern): every check reads in its own
-- statement; a wrong result raises and the paste applies nothing.
DO $$
DECLARE
    v_ok boolean;
BEGIN
    BEGIN
        IF (SELECT kalshi_code FROM public.sports_team_aliases WHERE league = 'cfb' AND espn_code = 'IU')
           IS DISTINCT FROM 'IND' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: cfb IU does not map to IND';
        END IF;
        IF (SELECT string_agg(league || ' ' || n, ',' ORDER BY league)
              FROM (SELECT league, count(*) AS n FROM public.sports_team_aliases GROUP BY league) c)
           <> 'cfb 15,epl 5,mcbb 61,mls 3,nba 6,nfl 2' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: alias counts are not cfb 15, epl 5, mcbb 61, mls 3, nba 6, nfl 2';
        END IF;
        IF (SELECT kalshi_code FROM public.sports_team_aliases WHERE league = 'mcbb' AND espn_code = 'IU')
           IS DISTINCT FROM 'IND' THEN
            RAISE EXCEPTION 'SELFTEST FAILED: the mcbb IU alias changed';
        END IF;

        v_ok := false;
        BEGIN
            INSERT INTO public.sports_team_aliases (league, espn_code, kalshi_code) VALUES ('cfb', 'XYZ', 'IND');
        EXCEPTION WHEN unique_violation THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a second cfb alias to IND was accepted'; END IF;

        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '067_cfb_alias_indiana') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '067 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '067_cfb_alias_indiana';
-- (b) SELECT league, espn_code, kalshi_code, note FROM sports_team_aliases WHERE espn_code = 'IU';
--      -- cfb IU IND, mcbb IU IND
-- (c) SELECT league, count(*) FROM sports_team_aliases GROUP BY league ORDER BY league;
--      -- cfb 15, epl 5, mcbb 61, mls 3, nba 6, nfl 2 (92)
-- (d) after the next kalshi_map run:
--     SELECT link_state, kalshi_event_ticker, link_method FROM sports_events WHERE id = 10190;
--      -- linked, KXNCAAFGAME-26NOV14USCIND, team_pair
