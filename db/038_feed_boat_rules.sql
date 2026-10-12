-- 038_feed_boat_rules.sql — Boat International's directory pages (Chat 3,
-- Phase 1; owner, 2026-10-11). Rule rows only.
--
-- The bare model pages ("Wajer 77", "Sunreef 80 Power", "Azimut 78 Fly") and
-- the yacht-for-sale listings share one path, the superyacht directory:
--     https://www.boatinternational.com/yachts/the-superyacht-directory/wajer-77--16833
--     https://www.boatinternational.com/yachts/the-superyacht-directory/blessed--16818
-- and builder pages ("Fountaine Pajot Yachts Yachts For Sale and Charter")
-- live under /profiles/:
--     https://www.boatinternational.com/profiles/fountaine-pajot-yachts--17883
-- News lives elsewhere (/yachts/news/, /yachts/reports/,
-- /yachts/editorial-features/), so a url_regex on those two paths drops the
-- pages, never a headline. Checked against every Boat International item in
-- the database on 2026-10-12 (db_inspect): 42 of 43 match (40 directory, 2
-- profiles); the one kept is the only news article.
--
-- tests/test_feed.py runs this pattern against both lists, including news
-- URLs for "Feadship launches 87m Project 1012" and "Sunreef sells 40m
-- catamaran to ...". Applies to new stories; stories already in the feed are
-- not refiled.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex', NULL, '^https?://(www\.)?boatinternational\.com/(yachts/the-superyacht-directory|profiles)/',
                  'junk: Boat International directory and builder pages (owner, 2026-10-11)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('038_feed_boat_rules')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect 1 row; the migration_log row.
SELECT id, kind, pattern FROM feed_rules
 WHERE note = 'junk: Boat International directory and builder pages (owner, 2026-10-11)';
SELECT name, applied_at FROM migration_log WHERE name = '038_feed_boat_rules';
