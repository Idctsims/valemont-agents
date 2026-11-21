-- 087_enable_parlay_agents.sql
--
-- Chat 8 Phase 4.4 (docs/parlay_generator_plan.md §4.4; owner, Nov 20). Paste
-- into the Supabase SQL Editor AFTER db/086. Runs top-to-bottom, no
-- BEGIN/COMMIT. Safe to re-run.
--
-- WHAT. Rows only: no table, column, function or trigger.
--   Enables the three parlay agents db/085 added disabled: parlay_safer (12),
--   parlay_mid (13), parlay_lotto (14). `enabled` is one of the two columns
--   db/015 leaves updatable; identity stays frozen.
--   nfl_ml and _kalshi_probe stay DISABLED; db/013 stays unpasted.
--
-- WHAT IT DOES ON ITS OWN: nothing runs. An agent is scheduled only when BOTH
-- gates hold (core/orchestrator.py `_wire`): a Registration (ROSTER=production
-- registers these three, plus nfl_ml and _kalshi_probe, which this file leaves
-- off) and agents.enabled. The worker reads agents.enabled at boot. Until the
-- owner sets ROSTER=production on Railway, the parlay agents are not
-- registered and the parlay_requote job skips ("ROSTER is not production").
-- Paper only: nothing places an order (CLAUDE.md §1).

-- ===================================================================== 1
UPDATE agents SET enabled = TRUE
 WHERE slug IN ('parlay_safer', 'parlay_mid', 'parlay_lotto') AND NOT is_test AND NOT enabled;

INSERT INTO migration_log (name) VALUES ('087_enable_parlay_agents')
ON CONFLICT (name) DO NOTHING;

-- ===================================================================== 2
-- Self-test, run on paste, rolled back ('selftest-rollback').
DO $$
DECLARE
    v_n  integer;
    v_ok boolean;
BEGIN
    BEGIN
        -- 1. The three parlay agents: enabled, real, domain kalshi, their db/085 ids.
        SELECT count(*) INTO v_n FROM public.agents
         WHERE (id, slug) IN ((12, 'parlay_safer'), (13, 'parlay_mid'), (14, 'parlay_lotto'))
           AND enabled AND NOT is_test AND domain = 'kalshi';
        IF v_n <> 3 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % of 3 parlay agents enabled, real, kalshi', v_n;
        END IF;

        -- 2. nfl_ml and _kalshi_probe stay off; db/013 is not pasted.
        IF EXISTS (SELECT 1 FROM public.agents WHERE slug IN ('nfl_ml', '_kalshi_probe') AND enabled) THEN
            RAISE EXCEPTION 'SELFTEST FAILED: nfl_ml or _kalshi_probe is enabled';
        END IF;
        IF EXISTS (SELECT 1 FROM public.migration_log WHERE name LIKE '013%') THEN
            RAISE EXCEPTION 'SELFTEST FAILED: db/013 has a migration_log row';
        END IF;

        -- 3. No real agent other than the three is enabled (only the _fake canary
        --    harness, is_test, may be: it runs only under CANARY, never ROSTER).
        SELECT count(*) INTO v_n FROM public.agents
         WHERE enabled AND NOT is_test AND slug NOT IN ('parlay_safer', 'parlay_mid', 'parlay_lotto');
        IF v_n <> 0 THEN
            RAISE EXCEPTION 'SELFTEST FAILED: % other real agents enabled', v_n;
        END IF;

        -- 4. Identity stays frozen (db/015): enabling does not open is_test or the slug.
        v_ok := false;
        BEGIN
            UPDATE public.agents SET is_test = true WHERE slug = 'parlay_lotto';
        EXCEPTION WHEN OTHERS THEN v_ok := true;
        END;
        IF NOT v_ok THEN RAISE EXCEPTION 'SELFTEST FAILED: a parlay agent''s is_test changed'; END IF;

        RAISE EXCEPTION 'selftest-rollback';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'selftest-rollback' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE '087 self-test passed (rolled back)';
END;
$$;

-- Verify after the paste (db_inspect; read-only):
-- (a) SELECT name, applied_at FROM migration_log WHERE name = '087_enable_parlay_agents';
-- (b) SELECT id, slug, enabled, is_test, domain FROM agents ORDER BY id;
--     parlay_safer/mid/lotto enabled; nfl_ml, _kalshi_probe and every other real agent disabled.
