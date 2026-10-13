-- 043_feed_author_rules.sql — author pages (Chat 3, Phase 3; owner,
-- 2026-10-13 16:19). Rule rows only: no function or trigger, so no self-test.
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
-- tests/test_feed.py runs this pattern against both lists. Applies to new
-- stories; stories already in the feed are not refiled.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex', NULL, '^https?://(www\.)?(axios\.com/local/[^/]+/authors|complex\.com/author)/[^/?#]+/?([?#]|$)',
                  'junk: author pages, Axios Local and Complex (owner, 2026-10-13)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('043_feed_author_rules')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect 1 active row; 51 active feed rules in all; the
-- migration_log row.
SELECT id, kind, pattern, active FROM feed_rules
 WHERE note = 'junk: author pages, Axios Local and Complex (owner, 2026-10-13)';
SELECT count(*) AS active_rules FROM feed_rules WHERE active;
SELECT name, applied_at FROM migration_log WHERE name = '043_feed_author_rules';
