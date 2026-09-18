-- 003_resolution_attempts.sql — bounded defer
--
-- `resolve()` may answer "not knowable yet": a postponed game, a delisted
-- ticker, a feed that hasn't updated. That is the honest answer and it must
-- stay available. But without a bound, a permanently unresolvable commitment
-- sits in the due set forever and every sweep gets a little slower — a leak
-- that only shows up weeks in, on the 24/7 process, which is the worst place
-- to find one.
--
-- So deferrals are counted. After a per-agent cap on attempts, or a per-agent
-- age past resolves_after, core closes the commitment as void with pnl NULL
-- and stops asking. Counting needs somewhere to live, and it cannot live on
-- `commitments` — that table is immutable, which is the point of it.
--
-- One row per attempt rather than a counter, because "how long did this take
-- to give up on, and why" is worth being able to ask later. A rising void
-- rate means an adapter is broken, and this is the table that shows it.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. The editor
-- supplies its own transaction, so there is no BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS resolution_attempts (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL REFERENCES commitments(id),
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- What the adapter said. 'deferred' is the ordinary case; 'error' means
    -- resolve() raised. Both count against the cap: an adapter that throws
    -- every sweep is no more resolvable than one that keeps saying "not yet".
    result        TEXT        NOT NULL DEFAULT 'deferred'
                  CHECK (result IN ('deferred','error')),
    reason        TEXT
);

-- The sweep counts attempts per commitment on every pass. This index is what
-- keeps that from becoming a scan once there are thousands of rows.
CREATE INDEX IF NOT EXISTS resolution_attempts_commitment_idx
    ON resolution_attempts (commitment_id);

COMMENT ON TABLE resolution_attempts IS
    'One row per unsuccessful resolution attempt. Feeds the bounded-defer '
    'policy in core/agent.py and makes give-up behaviour auditable.';

-- Append-only, same as the rest of the record. An attempt that happened
-- happened; editing the count would let us quietly extend our own patience.
CREATE TRIGGER resolution_attempts_immutable
    BEFORE UPDATE OR DELETE ON resolution_attempts
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- Supabase auto-exposes every public table over PostgREST. RLS on, no
-- policies — denies anon and authenticated, worker bypasses. Same as §5.
ALTER TABLE resolution_attempts ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Verify — expect one row, and the trigger listed.
-- ---------------------------------------------------------------------------

SELECT tgname
  FROM pg_trigger
 WHERE NOT tgisinternal
   AND tgrelid = 'resolution_attempts'::regclass;
