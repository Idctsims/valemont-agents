-- 043_feed_author_rules.sql — author pages and PGA Tour player pages (Chat 3,
-- Phase 3; owner, 2026-10-13 16:19 and 16:37). Rule rows only: no function
-- or trigger, so no self-test.
--
-- The first categorize pass filed two bare names, "Chelsea Brasted" and
-- "Carly Mallenbaum", as stories. They are author pages, not news. Every
-- author/people/staff/contributor/writer/profile path in content_items was
-- listed on 2026-10-13 (db_inspect); the author pages among them are exactly
-- two shapes, 13 items of 4,226:
--     https://www.axios.com/local/miami/authors/cbrasted          (10 like it)
--     https://www.complex.com/author/complexstaff3                 (3 like it)
-- The pattern ends at the author slug, so articles on the same sites
-- survive, including Complex's, which carry the author slug inside the path:
--     https://www.complex.com/pop-culture/a/complexstaff3/complexcon-bitcoin-cash-app-activation
--     https://www.axios.com/local/miami/2026/10/13/what-is-going-up-miami-development-real-estate
-- Not included: the one /people/ URL in the data is a real Sportico article
-- (https://www.sportico.com/personalities/people/2026/sportico-transactions-...),
-- no staff page occurs, and Boat International's /profiles/ pages are
-- already db/038's.
--
-- PGA Tour player pages (owner, 2026-10-13 16:37): profile, bio and results
-- pages, 13 of the 158 pgatour.com items (db_inspect, 2026-10-13), in four
-- shapes, with an optional language and tour prefix:
--     https://www.pgatour.com/player/profiles/32781/results
--     https://www.pgatour.com/player/615/-/bio
--     https://www.pgatour.com/players/player.53465.justin-thomas.html
--     https://www.pgatour.com/korn-ferry-tour/player/45486/joaquin-niemann
--     https://www.pgatour.com/pt/pgatour-champions/player/23386/david-roesch/career
-- News lives under /article/news/ (with or without a tour prefix) and
-- clips under /video/; neither matches:
--     https://www.pgatour.com/article/news/latest/2026/10/13/jackson-koivun-brooks-koepka-...-skins-game-...
--     https://www.pgatour.com/korn-ferry-tour/article/news/latest/2026/10/11/korn-ferry-tour-championship-austin-hitt-...
--
-- tests/test_feed.py runs both patterns against their lists. Applies to new
-- stories; stories already in the feed are not refiled.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex', NULL, '^https?://(www\.)?(axios\.com/local/[^/]+/authors|complex\.com/author)/[^/?#]+/?([?#]|$)',
                  'junk: author pages, Axios Local and Complex (owner, 2026-10-13)'),
    ('url_regex', NULL, '^https?://(www\.)?pgatour\.com/([a-z]{2}/)?((korn-ferry-tour|pgatour-champions|americas)/)?players?/',
                  'junk: PGA Tour player profile and results pages (owner, 2026-10-13)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('043_feed_author_rules')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect 2 active rows; 52 active feed rules in all; the
-- migration_log row.
SELECT id, kind, pattern, active FROM feed_rules
 WHERE note IN ('junk: author pages, Axios Local and Complex (owner, 2026-10-13)',
                'junk: PGA Tour player profile and results pages (owner, 2026-10-13)')
 ORDER BY id;
SELECT count(*) AS active_rules FROM feed_rules WHERE active;
SELECT name, applied_at FROM migration_log WHERE name = '043_feed_author_rules';
