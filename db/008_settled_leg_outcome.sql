-- 008_settled_leg_outcome.sql — a leg outcome for "settled at a fair value"
--
-- Kalshi never voids. A tied NFL game settles each team at $0.50; a game
-- postponed beyond 48 hours, a forfeit before kickoff, a home/away swap, and a
-- prop player who is inactive or active-but-never-snaps all settle at "the
-- last fair price as determined by the Exchange" (contract terms
-- FOOTBALLGAMEWIN, FOOTBALLSPREAD, FOOTBALLENTITYSTAT, read 2026-09-30).
--
-- None of those is a hit, a miss, a push or a void. Recording them as `push`
-- would claim the stake came back; recording them as `void` would claim no
-- settlement happened and would inflate void rate, the health metric that is
-- supposed to mean "a data source broke" (CLAUDE.md §8). So legs get a fifth
-- value, `settled`: the exchange paid out `actual` (a value strictly between 0
-- and 1), and pnl is computed from it like any other settlement.
--
-- The commitment-level outcome for such a resolution is the existing
-- `partial`; resolutions.outcome needs no change.
--
-- This alters a CHECK, not a row. `legs` is not append-only (its outcome
-- columns are written once by the resolution path, guarded by legs_frozen), and
-- no existing row can violate the widened constraint.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

ALTER TABLE legs DROP CONSTRAINT IF EXISTS legs_outcome_check;

ALTER TABLE legs ADD CONSTRAINT legs_outcome_check
    CHECK (outcome IN ('hit', 'miss', 'push', 'void', 'settled'));

-- ---------------------------------------------------------------------------
-- Verify — expect one row listing all five values.
-- ---------------------------------------------------------------------------

SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conrelid = 'legs'::regclass AND conname = 'legs_outcome_check';
