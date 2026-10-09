-- 024_no_untried_abandon.sql — never abandon a commitment nobody tried.
--
-- PR #10 made this a rule in core: no void resolution and no 'missed' close
-- snapshot for a commitment with zero recorded attempts (core/agent.py,
-- _require_attempted). This makes it a DATABASE invariant, so no path can
-- skip it: not core, not a script, not a test harness, not a future adapter.
--
-- Both writes are permanent (resolutions.commitment_id and
-- closing_snapshots.commitment_id are UNIQUE), so giving up on a commitment
-- the system never once asked about is data loss from bookkeeping.
--
-- Where attempts live: ONE table, resolution_attempts, split by purpose
-- (db/004, indexed on (commitment_id, purpose)):
--   purpose = 'resolve'   the resolution sweep asked resolve()
--   purpose = 'capture'   the close-capture sweep asked capture_close()
--
-- The rules:
--   resolutions        outcome = 'void'      needs a 'resolve' attempt
--   closing_snapshots  status  = 'missed'    needs a 'capture' attempt
--
-- Every void, whoever writes it: today only core's abandonment writes one
-- (no adapter returns a void). No exemption for is_test: the test harness
-- records an attempt first, as the hand cleanup on 2026-10-09 did.
--
-- New writes only. Rows already in these tables are untouched: a trigger on
-- INSERT does not look back (the earlier harness seals stay as they were).
--
-- Prerequisites: db/003, db/004 (resolution_attempts.purpose), db/015.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.

CREATE OR REPLACE FUNCTION public.refuse_untried_abandon()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_purpose text;
BEGIN
    IF TG_TABLE_NAME = 'resolutions' THEN
        IF NEW.outcome IS DISTINCT FROM 'void' THEN
            RETURN NEW;
        END IF;
        v_purpose := 'resolve';
    ELSE  -- closing_snapshots
        IF NEW.status IS DISTINCT FROM 'missed' THEN
            RETURN NEW;
        END IF;
        v_purpose := 'capture';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.resolution_attempts ra
         WHERE ra.commitment_id = NEW.commitment_id
           AND ra.purpose = v_purpose
    ) THEN
        RAISE EXCEPTION
            'commitment % has no % attempt on record: refusing to % it untried (db/024)',
            NEW.commitment_id, v_purpose,
            CASE v_purpose WHEN 'resolve' THEN 'void' ELSE 'tombstone the close of' END;
        -- A plain RAISE (SQLSTATE P0001), not check_violation: a missing
        -- attempt must not be mistaken for a CHECK failing on the row.
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.refuse_untried_abandon() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS resolutions_no_untried_void ON resolutions;
CREATE TRIGGER resolutions_no_untried_void
    BEFORE INSERT ON resolutions
    FOR EACH ROW EXECUTE FUNCTION public.refuse_untried_abandon();

DROP TRIGGER IF EXISTS closing_snapshots_no_untried_missed ON closing_snapshots;
CREATE TRIGGER closing_snapshots_no_untried_missed
    BEFORE INSERT ON closing_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.refuse_untried_abandon();

INSERT INTO migration_log (name) VALUES ('024_no_untried_abandon')
ON CONFLICT (name) DO NOTHING;

-- Verify — expect two triggers, both BEFORE INSERT, and the log row.
SELECT tgrelid::regclass AS on_table, tgname
  FROM pg_trigger
 WHERE tgname IN ('resolutions_no_untried_void', 'closing_snapshots_no_untried_missed')
 ORDER BY 1;
SELECT name, applied_at FROM migration_log WHERE name = '024_no_untried_abandon';
