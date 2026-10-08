-- 018_readonly_role.sql — a login that can read the database and nothing else
--
-- Every inspection of the database (a session checking a migration landed, a
-- count, a sanity query) goes through `scripts/db_inspect.py`, which connects
-- as this role and never reads DATABASE_URL. The read-only guarantee lives
-- HERE, in grants the database enforces, not in the script's good behaviour.
--
--   valemont_readonly   NOLOGIN as created. The owner enables login by hand in
--                       the SQL Editor with a password chosen there:
--                         ALTER ROLE valemont_readonly LOGIN PASSWORD '...';
--                       No password is ever written in this file or in git.
--
-- What it can do:
--   - USAGE on schema public, SELECT on every table in it, now and future
--     (default privileges for tables created by `postgres`, which is the role
--     the SQL Editor runs migrations as and which owns every ledger table).
--   - BYPASSRLS. Every table has RLS on with no policies, so without this the
--     role's SELECTs would succeed and return ZERO rows: a read-only role that
--     silently sees an empty database. BYPASSRLS skips row filtering only; it
--     grants no privilege. Writes still need INSERT/UPDATE/DELETE grants, and
--     this role has none.
--
-- What it cannot do: INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER on
-- any table; CREATE in schema public (PUBLIC holds USAGE only there); nextval
-- on any sequence (no sequence grants).
--
-- Inherited from PUBLIC, unchanged by this file, and stated so nobody assumes
-- otherwise: CONNECT and TEMP on the database (it can create session-scoped
-- temp tables, which vanish with the session and cannot touch a ledger table),
-- and EXECUTE on functions, which PostgreSQL grants PUBLIC by default. A
-- function runs with the caller's privileges unless SECURITY DEFINER; none in
-- db/001–017 is, so EXECUTE gives this role no write path.
--
-- No superuser step anywhere. `postgres` on Supabase is not a superuser; it
-- holds CREATEROLE and BYPASSRLS, and PostgreSQL 16+ lets a role holding
-- BYPASSRLS create a role with it. The first paste of this file failed because
-- it also ran `ALTER ROLE ... NOSUPERUSER ...`: on 16+, ALTER ROLE that so much
-- as mentions the SUPERUSER attribute needs a superuser, even to say NO. This
-- version never ALTERs the role. It creates it once and then ASSERTS its
-- attributes, which needs no privilege and fails loudly on a re-run against a
-- role that has drifted.
--
-- Prerequisites: db/015 (migration_log).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run: it never resets LOGIN or the password once the owner sets them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'valemont_readonly') THEN
        -- Defaults are NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
        -- BYPASSRLS is the only attribute stated.
        CREATE ROLE valemont_readonly NOLOGIN BYPASSRLS;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'valemont_readonly'
                 AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication
                      OR NOT rolbypassrls)) THEN
        RAISE EXCEPTION 'valemont_readonly exists with the wrong attributes; expected '
                        'BYPASSRLS and none of SUPERUSER, CREATEROLE, CREATEDB, REPLICATION.';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO valemont_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO valemont_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT ON TABLES TO valemont_readonly;

INSERT INTO migration_log (name) VALUES ('018_readonly_role')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect: the role with rolcanlogin false (until you enable it),
-- rolbypassrls true; every public table with SELECT and no other privilege;
-- and the default-privilege row for future tables.
-- ---------------------------------------------------------------------------

SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
  FROM pg_roles WHERE rolname = 'valemont_readonly';

SELECT c.relname,
       has_table_privilege('valemont_readonly', c.oid, 'SELECT') AS can_select,
       has_table_privilege('valemont_readonly', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
 ORDER BY c.relname;

SELECT pg_get_userbyid(defaclrole) AS for_role, defaclobjtype, defaclacl
  FROM pg_default_acl
 WHERE defaclnamespace = 'public'::regnamespace
   AND defaclacl::text LIKE '%valemont_readonly%';
