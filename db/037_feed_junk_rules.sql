-- 037_feed_junk_rules.sql — junk rules from the re-ingest (Chat 3, Phase 1;
-- owner, 2026-10-11). Rule rows only.
--
-- 1. Complex's Google News results include shop and tag pages:
--      https://www.complex.com/shop/products/uprs-la-rise-white-t-shirt
--      https://www.complex.com/shop/artists/Sabrina%20Carpente
--      https://www.complex.com/tag/rivian
--    Articles are always /<section>/a/<author>/<slug>
--    (https://www.complex.com/music/a/treyalston/megan-thee-stallion-miso-adopt),
--    so the rule names the shop, store and tag paths only.
-- 2. Titles that are not headlines: a leading "- " (NFL.com's
--    "- NFL Communications"), or a bare site or section name.
-- 3. Reviews, extended (db/036 caught a quoted title directly before
--    "Review:"): a quoted title, then at most 4 words, then "Review:" —
--    "“Severance” Season 3 Review: ...". The opening quote must start a word
--    (start of title, a space or "("), so an apostrophe ("Disney's board and
--    Iger's succession review:") is never read as a quoted title. Business
--    reviews survive: they have no quoted title.
--
-- tests/test_feed.py runs these patterns against keep/drop lists. Like every
-- rule, they apply to new stories; stories already in the feed are not refiled.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('url_regex',   NULL, '^https?://(www\.)?complex\.com/(shop|store|tags?)(/|\?|$)',
                    'junk: Complex shop/store/tag pages (owner, 2026-10-11)'),
    ('title_regex', NULL, '^- ',
                    'junk: not a headline (owner, 2026-10-11)'),
    ('title_regex', NULL, '^(nfl communications|nfl\.com|nba\.com|news|latest news|top stories|home|sports|video|videos|podcasts?)$',
                    'junk: bare site/section name (owner, 2026-10-11)'),
    ('title_regex', NULL, '(^|[ (])[‘''"“][^‘’''"“”]+[’''"”]( [^ :]+){0,4} review:',
                    'review, extended (owner, 2026-10-11)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('037_feed_junk_rules')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect 4 rows; the migration_log row.
SELECT id, kind, pattern FROM feed_rules
 WHERE note LIKE 'junk:%(owner, 2026-10-11)' OR note = 'review, extended (owner, 2026-10-11)'
 ORDER BY id;
SELECT name, applied_at FROM migration_log WHERE name = '037_feed_junk_rules';
