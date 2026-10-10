-- 028_readonly_views.sql — valemont_readonly can read every view.
--
-- db/018 gave valemont_readonly SELECT on every public table and view, but a
-- view that calls a helper function also needs EXECUTE on that function, and
-- db/021 onward revoked EXECUTE from PUBLIC on each helper. So two views
-- failed for db_inspect with "permission denied for function
-- goal_local_today": v_venture_today (db/022, since it was pasted) and
-- v_capital_today (db/026). has_table_privilege said yes; the SELECT said no.
--
-- The rule: EXECUTE for valemont_readonly only on functions that can only
-- READ, i.e. SECURITY INVOKER and STABLE or IMMUTABLE. They run with the
-- role's own privileges, which allow no write anywhere. Never on a SECURITY
-- DEFINER function (add_test_bankroll_entry would run as its owner) and never
-- on a VOLATILE one (carry_over_goals, carry_goal). No default privileges for
-- functions: future ones would include writers. tests_live/
-- test_readonly_role.py fails if any public view is unreadable by the role,
-- if a read-only helper is missing EXECUTE, or if it can execute a definer
-- or volatile function.
--
-- Prerequisites: db/018, db/021, db/022, db/026, db/027.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

GRANT EXECUTE ON FUNCTION
    public.goal_local_today(),
    public.goal_next_period(text, date),
    public.goal_periods_to_roll(text),
    public.is_owner(),
    public.venture_change_text(text, text, text),
    public.capital_day_end(date),
    public.capital_first_at(text),
    public.capital_value_as_of(text, timestamptz)
TO valemont_readonly;

INSERT INTO migration_log (name) VALUES ('028_readonly_views')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) the eight helpers: readonly_can = true; add_test_bankroll_entry,
--       carry_goal, carry_over_goals: false
--   (b) the migration_log row
-- Reading the views AS the role is checked through db_inspect afterwards
-- (no SET ROLE here: postgres may lack SET on the role, and an error would
-- roll back the whole paste).
-- ---------------------------------------------------------------------------

SELECT p.proname,
       CASE WHEN p.prosecdef THEN 'definer' ELSE 'invoker' END AS security,
       p.provolatile::text AS volatility,
       has_function_privilege('valemont_readonly', p.oid, 'EXECUTE') AS readonly_can
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.prorettype <> 'trigger'::regtype
 ORDER BY readonly_can DESC, p.proname;                                                   -- (a)

SELECT name, applied_at FROM migration_log WHERE name = '028_readonly_views';            -- (b)
