-- 086_parlay_preregistration_v2.sql
--
-- Chat 8 Phase 4.3 (owner, 2026-11-20). Paste into the Supabase SQL Editor
-- AFTER db/085. Runs top-to-bottom, no BEGIN/COMMIT. Safe to re-run
-- (ON CONFLICT DO NOTHING).
--
-- WHAT. Rows only: no table, column, function or trigger.
--   The stamp of docs/preregistration_parlays.md version 2 (commit 001559d),
--   owner-approved 2026-11-20 before any ticket was committed. Version 2
--   changes four sections; one preregistrations row each (db/017's pattern:
--   the section's sha256 by core/preregistration.py, CRLF to LF, trailing
--   whitespace stripped; registered_at set by the database):
--     §0  the version 2 note (why: the P1 dry runs selected on ESPN single-book
--         p and on normalization; v2 starts cohort 1, there is no P1 cohort);
--     §3  the selection order: ask / the raw fresh Kalshi side mid (heading
--         now "version P2"); fair_p from the full engine never ranks legs;
--     §4  rule_version P2, §7's switch makes P3;
--     §7  P2 stays if M5 is not accepted; the switch is version 3.
--   §1, §2, §5, §6, §8, §9 and §10 are unchanged and keep their db/085 stamps
--   (their hashes on disk equal db/085's). db/085's rows stay: a stamp is
--   never edited or removed.
--
-- The generator refuses to commit unless every section of the file, hashed as
-- it stands, matches a stamp made before now() (consensus/parlay_prereg.py).
-- tests/test_preregistration.py fails if any stamped text changes.

-- ===================================================================== 1
INSERT INTO preregistrations (document, section, content_sha256, note) VALUES
  ('docs/preregistration_parlays.md', '## 0. Amendments',
   'afbeed2e9ee61435cf18c5f303e62a9ce783fde834ae112b55d28076ba09829c', 'parlays v2 (001559d), owner-approved 2026-11-20'),
  ('docs/preregistration_parlays.md', '## 3. Leg rules, version P2 (provisional)',
   '5d0ea27009277d19ea47ee4d5f89e2d20fc4e685468f9b2e9041b5da24ca2d45', 'parlays v2 (001559d), owner-approved 2026-11-20'),
  ('docs/preregistration_parlays.md', '## 4. Prices recorded on every ticket',
   '910d4c0ee27ef20d8315a465dda08be7fed717716803b2279d0e8e8f584c1be3', 'parlays v2 (001559d), owner-approved 2026-11-20'),
  ('docs/preregistration_parlays.md', '## 7. The switch to M5''s rules',
   'e6c10b23c5c2a46215087c0808b4a0fa1e6e5f5606a688b78d314a0d4820a4b3', 'parlays v2 (001559d), owner-approved 2026-11-20')
ON CONFLICT (document, section, content_sha256) DO NOTHING;

INSERT INTO migration_log (name) VALUES ('086_parlay_preregistration_v2')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 2
-- Self-test, run on paste, rolled back ('selftest-rollback'). It checks that
-- every section of version 2 (by its heading as it now stands) has a stamp
-- made before now(): §0, §3, §4, §7 from this file with exactly these hashes,
-- the other seven from db/085; that db/085's rows are all still there; and
-- that a stamp cannot be rewritten or removed. The hashes themselves are
-- checked against the file by tests/test_preregistration.py, which the
-- database cannot read.
DO $$
DECLARE
    v_doc constant text := 'docs/preregistration_parlays.md';
    v_n   integer;
    v_ok  boolean;
    v_missing text;
BEGIN
    BEGIN
        -- 1. The four version 2 rows, exactly.
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = v_doc AND registered_at <= now() AND (section, content_sha256) IN (
               ('## 0. Amendments', 'afbeed2e9ee61435cf18c5f303e62a9ce783fde834ae112b55d28076ba09829c'),
               ('## 3. Leg rules, version P2 (provisional)', '5d0ea27009277d19ea47ee4d5f89e2d20fc4e685468f9b2e9041b5da24ca2d45'),
               ('## 4. Prices recorded on every ticket', '910d4c0ee27ef20d8315a465dda08be7fed717716803b2279d0e8e8f584c1be3'),
               ('## 7. The switch to M5''s rules', 'e6c10b23c5c2a46215087c0808b4a0fa1e6e5f5606a688b78d314a0d4820a4b3'));
        IF v_n <> 4 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 4 version 2 stamps present', v_n;
        END IF;

        -- 2. Every heading of version 2 has a stamp (the seven unchanged ones from db/085).
        SELECT string_agg(h.section, '; ') INTO v_missing
          FROM (VALUES ('## 0. Amendments'), ('## 1. Agents and money'), ('## 2. Schedule and volume'),
                       ('## 3. Leg rules, version P2 (provisional)'), ('## 4. Prices recorded on every ticket'),
                       ('## 5. The commitment'), ('## 6. The CLV proxy'), ('## 7. The switch to M5''s rules'),
                       ('## 8. Reporting'), ('## 9. What these tickets cannot claim'), ('## 10. Data rules')) h(section)
         WHERE NOT EXISTS (SELECT 1 FROM public.preregistrations p
                            WHERE p.document = v_doc AND p.section = h.section AND p.registered_at <= now());
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'SELFTEST FAILED: version 2 sections with no stamp: %', v_missing;
        END IF;

        -- 3. db/085's eleven version 1 rows are all still there (15 rows in all).
        SELECT count(*) INTO v_n FROM public.preregistrations
         WHERE document = v_doc AND note LIKE 'parlays v1 (484fd4d)%';
        IF v_n <> 11 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 11 version 1 stamps remain', v_n;
        END IF;
        SELECT count(*) INTO v_n FROM public.preregistrations WHERE document = v_doc;
        IF v_n <> 15 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % stamps of docs/preregistration_parlays.md, 15 expected', v_n;
        END IF;

        -- 4. A version 2 stamp cannot be rewritten or removed.
        v_ok := false;
        BEGIN
            UPDATE public.preregistrations SET content_sha256 = repeat('0', 64)
             WHERE document = v_doc AND section = '## 3. Leg rules, version P2 (provisional)';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a version 2 stamp was rewritten'; END IF;
        v_ok := false;
        BEGIN
            DELETE FROM public.preregistrations
             WHERE document = v_doc AND section = '## 0. Amendments' AND note LIKE 'parlays v2%';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a version 2 stamp was removed'; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '086 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '086_parlay_preregistration_v2';
-- (b) SELECT section, content_sha256, note, registered_at FROM preregistrations
--      WHERE document = 'docs/preregistration_parlays.md' ORDER BY id;   -- 15 rows: 11 v1, 4 v2
