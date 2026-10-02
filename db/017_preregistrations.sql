-- 017_preregistrations.sql — database-stamped pre-registrations
--
-- One row per frozen text: document path, section selector, the section's
-- SHA-256 (core/preregistration.py: CRLF→LF, trailing whitespace stripped)
-- and `registered_at`, set by the database. Append-only. Every holdout or
-- evaluation runner refuses to read data or write output unless the section,
-- hashed as it stands, matches a row stamped before the database's now().
--
-- Registered by this file, so their stamp is the moment of the paste:
--
--   F1, F2   docs/preregistration_nfl.md §8 (as amended by F1a). Their
--            evaluation has not run; it is due on or after 2027-01-20.
--   CFB      docs/preregistration_cfb_totals.md, everything before §9. That
--            hash equals the hash of the whole file as committed in 7eb5330,
--            before the holdout ran. **This stamp postdates that run**
--            (2026-10-01 23:36 UTC): for CFB, the pre-run evidence is the git
--            commit, and this row only fixes the text against later edits.
--
-- Not registered: the nfl_ml (§5) and props (§7) pre-registrations. They ran
-- before stamping existed, and a stamp written now would claim nothing.
--
-- Prerequisites: db/015 (migration_log).
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS preregistrations (
    id              BIGSERIAL   PRIMARY KEY,
    document        TEXT        NOT NULL,
    section         TEXT        NOT NULL,
    content_sha256  TEXT        NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note            TEXT,
    UNIQUE (document, section, content_sha256)
);
ALTER TABLE preregistrations ENABLE ROW LEVEL SECURITY;

DROP TRIGGER IF EXISTS preregistrations_immutable ON preregistrations;
CREATE TRIGGER preregistrations_immutable
    BEFORE UPDATE OR DELETE ON preregistrations
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

INSERT INTO preregistrations (document, section, content_sha256, note) VALUES
  ('docs/preregistration_nfl.md',
   '## 8. Forward-only hypotheses F1, F2 — committed 2026-10-01, before any analysis',
   '823934670559fa1d60286c79f85dec90ed40333ec91aa27d83afad5ea9d1ea58',
   'F1 (as amended F1a) and F2; evaluation not before 2027-01-20'),
  ('docs/preregistration_cfb_totals.md',
   'BEFORE ## 9. Execution log',
   '8fff700a31a33b177384e2c55caf23f27d809c3382d356742bdab2dfecaecc93',
   'CFB totals v1; equals 7eb5330 pre-run text; STAMPED AFTER the 2026-10-01 23:36 UTC run')
ON CONFLICT (document, section, content_sha256) DO NOTHING;

INSERT INTO migration_log (name) VALUES ('017_preregistrations')
ON CONFLICT (name) DO NOTHING;

SELECT document, section, left(content_sha256, 12) AS sha, registered_at FROM preregistrations ORDER BY id;
