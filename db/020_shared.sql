-- 020_shared.sql — shared app tables, the owner check, read-only ledger
-- access for apps/web, paper/live on commitments, and two hardening fixes
--
-- BEFORE YOU PASTE: replace __OWNER_USER_ID__ (ONE place, the first statement)
-- with your auth.users id (Supabase > Authentication > Users > your user >
-- User UID). Do not commit the edited file. If the placeholder is left in, or
-- the id is not a real auth user, the first statement raises and NOTHING in
-- this file is applied (the SQL Editor runs it as one transaction).
--
-- What it does, in order:
--   1. app_settings (single row, holds the owner id), and is_owner(), the
--      owner check every other policy uses. No UUID is hardcoded anywhere:
--      the id lives in this one row.
--   2. job_health, ai_usage, job_queue: app tables for Chat 1 Phase 4.
--   3. Read access to the ledger for the signed-in owner: SELECT-only
--      policies on the eleven tables apps/web will read. No write policy on
--      any ledger table; workers stay the only writers (CLAUDE.md §5).
--   4. commitments.mode ('paper'|'live') and commitments.origin (CLAUDE.md §1).
--   5. HARDENING (found while writing this file, see the notes there):
--      a. TRUNCATE is refused by trigger on every append-only table.
--      b. anon loses every privilege on every public table, now and future,
--         and authenticated loses every write privilege on ledger and archive
--         tables (it keeps SELECT, which RLS still filters).
--
-- Access pattern (same as db/019): RLS on, policies TO authenticated only,
-- nothing for anon. Workers use DATABASE_URL (postgres, BYPASSRLS) and need no
-- policy. valemont_readonly reads every new table through db/018's default
-- privileges.
--
-- Prerequisites: db/015 (migration_log, reject_mutation), db/018, db/019.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run (the seed row is ON CONFLICT DO NOTHING).

-- ===================================================================== 0
-- The owner id, checked first. Stored for this session only (set_config
-- with is_local = false), read back by the seed further down.

DO $$
DECLARE
    v_owner text := '__OWNER_USER_ID__';
BEGIN
    IF v_owner !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
        RAISE EXCEPTION 'Replace __OWNER_USER_ID__ at the top of db/020 with your auth.users id. Nothing was applied.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = v_owner::uuid) THEN
        RAISE EXCEPTION 'No auth.users row has id %. Nothing was applied.', v_owner;
    END IF;
    PERFORM set_config('valemont.owner_id', v_owner, false);
END
$$;

-- ===================================================================== 1
-- app_settings: exactly one row, by construction. `singleton` can only be
-- true and is UNIQUE, so a second row is a unique violation.

CREATE TABLE IF NOT EXISTS app_settings (
    singleton           BOOLEAN     PRIMARY KEY DEFAULT true CHECK (singleton),
    owner_id            UUID        NOT NULL UNIQUE REFERENCES auth.users (id) ON DELETE RESTRICT,
    timezone            TEXT        NOT NULL DEFAULT 'America/Chicago',
    notification_prefs  JSONB       NOT NULL DEFAULT '{}'::jsonb
                                    CHECK (jsonb_typeof(notification_prefs) = 'object'),
    seed_interests      JSONB       NOT NULL DEFAULT '{}'::jsonb
                                    CHECK (jsonb_typeof(seed_interests) = 'object'),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO app_settings (owner_id)
VALUES (current_setting('valemont.owner_id')::uuid)
ON CONFLICT (singleton) DO NOTHING;

-- The owner check. SECURITY INVOKER on purpose: it reads app_settings under
-- the CALLER's RLS, so it returns true only when the caller can see the row,
-- which is only when the caller is the owner. No privilege is borrowed.
CREATE OR REPLACE FUNCTION public.is_owner()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.app_settings WHERE owner_id = (SELECT auth.uid())
    );
$$;

REVOKE ALL ON FUNCTION public.is_owner() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_owner() TO authenticated;

-- ===================================================================== 2

-- job_health: latest state only, one row per job, UPDATEd in place by the
-- worker's job wrapper and the Vercel watchdog (its own 'watchdog' row).
CREATE TABLE IF NOT EXISTS job_health (
    job                   TEXT        PRIMARY KEY CHECK (job ~ '^[a-z][a-z0-9_]*$'),
    expected_interval_s   INTEGER     NOT NULL CHECK (expected_interval_s > 0),
    last_started_at       TIMESTAMPTZ,
    last_ok_at            TIMESTAMPTZ,
    -- Truncated by the writer; the cap here is the backstop. Never a secret:
    -- the wrapper records the exception type and message only.
    last_error            TEXT        CHECK (length(last_error) <= 500),
    consecutive_failures  INTEGER     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    alert_state           TEXT        NOT NULL DEFAULT 'ok' CHECK (alert_state IN ('ok', 'alerted')),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ai_usage: every Claude call, append-only. The budget guard sums cost_usd
-- for the month. Real spend, not paper/live trading money, so no `mode`.
CREATE TABLE IF NOT EXISTS ai_usage (
    id                  BIGSERIAL     PRIMARY KEY,
    purpose             TEXT          NOT NULL CHECK (purpose ~ '^[a-z][a-z0-9_]*$'),
    model               TEXT          NOT NULL CHECK (length(model) > 0),
    tokens_in           INTEGER       NOT NULL CHECK (tokens_in >= 0),
    tokens_out          INTEGER       NOT NULL CHECK (tokens_out >= 0),
    cache_read_tokens   INTEGER       NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    -- Not in the brief's column list, but cache writes are billed at a
    -- premium; cost_usd would be unexplainable without them.
    cache_write_tokens  INTEGER       NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    batch               BOOLEAN       NOT NULL DEFAULT false,
    cost_usd            NUMERIC(12,6) NOT NULL CHECK (cost_usd >= 0),
    critical            BOOLEAN       NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ai_usage_created ON ai_usage (created_at);

DROP TRIGGER IF EXISTS ai_usage_immutable ON ai_usage;
CREATE TRIGGER ai_usage_immutable
    BEFORE UPDATE OR DELETE ON ai_usage
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- job_queue: the app enqueues, the worker claims (FOR UPDATE SKIP LOCKED),
-- retries with backoff and finishes. Only workers UPDATE.
CREATE TABLE IF NOT EXISTS job_queue (
    id           BIGSERIAL   PRIMARY KEY,
    kind         TEXT        NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT        NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts     INTEGER     NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at    TIMESTAMPTZ,
    last_error   TEXT        CHECK (length(last_error) <= 500),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    CHECK ((status IN ('done', 'failed')) = (finished_at IS NOT NULL)),
    CHECK ((status = 'running') = (locked_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS job_queue_due ON job_queue (run_after) WHERE status = 'queued';

-- ---- access for the four app tables

ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_health   ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage     ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_queue    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON app_settings, job_health, ai_usage, job_queue FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE ai_usage_id_seq, job_queue_id_seq FROM anon, authenticated, PUBLIC;

-- app_settings: the owner reads and edits preferences. Column-level UPDATE,
-- so owner_id cannot be changed through the API, and no INSERT or DELETE:
-- deleting this row would lock the owner out of every policy below.
GRANT SELECT ON app_settings TO authenticated;
GRANT UPDATE (timezone, notification_prefs, seed_interests, updated_at) ON app_settings TO authenticated;
DROP POLICY IF EXISTS app_settings_owner ON app_settings;
CREATE POLICY app_settings_owner ON app_settings
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

-- job_health: read-only for the owner (/settings/health).
GRANT SELECT ON job_health TO authenticated;
DROP POLICY IF EXISTS job_health_owner_read ON job_health;
CREATE POLICY job_health_owner_read ON job_health
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));

-- ai_usage: the owner reads it and (Chat 2, the web twin) appends to it.
GRANT SELECT, INSERT ON ai_usage TO authenticated;
GRANT USAGE ON SEQUENCE ai_usage_id_seq TO authenticated;
DROP POLICY IF EXISTS ai_usage_owner_read ON ai_usage;
CREATE POLICY ai_usage_owner_read ON ai_usage
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS ai_usage_owner_append ON ai_usage;
CREATE POLICY ai_usage_owner_append ON ai_usage
    FOR INSERT TO authenticated WITH CHECK ((SELECT public.is_owner()));

-- job_queue: the owner enqueues and watches. An insert must be a fresh job;
-- claiming, retrying and finishing are the worker's alone.
GRANT SELECT, INSERT ON job_queue TO authenticated;
GRANT USAGE ON SEQUENCE job_queue_id_seq TO authenticated;
DROP POLICY IF EXISTS job_queue_owner_read ON job_queue;
CREATE POLICY job_queue_owner_read ON job_queue
    FOR SELECT TO authenticated USING ((SELECT public.is_owner()));
DROP POLICY IF EXISTS job_queue_owner_enqueue ON job_queue;
CREATE POLICY job_queue_owner_enqueue ON job_queue
    FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.is_owner())
                AND status = 'queued' AND attempts = 0
                AND locked_at IS NULL AND finished_at IS NULL AND last_error IS NULL);

-- ===================================================================== 3
-- The ledger, readable by the owner. SELECT policies only; with RLS on and no
-- INSERT/UPDATE/DELETE policy, every write through the API is refused, and
-- section 5b removes the write grants as well.

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'commitments', 'legs', 'resolutions', 'selections', 'commitment_factors',
        'closing_snapshots', 'events', 'runs', 'agents', 'resolution_attempts', 'briefs'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_owner_read', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING ((SELECT public.is_owner()))',
            t || '_owner_read', t);
        EXECUTE format('GRANT SELECT ON public.%I TO authenticated', t);
    END LOOP;
END
$$;

-- ===================================================================== 4
-- Paper/live (CLAUDE.md §1). ADD COLUMN with a constant default is a
-- catalogue change in PostgreSQL 11+: no row is rewritten and no row trigger
-- fires, so commitments_immutable (BEFORE UPDATE OR DELETE, per row) is not
-- involved. Existing rows read the default: every commitment so far is
-- paper, and every one came from an agent. The CHECKs scan existing rows
-- (a read) to validate them.
--
-- Both columns are then frozen like the rest of the row by
-- commitments_immutable: a commitment's mode is part of the claim.

ALTER TABLE commitments
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper'
        CONSTRAINT commitments_mode_check CHECK (mode IN ('paper', 'live'));

-- What produced the commitment: 'agent' for the worker agents; later
-- 'generator', 'analyzer', 'manual' (Chats 8 and 9). snake_case by CHECK.
ALTER TABLE commitments
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'agent'
        CONSTRAINT commitments_origin_check CHECK (origin ~ '^[a-z][a-z0-9_]*$');

-- ===================================================================== 5a
-- HARDENING: TRUNCATE.
--
-- Found while writing this file: every append-only table refuses UPDATE and
-- DELETE by row trigger, but TRUNCATE fires no row trigger at all. Any role
-- holding TRUNCATE (postgres, i.e. the worker's DATABASE_URL, and until 5b
-- also anon and authenticated) could empty commitments in one statement, and
-- §2's guarantee would not notice. A statement-level BEFORE TRUNCATE trigger
-- closes it. reject_mutation() cannot be reused: it reads OLD.id, which does
-- not exist at statement level.

CREATE OR REPLACE FUNCTION public.reject_truncate()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only. TRUNCATE is refused.', TG_TABLE_NAME;
END;
$$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agents', 'runs', 'events', 'commitments', 'legs', 'resolutions',
        'resolution_attempts', 'commitment_factors', 'closing_snapshots', 'selections',
        'briefs', 'model_versions', 'preregistrations', 'migration_log',
        'kalshi_markets', 'kalshi_candles', 'kalshi_trades', 'kalshi_settlements',
        'ai_usage'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_no_truncate', t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate()',
            t || '_no_truncate', t);
    END LOOP;
END
$$;

-- ===================================================================== 5b
-- HARDENING: API-role privileges.
--
-- Found while writing this file: Supabase's default privileges had given
-- anon and authenticated ALL (SELECT, INSERT, UPDATE, DELETE, TRUNCATE,
-- REFERENCES, TRIGGER) on every ledger and archive table. RLS with no
-- policies hid the rows, but the grants were live; db/019 already revoked
-- them for its own two tables.
--
-- anon: nothing in public, now or for tables created later.
-- authenticated: no write privilege on any ledger or archive table. It keeps
-- SELECT; RLS shows it only what section 3's policies allow, and that is the
-- owner's eleven ledger tables.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agents', 'runs', 'events', 'commitments', 'legs', 'resolutions',
        'resolution_attempts', 'commitment_factors', 'closing_snapshots', 'selections',
        'briefs', 'model_versions', 'preregistrations', 'migration_log',
        'kalshi_markets', 'kalshi_candles', 'kalshi_trades', 'kalshi_settlements'
    ] LOOP
        EXECUTE format(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON public.%I FROM authenticated', t);
    END LOOP;
END
$$;

INSERT INTO migration_log (name) VALUES ('020_shared')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) app_settings has exactly one row, with your id
--   (b) RLS on and the listed policies on all 15 tables, every one TO
--       authenticated, none TO anon
--   (c) anon: no privilege on any public table
--   (d) authenticated: no INSERT/UPDATE/DELETE/TRUNCATE on any ledger table
--   (e) every commitment reads mode = 'paper', origin = 'agent'
--   (f) a *_no_truncate trigger on all 19 append-only tables
--   (g) valemont_readonly can SELECT the four new tables
-- ---------------------------------------------------------------------------

SELECT count(*) AS settings_rows, min(owner_id::text) AS owner_id FROM app_settings;            -- (a)

SELECT tablename, policyname, roles, cmd
  FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname;                 -- (b)

SELECT count(*) AS anon_grants
  FROM information_schema.role_table_grants
 WHERE grantee = 'anon' AND table_schema = 'public';                                            -- (c) 0

SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) AS authenticated_privs
  FROM information_schema.role_table_grants
 WHERE grantee = 'authenticated' AND table_schema = 'public'
 GROUP BY table_name ORDER BY table_name;                                                       -- (d)

SELECT mode, origin, count(*) FROM commitments GROUP BY mode, origin;                           -- (e)

SELECT count(*) AS no_truncate_triggers
  FROM pg_trigger WHERE tgname LIKE '%\_no\_truncate' AND NOT tgisinternal;                     -- (f) 19

SELECT c.relname, has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS readonly_select
  FROM pg_class c
 WHERE c.relname IN ('app_settings', 'job_health', 'ai_usage', 'job_queue');                    -- (g)

SELECT name, applied_at FROM migration_log WHERE name = '020_shared';
