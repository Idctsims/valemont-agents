-- 026_capital.sql — Capital Tracker (Chat 2 Phase 3), on paper data.
--
-- Money tables. Every one carries mode ('paper'|'live'), default 'paper'
-- (CLAUDE.md §1). Paper and live are never summed: not here, not in a view,
-- not on a chart. Money records are append-only: a mistake is corrected with
-- an 'adjustment' entry, never an edit.
--
--   bankroll_entries   deposits, withdrawals, adjustments. APPEND-ONLY.
--                      The owner appends from the web (select + insert
--                      policies). INSERT is granted on mode, kind, amount and
--                      note only: created_at and owner_id come from the
--                      database, so an entry can never be backdated into
--                      history that snapshots were already taken from.
--   crypto_holdings    created now, empty, no UI; Chat 10 fills it.
--                      APPEND-ONLY like the rest (a new position row, never an
--                      edit); a later chat widens that in its own migration
--                      if it must.
--   capital_snapshots  one closing value per day, mode and source. Written by
--                      the worker only (owner: select). APPEND-ONLY. A day's
--                      snapshot can be written only once that day has ENDED
--                      in the owner's timezone (trigger), so no snapshot is a
--                      guess at an unfinished day.
--
-- THE SOURCE CONTRACT. capital_value_as_of(mode, at) returns one row per
-- capital source: (source, value) as it stood at that instant. Today there is
-- one source, 'bankroll': the signed sum of bankroll_entries with
-- created_at <= at for that mode (deposit +, withdrawal −, adjustment ±).
-- Later chats plug in without a rebuild, each adding one UNION ALL branch
-- here AND its earliest instant to capital_first_at():
--   Chat 9   settled bets P/L            ('bets')
--   Chat 10  bot equity, crypto holdings ('bots', 'crypto')
--   Chat 11  its own source
-- Every branch MUST be reconstructible as of a past instant, from append-only
-- rows with database-set timestamps. The worker's boot backfill recomputes
-- every past day from this function; a branch that only knows "now" would
-- write today's value into history.
--
-- capital_first_at(mode)   earliest instant any source has data for a mode;
--                          NULL = the mode has no data (no snapshots, no UI).
-- capital_day_end(date)    the last instant of a local day (owner's tz).
-- v_capital_today          per mode: each source's value now, its value at the
--                          latest snapshot before today (owner's tz), and the
--                          change; plus one is_total row per mode. Never a row
--                          that sums across modes. security_invoker.
--
-- Access (db/019–022): RLS on, owner policies TO authenticated, nothing for
-- anon. Guards on all three tables: UPDATE/DELETE (reject_mutation, db/001)
-- and TRUNCATE (reject_truncate, db/020).
--
-- SEED: one paper deposit of 1000.00, "Paper bankroll seed", owner from
-- app_settings; skipped if bankroll_entries has any row (safe to re-run).
--
-- Prerequisites: db/001 (reject_mutation), db/015 (migration_log), db/020
-- (app_settings, reject_truncate), db/021 (goal_local_today).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

-- ===================================================================== 1
-- Tables.

CREATE TABLE IF NOT EXISTS bankroll_entries (
    id          BIGSERIAL     PRIMARY KEY,
    owner_id    UUID          NOT NULL DEFAULT auth.uid()
                              REFERENCES auth.users (id) ON DELETE RESTRICT,
    mode        TEXT          NOT NULL DEFAULT 'paper'
                              CONSTRAINT bankroll_entries_mode_check CHECK (mode IN ('paper', 'live')),
    kind        TEXT          NOT NULL
                              CONSTRAINT bankroll_entries_kind_check CHECK (kind IN ('deposit', 'withdrawal', 'adjustment')),
    -- Positive for deposit and withdrawal (kind carries the sign); an
    -- adjustment may be either sign, never zero.
    amount      NUMERIC(14,2) NOT NULL
                              CONSTRAINT bankroll_entries_amount_sign CHECK (
                                  (kind IN ('deposit', 'withdrawal') AND amount > 0)
                                  OR (kind = 'adjustment' AND amount <> 0)),
    note        TEXT          CONSTRAINT bankroll_entries_note_length CHECK (char_length(note) <= 500),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bankroll_entries_mode_time ON bankroll_entries (mode, created_at);

CREATE TABLE IF NOT EXISTS crypto_holdings (
    id          BIGSERIAL   PRIMARY KEY,
    owner_id    UUID        NOT NULL DEFAULT auth.uid()
                            REFERENCES auth.users (id) ON DELETE RESTRICT,
    mode        TEXT        NOT NULL DEFAULT 'paper'
                            CONSTRAINT crypto_holdings_mode_check CHECK (mode IN ('paper', 'live')),
    asset       TEXT        NOT NULL CONSTRAINT crypto_holdings_asset_length CHECK (char_length(asset) BETWEEN 1 AND 40),
    qty         NUMERIC     NOT NULL,
    cost_basis  NUMERIC,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capital_snapshots (
    id          BIGSERIAL     PRIMARY KEY,
    owner_id    UUID          NOT NULL REFERENCES auth.users (id) ON DELETE RESTRICT,
    snap_date   DATE          NOT NULL,
    mode        TEXT          NOT NULL DEFAULT 'paper'
                              CONSTRAINT capital_snapshots_mode_check CHECK (mode IN ('paper', 'live')),
    -- snake_case, like commitment_factors (§10.1): free text would let
    -- 'bankroll' and 'Bankroll' split one series in two.
    source      TEXT          NOT NULL CONSTRAINT capital_snapshots_source_format
                              CHECK (source ~ '^[a-z][a-z0-9_]*$' AND char_length(source) <= 40),
    value       NUMERIC(14,2) NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT capital_snapshots_day_key UNIQUE (owner_id, snap_date, mode, source)
);

CREATE INDEX IF NOT EXISTS capital_snapshots_mode_date ON capital_snapshots (mode, snap_date);

-- ===================================================================== 2
-- Append-only, all three.

DROP TRIGGER IF EXISTS bankroll_entries_immutable ON bankroll_entries;
CREATE TRIGGER bankroll_entries_immutable
    BEFORE UPDATE OR DELETE ON bankroll_entries
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS bankroll_entries_no_truncate ON bankroll_entries;
CREATE TRIGGER bankroll_entries_no_truncate
    BEFORE TRUNCATE ON bankroll_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

DROP TRIGGER IF EXISTS crypto_holdings_immutable ON crypto_holdings;
CREATE TRIGGER crypto_holdings_immutable
    BEFORE UPDATE OR DELETE ON crypto_holdings
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS crypto_holdings_no_truncate ON crypto_holdings;
CREATE TRIGGER crypto_holdings_no_truncate
    BEFORE TRUNCATE ON crypto_holdings
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

DROP TRIGGER IF EXISTS capital_snapshots_immutable ON capital_snapshots;
CREATE TRIGGER capital_snapshots_immutable
    BEFORE UPDATE OR DELETE ON capital_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.reject_mutation();
DROP TRIGGER IF EXISTS capital_snapshots_no_truncate ON capital_snapshots;
CREATE TRIGGER capital_snapshots_no_truncate
    BEFORE TRUNCATE ON capital_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION public.reject_truncate();

-- ===================================================================== 3
-- The source contract.

-- The last instant of local day p_day, in the owner's timezone.
CREATE OR REPLACE FUNCTION public.capital_day_end(p_day date)
RETURNS timestamptz
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT ((p_day + 1)::timestamp AT TIME ZONE coalesce(
               (SELECT timezone FROM public.app_settings LIMIT 1), 'America/Chicago'))
           - interval '1 microsecond';
$$;

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
    -- Source 'bankroll'. Later chats add a UNION ALL branch each (header).
    SELECT 'bankroll'::text,
           coalesce(sum(CASE b.kind WHEN 'withdrawal' THEN -b.amount ELSE b.amount END), 0)::numeric
      FROM public.bankroll_entries b
     WHERE b.mode = p_mode
       AND b.created_at <= p_at;
END;
$$;

-- Earliest instant any source has data for p_mode; NULL = none. Each source
-- branch added above adds its own earliest instant to the least() here.
CREATE OR REPLACE FUNCTION public.capital_first_at(p_mode text)
RETURNS timestamptz
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = ''
AS $$
    SELECT least(
        (SELECT min(b.created_at) FROM public.bankroll_entries b WHERE b.mode = p_mode)
    );
$$;

-- A snapshot is a day's CLOSE: refuse one for a day that has not ended.
CREATE OR REPLACE FUNCTION public.capital_snapshots_day_ended()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.snap_date >= public.goal_local_today() THEN
        RAISE EXCEPTION 'capital_snapshots: % has not ended in the owner''s timezone (local date %); a close is written after the day',
            NEW.snap_date, public.goal_local_today();
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS capital_snapshots_day_ended ON capital_snapshots;
CREATE TRIGGER capital_snapshots_day_ended
    BEFORE INSERT ON capital_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.capital_snapshots_day_ended();

-- ===================================================================== 4
-- Today, per mode. Never a row across modes.

CREATE OR REPLACE VIEW v_capital_today
WITH (security_invoker = true)
AS
WITH modes AS (
    SELECT m.mode
      FROM (VALUES ('paper'), ('live')) AS m (mode)
     WHERE public.capital_first_at(m.mode) IS NOT NULL
),
prior AS (
    SELECT m.mode,
           (SELECT max(s.snap_date) FROM public.capital_snapshots s
             WHERE s.mode = m.mode AND s.snap_date < public.goal_local_today()) AS prior_date
      FROM modes m
),
per_source AS (
    SELECT p.mode, v.source, v.value, p.prior_date,
           -- A source with no row on the prior date contributed nothing then.
           CASE WHEN p.prior_date IS NULL THEN NULL
                ELSE coalesce((SELECT s.value FROM public.capital_snapshots s
                                WHERE s.mode = p.mode AND s.source = v.source
                                  AND s.snap_date = p.prior_date), 0)
           END AS prior_value
      FROM prior p
     CROSS JOIN LATERAL public.capital_value_as_of(p.mode, now()) v
)
SELECT mode, false AS is_total, source, value, prior_date, prior_value,
       value - prior_value AS change
  FROM per_source
UNION ALL
SELECT mode, true AS is_total, 'total' AS source, sum(value), max(prior_date),
       CASE WHEN max(prior_date) IS NULL THEN NULL ELSE sum(prior_value) END,
       CASE WHEN max(prior_date) IS NULL THEN NULL ELSE sum(value) - sum(prior_value) END
  FROM per_source
 GROUP BY mode;

-- ===================================================================== 5
-- Access.

ALTER TABLE bankroll_entries  ENABLE ROW LEVEL SECURITY;
ALTER TABLE crypto_holdings   ENABLE ROW LEVEL SECURITY;
ALTER TABLE capital_snapshots ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON bankroll_entries, crypto_holdings, capital_snapshots, v_capital_today
    FROM anon, authenticated, PUBLIC;
REVOKE ALL ON SEQUENCE bankroll_entries_id_seq, crypto_holdings_id_seq, capital_snapshots_id_seq
    FROM anon, authenticated, PUBLIC;

GRANT SELECT ON bankroll_entries, crypto_holdings, capital_snapshots, v_capital_today TO authenticated;
-- Column-level: the database sets owner_id and created_at, never the caller.
GRANT INSERT (mode, kind, amount, note) ON bankroll_entries TO authenticated;
GRANT INSERT (mode, asset, qty, cost_basis) ON crypto_holdings TO authenticated;
GRANT USAGE ON SEQUENCE bankroll_entries_id_seq, crypto_holdings_id_seq TO authenticated;

DROP POLICY IF EXISTS bankroll_entries_owner_read ON bankroll_entries;
CREATE POLICY bankroll_entries_owner_read ON bankroll_entries
    FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS bankroll_entries_owner_append ON bankroll_entries;
CREATE POLICY bankroll_entries_owner_append ON bankroll_entries
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS crypto_holdings_owner_read ON crypto_holdings;
CREATE POLICY crypto_holdings_owner_read ON crypto_holdings
    FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS crypto_holdings_owner_append ON crypto_holdings;
CREATE POLICY crypto_holdings_owner_append ON crypto_holdings
    FOR INSERT TO authenticated
    WITH CHECK (owner_id = (SELECT auth.uid()));

-- The worker writes snapshots (DATABASE_URL bypasses RLS); the owner reads.
DROP POLICY IF EXISTS capital_snapshots_owner_read ON capital_snapshots;
CREATE POLICY capital_snapshots_owner_read ON capital_snapshots
    FOR SELECT TO authenticated
    USING (owner_id = (SELECT auth.uid()));

REVOKE ALL ON FUNCTION public.capital_day_end(date) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.capital_value_as_of(text, timestamptz) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.capital_first_at(text) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.capital_snapshots_day_ended() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.capital_day_end(date) TO authenticated;
GRANT EXECUTE ON FUNCTION public.capital_value_as_of(text, timestamptz) TO authenticated;
GRANT EXECUTE ON FUNCTION public.capital_first_at(text) TO authenticated;

-- ===================================================================== 6
-- Seed: one paper deposit, skipped if any entry exists.

DO $$
DECLARE
    v_owner uuid := (SELECT owner_id FROM public.app_settings);
BEGIN
    IF v_owner IS NULL THEN
        RAISE EXCEPTION 'app_settings has no owner; paste db/020 first. Nothing was seeded.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.bankroll_entries) THEN
        RAISE NOTICE 'bankroll_entries already has rows; seed skipped';
        RETURN;
    END IF;
    INSERT INTO public.bankroll_entries (owner_id, mode, kind, amount, note)
    VALUES (v_owner, 'paper', 'deposit', 1000.00, 'Paper bankroll seed');
END
$$;

INSERT INTO migration_log (name) VALUES ('026_capital')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Verify — expect:
--   (a) RLS on; bankroll_entries and crypto_holdings: owner_read + owner_append;
--       capital_snapshots: owner_read only; all TO authenticated
--   (b) anon: 0 privileges on the four
--   (c) guards: immutable + no_truncate on each table, plus day_ended
--   (d) the seed: 1 row, paper deposit 1000.00 "Paper bankroll seed"
--   (e) paper bankroll now: 1000.00; live: no row in v_capital_today
--   (f) the view is security_invoker
--   (g) the migration_log row
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       (SELECT string_agg(p.policyname || ':' || p.cmd || ':' || array_to_string(p.roles, ','), ' ' ORDER BY p.policyname)
          FROM pg_policies p WHERE p.tablename = c.relname) AS policies
  FROM pg_class c
 WHERE c.relname IN ('bankroll_entries', 'crypto_holdings', 'capital_snapshots')
 ORDER BY c.relname;                                                                          -- (a)

SELECT count(*) AS anon_grants FROM information_schema.role_table_grants
 WHERE grantee = 'anon'
   AND table_name IN ('bankroll_entries', 'crypto_holdings', 'capital_snapshots', 'v_capital_today'); -- (b) 0

SELECT tgrelid::regclass AS on_table, string_agg(tgname, ', ' ORDER BY tgname) AS triggers
  FROM pg_trigger
 WHERE NOT tgisinternal
   AND tgrelid IN ('bankroll_entries'::regclass, 'crypto_holdings'::regclass, 'capital_snapshots'::regclass)
 GROUP BY 1 ORDER BY 1;                                                                       -- (c)

SELECT mode, kind, amount, note FROM bankroll_entries ORDER BY id;                            -- (d)

SELECT * FROM capital_value_as_of('paper', now());                                            -- (e) bankroll 1000.00
SELECT mode, is_total, source, value, prior_date, change FROM v_capital_today ORDER BY mode, is_total; -- (e) paper only

SELECT c.relname, c.reloptions FROM pg_class c WHERE c.relname = 'v_capital_today';          -- (f)

SELECT name, applied_at FROM migration_log WHERE name = '026_capital';                       -- (g)
