"""db/073 against the real database: the consensus engine.

Every test runs inside ONE transaction that is always rolled back and writes
only TEST rows (series tickers starting E2E, is_test) through the worker's own
statements (core/ledger.py), reading back only those rows. The ledger's read
texts are run so they return nothing (an until of now(), a game id of -1) or
a few aggregated rows. Skips when the database is unreachable or db/073 is
unpasted.
"""

from __future__ import annotations

import random
import re
import unittest
from datetime import timedelta
from decimal import Decimal

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import _rolled_back
from tests_live.test_kalshi_board_sql import BoardSqlCase, tag
from tests_live import test_kalshi_board_sql as board
from venues.kalshi.fees import FeeRegime


class ConsensusSqlCase(BoardSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.leg_consensus')").fetchone()[0] is None:
                raise unittest.SkipTest("db/073 not pasted")

    def two_team(self, conn: psycopg.Connection, bids: tuple[str, str], asks: tuple[str, str], *,
                 exclusive: bool | None = True) -> tuple[str, str, list[str]]:
        """A test game with one moneyline-style event of two markets."""
        series = f"E2ETL{tag()}"
        event, game = f"{series}-26NOV09AAABBB", f"{series}GAME-26NOV09AAABBB"
        ledger.kalshi_board_series_upsert([{
            "series_ticker": series, "title": "TL ML", "sport": "TL Sport", "league": None, "category": "Sports",
            "tags": [], "fee_type": "quadratic", "fee_multiplier": "1", "fee_regime": {"fee_type": "quadratic"},
            "combo_eligible": False, "collection_tickers": []}], is_test=True, conn=conn)
        ledger.kalshi_board_events_upsert([{
            "event_ticker": event, "series_ticker": series, "game_ticker": game, "game_title": "AAA vs BBB",
            "milestone_start": (board.NOW + timedelta(hours=3)).isoformat(), "mutually_exclusive": exclusive}],
            board.NOW, is_test=True, conn=conn)
        markets = [f"{event}-AAA", f"{event}-BBB"]
        ledger.kalshi_board_markets_upsert([
            self.market(event, markets[i], "game_line", i, yes_bid=bids[i], yes_ask=asks[i], line=None, subject=s,
                        label=s)
            for i, s in enumerate(("AAA", "BBB"))], board.NOW, is_test=True, conn=conn)
        return event, game, markets

    @staticmethod
    def rows(conn: psycopg.Connection, game: str) -> dict[str, dict]:
        cur = conn.execute("SELECT * FROM leg_consensus_for(%s)", ([game],))
        cols = [d.name for d in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


class FeeAndEdge(ConsensusSqlCase):
    def test_the_sql_fee_is_fees_py_on_every_cent(self) -> None:
        prices = [Decimal(i) / 100 for i in range(1, 100)]
        with _rolled_back() as conn:
            for mult in ("1", "0.5", "2"):
                got = conn.execute("SELECT p, kalshi_taker_fee(p, %s::numeric) FROM unnest(%s::numeric[]) p",
                                   (mult, prices)).fetchall()
                reg = FeeRegime(series="X", fee_type="quadratic", multiplier=Decimal(mult), effective_from=None,
                                fetched_at=board.NOW)
                for p, fee in got:
                    self.assertEqual(Decimal(fee), reg.taker_fee(Decimal(p)), f"{p} x {mult}")
            self.assertIsNone(conn.execute("SELECT kalshi_taker_fee(0, 1)").fetchone()[0])
            self.assertIsNone(conn.execute("SELECT kalshi_taker_fee(1, 1)").fetchone()[0])

    def test_edge_is_p_minus_ask_minus_fee_per_side_never_the_mid(self) -> None:
        rng = random.Random(73)
        cases = []
        for _ in range(60):
            bid = Decimal(rng.randint(1, 97)) / 100
            ask = bid + Decimal(rng.randint(1, 2)) / 100
            pk = (bid + ask) / 2
            out = None if rng.random() < 0.3 else Decimal(rng.randint(2, 98)) / 100
            cases.append((out, pk, bid, ask))
        reg = FeeRegime(series="X", fee_type="quadratic", multiplier=Decimal(1), effective_from=None,
                        fetched_at=board.NOW)
        with _rolled_back() as conn:
            for out, pk, bid, ask in cases:
                p, fy, fn, ey, en = conn.execute("SELECT * FROM consensus_leg(%s, %s, %s, %s, 1)",
                                                 (out, pk, bid, ask)).fetchone()
                want_p = round(pk if out is None else Decimal("0.7") * out + Decimal("0.3") * pk, 6)
                self.assertEqual(p, want_p)
                self.assertEqual(ey, p - (ask + reg.taker_fee(ask)))
                no_ask = 1 - bid
                self.assertEqual(en, (1 - p) - (no_ask + reg.taker_fee(no_ask)))
                # The fee is inside: an edge is always below p - ask.
                self.assertLess(ey, p - ask)
                if out is None:
                    self.assertLessEqual(ey, 0)  # against its own mid, after the spread and fee


class Normalization(ConsensusSqlCase):
    def test_two_teams_normalized_ladder_and_thin_events_plain(self) -> None:
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))      # 0.98
            r = self.rows(conn, game)
            self.assertTrue(r[mk[0]]["normalized"])
            self.assertEqual(r[mk[0]]["p_kalshi"], round(Decimal("0.62") / Decimal("0.98"), 6))
            self.assertEqual(r[mk[0]]["tier"], "kalshi")
            self.assertFalse(r[mk[0]]["edge_claimed"])
            _, game2, mk2 = self.two_team(conn, ("0.4500", "0.4500"), ("0.4700", "0.4700"))    # 0.92: under 0.95
            r2 = self.rows(conn, game2)
            self.assertFalse(r2[mk2[0]]["normalized"])
            self.assertEqual(r2[mk2[0]]["p_kalshi"], Decimal("0.46"))
            _, game3, mk3 = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"), exclusive=False)
            self.assertFalse(self.rows(conn, game3)[mk3[0]]["normalized"])
            _, game4, mk4 = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"), exclusive=None)
            self.assertFalse(self.rows(conn, game4)[mk4[0]]["normalized"])

    def test_a_band_edge_artifact_shows_its_edge_but_never_claims_it(self) -> None:
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.4700", "0.4700"), ("0.4800", "0.4800"))      # 0.95
            r = self.rows(conn, game)[mk[0]]
            self.assertTrue(r["normalized"])
            self.assertGreater(r["edge_yes"], 0)
            self.assertFalse(r["edge_claimed"])


class Writes(ConsensusSqlCase):
    def test_upsert_changed_only_tiers_claimed_and_the_sweep(self) -> None:
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))
            rows = [{"market_ticker": mk[0], "tier": "books", "n_books": 3, "p_books": 0.70, "p_books_mult": 0.69,
                     "p_books_lo": 0.69, "p_books_hi": 0.70,
                     "p_espn": None, "inputs": {"m": "power"}, "source_at": board.NOW.isoformat()},
                    {"market_ticker": mk[1], "tier": "espn", "n_books": 1, "p_books": None, "p_books_mult": None,
                     "p_espn": 0.30, "inputs": {"m": "power", "src": "espn"}, "source_at": board.NOW.isoformat()}]
            self.assertEqual(ledger.leg_consensus_upsert(rows, board.NOW, is_test=True, conn=conn), 2)
            self.assertEqual(ledger.leg_consensus_upsert(rows, board.NOW, is_test=True, conn=conn), 0)
            self.assertEqual(ledger.leg_consensus_upsert(rows, board.NOW, conn=conn), 0)  # real: not a test market
            r = self.rows(conn, game)
            self.assertEqual((r[mk[0]]["tier"], r[mk[0]]["n_books"]), ("books", 3))
            self.assertTrue(r[mk[0]]["edge_claimed"])  # 0.7 x 0.70 + 0.3 x 0.6327 = 0.678 > 0.63 + fee
            self.assertEqual(r[mk[1]]["tier"], "espn")
            # db/078: the espn tier shows its edge and never claims it.
            self.assertGreater(max(r[mk[1]]["edge_yes"], r[mk[1]]["edge_no"]), 0)
            self.assertFalse(r[mk[1]]["edge_claimed"])
            self.assertEqual(ledger.leg_consensus_stored([game], conn=conn), {})  # real rows only
            self.assertEqual(ledger.leg_consensus_sweep([game], [mk[0]], is_test=True, conn=conn), 1)
            self.assertEqual(self.rows(conn, game)[mk[1]]["tier"], "kalshi")
            # The board's sweep cascades.
            ledger.kalshi_board_sweep([mk[0].rsplit("-", 1)[0]], [mk[1]], is_test=True, conn=conn)
            n = conn.execute("SELECT count(*) FROM leg_consensus WHERE market_ticker = %s", (mk[0],)).fetchone()[0]
            self.assertEqual(n, 0)

    def test_a_books_edge_must_survive_the_least_favourable_of_four_methods(self) -> None:
        """db/078 (owner, 2026-11-13): claimed only when positive at p_books_lo
        (YES) too; a books row with no span (an older worker) claims nothing."""
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))
            base = {"market_ticker": mk[0], "tier": "books", "n_books": 3, "p_books": 0.70, "p_books_mult": 0.69,
                    "p_espn": None, "inputs": {"m": "power"}, "source_at": board.NOW.isoformat()}
            for span, want in (({"p_books_lo": 0.69, "p_books_hi": 0.70}, True),
                               ({"p_books_lo": 0.64, "p_books_hi": 0.70}, False),  # 0.7 x 0.64 + 0.3 x 0.6327 < cost
                               ({}, False)):
                self.assertEqual(ledger.leg_consensus_upsert([base | span], board.NOW, is_test=True, conn=conn), 1)
                r = self.rows(conn, game)[mk[0]]
                self.assertGreater(r["edge_yes"], 0, span)
                self.assertEqual(r["edge_claimed"], want, span)
            # The span sits around p_books, whole or absent.
            for bad in ({"p_books_lo": 0.75, "p_books_hi": 0.80}, {"p_books_lo": 0.69}):
                with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                    ledger.leg_consensus_upsert([base | bad], board.NOW, is_test=True, conn=conn)

    def test_the_game_reader_carries_q(self) -> None:
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))
            ledger.leg_consensus_upsert([{"market_ticker": mk[0], "tier": "books", "n_books": 2, "p_books": 0.70,
                                          "p_books_mult": 0.70, "p_books_lo": 0.70, "p_books_hi": 0.70, "p_espn": None, "inputs": {"m": "power"},
                                          "source_at": board.NOW.isoformat()}], board.NOW, is_test=True, conn=conn)
            ev = conn.execute("SELECT x FROM kalshi_board_game(%s, NULL, false, true) x", (game,)).fetchone()[0]
            qs = {m["lb"]: re.fullmatch(r"([bek])(\d*)([a-p])(\d{3})(-?\d+)", m["q"]) for m in ev["m"]}
            self.assertEqual((qs["AAA"][1], qs["AAA"][2]), ("b", "2"))
            self.assertEqual((ord(qs["AAA"][3]) - 97) & 1, 1)       # claimed: fresh both sides
            self.assertEqual(qs["BBB"][1], "k")
            self.assertEqual((ord(qs["BBB"][3]) - 97) & 7, 0)       # kalshi tier: never claimed, fresh
            # The packed payload stays small: one short string a market.
            self.assertLessEqual(max(len(m["q"]) for m in ev["m"]), 12)

    def test_a_stale_price_claims_nothing(self) -> None:
        """db/074 (owner, Nov 9): the Kalshi price within 20 min AND the outside
        within 12 h, or the edge is shown, tagged stale, and never claimed."""
        with _rolled_back() as conn:
            event, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))
            ledger.leg_consensus_upsert([{"market_ticker": mk[0], "tier": "books", "n_books": 2, "p_books": 0.70,
                                          "p_books_mult": 0.70, "p_books_lo": 0.70, "p_books_hi": 0.70, "p_espn": None, "inputs": {"m": "power"},
                                          "source_at": board.NOW.isoformat()}], board.NOW, is_test=True, conn=conn)
            self.assertTrue(self.rows(conn, game)[mk[0]]["edge_claimed"])
            conn.execute("UPDATE kalshi_board_events SET fetched_at = now() - interval '21 minutes' "
                         "WHERE event_ticker = %s", (event,))
            r = self.rows(conn, game)[mk[0]]
            self.assertEqual((r["edge_claimed"], r["stale_kalshi"], r["stale_outside"]), (False, True, False))
            self.assertGreater(r["edge_yes"], 0)
            conn.execute("UPDATE kalshi_board_events SET fetched_at = now() WHERE event_ticker = %s", (event,))
            conn.execute("UPDATE leg_consensus SET source_at = now() - interval '13 hours' WHERE market_ticker = %s",
                         (mk[0],))
            r = self.rows(conn, game)[mk[0]]
            self.assertEqual((r["edge_claimed"], r["stale_kalshi"], r["stale_outside"]), (False, False, True))
            # A confirmation by a newer pull makes it fresh again (source_at advances).
            self.assertEqual(ledger.leg_consensus_upsert([{"market_ticker": mk[0], "tier": "books", "n_books": 2,
                                                           "p_books": 0.70, "p_books_mult": 0.70, "p_books_lo": 0.70, "p_books_hi": 0.70, "p_espn": None,
                                                           "inputs": {"m": "power"},
                                                           "source_at": board.NOW.isoformat()}],
                                                         board.NOW, is_test=True, conn=conn), 1)
            self.assertTrue(self.rows(conn, game)[mk[0]]["edge_claimed"])


class BookLines(ConsensusSqlCase):
    def test_append_changed_only_then_collapse_to_the_last_pre_start_row(self) -> None:
        past = board.NOW - timedelta(hours=7)
        sid = -int(tag(), 16) % 10**9 - 1
        def row(at, line):
            return {"sports_event_id": sid, "starts_at": past.isoformat(), "fetched_at": at.isoformat(),
                    "source": "o", "league": "nba", "market": "total", "subject": None, "book": "draftkings",
                    "line": line, "price_a": -110, "price_b": -110, "p_a": 0.5}
        with _rolled_back() as conn:
            rows = [row(past - timedelta(hours=3), 221.5), row(past - timedelta(hours=2), 222.5),
                    row(past + timedelta(minutes=30), 225.5)]
            self.assertEqual(ledger.book_lines_append(rows, is_test=True, conn=conn), 3)
            self.assertEqual(ledger.book_lines_append(rows[-1:], is_test=True, conn=conn), 0)
            self.assertGreaterEqual(ledger.book_lines_prune(is_test=True, conn=conn), 2)
            left = conn.execute("SELECT line FROM book_lines WHERE sports_event_id = %s AND is_test", (sid,)).fetchall()
            self.assertEqual([float(x[0]) for x in left], [222.5])
            self.assertEqual(ledger.book_lines_prune(is_test=True, conn=conn), 0)


class Calls(ConsensusSqlCase):
    def test_a_test_call_is_logged_and_never_counted(self) -> None:
        with _rolled_back() as conn:
            before = ledger.odds_api_usage(conn=conn)
            ledger.odds_api_call_record({"endpoint": "/sports/:sport/odds", "sport": "basketball_nba",
                                         "event_id": None, "markets": "h2h", "regions": "us", "status": 200,
                                         "cost": 99, "used": 1, "remaining": 1, "bytes": 10}, is_test=True, conn=conn)
            after = ledger.odds_api_usage(conn=conn)
            self.assertEqual(after["spent"], before["spent"])
            self.assertEqual(after["remaining"], before["remaining"])
            self.assertEqual(after["allowance"], 500)
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    ledger.odds_api_call_record({"endpoint": "/sports?apiKey=x", "sport": None, "event_id": None,
                                                 "markets": None, "regions": None, "status": 200, "cost": 0,
                                                 "used": None, "remaining": None, "bytes": 0}, is_test=True,
                                                conn=conn)


class Readers(ConsensusSqlCase):
    def test_the_worker_s_read_texts_run(self) -> None:
        with _rolled_back() as conn:
            self.assertEqual(ledger.consensus_games(board.NOW, ["nba"], conn=conn), [])  # until now(): none
            self.assertEqual(ledger.consensus_markets([-1], ["KXNBAGAME"], conn=conn), [])
            names = ledger.consensus_team_names(["nfl"], conn=conn)
            self.assertIn("minnesota vikings", names.get("nfl", frozenset()))
            for sport, tier, n, claimed, pos, stale in ledger.consensus_tier_counts(conn=conn):
                self.assertIn(tier, ("books", "espn", "kalshi"))
                self.assertLessEqual(claimed + stale, n)
                if tier == "kalshi":
                    self.assertEqual((claimed, stale), (0, 0))
                if tier == "espn":
                    self.assertEqual(claimed, 0)  # db/078: one soft book never claims
                else:
                    self.assertEqual(pos, 0)
            used = ledger.odds_api_usage(conn=conn)
            self.assertIn("used", used)

    def test_ctx_consensus_keys_and_no_test_rows(self) -> None:
        with _rolled_back() as conn:
            _, game, mk = self.two_team(conn, ("0.6100", "0.3500"), ("0.6300", "0.3700"))
            ledger.leg_consensus_upsert([{"market_ticker": mk[0], "tier": "books", "n_books": 2, "p_books": 0.70,
                                          "p_books_mult": 0.70, "p_books_lo": 0.70, "p_books_hi": 0.70, "p_espn": None, "inputs": {"m": "power"},
                                          "source_at": board.NOW.isoformat()}], board.NOW, is_test=True, conn=conn)
            j = conn.execute("SELECT ctx_consensus()").fetchone()[0]
            self.assertEqual(set(j), {"about", "as_of", "counts", "best_edges_tonight", "credits"})
            self.assertNotIn("TL Sport", j["counts"])
            self.assertFalse(any(x.get("tier") == "kalshi" for x in j["best_edges_tonight"]))


if __name__ == "__main__":
    unittest.main()
