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


if __name__ == "__main__":
    unittest.main()
