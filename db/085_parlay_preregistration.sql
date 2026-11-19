-- 085_parlay_preregistration.sql
--
-- Chat 8 Phase 4.1 (docs/parlay_generator_plan.md). Paste into the Supabase
-- SQL Editor AFTER db/084 and after the PR that carries
-- docs/preregistration_parlays.md has merged. Runs top-to-bottom, no
-- BEGIN/COMMIT. Safe to re-run (ON CONFLICT DO NOTHING).
--
-- WHAT. Rows only: no table, column, function or trigger.
--   1. The stamp. docs/preregistration_parlays.md v1, approved by the owner
--      2026-11-19 (commit 484fd4d), one preregistrations row per section,
--      §0-§10 (db/017's pattern: the section's sha256 by
--      core/preregistration.py, CRLF to LF, trailing whitespace stripped;
--      registered_at set by the database). The generator refuses to commit
--      unless every section, hashed as it stands, matches a row stamped
--      before now(). tests/test_preregistration.py fails if the text changes.
--   2. The three parlay agents, DISABLED (owner, 2026-11-19): parlay_safer,
--      parlay_mid, parlay_lotto. Enabled by their own migration, with
--      ROSTER=production set by the owner, when Phase 4.4 closes. nfl_ml and
--      _kalshi_probe stay off; db/013 stays unpasted.

-- ===================================================================== 1
INSERT INTO preregistrations (document, section, content_sha256, note) VALUES
  ('docs/preregistration_parlays.md', '## 0. Amendments',
   '21a192adf8382e45efc9be7e908d4d35ed2e4be04c988de576dae3493f1c004b', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 1. Agents and money',
   'a7a4b877055b1c60d61dceccdf812bc196cb2490e3db5c381e428c1e67194f9d', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 2. Schedule and volume',
   '27cf6dd9e44aef020f6a934df6f3807a73df6604001741d84d1559f81304c633', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 3. Leg rules, version P1 (provisional)',
   'f9c9c26e1711c660af478b81c2463e71fc6794ad5ef639f9ca2036f75195c201', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 4. Prices recorded on every ticket',
   '51fb0e679dafc59be726f7fa32a8500f812e25ab858d29ec1006077f55c8ad51', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 5. The commitment',
   '2bf1a263250b51853ea68fc56b19cb173329c8a232afc773da8016a9c7b524e9', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 6. The CLV proxy',
   '8c9a4b1e8bfbe1cd12b6cf0464c40bb1268c9ac312b6d7fcd2f50e9eb2fb2062', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 7. The switch to M5''s rules',
   '3d82a64391d2e646d71479f15dec53655164d80cab5b5967a4cc67623047589f', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 8. Reporting',
   'fb2a9333820e77b04420a077aee285057a4eb1bd822c3ef086522afb6cff5369', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 9. What these tickets cannot claim',
   'b489e818ee6a191b4abf9f0d5664153be4115491952a88e6bc1d14bd959882e0', 'parlays v1 (484fd4d), owner-approved 2026-11-19'),
  ('docs/preregistration_parlays.md', '## 10. Data rules',
   'd091e51559429c5abb643e5e9d24c980ce6cfcfcda47f4bb88ae6003c0b65564', 'parlays v1 (484fd4d), owner-approved 2026-11-19')
ON CONFLICT (document, section, content_sha256) DO NOTHING;

-- ===================================================================== 2
INSERT INTO agents (slug, display_name, domain, enabled, is_test) VALUES
    ('parlay_safer', 'Parlays: Safer (3-5 legs, paper)',  'kalshi', FALSE, FALSE),
    ('parlay_mid',   'Parlays: Mid (6-10 legs, paper)',   'kalshi', FALSE, FALSE),
    ('parlay_lotto', 'Parlays: Lotto (11-20 legs, paper)', 'kalshi', FALSE, FALSE)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO migration_log (name) VALUES ('085_parlay_preregistration')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 3
-- Self-test, run on paste, rolled back ('selftest-rollback'). It checks what
-- was written (the hashes themselves are checked against the file by
-- tests/test_preregistration.py, which the database cannot read) and that
-- the stamp and the agents' identity cannot be changed afterwards.
DO $$
DECLARE
    v_n  integer;
    v_ok boolean;
BEGIN
    BEGIN
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = 'docs/preregistration_parlays.md' AND content_sha256 ~ '^[0-9a-f]{64}$'
           AND section ~ '^## ([0-9]|10)\. ' AND registered_at <= now();
        IF v_n <> 11 OR (SELECT count(DISTINCT section) FROM public.preregistrations
                          WHERE document = 'docs/preregistration_parlays.md') <> 11 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stamped sections of docs/preregistration_parlays.md, 11 expected', v_n;
        END IF;
        IF EXISTS (SELECT 1 FROM generate_series(0, 10) g(i)
                    WHERE NOT EXISTS (SELECT 1 FROM public.preregistrations
                                       WHERE document = 'docs/preregistration_parlays.md'
                                         AND section LIKE '## ' || g.i || '. %')) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: a section of 0-10 has no stamp';
        END IF;

        SELECT count(*) INTO v_n FROM public.agents
         WHERE slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto') AND NOT enabled AND NOT is_test AND domain = 'kalshi';
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 3 parlay agents present, disabled and real', v_n;
        END IF;
        IF EXISTS (SELECT 1 FROM public.agents WHERE slug IN ('nfl_ml', '_kalshi_probe') AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: nfl_ml or _kalshi_probe is enabled';
        END IF;

        v_ok := false;
        BEGIN
            UPDATE public.preregistrations SET content_sha256 = repeat('0', 64)
             WHERE document = 'docs/preregistration_parlays.md' AND section = '## 3. Leg rules, version P1 (provisional)';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a stamp was rewritten'; END IF;
        v_ok := false;
        BEGIN
            UPDATE public.agents SET is_test = true WHERE slug = 'parlay_lotto';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a parlay agent''s identity changed'; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '085 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '085_parlay_preregistration';
-- (b) SELECT section, content_sha256, registered_at FROM preregistrations
--      WHERE document = 'docs/preregistration_parlays.md' ORDER BY id;   -- 11 rows, the file's hashes
-- (c) SELECT slug, display_name, domain, enabled, is_test FROM agents WHERE slug LIKE 'parlay_%';   -- 3, disabled
