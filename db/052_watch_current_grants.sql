-- 052_watch_current_grants.sql — v_watch_current is read-only for the owner
-- (Chat 4 Phase 3; 2026-10-15).
--
-- db/051 granted SELECT on the view but did not revoke Supabase's default
-- grants on a new object, so `authenticated` also held INSERT, UPDATE and
-- DELETE on it: the only public view that did (read back via db_inspect,
-- 2026-10-15). Not exploitable (the view is neither insertable nor updatable,
-- it is security_invoker, and watch_status refuses UPDATE and DELETE by grant
-- and by trigger), but every other view has those grants revoked, and this
-- one should match. No function or trigger changes, so no rolled-back
-- self-test; an assertion block checks the result instead and raises if it is
-- wrong (and the SQL Editor's transaction then applies nothing).
--
-- Prerequisites: db/051.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

REVOKE ALL ON v_watch_current FROM PUBLIC, anon, authenticated;
GRANT SELECT ON v_watch_current TO authenticated, valemont_readonly;

DO $$
BEGIN
    IF has_table_privilege('authenticated', 'public.v_watch_current', 'INSERT')
       OR has_table_privilege('authenticated', 'public.v_watch_current', 'UPDATE')
       OR has_table_privilege('authenticated', 'public.v_watch_current', 'DELETE')
       OR has_table_privilege('authenticated', 'public.v_watch_current', 'TRUNCATE')
       OR NOT has_table_privilege('authenticated', 'public.v_watch_current', 'SELECT')
       OR NOT has_table_privilege('valemont_readonly', 'public.v_watch_current', 'SELECT')
       OR has_table_privilege('anon', 'public.v_watch_current', 'SELECT') THEN
        RAISE EXCEPTION 'CHECK FAILED: v_watch_current grants are not select-only for the owner';
    END IF;
    RAISE NOTICE '052 grants checked: v_watch_current is select-only';
END
$$;

INSERT INTO migration_log (name) VALUES ('052_watch_current_grants')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect one row per public view, every one i/u/d false:
-- SELECT c.relname, has_table_privilege('authenticated', c.oid, 'INSERT') AS i,
--        has_table_privilege('authenticated', c.oid, 'UPDATE') AS u,
--        has_table_privilege('authenticated', c.oid, 'DELETE') AS d
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relkind = 'v' ORDER BY 1;
-- SELECT name, applied_at FROM migration_log WHERE name = '052_watch_current_grants';
