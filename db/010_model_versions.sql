-- 010_model_versions.sql — versioned, append-only model fits
--
-- Every agent that fits a model refits weekly, and the four-hook loop has no
-- place for fitting, correctly (docs/kalshi_nfl.md §5). A weekly job fits and
-- writes one row here; `form_thesis()` loads the latest row whose
-- `data_through` precedes the commit instant, and each commitment's payload
-- records the `model_versions.id` it used. That makes every commitment
-- reproducible from the exact parameters that produced it.
--
-- Append-only, like the ledger: a fit you could edit after seeing how it did
-- is a thesis revised after the outcome (§2). A bad fit is superseded by a new
-- row, never repaired in place.
--
-- `data_through` is the walk-forward fence: the latest kickoff whose data the
-- fit saw. The CHECK keeps it honest relative to `fitted_at`, which the
-- database stamps.
--
-- `usable = false` records a fit that failed its pre-registered calibration
-- tolerance (preregistration_nfl.md §5). It is kept, with the reason, and
-- never loaded.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS model_versions (
    id            BIGSERIAL   PRIMARY KEY,
    agent_id      SMALLINT    NOT NULL REFERENCES agents(id),
    model         TEXT        NOT NULL CHECK (model ~ '^[a-z][a-z0-9_]*$'),
    fitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_through  TIMESTAMPTZ NOT NULL,
    params        JSONB       NOT NULL,
    diagnostics   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    usable        BOOLEAN     NOT NULL,
    reason        TEXT,

    CONSTRAINT fit_sees_only_the_past CHECK (data_through <= fitted_at),
    CONSTRAINT unusable_fits_say_why  CHECK (usable OR reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS model_versions_lookup_idx
    ON model_versions (agent_id, model, data_through DESC);

DROP TRIGGER IF EXISTS model_versions_immutable ON model_versions;
CREATE TRIGGER model_versions_immutable
    BEFORE UPDATE OR DELETE ON model_versions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

ALTER TABLE model_versions ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Verify — expect the table, its trigger, and RLS on.
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       (SELECT count(*) FROM pg_trigger t
         WHERE t.tgrelid = c.oid AND t.tgname = 'model_versions_immutable') AS immutable_trigger
  FROM pg_class c
 WHERE c.relname = 'model_versions';
