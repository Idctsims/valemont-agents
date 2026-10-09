-- 021_goals.sql — Goals (Chat 2 Phase 1): weekly, monthly and long-term
-- goals, set on Mondays, carried over when unfinished, kept as history.
--
-- An APP table, not the ledger: rows are edited in place (complete, drop,
-- rename). What is protected is the carry history, because that is the one
-- honest number here: how often a goal slipped.
--
--   - A carried goal is a NEW row pointing at the original (carried_from).
--     The original is never moved or edited by a carry; it stays 'open' in
--     its own period, and "has a child" is what reads as "carried" in history.
--   - carried_from is UNIQUE, so a goal can be carried once. That is what
--     makes carry-over idempotent: running it twice inserts nothing the
--     second time (ON CONFLICT DO NOTHING).
--   - A trigger checks every carried row against its original: same owner and
--     horizon, the original still open, the next period, carry_count + 1. So
--     slippage cannot be reset or skipped, whoever inserts.
--   - The signed-in owner may UPDATE only title, notes, area, status,
--     sort_order and completed_at (column grants). period_start, horizon,
--     carried_from and carry_count are not writable through the API.
--
-- Periods are dates in the owner's timezone (app_settings.timezone,
-- America/Chicago): a week starts on Monday, a month on the 1st, and a
-- long-term goal has no period. A period has ENDED once the local date has
-- reached the start of the next one.
--
-- Functions (SECURITY INVOKER: RLS applies to the web; the worker's
-- DATABASE_URL role bypasses RLS and sees every row):
--   carry_over_goals(horizon, from)  carry one ended period's open goals into
--                                    the next period; returns rows inserted
--   goal_periods_to_roll(horizon)    ended periods that still hold an open,
--                                    uncarried goal, oldest first (the
--                                    catch-up list for the worker's boot run
--                                    and the web's ensureRollover)
--   carry_goal(id)                   "move to next week/month" for one goal,
--                                    before its period ends
--
-- Access pattern (db/019, db/020): RLS on, one owner policy TO authenticated,
-- nothing for anon. valemont_readonly reads it through db/018's defaults.
--
-- Prerequisites: db/015 (migration_log), db/018, db/020 (app_settings).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1

CREATE TABLE IF NOT EXISTS goals (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      UUID        NOT NULL DEFAULT auth.uid()
                              REFERENCES auth.users (id) ON DELETE RESTRICT,
    title         TEXT        NOT NULL
                              CONSTRAINT goals_title_length CHECK (char_length(title) BETWEEN 1 AND 200),
    notes         TEXT        CONSTRAINT goals_notes_length CHECK (char_length(notes) <= 2000),
    horizon       TEXT        NOT NULL
                              CONSTRAINT goals_horizon_check CHECK (horizon IN ('weekly', 'monthly', 'long_term')),
    area          TEXT        CONSTRAINT goals_area_check
                              CHECK (area IN ('business', 'personal', 'health', 'money', 'people')),
    period_start  DATE,
    status        TEXT        NOT NULL DEFAULT 'open'
                              CONSTRAINT goals_status_check CHECK (status IN ('open', 'done', 'dropped')),
    -- RESTRICT: a goal that has been carried cannot be deleted out from under
    -- its history. Drop it instead.
    carried_from  UUID        CONSTRAINT goals_carried_from_key UNIQUE
                              REFERENCES goals (id) ON DELETE RESTRICT,
    carry_count   INTEGER     NOT NULL DEFAULT 0 CHECK (carry_count >= 0),
    sort_order    INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,

    CONSTRAINT goals_completed_iff_done CHECK ((status = 'done') = (completed_at IS NOT NULL)),
    -- One CHECK per horizon, named, so a bad row says which rule it broke.
    CONSTRAINT goals_weekly_period CHECK (
        horizon <> 'weekly' OR (period_start IS NOT NULL AND extract(isodow FROM period_start) = 1)),
    CONSTRAINT goals_monthly_period CHECK (
        horizon <> 'monthly' OR (period_start IS NOT NULL AND extract(day FROM period_start) = 1)),
    CONSTRAINT goals_long_term_period CHECK (
        horizon <> 'long_term' OR period_start IS NULL),
    -- An original has carry_count 0; every carried row has at least 1.
    CONSTRAINT goals_carry_pairing CHECK ((carried_from IS NULL) = (carry_count = 0)),
    CONSTRAINT goals_not_own_parent CHECK (carried_from <> id)
);

CREATE INDEX IF NOT EXISTS goals_owner_period ON goals (owner_id, horizon, period_start);

-- ===================================================================== 2
-- Period arithmetic, in one place.

-- The owner's local date. Under RLS a non-owner cannot read app_settings and
-- gets the default zone, which is the same zone.
CREATE OR REPLACE FUNCTION public.goal_local_today()
RETURNS date
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT (now() AT TIME ZONE coalesce(
        (SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))::date;
$$;

-- Start of the period after p_start. NULL for long_term (no next period).
CREATE OR REPLACE FUNCTION public.goal_next_period(p_horizon text, p_start date)
RETURNS date
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT CASE p_horizon
               WHEN 'weekly'  THEN p_start + 7
               WHEN 'monthly' THEN (p_start + interval '1 month')::date
           END;
$$;

-- ===================================================================== 3
-- Every carried row is checked against its original.

CREATE OR REPLACE FUNCTION public.goals_check_carry()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    p public.goals%ROWTYPE;
BEGIN
    IF NEW.carried_from IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO p FROM public.goals WHERE id = NEW.carried_from;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'goals: carried_from % is not a goal you can see', NEW.carried_from;
    END IF;
    IF p.status <> 'open'
       OR p.horizon = 'long_term'
       OR NEW.owner_id <> p.owner_id
       OR NEW.horizon <> p.horizon
       OR NEW.period_start IS DISTINCT FROM public.goal_next_period(p.horizon, p.period_start)
       OR NEW.carry_count <> p.carry_count + 1 THEN
        RAISE EXCEPTION 'goals: a carried goal must come from an open weekly or monthly goal, '
                        'keep its owner and horizon, land in the next period, and have carry_count + 1';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS goals_carry_check ON goals;
CREATE TRIGGER goals_carry_check
    BEFORE INSERT ON goals
    FOR EACH ROW EXECUTE FUNCTION public.goals_check_carry();

-- ===================================================================== 4
-- Carry-over.

CREATE OR REPLACE FUNCTION public.carry_over_goals(p_horizon text, p_from date)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_next  date;
    v_today date := public.goal_local_today();
    v_count integer;
BEGIN
    IF p_horizon IS NULL OR p_horizon NOT IN ('weekly', 'monthly') THEN
        RAISE EXCEPTION 'carry_over_goals: horizon must be weekly or monthly, got %', p_horizon;
    END IF;
    IF p_from IS NULL THEN
        RAISE EXCEPTION 'carry_over_goals: from date is required';
    END IF;
    IF p_horizon = 'weekly' AND extract(isodow FROM p_from) <> 1 THEN
        RAISE EXCEPTION 'carry_over_goals: % is not a Monday', p_from;
    END IF;
    IF p_horizon = 'monthly' AND extract(day FROM p_from) <> 1 THEN
        RAISE EXCEPTION 'carry_over_goals: % is not the 1st of a month', p_from;
    END IF;

    v_next := public.goal_next_period(p_horizon, p_from);
    IF v_next > v_today THEN
        RAISE EXCEPTION 'carry_over_goals: the % period starting % has not ended (it ends when % begins; local date is %)',
            p_horizon, p_from, v_next, v_today;
    END IF;

    INSERT INTO public.goals (owner_id, title, notes, horizon, area, period_start,
                              carried_from, carry_count, sort_order)
    SELECT g.owner_id, g.title, g.notes, g.horizon, g.area, v_next,
           g.id, g.carry_count + 1, g.sort_order
      FROM public.goals g
     WHERE g.horizon = p_horizon
       AND g.period_start = p_from
       AND g.status = 'open'
     ORDER BY g.sort_order, g.created_at
    ON CONFLICT (carried_from) DO NOTHING;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- The catch-up list: ended periods still holding an open goal that has not
-- been carried. Carrying the oldest can make the next one due (its new rows
-- are open), so callers loop: carry the first, ask again, until empty.
CREATE OR REPLACE FUNCTION public.goal_periods_to_roll(p_horizon text)
RETURNS SETOF date
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT DISTINCT g.period_start
      FROM public.goals g
     WHERE g.horizon = p_horizon
       AND g.horizon IN ('weekly', 'monthly')
       AND g.status = 'open'
       AND public.goal_next_period(g.horizon, g.period_start) <= public.goal_local_today()
       AND NOT EXISTS (SELECT 1 FROM public.goals c WHERE c.carried_from = g.id)
     ORDER BY 1;
$$;

-- "Move to next week" (or month) for one goal, before its period ends.
-- Returns 1 if moved, 0 if it had already been moved. Refuses anything else.
CREATE OR REPLACE FUNCTION public.carry_goal(p_goal uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    v_count integer;
BEGIN
    INSERT INTO public.goals (owner_id, title, notes, horizon, area, period_start,
                              carried_from, carry_count, sort_order)
    SELECT g.owner_id, g.title, g.notes, g.horizon, g.area,
           public.goal_next_period(g.horizon, g.period_start),
           g.id, g.carry_count + 1, g.sort_order
      FROM public.goals g
     WHERE g.id = p_goal
       AND g.status = 'open'
       AND g.horizon IN ('weekly', 'monthly')
    ON CONFLICT (carried_from) DO NOTHING;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 AND NOT EXISTS (SELECT 1 FROM public.goals WHERE carried_from = p_goal) THEN
        RAISE EXCEPTION 'carry_goal: % is not an open weekly or monthly goal you can see', p_goal;
    END IF;
    RETURN v_count;
END;
$$;

-- ===================================================================== 5
-- Access.

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;

-- Start from nothing (Supabase's default privileges grant ALL to
-- authenticated), then grant back exactly what the app needs.
REVOKE ALL ON goals FROM anon, authenticated, PUBLIC;
GRANT SELECT, INSERT, DELETE ON goals TO authenticated;
GRANT UPDATE (title, notes, area, status, sort_order, completed_at) ON goals TO authenticated;

DROP POLICY IF EXISTS goals_owner ON goals;
CREATE POLICY goals_owner ON goals
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

REVOKE ALL ON FUNCTION public.goal_local_today() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.goal_next_period(text, date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.goals_check_carry() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.carry_over_goals(text, date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.goal_periods_to_roll(text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.carry_goal(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.goal_local_today() TO authenticated;
GRANT EXECUTE ON FUNCTION public.goal_next_period(text, date) TO authenticated;
GRANT EXECUTE ON FUNCTION public.carry_over_goals(text, date) TO authenticated;
GRANT EXECUTE ON FUNCTION public.goal_periods_to_roll(text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.carry_goal(uuid) TO authenticated;

INSERT INTO migration_log (name) VALUES ('021_goals')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) RLS on, exactly one policy, goals_owner, TO authenticated
--   (b) anon: no privilege on goals
--   (c) authenticated: SELECT, INSERT, DELETE on the table; UPDATE on six
--       columns only
--   (d) the local date in America/Chicago
--   (e) valemont_readonly can SELECT goals
--   (f) the migration_log row
-- ---------------------------------------------------------------------------

SELECT c.relrowsecurity AS rls_on, p.policyname, p.roles, p.cmd
  FROM pg_class c LEFT JOIN pg_policies p ON p.tablename = c.relname
 WHERE c.relname = 'goals';                                                                    -- (a)

SELECT count(*) AS anon_grants
  FROM information_schema.role_table_grants
 WHERE grantee = 'anon' AND table_name = 'goals';                                               -- (b) 0

SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) AS authenticated_table_privs
  FROM information_schema.role_table_grants
 WHERE grantee = 'authenticated' AND table_name = 'goals';                                      -- (c)

SELECT string_agg(column_name, ',' ORDER BY column_name) AS authenticated_update_columns
  FROM information_schema.column_privileges
 WHERE grantee = 'authenticated' AND table_name = 'goals' AND privilege_type = 'UPDATE';         -- (c)

SELECT public.goal_local_today() AS local_today;                                               -- (d)

SELECT has_table_privilege('valemont_readonly', 'public.goals', 'SELECT') AS readonly_select;   -- (e)

SELECT name, applied_at FROM migration_log WHERE name = '021_goals';                           -- (f)
