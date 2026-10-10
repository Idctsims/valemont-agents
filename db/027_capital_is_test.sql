-- 027_capital_is_test.sql — test entries stay out of the real paper record.
--
-- bankroll_entries is append-only, so a row an e2e run writes is permanent.
-- This marks such rows and makes every capital number ignore them:
--
--   bankroll_entries.is_test   boolean, default false. NOT in the web role's
--                              INSERT grant (db/026 granted mode, kind,
--                              amount, note only), so no client can set it.
--   capital_value_as_of        excludes is_test rows (so do the snapshots,
--   capital_first_at           which are computed from it, and
--                              v_capital_today, which reads both).
--   e2e_markers                SHA-256 hashes of the e2e marker. Private: RLS
--                              on, no policies, no grant to anon/authenticated.
--                              Append-only; the newest row is the live one.
--   add_test_bankroll_entry    the ONLY way to write is_test = true. The owner's
--                              session calls it with the marker; it refuses
--                              unless the marker's hash matches the newest
--                              e2e_markers row. Paper only.
--
-- Where the marker lives: E2E_TEST_MARKER in apps/web/.env.local, for the
-- local test build (pnpm gen:e2e-marker writes it without printing it). The
-- Next server passes it to this function only when the request's
-- x-valemont-e2e header matches that server-only variable, and refuses any
-- request carrying the header when the variable is unset or on Vercel
-- production (src/lib/capital/marker.ts). It is never set on Vercel. A
-- browser never holds it, so no client can write a test row directly.
--
-- The hash below is safe to commit: the marker is 256 random bits.
--
-- Rows written before this migration (2026-10-09/10 e2e runs: 3 deposits and
-- their 3 correcting adjustments, net 0.00) stay exactly as they are,
-- is_test = false. Append-only, no exceptions.
--
-- Prerequisites: db/020 (app_settings, is_owner, reject_truncate), db/026.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- The flag. A constant default is metadata only: no row is rewritten, and no
-- row trigger fires.

ALTER TABLE bankroll_entries
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

-- ===================================================================== 2
-- Exclude test rows from every capital number.

CREATE OR REPLACE FUNCTION public.capital_value_as_of(p_mode text, p_at timestamptz)
RETURNS TABLE (source text, value numeric)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('paper', 'live') THEN
        RAISE EXCEPTION 'capital_value_as_of: mode must be paper or live, got %', p_mode;
    END IF;
    IF p_at IS NULL THEN
        RAISE EXCEPTION 'capital_value_as_of: an instant is required';
    END IF;

    RETURN QUERY
    -- Source 'bankroll'. Later chats add a UNION ALL branch each (db/026
    -- header), and each excludes its own test rows the same way.
    SELECT 'bankroll'::text,
           coalesce(sum(CASE b.kind WHEN 'withdrawal' THEN -b.amount ELSE b.amount END), 0)::numeric
      FROM public.bankroll_entries b
     WHERE b.mode = p_mode
       AND NOT b.is_test
       AND b.created_at <= p_at;
END;
$$;

CREATE OR REPLACE FUNCTION public.capital_first_at(p_mode text)
RETURNS timestamptz
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT least(
        (SELECT min(b.created_at) FROM public.bankroll_entries b WHERE b.mode = p_mode AND NOT b.is_test)
    );
$$;

-- ===================================================================== 3
-- The marker hashes.

CREATE TABLE IF NOT EXISTS e2e_markers (
    id             BIGSERIAL   PRIMARY KEY,
    marker_sha256  TEXT        NOT NULL CONSTRAINT e2e_markers_sha256_format CHECK (marker_sha256 ~ '^[0-9a-f]{64}$'),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS e2e_markers_immutable ON e2e_markers;
CREATE TRIGGER e2e_markers_immutable
    BEFORE UPDATE OR DELETE ON e2e_markers
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS e2e_markers_no_truncate ON e2e_markers;
CREATE TRIGGER e2e_markers_no_truncate
    BEFORE TRUNCATE ON e2e_markers
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

ALTER TABLE e2e_markers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON e2e_markers FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE e2e_markers_id_seq FROM anon, authenticated, PUBLIC;

INSERT INTO e2e_markers (marker_sha256)
SELECT 'eb57bcccfb65fa0cb85c907223c072ec882b2c06b73d575305381edb4e17b4f0'
 WHERE NOT EXISTS (SELECT 1 FROM e2e_markers
                    WHERE marker_sha256 = 'eb57bcccfb65fa0cb85c907223c072ec882b2c06b73d575305381edb4e17b4f0');

-- ===================================================================== 4
-- The one writer of test rows.

CREATE OR REPLACE FUNCTION public.add_test_bankroll_entry(
    p_marker text, p_kind text, p_amount numeric, p_note text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_uid  uuid := auth.uid();
    v_hash text;
    v_id   bigint;
BEGIN
    IF v_uid IS NULL OR NOT public.is_owner() THEN
        RAISE EXCEPTION 'add_test_bankroll_entry: the owner only';
    END IF;
    SELECT m.marker_sha256 INTO v_hash FROM public.e2e_markers m ORDER BY m.id DESC LIMIT 1;
    IF v_hash IS NULL OR p_marker IS NULL
       OR encode(sha256(convert_to(p_marker, 'UTF8')), 'hex') <> v_hash THEN
        RAISE EXCEPTION 'add_test_bankroll_entry: test marker refused';
    END IF;
    -- Paper only, owner and time from the database; the table's CHECKs still
    -- apply (sign rules, note length).
    INSERT INTO public.bankroll_entries (owner_id, mode, kind, amount, note, is_test)
    VALUES (v_uid, 'paper', p_kind, p_amount, p_note, true)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

REVOKE ALL ON FUNCTION public.add_test_bankroll_entry(text, text, numeric, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.add_test_bankroll_entry(text, text, numeric, text) TO authenticated;

INSERT INTO migration_log (name) VALUES ('027_capital_is_test')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) is_test: boolean, not null, default false; every existing row false
--   (b) the web role's INSERT columns on bankroll_entries: amount, kind, mode,
--       note (no is_test)
--   (c) e2e_markers: RLS on, no policies, 1 row, anon/authenticated no grants
--   (d) paper bankroll still 1000.00; v_capital_today unchanged
--   (e) the migration_log row
-- ---------------------------------------------------------------------------

SELECT column_name, data_type, is_nullable, column_default,
       (SELECT count(*) FROM bankroll_entries WHERE is_test) AS test_rows,
       (SELECT count(*) FROM bankroll_entries) AS all_rows
  FROM information_schema.columns
 WHERE table_name = 'bankroll_entries' AND column_name = 'is_test';                         -- (a)

SELECT string_agg(column_name, ', ' ORDER BY column_name) AS authenticated_insert_columns
  FROM information_schema.column_privileges
 WHERE table_name = 'bankroll_entries' AND grantee = 'authenticated' AND privilege_type = 'INSERT'; -- (b)

SELECT c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = 'e2e_markers') AS policies,
       (SELECT count(*) FROM e2e_markers) AS markers,
       (SELECT count(*) FROM information_schema.role_table_grants
         WHERE table_name = 'e2e_markers' AND grantee IN ('anon', 'authenticated')) AS web_grants
  FROM pg_class c WHERE c.relname = 'e2e_markers';                                           -- (c)

SELECT * FROM capital_value_as_of('paper', now());                                           -- (d)
SELECT mode, is_total, source, value, prior_date, change FROM v_capital_today ORDER BY mode, is_total;

SELECT name, applied_at FROM migration_log WHERE name = '027_capital_is_test';               -- (e)
