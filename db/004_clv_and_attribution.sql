-- 004_clv_and_attribution.sql — closing line value, factor attribution, selections
--
-- The ledger has only ever known two moments: commit and resolve. CLV needs a
-- third — commit -> CLOSE -> resolve — because the close is when the market's
-- final opinion is fixed, and comparing our price to it produces a measurement
-- on EVERY commitment rather than only on the ones that resolve favorably.
-- Hit rate needs hundreds of resolutions to say anything. CLV says something
-- on the first one.
--
-- Five changes, and the ordering matters because later ones reference earlier:
--
--   1. commitments.closes_at      when the market's opinion becomes final
--   2. closing_snapshots          the third observation, one per commitment
--   3. commitment_factors         named, signed thesis adjustments
--   4. selections                 the operator's pick, recorded before the close
--   5. resolution_attempts.purpose  the bounded-retry counter, now shared
--
-- Everything here is append-only under the same reject_mutation() trigger as
-- the rest of the ledger. A snapshot you can edit after the fact is worth
-- nothing: the whole point is that it was taken when the outcome was unknown.
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. The editor
-- supplies its own transaction, so there is no BEGIN/COMMIT here. Safe to
-- re-run: every statement is IF NOT EXISTS or guarded.

-- ---------------------------------------------------------------------------
-- 1. commitments.closes_at
--
-- On `commitments` and not elsewhere, because the close time is part of the
-- claim. "This closes at kickoff" says which market you are pricing against,
-- and a close time that could move afterwards would let a thesis shop for a
-- flattering comparison point. Immutable at commit time, same as everything
-- else on this table.
--
-- READ IT AS "the earliest moment worth looking", not "look at exactly this".
-- Games get postponed. Rather than revising the column — which would be both
-- an UPDATE against an immutable table and a gaming vector — the capture job
-- keeps asking, and the adapter reports the market as still open until it
-- genuinely closes. Postponement is handled by the same bounded-retry
-- discipline that already handles a postponed resolution.
--
-- NULL means this domain has no meaningful close. Crypto trades 24/7; there is
-- no moment its market opinion is final, so crypto commitments leave it NULL
-- and no snapshot is ever expected.
-- ---------------------------------------------------------------------------

ALTER TABLE commitments
    ADD COLUMN IF NOT EXISTS closes_at TIMESTAMPTZ;

DO $$
BEGIN
    -- A close before the commit would mean pricing against a moment that had
    -- already passed; a close after resolution would mean the market's final
    -- opinion arrived after reality did. Both are nonsense.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'closes_between_commit_and_resolve'
    ) THEN
        ALTER TABLE commitments ADD CONSTRAINT closes_between_commit_and_resolve
            CHECK (
                closes_at IS NULL
                OR (closes_at > committed_at AND closes_at <= resolves_after)
            );
    END IF;
END $$;

COMMENT ON COLUMN commitments.closes_at IS
    'Earliest moment worth capturing the closing price. NULL when the domain '
    'has no meaningful close (crypto). Immutable: the close time is part of '
    'the claim, not a parameter to be revised afterwards.';

-- The capture sweep scans this. Partial index: most rows are NULL.
CREATE INDEX IF NOT EXISTS commitments_closes_at_idx
    ON commitments (closes_at) WHERE closes_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. closing_snapshots — the third observation
--
-- One row per commitment, ever (UNIQUE). A `missed` row is written when the
-- close could not be captured, so that a permanently uncapturable commitment
-- stops being retried forever and the loss is RECORDED rather than merely
-- absent. Absent and unrecoverable look identical otherwise, and the close
-- happens exactly once.
--
-- CLV, for a probability-priced market. We hold a side at price `entry_price`;
-- that same side closes at `close_price`. Both are the price OF THE SIDE WE
-- HOLD, which is what makes the sign convention work without a branch:
--
--     clv = close_price - entry_price
--
--     YES bought at 0.34, YES closes 0.40  ->  +0.06   market came to us
--     NO  bought at 0.66, NO  closes 0.60  ->  -0.06   market went against us
--
-- A NO position is stored with entry_price = 1 - yes_price, so the adapter
-- normalizes once and the arithmetic here never has to know which side it is.
-- Positive is always "the market moved toward our view".
--
-- STORED, not derived on read. A formula that lives in a query can be changed,
-- and changing it silently rewrites every historical measurement. Freezing the
-- number at capture time is the same principle as freezing the commitment.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS closing_snapshots (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL UNIQUE REFERENCES commitments(id),
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    status        TEXT        NOT NULL DEFAULT 'captured'
                  CHECK (status IN ('captured','missed')),

    -- Price of the side we hold, at commit and at close. Probability units
    -- (0..1) for a probability-priced market. NULL on a 'missed' row.
    entry_price   NUMERIC,
    close_price   NUMERIC,

    -- close_price - entry_price. Signed; positive means the market moved
    -- toward us. Computed by core and written here, never recomputed on read.
    clv           NUMERIC,
    -- clv / entry_price. Relative version, for comparing a 0.06 move on a
    -- 0.10 contract against the same move on a 0.80 one.
    clv_pct       NUMERIC,

    -- Why a 'missed' row exists. Required for missed, pointless for captured.
    reason        TEXT,
    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT captured_rows_have_prices CHECK (
        status <> 'captured'
        OR (entry_price IS NOT NULL AND close_price IS NOT NULL AND clv IS NOT NULL)
    ),
    CONSTRAINT missed_rows_have_a_reason CHECK (
        status <> 'missed' OR reason IS NOT NULL
    )
);

COMMENT ON TABLE closing_snapshots IS
    'The third observation: the market price when its opinion became final. '
    'One per commitment, append-only. A missed row records permanent data '
    'loss explicitly rather than leaving it indistinguishable from absence.';

CREATE TRIGGER closing_snapshots_immutable
    BEFORE UPDATE OR DELETE ON closing_snapshots
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- A snapshot cannot predate the close it claims to be of, and cannot exist at
-- all for a commitment that declared no close. Mirrors resolutions_timing.
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
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER closing_snapshots_timing
    BEFORE INSERT ON closing_snapshots
    FOR EACH ROW EXECUTE FUNCTION snapshot_timing();

-- ---------------------------------------------------------------------------
-- 3. commitment_factors — named, signed thesis adjustments
--
-- The question this exists to answer: "when the injury factor fired, did those
-- commitments beat the close?" A relational row makes that a join. A JSONB
-- array would make it an unnest, and would not index on the factor name.
--
--     SELECT f.name, count(*), avg(s.clv)
--       FROM commitment_factors f
--       JOIN closing_snapshots s ON s.commitment_id = f.commitment_id
--      WHERE s.status = 'captured'
--      GROUP BY f.name ORDER BY avg(s.clv);
--
-- That query is how a factor earns its keep or gets cut, and it is the reason
-- the name is constrained rather than free text. 'injury', 'injuries' and
-- 'Injury' would silently become three factors and every average would be
-- computed over a third of the evidence. snake_case, enforced here.
--
-- leg_index NULL means the factor applies to the whole commitment; otherwise
-- to that one leg. A six-leg slip where one player is hurt needs the
-- distinction, and it costs nothing to carry now.
--
-- Immutable, because a factor is part of the thesis. Adjusting an attribution
-- after seeing the result is the exact failure §2 exists to prevent.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS commitment_factors (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL REFERENCES commitments(id),

    -- NULL = applies to the whole commitment.
    leg_index     SMALLINT,

    name          TEXT        NOT NULL
                  CHECK (name ~ '^[a-z][a-z0-9_]*$'),

    -- Signed adjustment the factor contributed to the thesis price.
    -- Negative lowers our estimate, positive raises it. Same units as the
    -- market price, so a probability market's factors are in probability
    -- points and sum meaningfully against clv.
    value         NUMERIC     NOT NULL,

    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- NULLS NOT DISTINCT so two commitment-level rows cannot share a name
    -- (default NULL handling would treat them as distinct and allow it).
    UNIQUE NULLS NOT DISTINCT (commitment_id, leg_index, name)
);

COMMENT ON TABLE commitment_factors IS
    'Named signed adjustments making up a thesis. Queried against '
    'closing_snapshots to find which factors actually earn their keep. '
    'Names are snake_case by CHECK so attribution cannot fragment.';

CREATE INDEX IF NOT EXISTS commitment_factors_name_idx
    ON commitment_factors (name);
CREATE INDEX IF NOT EXISTS commitment_factors_commitment_idx
    ON commitment_factors (commitment_id);

CREATE TRIGGER commitment_factors_immutable
    BEFORE UPDATE OR DELETE ON commitment_factors
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- ---------------------------------------------------------------------------
-- 4. selections — the operator's pick
--
-- NOT a boolean on commitments. The agent commits the slate at T and the
-- operator picks at T+30min, so a column would require an UPDATE against an
-- immutable table. More importantly, the pick is itself a commitment and needs
-- the same discipline: recorded before the outcome, never revised after.
--
-- Hence the timing trigger. A selection made after the close is not judgment,
-- it is hindsight, and it would silently inflate any measurement of whether
-- the operator adds value over the model. This is resolutions_timing inverted:
-- a resolution cannot be too EARLY, a selection cannot be too LATE.
--
-- `selected` is explicit rather than presence-means-yes, because declining is
-- a decision. "I looked and passed" and "I never looked" must not collapse
-- into the same absent row.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS selections (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL UNIQUE REFERENCES commitments(id),
    selected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    selected      BOOLEAN     NOT NULL,
    note          TEXT
);

COMMENT ON TABLE selections IS
    'The operator''s pick on a commitment, recorded before the close. Exists '
    'to measure whether human judgment adds to the model or subtracts from '
    'it. Append-only, one per commitment, and rejected if made too late.';

CREATE TRIGGER selections_immutable
    BEFORE UPDATE OR DELETE ON selections
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE OR REPLACE FUNCTION selection_before_close() RETURNS TRIGGER AS $$
DECLARE ca TIMESTAMPTZ; ra TIMESTAMPTZ;
BEGIN
    SELECT closes_at, resolves_after INTO ca, ra
      FROM commitments WHERE id = NEW.commitment_id;
    -- Fall back to resolves_after when the domain has no close, so a domain
    -- without a market close still cannot be picked in hindsight.
    IF NEW.selected_at > COALESCE(ca, ra) THEN
        RAISE EXCEPTION
            'Selection on commitment % was made at %, after its deadline of % '
            '— a pick recorded after the fact is hindsight, not judgement.',
            NEW.commitment_id, NEW.selected_at, COALESCE(ca, ra);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER selections_timing
    BEFORE INSERT ON selections
    FOR EACH ROW EXECUTE FUNCTION selection_before_close();

-- ---------------------------------------------------------------------------
-- 5. resolution_attempts.purpose
--
-- Capture needs exactly the bounded-retry counter resolution already has:
-- count failures, give up after a per-agent cap, fail toward retry rather than
-- abandonment. Rather than duplicate the table, the existing one gains a
-- discriminator. One mechanism, one policy type, one index.
--
-- The table name now reads narrower than its contents. Left alone deliberately
-- — renaming it would break the existing trigger and index names in a
-- paste-by-hand workflow for no behavioural gain.
-- ---------------------------------------------------------------------------

ALTER TABLE resolution_attempts
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'resolve';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'resolution_attempts_purpose_check'
    ) THEN
        ALTER TABLE resolution_attempts ADD CONSTRAINT resolution_attempts_purpose_check
            CHECK (purpose IN ('resolve','capture'));
    END IF;
END $$;

COMMENT ON COLUMN resolution_attempts.purpose IS
    'Which sweep the attempt belongs to. Capture and resolution share this '
    'counter because they share the bounded-retry discipline exactly.';

-- The counters are read per commitment AND per purpose now.
DROP INDEX IF EXISTS resolution_attempts_commitment_idx;
CREATE INDEX IF NOT EXISTS resolution_attempts_commitment_purpose_idx
    ON resolution_attempts (commitment_id, purpose);

-- ---------------------------------------------------------------------------
-- RLS — same as everything else: enabled, no policies, worker bypasses.
-- ---------------------------------------------------------------------------

ALTER TABLE closing_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE commitment_factors  ENABLE ROW LEVEL SECURITY;
ALTER TABLE selections          ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Verify — expect 6 rows: three tables x (immutable + timing), except
-- commitment_factors which has only the immutability trigger.
-- ---------------------------------------------------------------------------

SELECT c.relname AS table_name, t.tgname AS trigger_name
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
 WHERE NOT t.tgisinternal
   AND c.relname IN ('closing_snapshots','commitment_factors','selections')
 ORDER BY 1, 2;
