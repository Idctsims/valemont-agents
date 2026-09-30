-- 011_kalshi_candle_archive.sql — our copy of Kalshi price history, pre-game only
--
-- Kalshi serves candlesticks for settled markets today, but its retention is
-- undocumented (docs/kalshi_benchmark.md §1). These tables are the archive we
-- control. Scope, per owner decision: **only rungs an agent evaluated**, plus
-- the game-winner markets (cheap: two per game). Unevaluated prop rungs are
-- fetched from Kalshi on demand; the ~40k-markets-a-season prop ladders would
-- otherwise not fit the free tier (docs/kalshi_nfl.md §10).
--
-- Both tables are append-only. Rows are written after the fact by the
-- collector, from data Kalshi timestamps itself (`end_period_ts`), so nothing
-- here is a capture time we manufactured.
--
-- The load-bearing rule is `kalshi_candles_pregame`: a candle ending after its
-- market's kickoff is REFUSED. In-play prices therefore cannot exist in the
-- archive, which makes "the close or anything after it leaked into a feature"
-- structurally impossible for anything reading from here, rather than merely
-- avoided (preregistration_nfl.md §4).
--
-- SUPABASE: paste the whole file into the SQL Editor and Run. No BEGIN/COMMIT.
-- Safe to re-run.

-- ---------------------------------------------------------------------------
-- Markets: one row per Kalshi ticker, written once.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kalshi_markets (
    id              BIGSERIAL   PRIMARY KEY,
    ticker          TEXT        NOT NULL UNIQUE,
    event_ticker    TEXT        NOT NULL,
    series_ticker   TEXT        NOT NULL,
    sport           TEXT        NOT NULL CHECK (sport ~ '^[a-z][a-z0-9_]*$'),
    game_id         TEXT,                       -- schedule source id, e.g. nflverse 2026_03_BAL_DAL
    kickoff         TIMESTAMPTZ NOT NULL,       -- the fence for kalshi_candles_pregame
    kickoff_source  TEXT        NOT NULL,       -- 'nflverse'
    floor_strike    NUMERIC,                    -- ladder rung; NULL for a winner market
    title           TEXT,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kalshi_markets_event_idx ON kalshi_markets (event_ticker);

-- ---------------------------------------------------------------------------
-- Candles: pre-game only.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kalshi_candles (
    id              BIGSERIAL   PRIMARY KEY,
    market_id       BIGINT      NOT NULL REFERENCES kalshi_markets(id),
    period_minutes  SMALLINT    NOT NULL CHECK (period_minutes IN (1, 60, 1440)),
    end_period_ts   TIMESTAMPTZ NOT NULL,
    yes_bid_open    NUMERIC, yes_bid_high NUMERIC, yes_bid_low NUMERIC, yes_bid_close NUMERIC,
    yes_ask_open    NUMERIC, yes_ask_high NUMERIC, yes_ask_low NUMERIC, yes_ask_close NUMERIC,
    trade_open      NUMERIC, trade_high  NUMERIC, trade_low  NUMERIC, trade_close  NUMERIC,
    volume          NUMERIC,
    open_interest   NUMERIC,
    source          TEXT        NOT NULL CHECK (source IN ('live', 'historical')),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (market_id, period_minutes, end_period_ts)
);

CREATE OR REPLACE FUNCTION kalshi_candle_pregame() RETURNS TRIGGER AS $$
DECLARE k TIMESTAMPTZ;
BEGIN
    SELECT kickoff INTO k FROM kalshi_markets WHERE id = NEW.market_id;
    IF NEW.end_period_ts > k THEN
        RAISE EXCEPTION
            'Candle ending % is after kickoff % for market % — in-play prices are never archived.',
            NEW.end_period_ts, k, NEW.market_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kalshi_candles_pregame ON kalshi_candles;
CREATE TRIGGER kalshi_candles_pregame
    BEFORE INSERT ON kalshi_candles
    FOR EACH ROW EXECUTE FUNCTION kalshi_candle_pregame();

DROP TRIGGER IF EXISTS kalshi_markets_immutable ON kalshi_markets;
CREATE TRIGGER kalshi_markets_immutable
    BEFORE UPDATE OR DELETE ON kalshi_markets
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

DROP TRIGGER IF EXISTS kalshi_candles_immutable ON kalshi_candles;
CREATE TRIGGER kalshi_candles_immutable
    BEFORE UPDATE OR DELETE ON kalshi_candles
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

ALTER TABLE kalshi_markets ENABLE ROW LEVEL SECURITY;
ALTER TABLE kalshi_candles ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Verify — expect both tables with RLS on, and three triggers.
-- ---------------------------------------------------------------------------

SELECT c.relname, c.relrowsecurity,
       array_agg(t.tgname ORDER BY t.tgname) AS triggers
  FROM pg_class c
  LEFT JOIN pg_trigger t ON t.tgrelid = c.oid AND NOT t.tgisinternal
 WHERE c.relname IN ('kalshi_markets', 'kalshi_candles')
 GROUP BY c.relname, c.relrowsecurity;
