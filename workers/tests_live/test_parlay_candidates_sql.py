"""Phase 4.2's candidate reader (ledger.PARLAY_CANDIDATES_SQL, PARLAY_CUT_SQL) against the real database.

One rolled-back transaction per test; TEST rows only (E2ETL series, is_test), built with the board's own
writers; the reader runs with tests=True and only this test's tickers are checked."""

from __future__ import annotations

import unittest
from datetime import timedelta

from consensus import parlay_legs as pl
from core import ledger
from tests_live import test_kalshi_board_sql as board
from tests_live.test_feed_rank_sql import _rolled_back


class Candidates(board.BoardSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure('public.leg_consensus_for(text[])')").fetchone()[0] is None:
                raise unittest.SkipTest("leg_consensus_for not present")

    def test_eligible_not_started_markets_come_back_with_their_flags(self) -> None:
        with _rolled_back() as conn:
            _, event, game, markets = self.game(conn, start=board.NOW + timedelta(hours=3), n_markets=2)
            _, started, _, _ = self.game(conn, start=board.NOW - timedelta(hours=1), n_markets=2)
            rows, _ = ledger.parlay_candidates(tests=True, conn=conn)
            mine = {r["market_ticker"]: r for r in rows if r["event_ticker"] in (event, started)}
            self.assertEqual(sorted(mine), sorted(markets))
            r = mine[markets[0]]
            self.assertEqual((r["game_ticker"], r["sport"], r["market_kind"], r["stat"], r["payout"], r["tier"]),
                             (game, "TL Sport", "prop", "points", "binary", "kalshi"))
            self.assertTrue(r["combo_eligible"])
            self.assertFalse(r["combo_yes_only"])
            self.assertIsNotNone(r["p_yes"])
            m = pl.markets_from_rows([r])[0]
            self.assertEqual(str(m.yes_ask), "0.4200")
            # The Kalshi title rides along (owner, Nov 20); the ticker stays the key.
            self.assertEqual((r["label"], m.label), ("TL Player: 10+", "TL Player: 10+"))

    def test_not_eligible_events_are_counted_by_sport_never_returned(self) -> None:
        with _rolled_back() as conn:
            before = ledger.parlay_candidates(tests=True, conn=conn)[1].get("TL Sport", 0)
            _, event, _, markets = self.game(conn, start=board.NOW + timedelta(hours=3), n_markets=3)
            conn.execute("UPDATE kalshi_board_events SET combo_eligible = false, combo_collections = '{}' "
                         "WHERE event_ticker = %s AND is_test", (event,))
            rows, cut = ledger.parlay_candidates(tests=True, conn=conn)
            self.assertFalse(any(r["event_ticker"] == event for r in rows))
            self.assertEqual(cut.get("TL Sport", 0) - before, 3)


class FreshTwin(unittest.TestCase):
    """parlay_legs.fresh_p is the Python twin of the engine's blend and
    normalization (owner, Nov 20). Fed the board's own stored quotes, it must
    give leg_consensus_for's p_consensus exactly, on real rows: games chosen to
    hold a mutually exclusive event and a books and an espn row. Read only;
    narrow, a few games."""

    def test_the_twin_equals_the_engine_on_the_real_board(self) -> None:
        with _rolled_back() as conn:
            games = [r[0] for r in conn.execute("""
                (SELECT e.game_ticker FROM kalshi_board_events e
                  WHERE NOT e.is_test AND e.combo_eligible AND e.starts_at > now() AND e.mutually_exclusive
                  ORDER BY e.game_ticker LIMIT 3)
                UNION
                (SELECT e.game_ticker FROM leg_consensus c JOIN kalshi_board b USING (market_ticker)
                   JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
                  WHERE NOT e.is_test AND e.combo_eligible AND e.starts_at > now() AND c.tier = 'espn'
                  ORDER BY e.game_ticker LIMIT 2)
                UNION
                (SELECT e.game_ticker FROM leg_consensus c JOIN kalshi_board b USING (market_ticker)
                   JOIN kalshi_board_events e ON e.event_ticker = b.event_ticker
                  WHERE NOT e.is_test AND e.combo_eligible AND e.starts_at > now() AND c.tier = 'books'
                  ORDER BY e.game_ticker LIMIT 2)""").fetchall()]
            if not games:
                self.skipTest("no eligible, not-started game on the board right now")
            rows, _ = ledger.parlay_candidates(games=games, conn=conn)
        markets = pl.markets_from_rows(rows)
        quotes = {m.market_ticker: pl.Quote(m.yes_bid, m.yes_ask, board.NOW) for m in markets}
        twin = pl.fresh_p(markets, quotes)
        diff = [(m.market_ticker, m.p_yes, twin[m.market_ticker]) for m in markets if twin[m.market_ticker] != m.p_yes]
        self.assertEqual(diff, [], f"{len(diff)} of {len(markets)} differ")
        self.assertGreater(len(markets), 0)
        tiers = {m.tier for m in markets}
        print(f"\n  FreshTwin: {len(markets)} markets in {len(games)} games, tiers {sorted(tiers)}, "
              f"exclusive events {len({m.event_ticker for m in markets if m.mutually_exclusive})}")


if __name__ == "__main__":
    unittest.main()
