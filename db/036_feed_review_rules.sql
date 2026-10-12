-- 036_feed_review_rules.sql — drop film/TV/album reviews (Chat 3, Phase 1;
-- owner, 2026-10-11). Rule rows only.
--
-- A review is a headline that starts "Review:", or names a quoted title
-- followed by "Review:" or ending in " Review":
--     Review: 'Hamnet' is a quiet triumph
--     ‘The Sea Speaks His Name’ Review: Absorbing ...
--     Taylor Swift’s ‘The Life of a Showgirl’ Review
-- A business "review" has no quoted title before it, so it survives:
--     Paramount Board Launches Strategic Review
--     Deal Under Regulatory Review
--     Warner Bros. Discovery Faces Antitrust Review
-- (tests/test_feed.py runs these exact patterns against both lists.)
--
-- Quotes: straight ' and ", curly ‘ ’ “ ”. Patterns are case-insensitive and
-- valid in Python and Postgres alike (no \b); feed_rules' CHECK compiles them.
-- Like every title rule, they apply when a story is created: stories already
-- in the feed are not refiled.
--
-- Prerequisites: db/032.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

INSERT INTO feed_rules (kind, pillar, pattern, note) VALUES
    ('title_regex', NULL, '^review:',                                    'review (owner, 2026-10-11)'),
    ('title_regex', NULL, '[‘''"“][^‘’''"“”]+[’''"”] review:',            'review (owner, 2026-10-11)'),
    ('title_regex', NULL, '[‘''"“][^‘’''"“”]+[’''"”] review$',            'review (owner, 2026-10-11)')
ON CONFLICT ON CONSTRAINT feed_rules_once DO NOTHING;

INSERT INTO migration_log (name) VALUES ('036_feed_review_rules')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect 3 rows; the migration_log row.
SELECT id, pattern FROM feed_rules WHERE note = 'review (owner, 2026-10-11)' ORDER BY id;
SELECT name, applied_at FROM migration_log WHERE name = '036_feed_review_rules';
