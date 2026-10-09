-- 022_ventures.sql — Ventures HQ (Chat 2 Phase 2): ventures, their
-- workstreams and dates, an append-only running log written partly by the
-- database itself, goals linked to a venture, and the Today view.
--
-- App tables (mutable), except venture_log, which is append-only:
--   ventures              one row per venture; name/slug unique per owner
--   venture_workstreams   the parallel tracks inside a venture
--   venture_dates         due dates, optionally tied to a workstream
--   venture_log           the running log. SELECT + INSERT only; a trigger
--                         refuses UPDATE and DELETE (see the cascade note)
--   goals.venture_id      a goal may name the venture it serves
--   v_venture_today       open dates due today or earlier, owner's timezone
--
-- AUTO-LOG. When a venture's stage, next_action or blockers changes, or a
-- workstream's state or next_action changes, a trigger writes a venture_log
-- row of kind 'auto': "Next action: <old> → <new>". Names, notes, taglines
-- and sort order do not log. The log is how a venture moved, kept by the
-- database so it can't be forgotten.
--
-- CASCADE AND THE APPEND-ONLY LOG. venture_log.venture_id is ON DELETE
-- CASCADE (the spec). A cascade deletes child rows through ordinary row
-- deletes, so it fires the log's BEFORE DELETE trigger. The trigger allows a
-- delete only when the log row's venture no longer exists, which is true
-- only inside that cascade (the parent row is already gone). Any other
-- DELETE, and every UPDATE, is refused. TRUNCATE is refused too.
--
-- Access pattern (db/019, db/020, db/021): RLS on, one owner policy per table
-- TO authenticated, nothing for anon. Child rows must also belong to a
-- venture the caller can see (checked in the policy, under RLS).
-- The view is security_invoker, so RLS applies through it.
--
-- SEED: seven ventures (owner from app_settings), skipped if ventures already
-- has rows, so the file is safe to re-run.
--
-- Prerequisites: db/015 (migration_log, reject_mutation), db/020
-- (app_settings, reject_truncate), db/021 (goals, goal_local_today).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Tables.

CREATE TABLE IF NOT EXISTS ventures (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     UUID        NOT NULL DEFAULT auth.uid()
                             REFERENCES auth.users (id) ON DELETE RESTRICT,
    name         TEXT        NOT NULL CONSTRAINT ventures_name_length CHECK (char_length(name) BETWEEN 1 AND 120),
    slug         TEXT        NOT NULL CONSTRAINT ventures_slug_format
                             CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 80),
    tagline      TEXT        CONSTRAINT ventures_tagline_length CHECK (char_length(tagline) <= 200),
    role         TEXT        CONSTRAINT ventures_role_length CHECK (char_length(role) <= 120),
    stage        TEXT        CONSTRAINT ventures_stage_check
                             CHECK (stage IN ('idea', 'building', 'pre_launch', 'launched', 'scaling', 'paused')),
    next_action  TEXT        CONSTRAINT ventures_next_action_length CHECK (char_length(next_action) <= 300),
    blockers     TEXT        CONSTRAINT ventures_blockers_length CHECK (char_length(blockers) <= 2000),
    notes        TEXT        CONSTRAINT ventures_notes_length CHECK (char_length(notes) <= 20000),
    sort_order   INTEGER     NOT NULL DEFAULT 0,
    archived_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ventures_owner_slug_key UNIQUE (owner_id, slug)
);

CREATE TABLE IF NOT EXISTS venture_workstreams (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     UUID        NOT NULL DEFAULT auth.uid()
                             REFERENCES auth.users (id) ON DELETE RESTRICT,
    venture_id   UUID        NOT NULL REFERENCES ventures (id) ON DELETE CASCADE,
    name         TEXT        NOT NULL CONSTRAINT venture_workstreams_name_length CHECK (char_length(name) BETWEEN 1 AND 120),
    state        TEXT        NOT NULL DEFAULT 'active'
                             CONSTRAINT venture_workstreams_state_check CHECK (state IN ('active', 'parked', 'done')),
    next_action  TEXT        CONSTRAINT venture_workstreams_next_action_length CHECK (char_length(next_action) <= 300),
    notes        TEXT        CONSTRAINT venture_workstreams_notes_length CHECK (char_length(notes) <= 20000),
    sort_order   INTEGER     NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS venture_workstreams_venture ON venture_workstreams (venture_id, sort_order);

CREATE TABLE IF NOT EXISTS venture_dates (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id       UUID        NOT NULL DEFAULT auth.uid()
                               REFERENCES auth.users (id) ON DELETE RESTRICT,
    venture_id     UUID        NOT NULL REFERENCES ventures (id) ON DELETE CASCADE,
    workstream_id  UUID        REFERENCES venture_workstreams (id) ON DELETE SET NULL,
    label          TEXT        NOT NULL CONSTRAINT venture_dates_label_length CHECK (char_length(label) BETWEEN 1 AND 200),
    due_on         DATE        NOT NULL,
    done_at        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS venture_dates_open ON venture_dates (owner_id, due_on) WHERE done_at IS NULL;
CREATE INDEX IF NOT EXISTS venture_dates_venture ON venture_dates (venture_id);

CREATE TABLE IF NOT EXISTS venture_log (
    id          BIGSERIAL   PRIMARY KEY,
    owner_id    UUID        NOT NULL DEFAULT auth.uid()
                            REFERENCES auth.users (id) ON DELETE RESTRICT,
    venture_id  UUID        NOT NULL REFERENCES ventures (id) ON DELETE CASCADE,
    entry       TEXT        NOT NULL CONSTRAINT venture_log_entry_length CHECK (char_length(entry) BETWEEN 1 AND 4000),
    kind        TEXT        NOT NULL DEFAULT 'note'
                            CONSTRAINT venture_log_kind_check CHECK (kind IN ('note', 'decision', 'milestone', 'auto')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS venture_log_venture ON venture_log (venture_id, created_at DESC);

ALTER TABLE goals
    ADD COLUMN IF NOT EXISTS venture_id UUID REFERENCES ventures (id) ON DELETE SET NULL;

-- ===================================================================== 2
-- venture_log is append-only.

CREATE OR REPLACE FUNCTION public.venture_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    -- The only delete allowed: the cascade from deleting the venture itself.
    -- By the time a cascade reaches this row the venture is gone; for any
    -- direct DELETE it still exists (the FK guarantees it).
    IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM public.ventures v WHERE v.id = OLD.venture_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'venture_log is append-only: % refused (write a new entry instead)', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS venture_log_immutable ON venture_log;
CREATE TRIGGER venture_log_immutable
    BEFORE UPDATE OR DELETE ON venture_log
    FOR EACH ROW EXECUTE FUNCTION public.venture_log_append_only();

DROP TRIGGER IF EXISTS venture_log_no_truncate ON venture_log;
CREATE TRIGGER venture_log_no_truncate
    BEFORE TRUNCATE ON venture_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 3
-- updated_at, and the auto-log.

CREATE OR REPLACE FUNCTION public.ventures_touch()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS ventures_touch ON ventures;
CREATE TRIGGER ventures_touch
    BEFORE UPDATE ON ventures
    FOR EACH ROW EXECUTE FUNCTION public.ventures_touch();

-- "<old> → <new>" with an em dash standing in for an empty value.
CREATE OR REPLACE FUNCTION public.venture_change_text(p_label text, p_old text, p_new text)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = ''
AS $$
    SELECT left(p_label || ': ' || coalesce(nullif(p_old, ''), '—') || ' → ' || coalesce(nullif(p_new, ''), '—'), 4000);
$$;

CREATE OR REPLACE FUNCTION public.ventures_auto_log()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.stage IS DISTINCT FROM OLD.stage THEN
        INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
        VALUES (NEW.owner_id, NEW.id, 'auto', public.venture_change_text('Stage', OLD.stage, NEW.stage));
    END IF;
    IF NEW.next_action IS DISTINCT FROM OLD.next_action THEN
        INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
        VALUES (NEW.owner_id, NEW.id, 'auto', public.venture_change_text('Next action', OLD.next_action, NEW.next_action));
    END IF;
    IF NEW.blockers IS DISTINCT FROM OLD.blockers THEN
        INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
        VALUES (NEW.owner_id, NEW.id, 'auto', public.venture_change_text('Blockers', OLD.blockers, NEW.blockers));
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS ventures_auto_log ON ventures;
CREATE TRIGGER ventures_auto_log
    AFTER UPDATE ON ventures
    FOR EACH ROW EXECUTE FUNCTION public.ventures_auto_log();

CREATE OR REPLACE FUNCTION public.venture_workstreams_auto_log()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
        VALUES (NEW.owner_id, NEW.venture_id, 'auto',
                public.venture_change_text(NEW.name || ' · state', OLD.state, NEW.state));
    END IF;
    IF NEW.next_action IS DISTINCT FROM OLD.next_action THEN
        INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
        VALUES (NEW.owner_id, NEW.venture_id, 'auto',
                public.venture_change_text(NEW.name || ' · next action', OLD.next_action, NEW.next_action));
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS venture_workstreams_auto_log ON venture_workstreams;
CREATE TRIGGER venture_workstreams_auto_log
    AFTER UPDATE ON venture_workstreams
    FOR EACH ROW EXECUTE FUNCTION public.venture_workstreams_auto_log();

-- ===================================================================== 4
-- Goals carry their venture with them (db/021's carry functions, plus one
-- column). Everything else in them is unchanged.

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
                              carried_from, carry_count, sort_order, venture_id)
    SELECT g.owner_id, g.title, g.notes, g.horizon, g.area, v_next,
           g.id, g.carry_count + 1, g.sort_order, g.venture_id
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
                              carried_from, carry_count, sort_order, venture_id)
    SELECT g.owner_id, g.title, g.notes, g.horizon, g.area,
           public.goal_next_period(g.horizon, g.period_start),
           g.id, g.carry_count + 1, g.sort_order, g.venture_id
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
-- Today: open dates due today or earlier, in the owner's timezone.

CREATE OR REPLACE VIEW v_venture_today
WITH (security_invoker = true)
AS
SELECT d.id,
       d.venture_id,
       v.name               AS venture_name,
       v.slug               AS venture_slug,
       w.name               AS workstream_name,
       d.label,
       d.due_on,
       d.due_on < public.goal_local_today() AS overdue
  FROM public.venture_dates d
  JOIN public.ventures v ON v.id = d.venture_id
  LEFT JOIN public.venture_workstreams w ON w.id = d.workstream_id
 WHERE d.done_at IS NULL
   AND v.archived_at IS NULL
   AND d.due_on <= public.goal_local_today();

-- ===================================================================== 6
-- Access.

ALTER TABLE ventures            ENABLE ROW LEVEL SECURITY;
ALTER TABLE venture_workstreams ENABLE ROW LEVEL SECURITY;
ALTER TABLE venture_dates       ENABLE ROW LEVEL SECURITY;
ALTER TABLE venture_log         ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ventures, venture_workstreams, venture_dates, venture_log, v_venture_today
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE venture_log_id_seq FROM anon, authenticated, PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON ventures, venture_workstreams, venture_dates TO authenticated;
GRANT SELECT, INSERT ON venture_log TO authenticated;
GRANT USAGE ON SEQUENCE venture_log_id_seq TO authenticated;
GRANT SELECT ON v_venture_today TO authenticated;
-- goals gained a column; the owner may link and unlink it.
GRANT UPDATE (venture_id) ON goals TO authenticated;

DROP POLICY IF EXISTS ventures_owner ON ventures;
CREATE POLICY ventures_owner ON ventures
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

-- A child row must hang off a venture the caller can see (the EXISTS runs
-- under RLS), so no row can point into someone else's venture.
DROP POLICY IF EXISTS venture_workstreams_owner ON venture_workstreams;
CREATE POLICY venture_workstreams_owner ON venture_workstreams
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid())
                AND EXISTS (SELECT 1 FROM public.ventures v WHERE v.id = venture_id));

DROP POLICY IF EXISTS venture_dates_owner ON venture_dates;
CREATE POLICY venture_dates_owner ON venture_dates
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid())
                AND EXISTS (SELECT 1 FROM public.ventures v WHERE v.id = venture_id)
                AND (workstream_id IS NULL OR EXISTS (
                     SELECT 1 FROM public.venture_workstreams w
                      WHERE w.id = workstream_id AND w.venture_id = venture_dates.venture_id)));

-- Select and insert only: there is no UPDATE or DELETE policy, so the API
-- cannot change the log even before the trigger is reached.
DROP POLICY IF EXISTS venture_log_owner_read ON venture_log;
CREATE POLICY venture_log_owner_read ON venture_log
    FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS venture_log_owner_append ON venture_log;
CREATE POLICY venture_log_owner_append ON venture_log
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (SELECT auth.uid())
                AND EXISTS (SELECT 1 FROM public.ventures v WHERE v.id = venture_id));

REVOKE ALL ON FUNCTION public.venture_log_append_only() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ventures_touch() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ventures_auto_log() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.venture_workstreams_auto_log() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.venture_change_text(text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.venture_change_text(text, text, text) TO authenticated;
-- db/021's functions were replaced above; re-assert their grants.
REVOKE ALL ON FUNCTION public.carry_over_goals(text, date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.carry_goal(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.carry_over_goals(text, date) TO authenticated;
GRANT EXECUTE ON FUNCTION public.carry_goal(uuid) TO authenticated;

-- ===================================================================== 7
-- Seed: seven ventures, skipped if any venture exists. Inserts only, so
-- the auto-log (AFTER UPDATE) writes nothing; the one log entry is explicit.

DO $$
DECLARE
    v_owner uuid := (SELECT owner_id FROM public.app_settings);
    v_sbc   uuid;
BEGIN
    IF v_owner IS NULL THEN
        RAISE EXCEPTION 'app_settings has no owner; paste db/020 first. Nothing was seeded.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.ventures) THEN
        RAISE NOTICE 'ventures already has rows; seed skipped';
        RETURN;
    END IF;

    INSERT INTO public.ventures (owner_id, name, slug, tagline, role, stage, next_action, notes, sort_order)
    VALUES (v_owner, 'Sail Beach Club', 'sail-beach-club',
            'Experiential hospitality · Biscayne Bay', 'Co-owner & CEO', 'pre_launch',
            'Build 50 LinkedIn connections this week',
            'Sail Beach Club Holdings Inc. (Delaware C-Corp) with a Florida OpCo subsidiary; '
            'personal holding company planned for my stake. Co-founder & COO: Kendrick Goodloe.',
            1)
    RETURNING id INTO v_sbc;

    INSERT INTO public.venture_workstreams (owner_id, venture_id, name, state, next_action, notes, sort_order)
    VALUES
      (v_owner, v_sbc, 'Network', 'active', 'Build 50 LinkedIn connections this week',
       'Meetings taken with multiple people familiar with the space.', 1),
      (v_owner, v_sbc, 'Vessel renders', 'active', 'Find a renderer', NULL, 2),
      (v_owner, v_sbc, 'Permitting', 'active', 'Submit DEP application',
       'Pre-app meeting held. Pre-app documents drafted: Aquatic Preserve, OFW, ERP, '
       'sovereign submerged lands, DERM.', 3),
      (v_owner, v_sbc, 'Investor readiness', 'parked', 'SAFE in draft; parked until investment stage',
       'Pitch deck and project development brief done. Cap table, data room, org chart.', 4),
      (v_owner, v_sbc, 'Legal', 'parked', 'SPAs in draft; parked until investment stage',
       'IP assignment, board/tie-breaker clauses, founder vesting and acceleration, operating '
       'agreements, expense reimbursement ledger, liability coverage.', 5);

    INSERT INTO public.venture_log (owner_id, venture_id, kind, entry)
    VALUES (v_owner, v_sbc, 'milestone',
            'Venture HQ created. Pre-app meeting with DEP done; pitch deck and development brief complete.');

    INSERT INTO public.ventures (owner_id, name, slug, sort_order)
    VALUES (v_owner, 'Perfect Timing Management', 'perfect-timing-management', 2),
           (v_owner, 'Clipd', 'clipd', 3),
           (v_owner, 'Valemont Grow', 'valemont-grow', 4),
           (v_owner, 'Excursion', 'excursion', 5),
           (v_owner, 'Sims & Vale Capital', 'sims-vale-capital', 6),
           (v_owner, 'Freelance web dev', 'freelance-web-dev', 7);
END
$$;

INSERT INTO migration_log (name) VALUES ('022_ventures')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) RLS on and one owner policy on each of the three app tables, two on
--       venture_log (read, append), all TO authenticated
--   (b) anon: no privilege on any of the five
--   (c) the seed: 7 ventures in order; SBC with 5 workstreams (2 parked) and 1 log row
--   (d) the view is security_invoker
--   (e) the migration_log row
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       (SELECT string_agg(p.policyname || ':' || array_to_string(p.roles, ','), ' ' ORDER BY p.policyname)
          FROM pg_policies p WHERE p.tablename = c.relname) AS policies
  FROM pg_class c
 WHERE c.relname IN ('ventures', 'venture_workstreams', 'venture_dates', 'venture_log')
 ORDER BY c.relname;                                                                            -- (a)

SELECT count(*) AS anon_grants FROM information_schema.role_table_grants
 WHERE grantee = 'anon'
   AND table_name IN ('ventures', 'venture_workstreams', 'venture_dates', 'venture_log', 'v_venture_today'); -- (b) 0

SELECT v.sort_order, v.name, v.stage,
       (SELECT count(*) FROM venture_workstreams w WHERE w.venture_id = v.id) AS workstreams,
       (SELECT count(*) FROM venture_workstreams w WHERE w.venture_id = v.id AND w.state = 'parked') AS parked,
       (SELECT count(*) FROM venture_log l WHERE l.venture_id = v.id) AS log_rows
  FROM ventures v ORDER BY v.sort_order;                                                        -- (c)

SELECT c.relname, c.reloptions FROM pg_class c WHERE c.relname = 'v_venture_today';            -- (d)

SELECT name, applied_at FROM migration_log WHERE name = '022_ventures';                        -- (e)
