-- 005_capture_lag.sql — how late was the "close" we recorded?
--
-- A close captured thirty minutes after the market closed, on a market that
-- kept moving, is a late price wearing the name "close". CLV computed against
-- it is contaminated, and nothing in the row says so — it looks identical to a
-- clean measurement.
--
-- So the lag is recorded on every snapshot. Two uses:
--
--   1. Audit. Segment CLV by lag and check whether slow captures are quietly
--      skewing the primary metric:
--
--        SELECT width_bucket(capture_lag_seconds, 0, 3600, 6) AS bucket,
--               count(*), avg(clv), stddev(clv)
--          FROM closing_snapshots WHERE status = 'captured'
--         GROUP BY 1 ORDER BY 1;
--
--   2. Tuning. Set the capture job's interval from the observed distribution
--      rather than from a guess:
--
--        SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY capture_lag_seconds)
--          FROM closing_snapshots WHERE status = 'captured';
--
-- Separate file from 004 because 004 is already being applied. Both are
-- idempotent; apply 004 first, then this.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

ALTER TABLE closing_snapshots
    ADD COLUMN IF NOT EXISTS capture_lag_seconds NUMERIC;

COMMENT ON COLUMN closing_snapshots.capture_lag_seconds IS
    'captured_at - commitments.closes_at, in seconds. Set by the '
    'closing_snapshots_timing trigger, never by application code. On a '
    'captured row it is how late the "close" price actually is; on a missed '
    'row it is how long we tried before giving up. NULL on any row written '
    'before this migration: those predate the column and are deliberately '
    'not backfilled, because backfilling would mean disabling the '
    'append-only trigger. Treat NULL as "unknown lag", not as zero.';

-- ---------------------------------------------------------------------------
-- Computed by the DATABASE, not the worker.
--
-- `captured_at` defaults to now() on the database clock and `closes_at` lives
-- on `commitments`, so a worker computing this from its own clock would write
-- a wrong lag on any container with clock skew — and a wrong lag is worse than
-- none, because it would be trusted. The timing trigger already fetches
-- closes_at to validate the insert, so filling this in costs nothing.
--
-- This replaces the function created in 004. Same trigger, extended body.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION snapshot_timing() RETURNS TRIGGER AS $$
DECLARE ca TIMESTAMPTZ;
BEGIN
    SELECT closes_at INTO ca FROM commitments WHERE id = NEW.commitment_id;
    IF ca IS NULL THEN
        RAISE EXCEPTION
            'Commitment % declared no closes_at — it has no close to snapshot.',
            NEW.commitment_id;
    END IF;
    IF NEW.captured_at < ca THEN
        RAISE EXCEPTION
            'Cannot snapshot the close of commitment % before %.',
            NEW.commitment_id, ca;
    END IF;

    -- Always overwrite: the lag is a measured fact, not something a caller
    -- gets to assert. Anything passed in is discarded.
    NEW.capture_lag_seconds :=
        EXTRACT(epoch FROM (NEW.captured_at - ca));

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- NOT backfilled, on purpose.
--
-- Rows written before this migration keep a NULL lag. Filling them would mean
-- disabling `closing_snapshots_immutable` for the duration of an UPDATE, and
-- the case for doing it here is good: every affected row is `_fake` test data
-- that nothing will ever compute from, and the value is fully implied by
-- closes_at and captured_at.
--
-- That is exactly why it is not done. The cost is not this UPDATE, it is
-- establishing in the repository that the immutability trigger comes off when
-- the argument is good enough — after which a later session finds the
-- precedent and applies it where the argument is only almost good enough.
-- The trigger's whole value is that it has no exceptions. Keep the escape
-- hatch out of the codebase.
--
-- So: NULL means "unknown lag", never zero. Filter on
-- `capture_lag_seconds IS NOT NULL` in any lag analysis.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Verify — expect the column present, both triggers still armed, and a count
-- of pre-005 rows that will stay NULL.
-- ---------------------------------------------------------------------------

SELECT
    (SELECT count(*) FROM information_schema.columns
      WHERE table_name = 'closing_snapshots'
        AND column_name = 'capture_lag_seconds')      AS column_present,
    (SELECT count(*) FROM pg_trigger
      WHERE NOT tgisinternal
        AND tgrelid = 'closing_snapshots'::regclass)  AS triggers_armed,
    (SELECT count(*) FROM closing_snapshots
      WHERE capture_lag_seconds IS NULL)              AS pre_005_rows_left_null;
