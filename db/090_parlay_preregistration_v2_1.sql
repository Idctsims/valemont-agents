-- 090_parlay_preregistration_v2_1.sql
--
-- Chat 8 Phase 4 (owner, 2026-11-24). Paste into the Supabase SQL Editor
-- AFTER db/086. Runs top-to-bottom, no BEGIN/COMMIT. Safe to re-run
-- (ON CONFLICT DO NOTHING).
--
-- WHAT. Rows only: no table, column, function or trigger.
--   The stamp of docs/preregistration_parlays.md version 2.1 (commit 34bc2b4),
--   owner-approved 2026-11-24, rule version P2.1, forward only. Version 2.1
--   changes four sections; one preregistrations row each (db/017's pattern:
--   the section's sha256 by core/preregistration.py, CRLF to LF, trailing
--   whitespace stripped; registered_at set by the database):
--     §0  the version 2.1 note (why §3, §5, §6; P2 tickets are cohort 1,
--         P2.1 tickets cohort 2, never pooled, cohort 1 never re-scored);
--     §3  heading now "version P2.1": the depth-first rebuild when the greedy
--         build draws nothing for a scope (20,000 rule checks per leg count),
--         and `3 per game` as a binding rule;
--     §5  resolves_after = the later of the latest leg's expected expiration
--         and closes_at + 1 minute;
--     §6  the close: the last candle within 60 minutes before kickoff, two-
--         sided, with its age; ages beside every CLV figure; a postponed
--         leg's kickoff re-read at capture.
--   §1, §4, §7 keep their db/086 stamps and §2, §8, §9, §10 their db/085
--   stamps (their hashes on disk are unchanged). db/085's and db/086's rows
--   stay: a stamp is never edited or removed.
--
-- The generator refuses to commit unless every section of the file, hashed as
-- it stands, matches a stamp made before now() (consensus/parlay_prereg.py).
-- tests/test_preregistration.py fails if any stamped text changes.

-- ===================================================================== 1
INSERT INTO preregistrations (document, section, content_sha256, note) VALUES
  ('docs/preregistration_parlays.md', '## 0. Amendments',
   'dbf52ad55c2b77f0cebcb7bd2ca706643c1fc026b91fe5fea2dc307008696e6e', 'parlays v2.1 (34bc2b4), owner-approved 2026-11-24'),
  ('docs/preregistration_parlays.md', '## 3. Leg rules, version P2.1 (provisional)',
   '074475cdfeee63db1635bf17136500a73858a5049434655e31b6b0decfd95eb7', 'parlays v2.1 (34bc2b4), owner-approved 2026-11-24'),
  ('docs/preregistration_parlays.md', '## 5. The commitment',
   '18e085881630eb2977c636b9ad8916afa390bf488ce4b2f9dd415473d9a56298', 'parlays v2.1 (34bc2b4), owner-approved 2026-11-24'),
  ('docs/preregistration_parlays.md', '## 6. The CLV proxy',
   '878d8f7147eeb4df8c348f896561119f22546fb21359706e27dfd6628dd43916', 'parlays v2.1 (34bc2b4), owner-approved 2026-11-24')
ON CONFLICT (document, section, content_sha256) DO NOTHING;

INSERT INTO migration_log (name) VALUES ('090_parlay_preregistration_v2_1')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 2
-- Self-test, run on paste, rolled back ('selftest-rollback'). It checks that
-- every section of version 2.1 (by its heading as it now stands) has a stamp
-- made before now(): §0, §3, §5, §6 from this file with exactly these hashes,
-- §1, §4, §7 from db/086 and §2, §8, §9, §10 from db/085 with their hashes;
-- that db/085's and db/086's rows are all still there; and that a stamp
-- cannot be rewritten or removed. The hashes themselves are checked against
-- the file by tests/test_preregistration.py, which the database cannot read.
DO $$
DECLARE
    v_doc constant text := 'docs/preregistration_parlays.md';
    v_n   integer;
    v_ok  boolean;
    v_missing text;
BEGIN
    BEGIN
        -- 1. The four version 2.1 rows, exactly.
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = v_doc AND registered_at <= now() AND (section, content_sha256) IN (
               ('## 0. Amendments', 'dbf52ad55c2b77f0cebcb7bd2ca706643c1fc026b91fe5fea2dc307008696e6e'),
               ('## 3. Leg rules, version P2.1 (provisional)', '074475cdfeee63db1635bf17136500a73858a5049434655e31b6b0decfd95eb7'),
               ('## 5. The commitment', '18e085881630eb2977c636b9ad8916afa390bf488ce4b2f9dd415473d9a56298'),
               ('## 6. The CLV proxy', '878d8f7147eeb4df8c348f896561119f22546fb21359706e27dfd6628dd43916'));
        IF v_n <> 4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 4 version 2.1 stamps present', v_n;
        END IF;

        -- 2. Every section of version 2.1, at the hash it has on disk, has a stamp:
        --    the four above, §1 §4 §7 from db/086, §2 §8 §9 §10 from db/085.
        SELECT string_agg(h.section, '; ') INTO v_missing
          FROM (VALUES
                ('## 0. Amendments', 'dbf52ad55c2b77f0cebcb7bd2ca706643c1fc026b91fe5fea2dc307008696e6e'),
                ('## 1. Agents and money', '598af2a4f6564fa810ccc4e5c5b64fec58739b2a13dbc4824a35b373ca1e6a5f'),
                ('## 2. Schedule and volume', '27cf6dd9e44aef020f6a934df6f3807a73df6604001741d84d1559f81304c633'),
                ('## 3. Leg rules, version P2.1 (provisional)', '074475cdfeee63db1635bf17136500a73858a5049434655e31b6b0decfd95eb7'),
                ('## 4. Prices recorded on every ticket', '910d4c0ee27ef20d8315a465dda08be7fed717716803b2279d0e8e8f584c1be3'),
                ('## 5. The commitment', '18e085881630eb2977c636b9ad8916afa390bf488ce4b2f9dd415473d9a56298'),
                ('## 6. The CLV proxy', '878d8f7147eeb4df8c348f896561119f22546fb21359706e27dfd6628dd43916'),
                ('## 7. The switch to M5''s rules', 'e6c10b23c5c2a46215087c0808b4a0fa1e6e5f5606a688b78d314a0d4820a4b3'),
                ('## 8. Reporting', 'fb2a9333820e77b04420a077aee285057a4eb1bd822c3ef086522afb6cff5369'),
                ('## 9. What these tickets cannot claim', 'b489e818ee6a191b4abf9f0d5664153be4115491952a88e6bc1d14bd959882e0'),
                ('## 10. Data rules', 'd091e51559429c5abb643e5e9d24c980ce6cfcfcda47f4bb88ae6003c0b65564')) h(section, sha)
         WHERE NOT EXISTS (SELECT 1 FROM public.preregistrations p
                            WHERE p.document = v_doc AND p.section = h.section
                              AND p.content_sha256 = h.sha AND p.registered_at <= now());
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: version 2.1 sections with no stamp at their hash: %', v_missing;
        END IF;

        -- 3. db/085's eleven and db/086's five rows are all still there (20 rows in all).
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = v_doc AND note LIKE 'parlays v1 (484fd4d)%';
        IF v_n <> 11 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 11 version 1 stamps remain', v_n;
        END IF;
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = v_doc AND note LIKE 'parlays v2 (d34a8e8)%';
        IF v_n <> 5 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 5 version 2 stamps remain', v_n;
        END IF;
        SELECT count(*) INTO v_n FROM public.preregistrations WHERE document = v_doc;
        IF v_n <> 20 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stamps of docs/preregistration_parlays.md, 20 expected', v_n;
        END IF;

        -- 4. A version 2.1 stamp cannot be rewritten or removed.
        v_ok := false;
        BEGIN
            UPDATE public.preregistrations SET content_sha256 = repeat('0', 64)
             WHERE document = v_doc AND section = '## 3. Leg rules, version P2.1 (provisional)';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a version 2.1 stamp was rewritten'; END IF;
        v_ok := false;
        BEGIN
            DELETE FROM public.preregistrations
             WHERE document = v_doc AND section = '## 6. The CLV proxy' AND note LIKE 'parlays v2.1%';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a version 2.1 stamp was removed'; END IF;

        -- 5. This file's migration_log row.
        IF NOT EXISTS (SELECT 1 FROM public.migration_log WHERE name = '090_parlay_preregistration_v2_1') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: no migration_log row for 090';
        END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '090 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '090_parlay_preregistration_v2_1';
-- (b) SELECT section, content_sha256, note, registered_at FROM preregistrations
--      WHERE document = 'docs/preregistration_parlays.md' ORDER BY id;   -- 20 rows: 11 v1, 5 v2, 4 v2.1
