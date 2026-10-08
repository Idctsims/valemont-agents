-- 019_push.sql — Web Push: device subscriptions and the notifications log
--
-- The FIRST app tables, and the first RLS policies in this database.
--
-- App tables, not ledger tables: rows are owned by a Supabase Auth user and
-- are ordinary mutable data (a subscription goes inactive, a notification
-- moves from queued to sent). Nothing here is a commitment, so §2's
-- immutability does not apply and no trigger freezes them. Not money tables,
-- so no `mode` column (§1).
--
-- Two writers, one table each way (CLAUDE.md §5):
--   apps/web   as the signed-in owner, through RLS: saves and removes this
--              device's subscription, logs the "Send test push" it sends.
--   workers    through DATABASE_URL (postgres, BYPASSRLS): reads active
--              subscriptions, logs every send, marks a subscription inactive
--              when the push service answers 404/410.
--
-- Access:
--   authenticated   SELECT/INSERT/UPDATE/DELETE, and only rows where
--                   owner_id = auth.uid() (one policy per table, USING and
--                   WITH CHECK both). Sequence USAGE for its inserts.
--   anon            nothing. Supabase's default privileges grant anon ALL on
--                   every new public table; this file revokes it, so the
--                   publishable key cannot even read the shape of the data.
--   valemont_readonly  SELECT, from db/018's default privileges for postgres;
--                   BYPASSRLS lets it see every row, as it must for inspection.
--
-- Endpoints and keys are capabilities: anyone holding (endpoint, p256dh, auth)
-- plus the VAPID private key can push to the device. They never leave the
-- server side, and workers/core/push.py logs endpoints truncated.
--
-- Prerequisites: db/015 (migration_log), db/018 (readonly default privileges).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id               BIGSERIAL   PRIMARY KEY,
    owner_id         UUID        NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    -- A push service URL is always https; anything else is not a subscription.
    endpoint         TEXT        NOT NULL UNIQUE CHECK (endpoint LIKE 'https://%'),
    p256dh           TEXT        NOT NULL CHECK (length(p256dh) > 0),
    auth             TEXT        NOT NULL CHECK (length(auth) > 0),
    device_label     TEXT,
    user_agent       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at  TIMESTAMPTZ,
    failed_at        TIMESTAMPTZ,
    active           BOOLEAN     NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS push_subscriptions_owner_active
    ON push_subscriptions (owner_id) WHERE active;

CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL   PRIMARY KEY,
    owner_id    UUID        NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    -- snake_case by CHECK, for the reason commitment_factors gives (§10.1):
    -- `test`, `Test` and `tests` must not fragment one kind into three.
    kind        TEXT        NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    title       TEXT        NOT NULL CHECK (length(title) > 0),
    body        TEXT,
    -- Same-origin path only, matching what the service worker will open.
    deep_link   TEXT        CHECK (deep_link IS NULL
                                   OR (deep_link LIKE '/%' AND deep_link NOT LIKE '//%')),
    -- queued: written, not yet sent. sent: every active device accepted it.
    -- partial: some did. failed: none did, or there were none (see error).
    status      TEXT        NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'sent', 'partial', 'failed')),
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ,
    CHECK (status = 'queued' OR sent_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS notifications_owner_created
    ON notifications (owner_id, created_at DESC);

-- ---------------------------------------------------------------- access

ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON push_subscriptions, notifications FROM anon, PUBLIC;
REVOKE ALL ON SEQUENCE push_subscriptions_id_seq, notifications_id_seq FROM anon, PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON push_subscriptions, notifications TO authenticated;
GRANT USAGE ON SEQUENCE push_subscriptions_id_seq, notifications_id_seq TO authenticated;
-- No TRUNCATE, REFERENCES or TRIGGER for the API role; Supabase's defaults
-- would have granted them.
REVOKE TRUNCATE, REFERENCES, TRIGGER ON push_subscriptions, notifications FROM authenticated;

-- `(select auth.uid())`, not `auth.uid()`: evaluated once per statement
-- instead of once per row (Supabase's documented RLS performance pattern).
DROP POLICY IF EXISTS push_subscriptions_owner ON push_subscriptions;
CREATE POLICY push_subscriptions_owner ON push_subscriptions
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS notifications_owner ON notifications;
CREATE POLICY notifications_owner ON notifications
    FOR ALL TO authenticated
    USING (owner_id = (SELECT auth.uid()))
    WITH CHECK (owner_id = (SELECT auth.uid()));

INSERT INTO migration_log (name) VALUES ('019_push')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect: both tables with RLS on; one policy each, TO authenticated;
-- anon with no privilege at all; authenticated with exactly
-- SELECT/INSERT/UPDATE/DELETE; valemont_readonly with SELECT only.
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity AS rls_on
  FROM pg_class c
 WHERE c.relname IN ('push_subscriptions', 'notifications');

SELECT tablename, policyname, roles, cmd, qual, with_check
  FROM pg_policies
 WHERE tablename IN ('push_subscriptions', 'notifications');

SELECT t.relname, r.rolname,
       has_table_privilege(r.rolname, t.oid, 'SELECT')   AS sel,
       has_table_privilege(r.rolname, t.oid, 'INSERT')   AS ins,
       has_table_privilege(r.rolname, t.oid, 'UPDATE')   AS upd,
       has_table_privilege(r.rolname, t.oid, 'DELETE')   AS del,
       has_table_privilege(r.rolname, t.oid, 'TRUNCATE') AS trunc
  FROM pg_class t
 CROSS JOIN (VALUES ('anon'), ('authenticated'), ('valemont_readonly')) AS r (rolname)
 WHERE t.relname IN ('push_subscriptions', 'notifications')
 ORDER BY t.relname, r.rolname;

SELECT name, applied_at FROM migration_log WHERE name = '019_push';
