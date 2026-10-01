-- 015_close_mutation_gaps.sql — make the database refuse what the audit found it accepted
--
-- Empirical audit, 2026-10-01: UPDATE and DELETE were attempted on a _test row
-- of every table inside rolled-back transactions (CLAUDE.md, Current State).
-- Found:
--
--   resolutions   UPDATE ACCEPTED, DELETE ACCEPTED. The scored outcome and pnl
--                 of a commitment could be rewritten or erased, and a new
--                 resolution written in place of a deleted void. Nothing but
--                 convention protected the track record's result rows.
--   legs          DELETE ACCEPTED. UPDATE was already guarded (legs_frozen).
--   briefs        UPDATE and DELETE ACCEPTED.
--   runs          UPDATE ACCEPTED on any row, ended or not. DELETE refused only
--                 by a foreign key, so a run with no commitments could go.
--   agents        UPDATE ACCEPTED on every column, including slug and is_test
--                 (flipping is_test would move a record in or out of the
--                 quarantine). DELETE refused only by a foreign key.
--
-- After this file:
--
--   resolutions, briefs  append-only (reject_mutation, db/001).
--   legs                 DELETE refused. UPDATE keeps legs_frozen: the outcome
--                        is written once and the commit-time fields never move.
--   runs                 DELETE refused. UPDATE allowed only while the run is
--                        open (ended_at IS NULL), and never on id, agent_id or
--                        started_at. That is exactly what end_run does, once.
--   agents               DELETE refused. UPDATE allowed only on enabled and
--                        display_name; id, slug, domain, is_test and created_at
--                        are frozen.
--
-- migration_log is new and append-only. Its row for this file carries the
-- database's own timestamp: **rows written before it were protected by
-- convention only** in the tables above. CLAUDE.md records it.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS migration_log (
    id          BIGSERIAL   PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE migration_log ENABLE ROW LEVEL SECURITY;

DROP TRIGGER IF EXISTS migration_log_immutable ON migration_log;
CREATE TRIGGER migration_log_immutable
    BEFORE UPDATE OR DELETE ON migration_log
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- resolutions, briefs: append-only ------------------------------------------

DROP TRIGGER IF EXISTS resolutions_immutable ON resolutions;
CREATE TRIGGER resolutions_immutable
    BEFORE UPDATE OR DELETE ON resolutions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

DROP TRIGGER IF EXISTS briefs_immutable ON briefs;
CREATE TRIGGER briefs_immutable
    BEFORE UPDATE OR DELETE ON briefs
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- legs: no DELETE ------------------------------------------------------------

DROP TRIGGER IF EXISTS legs_no_delete ON legs;
CREATE TRIGGER legs_no_delete
    BEFORE DELETE ON legs
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- runs: closed once, never deleted ---------------------------------------------

CREATE OR REPLACE FUNCTION runs_close_once() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION 'Run % already ended at %; a closed run is final.', OLD.id, OLD.ended_at;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.agent_id IS DISTINCT FROM OLD.agent_id
    OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'Run % identity fields (id, agent_id, started_at) are frozen.', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS runs_close_once ON runs;
CREATE TRIGGER runs_close_once
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION runs_close_once();

DROP TRIGGER IF EXISTS runs_no_delete ON runs;
CREATE TRIGGER runs_no_delete
    BEFORE DELETE ON runs
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- agents: only the switch and the label move ---------------------------------

CREATE OR REPLACE FUNCTION agents_identity_frozen() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
    OR NEW.slug IS DISTINCT FROM OLD.slug
    OR NEW.domain IS DISTINCT FROM OLD.domain
    OR NEW.is_test IS DISTINCT FROM OLD.is_test
    OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Agent % identity fields (id, slug, domain, is_test, created_at) are frozen; only enabled and display_name may change.', OLD.slug;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agents_identity_frozen ON agents;
CREATE TRIGGER agents_identity_frozen
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION agents_identity_frozen();

DROP TRIGGER IF EXISTS agents_no_delete ON agents;
CREATE TRIGGER agents_no_delete
    BEFORE DELETE ON agents
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- The stamp: the database's own time at which these guards began --------------

INSERT INTO migration_log (name) VALUES ('015_close_mutation_gaps')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect applied_at, and every guard listed.
SELECT name, applied_at FROM migration_log WHERE name = '015_close_mutation_gaps';

SELECT c.relname, array_agg(t.tgname ORDER BY t.tgname) AS triggers
  FROM pg_class c
  JOIN pg_trigger t ON t.tgrelid = c.oid AND NOT t.tgisinternal
 WHERE c.relname IN ('resolutions', 'briefs', 'legs', 'runs', 'agents', 'migration_log')
 GROUP BY c.relname ORDER BY c.relname;
