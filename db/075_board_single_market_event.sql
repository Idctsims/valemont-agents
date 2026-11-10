-- 075_board_single_market_event.sql: a market may carry its event's own
-- ticker (owner, Nov 10; Chat 8 Phase 1 fix; docs/kalshi_board.md §3).
--
-- The 09:30 Chicago full pull of Nov 10 (15:30 UTC) failed on
-- KXPGAPLAYOFF-AUCPBCS26: a single-market event whose one market's ticker
-- IS the event ticker, with no "-<suffix>". The worker's parser now accepts
-- that (venues/kalshi/board.py parse_market), and db/071's
-- kalshi_board_under_event (market_ticker LIKE event_ticker || '-%') would
-- refuse the row at write time. This file widens that one CHECK:
--
--     market_ticker = event_ticker OR market_ticker LIKE event_ticker || '-%'
--
-- Strictly wider than db/071's, so every existing row passes the ADD's
-- validation. Nothing else changes: no function, no trigger, no grant (no
-- reader or writer splits a market ticker on its event's dash; checked
-- across db/ and workers/ before handover).
--
-- Paste-by-hand, top to bottom, no BEGIN/COMMIT (§5). The DO block at the
-- end asserts the new definition; a wrong result raises and the SQL Editor
-- rolls the whole file back.

ALTER TABLE kalshi_board DROP CONSTRAINT IF EXISTS kalshi_board_under_event;
ALTER TABLE kalshi_board ADD CONSTRAINT kalshi_board_under_event
    CHECK (market_ticker = event_ticker OR market_ticker LIKE event_ticker || '-%');

INSERT INTO migration_log (name) VALUES ('075_board_single_market_event')
ON CONFLICT (name) DO NOTHING;

DO $$
DECLARE
    def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO def
      FROM pg_constraint
     WHERE conrelid = 'public.kalshi_board'::regclass AND conname = 'kalshi_board_under_event';
    IF def IS NULL OR position('market_ticker = event_ticker' IN def) = 0 THEN
        RAISE EXCEPTION 'SELFTEST FAILED: kalshi_board_under_event is %', def;
    END IF;
    RAISE NOTICE '075 check: %', def;
END
$$;
