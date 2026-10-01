-- 014_kalshi_trade_settlement_archive.sql — trade prints and settlements for F1/F2
--
-- Extends the db/011 archive so the forward-only hypotheses F1 and F2
-- (preregistration_nfl.md §8) can be evaluated in January even if Kalshi no
-- longer serves 2026 history then. Written by `jobs/archive_nfl_props.py`,
-- weekly, store-only: that job never reads these tables back.
--
--   kalshi_trades       every print on an archived market in [kickoff − 3 h,
--                       kickoff), for the §7 trade-through maker-fill rule.
--                       A print at or after kickoff is REFUSED, as db/011
--                       refuses an in-play candle.
--   kalshi_settlements  one row per settled market: result, value, time.
--                       Separate from kalshi_markets because a market is
--                       archived before it settles and kalshi_markets is
--                       immutable.
--
-- Both are append-only (reject_mutation, db/001) with RLS on and no policies.
--
-- Prerequisites: db/011.
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS kalshi_trades (
    id              BIGSERIAL   PRIMARY KEY,
    market_id       BIGINT      NOT NULL REFERENCES kalshi_markets(id),
    trade_id        TEXT        NOT NULL UNIQUE,     -- Kalshi's own id
    created_time    TIMESTAMPTZ NOT NULL,            -- Kalshi's own timestamp
    yes_price       NUMERIC     NOT NULL CHECK (yes_price >= 0 AND yes_price <= 1),
    count           NUMERIC     NOT NULL CHECK (count > 0),
    taker_side      TEXT        CHECK (taker_side IN ('yes', 'no')),
    source          TEXT        NOT NULL CHECK (source IN ('live', 'historical')),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kalshi_trades_market_idx ON kalshi_trades (market_id, created_time);

CREATE OR REPLACE FUNCTION kalshi_trade_pregame() RETURNS TRIGGER AS $$
DECLARE k TIMESTAMPTZ;
BEGIN
    SELECT kickoff INTO k FROM kalshi_markets WHERE id = NEW.market_id;
    IF NEW.created_time >= k THEN
        RAISE EXCEPTION
            'Trade at % is at or after kickoff % for market % — in-play prints are never archived.',
            NEW.created_time, k, NEW.market_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kalshi_trades_pregame ON kalshi_trades;
CREATE TRIGGER kalshi_trades_pregame
    BEFORE INSERT ON kalshi_trades
    FOR EACH ROW EXECUTE FUNCTION kalshi_trade_pregame();

DROP TRIGGER IF EXISTS kalshi_trades_immutable ON kalshi_trades;
CREATE TRIGGER kalshi_trades_immutable
    BEFORE UPDATE OR DELETE ON kalshi_trades
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TABLE IF NOT EXISTS kalshi_settlements (
    id                BIGSERIAL   PRIMARY KEY,
    market_id         BIGINT      NOT NULL UNIQUE REFERENCES kalshi_markets(id),
    result            TEXT        NOT NULL,
    settlement_value  NUMERIC     NOT NULL CHECK (settlement_value >= 0 AND settlement_value <= 1),
    settlement_ts     TIMESTAMPTZ NOT NULL,
    captured_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS kalshi_settlements_immutable ON kalshi_settlements;
CREATE TRIGGER kalshi_settlements_immutable
    BEFORE UPDATE OR DELETE ON kalshi_settlements
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

ALTER TABLE kalshi_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE kalshi_settlements ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Verify — expect both tables with RLS on; trades with two triggers,
-- settlements with one.
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       array_agg(t.tgname ORDER BY t.tgname) AS triggers
  FROM pg_class c
  LEFT JOIN pg_trigger t ON t.tgrelid = c.oid AND NOT t.tgisinternal
 WHERE c.relname IN ('kalshi_trades', 'kalshi_settlements')
 GROUP BY c.relname, c.relrowsecurity;
