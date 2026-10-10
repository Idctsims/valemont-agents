-- 030_morning_brief.sql — the Morning Brief's storage (Chat 2, Phase 5).
--
-- CHOICE: extend the existing `briefs` table (db/001), not a new one.
--   * It was made for exactly this (CLAUDE.md §4: the chief of staff writes
--     briefs), it is already append-only (db/001 reject_mutation, db/020
--     TRUNCATE guard) with an owner read-only policy (db/020), and nothing
--     has ever written to it: 0 rows on 2026-10-10 (db_inspect), no writer in
--     the code.
--   * Its period_start/period_end (NOT NULL) are the owner's local day for a
--     morning brief, the reporting window for a chief-of-staff brief.
--   * body was TEXT; it becomes JSONB, safe only because the table is empty
--     (the DO block below refuses otherwise). A chief-of-staff brief stores
--     {"text": ...} in it later.
--
-- Rows are never edited: a regenerated brief is a NEW row, and the latest row
-- for a (kind, date) wins (v_briefs_latest). Delivery is recorded separately
-- in brief_deliveries, also append-only, one row per kind and date: the
-- worker's 09:45 push and its boot catch-up can never send twice.
--
-- is_test rows come only from add_test_brief() with the verified e2e marker
-- (db/027's pattern); v_briefs_latest and ctx_brief() never show them.
--
-- ctx_brief() is a context provider (Wags can say "per this morning's
-- brief") with brief_section = false: the brief never feeds itself.
--
-- Prerequisites: db/001, db/015, db/020, db/027, db/028, db/029.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- briefs: kind, date, model, cost link, fallback; body as JSONB.

-- Converted once, and only while empty; a second paste finds jsonb and
-- leaves it alone.
DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'briefs' AND column_name = 'body') = 'text' THEN
        IF EXISTS (SELECT 1 FROM public.briefs) THEN
            RAISE EXCEPTION 'briefs has rows: converting body to jsonb would rewrite them. Nothing was changed.';
        END IF;
        ALTER TABLE public.briefs ALTER COLUMN body TYPE jsonb USING jsonb_build_object('text', body);
    END IF;
END
$$;

ALTER TABLE briefs
    ADD COLUMN IF NOT EXISTS kind           TEXT        NOT NULL DEFAULT 'chief_of_staff'
        CONSTRAINT briefs_kind_check CHECK (kind IN ('morning', 'chief_of_staff')),
    -- The owner's local date the brief is for (America/Chicago).
    ADD COLUMN IF NOT EXISTS brief_date     DATE,
    ADD COLUMN IF NOT EXISTS model          TEXT,
    ADD COLUMN IF NOT EXISTS ai_usage_id    BIGINT      REFERENCES ai_usage (id) ON DELETE RESTRICT,
    -- True: the deterministic template, built when the AI path failed.
    ADD COLUMN IF NOT EXISTS fallback       BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS voice_version  TEXT,
    ADD COLUMN IF NOT EXISTS word_count     INTEGER     CHECK (word_count >= 0),
    ADD COLUMN IF NOT EXISTS is_test        BOOLEAN     NOT NULL DEFAULT false;

ALTER TABLE briefs DROP CONSTRAINT IF EXISTS briefs_morning_shape;
-- Superseded by db/031: as written here a missing field made the CHECK NULL,
-- which passes. db/031 wraps the condition in coalesce(..., false).
ALTER TABLE briefs ADD CONSTRAINT briefs_morning_shape CHECK (
    kind <> 'morning' OR (
        brief_date IS NOT NULL
        AND jsonb_typeof(body -> 'headline') = 'string'
        AND jsonb_typeof(body -> 'sections') = 'array'
        AND jsonb_typeof(body -> 'one_move') = 'string'
        AND word_count IS NOT NULL
        AND (fallback OR (model IS NOT NULL AND ai_usage_id IS NOT NULL))));

CREATE INDEX IF NOT EXISTS briefs_kind_date ON briefs (kind, brief_date, id DESC);

-- The latest real brief per kind and date. security_invoker: RLS applies.
CREATE OR REPLACE VIEW v_briefs_latest
WITH (security_invoker = true)
AS
SELECT DISTINCT ON (b.kind, b.brief_date)
       b.id, b.kind, b.brief_date, b.created_at, b.body, b.model, b.ai_usage_id,
       b.fallback, b.voice_version, b.word_count
  FROM public.briefs b
 WHERE NOT b.is_test AND b.brief_date IS NOT NULL
 ORDER BY b.kind, b.brief_date, b.id DESC;

-- ===================================================================== 2
-- brief_deliveries: one per kind and date, append-only.

CREATE TABLE IF NOT EXISTS brief_deliveries (
    id               BIGSERIAL    PRIMARY KEY,
    brief_id         BIGINT       NOT NULL REFERENCES briefs (id) ON DELETE RESTRICT,
    kind             TEXT         NOT NULL CONSTRAINT brief_deliveries_kind_check CHECK (kind IN ('morning', 'chief_of_staff')),
    brief_date       DATE         NOT NULL,
    -- The push it went out as (db/019); NULL never, but kept loose so a
    -- delivery is recordable even if notifications ever moves.
    notification_id  BIGINT       REFERENCES notifications (id) ON DELETE SET NULL,
    -- Sent by the boot catch-up after 09:45 ("Late ·").
    late             BOOLEAN      NOT NULL DEFAULT false,
    pushed_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT brief_deliveries_once UNIQUE (kind, brief_date)
);

DROP TRIGGER IF EXISTS brief_deliveries_immutable ON brief_deliveries;
CREATE TRIGGER brief_deliveries_immutable
    BEFORE UPDATE OR DELETE ON brief_deliveries
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS brief_deliveries_no_truncate ON brief_deliveries;
CREATE TRIGGER brief_deliveries_no_truncate
    BEFORE TRUNCATE ON brief_deliveries
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- A delivery must point at a real (non-test) brief of its kind and date.
CREATE OR REPLACE FUNCTION public.brief_deliveries_match()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.briefs b
                    WHERE b.id = NEW.brief_id AND b.kind = NEW.kind
                      AND b.brief_date = NEW.brief_date AND NOT b.is_test) THEN
        RAISE EXCEPTION 'brief_deliveries: brief % is not a real % brief for %', NEW.brief_id, NEW.kind, NEW.brief_date;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS brief_deliveries_match ON brief_deliveries;
CREATE TRIGGER brief_deliveries_match
    BEFORE INSERT ON brief_deliveries
    FOR EACH ROW EXECUTE FUNCTION public.brief_deliveries_match();

-- ===================================================================== 3
-- ctx_brief(): today's morning brief for Wags (headline and its one move).

CREATE OR REPLACE FUNCTION public.ctx_brief()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT coalesce((
        SELECT jsonb_build_object(
                   'date', l.brief_date,
                   'headline', l.body ->> 'headline',
                   'one_move', l.body ->> 'one_move',
                   'plain_mode', l.fallback)
          FROM public.v_briefs_latest l
         WHERE l.kind = 'morning' AND l.brief_date = public.goal_local_today()),
        jsonb_build_object('today', null, 'note', 'No morning brief yet today.'));
$$;

INSERT INTO context_providers (key, label, fn, brief_section, sort)
VALUES ('brief', 'Morning brief', 'public.ctx_brief()'::regprocedure, false, 5)
ON CONFLICT (key) DO NOTHING;

-- ===================================================================== 4
-- The one writer of test briefs (e2e, verified marker; db/027's rule).

CREATE OR REPLACE FUNCTION public.add_test_brief(p_marker text, p_brief_date date, p_body jsonb)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_hash  text;
    v_id    bigint;
    v_tz    text := coalesce((SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago');
BEGIN
    IF auth.uid() IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'add_test_brief: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_brief: test marker refused';
    END IF;
    -- The CHECKs still apply: a test brief has the real shape, as a fallback
    -- (no model, no cost row), and is_test keeps it out of every real view.
    INSERT INTO public.briefs (period_start, period_end, body, kind, brief_date, fallback,
                               word_count, is_test)
    VALUES ((p_brief_date::timestamp AT TIME ZONE v_tz),
            ((p_brief_date + 1)::timestamp AT TIME ZONE v_tz),
            p_body, 'morning', p_brief_date, true,
            array_length(regexp_split_to_array(trim(p_body::text), '\s+'), 1), true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- ===================================================================== 5
-- Access.

ALTER TABLE brief_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON brief_deliveries, v_briefs_latest FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE brief_deliveries_id_seq FROM anon, authenticated, PUBLIC;

-- The owner reads; only the worker (DATABASE_URL) writes.
GRANT SELECT ON brief_deliveries, v_briefs_latest TO authenticated;
DROP POLICY IF EXISTS brief_deliveries_owner_read ON brief_deliveries;
CREATE POLICY brief_deliveries_owner_read ON brief_deliveries
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
-- briefs keeps db/020's owner read policy and SELECT-only grant.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON briefs FROM anon, authenticated;

REVOKE ALL ON FUNCTION public.brief_deliveries_match() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ctx_brief() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.ctx_brief() TO authenticated, valemont_readonly;
REVOKE ALL ON FUNCTION public.add_test_brief(text, date, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_brief(text, date, jsonb) TO authenticated;

INSERT INTO migration_log (name) VALUES ('030_morning_brief')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) briefs.body jsonb; kind, brief_date, model, ai_usage_id, fallback,
--       voice_version, word_count, is_test present; 0 rows
--   (b) brief_deliveries: RLS on, one owner read policy, anon nothing
--   (c) providers: brief (sort 5, brief_section false), goals, ventures,
--       capital (brief_section true)
--   (d) ctx_brief() today: {"note": "No morning brief yet today.", "today": null}
--   (e) the migration_log row
-- ---------------------------------------------------------------------------

SELECT column_name, data_type, is_nullable,
       (SELECT count(*) FROM briefs) AS rows
  FROM information_schema.columns
 WHERE table_name = 'briefs' ORDER BY ordinal_position;                                  -- (a)

SELECT c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = 'brief_deliveries') AS policies,
       has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS anon_any
  FROM pg_class c WHERE c.relname = 'brief_deliveries';                                 -- (b)

SELECT key, sort, brief_section, enabled FROM context_providers ORDER BY sort;           -- (c)

SELECT ctx_brief();                                                                      -- (d)

SELECT name, applied_at FROM migration_log WHERE name = '030_morning_brief';             -- (e)
