"""db/094 against the real database: "bet $X to win $Y" (E1).

Every test runs inside ONE transaction that is always rolled back (the
production job_queue poller never sees its rows), writes only TEST rows (E2ETL
series, is_test) through the worker's own statements, and reads back only
those rows by id. The job itself runs (consensus/payout_job.py) with the
transaction's connection, so it reads the test board only. Skips when the
database is unreachable or db/094 is unpasted.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

import psycopg

from consensus import correlation as cr
from consensus import payout, payout_job
from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back
from tests_live.test_kalshi_board_sql import tag

#: One moneyline per test game (independent across games), at a spread of prices.
ASKS = ("0.30", "0.42", "0.48", "0.55", "0.61", "0.67", "0.73", "0.80", "0.86", "0.45")


class PayoutSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.payout_requests')").fetchone()[0] is None:
                raise unittest.SkipTest("db/094 not pasted")

    def board(self, conn: psycopg.Connection) -> list[str]:
        now = conn.execute("SELECT now()").fetchone()[0]
        p = f"E2ETL{tag()}"
        ledger.kalshi_board_series_upsert([
            {"series_ticker": f"{p}GAME", "title": "TL game", "sport": "Basketball", "league": "nba",
             "category": "Sports", "tags": ["Basketball"], "fee_type": "quadratic", "fee_multiplier": "1",
             "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": True, "collection_tickers": ["TLCOLL-R"]}],
            is_test=True, conn=conn)
        start = (now + timedelta(hours=3)).isoformat()
        games = [f"{p}GAME-26NOV25G{i:02d}" for i in range(len(ASKS))]
        ledger.kalshi_board_events_upsert([
            {"event_ticker": g, "series_ticker": f"{p}GAME", "game_ticker": g, "game_title": f"TL away at TL {i}",
             "milestone_start": start, "close_time": None, "expected_expiration_time": None,
             "combo_eligible": True, "combo_yes_only": False, "combo_collections": ["TLCOLL-R"],
             "mutually_exclusive": False} for i, g in enumerate(games)], now, is_test=True, conn=conn)
        ledger.kalshi_board_markets_upsert([
            {"market_ticker": f"{g}-HOM", "event_ticker": g, "market_kind": "game_line", "variant": None,
             "payout": "binary", "subject": f"TL {i}", "stat": "winner", "line": None, "label": f"TL {i}",
             "yes_bid": str(Decimal(a) - Decimal("0.02")), "yes_ask": a, "volume": "100", "open_interest": "50",
             "taker_fee_at_ask": "0.0170", "team": f"T{i:02d}"} for i, (g, a) in enumerate(zip(games, ASKS))],
            now, is_test=True, conn=conn)
        return games

    def request(self, conn: psycopg.Connection, stake: str, back: str) -> int:
        conn.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(self.owner),))
        return int(conn.execute("INSERT INTO payout_requests (stake, payout, is_test) VALUES (%s, %s, true) RETURNING id",
                                (stake, back)).fetchone()[0])


class Build(PayoutSqlCase):
    def test_the_job_answers_with_tickets_that_pay_at_least_the_target(self) -> None:
        with _rolled_back() as conn:
            games = self.board(conn)
            rid = self.request(conn, "10", "100")
            jobs = conn.execute("SELECT count(*) FROM job_queue WHERE kind = 'build_payout' "
                                "AND (payload ->> 'request_id')::bigint = %s", (rid,)).fetchone()[0]
            self.assertEqual(jobs, 1)
            run = payout_job.build_job({"request_id": rid}, table=cr.load_table(), conn=conn)
            self.assertEqual(run.outcome, "answered", run.reason)
            status, tickets, nearest, test = conn.execute(
                "SELECT r.status, a.tickets, a.nearest, a.is_test FROM payout_requests r "
                "JOIN payout_answers a ON a.request_id = r.id WHERE r.id = %s", (rid,)).fetchone()
            self.assertEqual(status, "answered")
            self.assertTrue(test)
            self.assertFalse(nearest)
            self.assertTrue(1 <= len(tickets) <= 3)
            for t in tickets:
                self.assertGreaterEqual(t["contracts"], 100)
                self.assertLessEqual(t["contracts"], 111)
                self.assertTrue(all(leg["m"].rsplit("-", 1)[0] in games for leg in t["legs"]))
            # A second run finds the request settled and does nothing.
            again = payout_job.build_job({"request_id": rid}, table=cr.load_table(), conn=conn)
            self.assertEqual(again.outcome, "skipped")

    def test_an_unreachable_target_answers_the_nearest(self) -> None:
        with _rolled_back() as conn:
            self.board(conn)
            rid = self.request(conn, "1", "5000")
            run = payout_job.build_job({"request_id": rid}, table=cr.load_table(), conn=conn)
            self.assertEqual(run.outcome, "answered", run.reason)
            nearest, tickets = conn.execute("SELECT nearest, tickets FROM payout_answers WHERE request_id = %s",
                                            (rid,)).fetchone()
            # Ten test games, one leg each: 5,000x is out of reach, so the nearest it can build.
            self.assertTrue(nearest)
            self.assertTrue(tickets)
            for t in tickets:
                self.assertLess(t["contracts"], 5000)
                games = [leg["m"].rsplit("-", 1)[0] for leg in t["legs"]]
                self.assertEqual(len(games), len(set(games)))


if __name__ == "__main__":
    unittest.main()
