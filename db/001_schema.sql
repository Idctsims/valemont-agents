-- 001_schema.sql — the ledger
--
-- Design note: commitments are APPEND-ONLY and enforced by trigger, not by
-- convention. The whole value of this project is an honest track record, so
-- rewriting history is made structurally impossible rather than merely
-- discouraged. Resolutions are separate rows written after the fact.
--
-- SUPABASE: paste this whole file into the SQL Editor and Run. The editor
-- wraps execution in its own transaction, so there is no BEGIN/COMMIT here.
-- Do not create roles — Supabase manages those.

-- ---------------------------------------------------------------------------
-- Agent registry
-- ---------------------------------------------------------------------------

CREATE TABLE agents (
    id          SMALLSERIAL PRIMARY KEY,
    slug        TEXT        NOT NULL UNIQUE,      -- 'crypto', 'equities', ...
    display_name TEXT       NOT NULL,
    domain      TEXT        NOT NULL,             -- 'crypto' | 'equities' | 'props'
    enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Runs — one row per agent wake-up. Gives every commitment a traceable parent.
-- ---------------------------------------------------------------------------

CREATE TABLE runs (
    id          BIGSERIAL   PRIMARY KEY,
    agent_id    SMALLINT    NOT NULL REFERENCES agents(id),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    status      TEXT        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','ok','error')),
    error       TEXT,
    notes       TEXT
);

CREATE INDEX runs_agent_started_idx ON runs (agent_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- Commitments — THE core table. Written before the outcome is known.
-- Immutable once inserted.
-- ---------------------------------------------------------------------------

CREATE TABLE commitments (
    id            BIGSERIAL   PRIMARY KEY,
    agent_id      SMALLINT    NOT NULL REFERENCES agents(id),
    run_id        BIGINT      NOT NULL REFERENCES runs(id),

    kind          TEXT        NOT NULL
                  CHECK (kind IN ('paper_position','prop_slip','event_contract')),

    -- The reasoning, in the agent's own words. Kept so we can audit WHY,
    -- not just whether it hit.
    thesis        TEXT        NOT NULL,
    confidence    NUMERIC(4,3)
                  CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),

    -- Domain-specific detail. Deliberately jsonb: a paper position, a
    -- six-leg slip, and an event contract do not share a column shape, and
    -- forcing them to would mean a schema migration every time an adapter
    -- learns something new. Three payload shapes, one per kind.
    payload       JSONB       NOT NULL,

    -- Set by the DATABASE, never by application code. No backdating.
    committed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- When reality is expected to land. Resolutions before this are rejected.
    resolves_after TIMESTAMPTZ NOT NULL,

    CONSTRAINT resolves_in_future CHECK (resolves_after > committed_at)
);

CREATE INDEX commitments_agent_idx      ON commitments (agent_id, committed_at DESC);
CREATE INDEX commitments_unresolved_idx ON commitments (resolves_after);
CREATE INDEX commitments_payload_idx    ON commitments USING GIN (payload);

-- ---------------------------------------------------------------------------
-- Legs — a paper position is one leg; a slip is 2-6. Same table, so scoring
-- and the dashboard don't need to special-case by domain.
-- ---------------------------------------------------------------------------

CREATE TABLE legs (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL REFERENCES commitments(id),
    leg_index     SMALLINT    NOT NULL,

    subject       TEXT        NOT NULL,   -- 'BTC-USD', 'NVDA', 'Ja Morant'
    market        TEXT        NOT NULL,   -- 'spot_long', 'points_over', ...
    line          NUMERIC,                -- entry price, or the prop line
    direction     TEXT        CHECK (direction IN ('over','under','long','short','yes','no')),
    size          NUMERIC,                -- units / notional / stake weight

    -- Outcome, filled in ONLY by the resolution path.
    actual        NUMERIC,
    outcome       TEXT        CHECK (outcome IN ('hit','miss','push','void')),

    UNIQUE (commitment_id, leg_index)
);

CREATE INDEX legs_commitment_idx ON legs (commitment_id);

-- ---------------------------------------------------------------------------
-- Resolutions — written after resolves_after. Never edits the commitment.
-- ---------------------------------------------------------------------------

CREATE TABLE resolutions (
    id            BIGSERIAL   PRIMARY KEY,
    commitment_id BIGINT      NOT NULL UNIQUE REFERENCES commitments(id),
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    outcome       TEXT        NOT NULL
                  CHECK (outcome IN ('hit','miss','partial','push','void')),
    -- Paper P&L or hypothetical slip return. Never real money.
    pnl           NUMERIC,
    detail        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- Events — append-only activity stream. The dashboard is a consumer of this.
-- Build the stream first, the pixel layer last.
-- ---------------------------------------------------------------------------

CREATE TABLE events (
    id          BIGSERIAL   PRIMARY KEY,
    agent_id    SMALLINT    REFERENCES agents(id),
    run_id      BIGINT      REFERENCES runs(id),
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT        NOT NULL,   -- 'woke','observing','committed','resolved','idle','error'
    message     TEXT,
    detail      JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX events_at_idx ON events (at DESC);

-- ---------------------------------------------------------------------------
-- Briefs — chief of staff output. It writes here and nowhere else.
-- ---------------------------------------------------------------------------

CREATE TABLE briefs (
    id          BIGSERIAL   PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,
    body        TEXT        NOT NULL,
    stats       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- Immutability enforcement
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Table % is append-only. Insert a new row instead of % (id=%).',
        TG_TABLE_NAME, TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER commitments_immutable
    BEFORE UPDATE OR DELETE ON commitments
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER events_immutable
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- Legs are writable exactly once, to record the actual outcome. The commit-time
-- fields stay frozen — you cannot move the line after the fact.
CREATE OR REPLACE FUNCTION legs_freeze_commit_fields() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.subject   IS DISTINCT FROM OLD.subject
    OR NEW.market    IS DISTINCT FROM OLD.market
    OR NEW.line      IS DISTINCT FROM OLD.line
    OR NEW.direction IS DISTINCT FROM OLD.direction
    OR NEW.size      IS DISTINCT FROM OLD.size THEN
        RAISE EXCEPTION 'Leg % commit-time fields are frozen.', OLD.id;
    END IF;
    IF OLD.outcome IS NOT NULL THEN
        RAISE EXCEPTION 'Leg % is already resolved.', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER legs_frozen
    BEFORE UPDATE ON legs
    FOR EACH ROW EXECUTE FUNCTION legs_freeze_commit_fields();

-- A resolution cannot land before the outcome was knowable.
CREATE OR REPLACE FUNCTION resolution_not_early() RETURNS TRIGGER AS $$
DECLARE ra TIMESTAMPTZ;
BEGIN
    SELECT resolves_after INTO ra FROM commitments WHERE id = NEW.commitment_id;
    IF NEW.resolved_at < ra THEN
        RAISE EXCEPTION
            'Cannot resolve commitment % before %.', NEW.commitment_id, ra;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER resolutions_timing
    BEFORE INSERT ON resolutions
    FOR EACH ROW EXECUTE FUNCTION resolution_not_early();

-- ---------------------------------------------------------------------------
-- Seed the roster
-- ---------------------------------------------------------------------------

INSERT INTO agents (slug, display_name, domain) VALUES
    ('crypto',     'Crypto Trader',   'crypto'),
    ('equities',   'Stock Trader',    'equities'),
    ('prizepicks', 'Props Analyst',   'props');

-- ---------------------------------------------------------------------------
-- Row Level Security — LOCK EVERYTHING DOWN
--
-- Supabase auto-exposes every public table over PostgREST. Without RLS, anyone
-- holding the anon key can read the entire ledger. We enable RLS and write NO
-- policies, which denies anon and authenticated by default. The Python worker
-- connects with the Postgres connection string (service role), which bypasses
-- RLS entirely — so the agents keep full access and the outside world gets
-- nothing.
--
-- The dashboard reads from the Python API, never from Supabase directly. If a
-- session proposes adding policies to let the frontend query Supabase, stop —
-- that contradicts CLAUDE.md section 5.
-- ---------------------------------------------------------------------------

ALTER TABLE agents      ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;
ALTER TABLE legs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE briefs      ENABLE ROW LEVEL SECURITY;
