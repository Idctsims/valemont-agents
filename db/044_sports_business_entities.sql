-- 044_sports_business_entities.sql — broadcasters and more leagues, for the
-- business-of-sports gate (Chat 3 close-out; owner, 2026-10-13 17:08).
--
-- The shipped categories pass (docs/feed_checks/recategorize-2026-10-13-final/)
-- asks the business-of-sports question of a story answered film_music or
-- other only when its entities include a sports entity from this table, and
-- the table had no broadcaster and no MLB, NWSL, WNBA or NCAA: McAfee on
-- ESPN, Skydance, Disney's alt-casts, Tirico on NBC, the Dodgers and MLB,
-- the NCAA eligibility case were never asked.
--
-- What it holds:
--   entities.kind  gains 'broadcaster' (the CHECK is replaced).
--   entities       7 broadcasters (ESPN, NBC Sports, CBS Sports, FOX Sports,
--                  Amazon Prime Video, Netflix, TNT Sports) and 4 leagues
--                  (MLB, NWSL, WNBA, NCAA), sport NULL (the sport column
--                  covers the five tracked sports only). NO follows: they
--                  change no ranking, only categorization.
--
-- Aliases are lowercase (the CHECK) and written the way a summary names the
-- entity, because workers/feed/category.sports_entity matches the WHOLE
-- entity string. Broadcasters are mostly entertainment companies, so a
-- story gated by an entity alone keeps its enum answer on "no" (a Netflix
-- drama stays film_music); only "yes" moves it to sports_business
-- (category.decide, owner-approved 2026-10-13).
--
-- No function or trigger, so no self-test.
-- Prerequisites: db/039 (entities), db/042 (entities.is_test).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_kind_check;
ALTER TABLE entities ADD CONSTRAINT entities_kind_check
    CHECK (kind IN ('person', 'team', 'program', 'league', 'tour', 'company', 'broadcaster'));

INSERT INTO entities (kind, name, aliases, sport) VALUES
    ('broadcaster', 'ESPN',               ARRAY['espn', 'espn+', 'espn2', 'abc sports'],                    NULL),
    ('broadcaster', 'NBC Sports',         ARRAY['nbc', 'nbc sports', 'peacock'],                            NULL),
    ('broadcaster', 'CBS Sports',         ARRAY['cbs', 'cbs sports', 'paramount+'],                         NULL),
    ('broadcaster', 'FOX Sports',         ARRAY['fox', 'fox sports', 'fs1'],                                NULL),
    ('broadcaster', 'Amazon Prime Video', ARRAY['prime video', 'amazon prime video', 'amazon'],             NULL),
    ('broadcaster', 'Netflix',            ARRAY['netflix'],                                                 NULL),
    ('broadcaster', 'TNT Sports',         ARRAY['tnt', 'tnt sports'],                                       NULL),
    ('league',      'MLB',                ARRAY['mlb', 'major league baseball'],                            NULL),
    ('league',      'NWSL',               ARRAY['nwsl', 'national women''s soccer league'],                 NULL),
    ('league',      'WNBA',               ARRAY['wnba'],                                                    NULL),
    ('league',      'NCAA',               ARRAY['ncaa'],                                                    NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO migration_log (name) VALUES ('044_sports_business_entities')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect: (a) the CHECK lists broadcaster; (b) 11 rows, 7
-- broadcasters and 4 leagues, none test, none followed; (c) the
-- migration_log row.
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'entities_kind_check';   -- (a)
SELECT e.kind, e.name, e.aliases, e.is_test,
       EXISTS (SELECT 1 FROM follows f WHERE f.entity_id = e.id) AS followed
  FROM entities e
 WHERE e.name IN ('ESPN', 'NBC Sports', 'CBS Sports', 'FOX Sports', 'Amazon Prime Video', 'Netflix',
                  'TNT Sports', 'MLB', 'NWSL', 'WNBA', 'NCAA')
 ORDER BY e.kind, e.name;                                                                    -- (b)
SELECT name, applied_at FROM migration_log WHERE name = '044_sports_business_entities';      -- (c)
