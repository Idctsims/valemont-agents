"""db/076 and db/077 against the real database: combo pricing.

Every test runs inside ONE transaction that is always rolled back (the
production job_queue poller never sees its rows), writes only TEST rows (an
E2ETL series, is_test) through the worker's own statements, and reads back
only those rows by id or ticker. The job itself runs (consensus/combo_job.py)
with the transaction's connection and a test rho table whose NFL prefix is the
test series'. Skips when the database is unreachable or db/076 is unpasted.
"""

from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

import psycopg

from consensus import combo_job, correlation as cr
from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _act_as, _rolled_back
from tests_live.test_kalshi_board_sql import tag


def test_table(prefix: str) -> cr.RhoTable:
    real = cr.load_table()
    data = copy.deepcopy(dict(real.data))
    data["prefixes"] = dict(data["prefixes"]) | {"nfl": prefix}
    return cr.RhoTable(data, real.sha256)


class ComboSqlCase(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.combo_requests')").fetchone()[0] is None:
                raise unittest.SkipTest("db/076 not pasted")

    def board(self, conn: psycopg.Connection) -> dict[str, str]:
        """An NFL-shaped test game: a moneyline event (AAA, BBB) and a pass
        yards prop for AAA's QB. Returns the tickers."""
        now = conn.execute("SELECT now()").fetchone()[0]
        p = f"E2ETL{tag()}"
        game = f"{p}GAME-26NOV15AAABBB"
        props = f"{p}PASSYDS-26NOV15AAABBB"
        ledger.kalshi_board_series_upsert([
            {"series_ticker": f"{p}{s}", "title": f"TL {s}", "sport": "Football", "league": "nfl",
             "category": "Sports", "tags": ["Football"], "fee_type": "quadratic", "fee_multiplier": "1",
             "fee_regime": {"fee_type": "quadratic"}, "combo_eligible": True, "collection_tickers": ["TLCOLL-R"]}
            for s in ("GAME", "PASSYDS")], is_test=True, conn=conn)
        start = (now + timedelta(hours=3)).isoformat()
        ledger.kalshi_board_events_upsert([
            {"event_ticker": game, "series_ticker": f"{p}GAME", "game_ticker": game, "game_title": "TL at TL",
             "milestone_start": start, "close_time": None, "expected_expiration_time": None,
             "combo_eligible": True, "combo_yes_only": True, "combo_collections": ["TLCOLL-R"],
             "combo_size_max": 1, "mutually_exclusive": True},
            {"event_ticker": props, "series_ticker": f"{p}PASSYDS", "game_ticker": game, "game_title": "TL at TL",
             "milestone_start": start, "close_time": None, "expected_expiration_time": None,
             "combo_eligible": True, "combo_yes_only": False, "combo_collections": ["TLCOLL-R"],
             "mutually_exclusive": False}], now, is_test=True, conn=conn)

        def m(t: str, ev: str, kind: str, subject: str, stat: str, line: str | None, bid: str, ask: str,
              team: str) -> dict:
            return {"market_ticker": t, "event_ticker": ev, "market_kind": kind, "variant": None, "payout": "binary",
                    "subject": subject, "stat": stat, "line": line, "label": f"{subject} {stat} {line or ''}".strip(),
                    "yes_bid": bid, "yes_ask": ask, "volume": "100", "open_interest": "50",
                    "taker_fee_at_ask": "0.0170", "team": team}
        t = {"aaa": f"{game}-AAA", "bbb": f"{game}-BBB", "qb": f"{props}-AAAQB-250"}
        ledger.kalshi_board_markets_upsert([
            m(t["aaa"], game, "game_line", "TL AAA", "winner", None, "0.6000", "0.6200", "AAA"),
            m(t["bbb"], game, "game_line", "TL BBB", "winner", None, "0.3800", "0.4000", "BBB"),
            m(t["qb"], props, "prop", "TL QB", "passing yards", "249.5", "0.5400", "0.5600", "AAA")],
            now, is_test=True, conn=conn)
        t["prefix"] = p
        return t

    def request(self, conn: psycopg.Connection, legs: list[tuple[str, str]]) -> int:
        conn.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(self.owner),))
        rid = conn.execute("INSERT INTO combo_requests (legs) VALUES (%s) RETURNING id",
                           (psycopg.types.json.Jsonb([{"market_ticker": x, "side": s} for x, s in legs]),)).fetchone()[0]
        return int(rid)


class Legs(ComboSqlCase):
    def test_the_legs_read_team_consensus_and_combo_terms(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            rows = combo_job.leg_rows(ledger.combo_legs([t["qb"], t["aaa"]], conn=conn))
            qb, aaa = rows[t["qb"]], rows[t["aaa"]]
            self.assertEqual((qb.team, qb.league, qb.market_kind, qb.line), ("AAA", "nfl", "prop", 249.5))
            self.assertEqual(qb.tier, "kalshi")
            self.assertAlmostEqual(qb.p_yes, 0.55, places=6)  # the mid, no outside line
            self.assertEqual((aaa.mutually_exclusive, aaa.combo_yes_only, aaa.combo_size_max), (True, True, 1))
            self.assertEqual(aaa.game_ticker, qb.game_ticker)
            self.assertIsNotNone(aaa.fee_yes)


class Pricing(ComboSqlCase):
    def test_a_request_queues_one_job_and_is_answered_with_its_quote(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            rid = self.request(conn, [(t["qb"], "yes"), (t["aaa"], "yes")])
            jobs = conn.execute("SELECT count(*) FROM job_queue WHERE kind = 'price_combo' AND payload = %s",
                                (psycopg.types.json.Jsonb({"request_id": rid}),)).fetchone()[0]
            self.assertEqual(jobs, 1)
            table = test_table(t["prefix"])
            run = combo_job.price_job({"request_id": rid}, table=table, conn=conn)
            self.assertEqual(run.outcome, "answered", run.reason)
            st, ans = conn.execute("SELECT status, answered_at FROM combo_requests WHERE id = %s", (rid,)).fetchone()
            self.assertEqual(st, "answered")
            self.assertIsNotNone(ans)
            naive, fair, se, status, reason, rho, prior, blocks, is_test = conn.execute(
                "SELECT naive_p, fair_p, se, combo_status, combo_reason, rho_version, prior_pairs, blocks, is_test "
                "FROM combo_quotes WHERE request_id = %s", (rid,)).fetchone()
            # The done-when pair on the real SQL path: own QB pass yards + own win, above the naive product.
            self.assertGreater(fair, naive)
            self.assertIsNone(se)
            self.assertEqual(rho, table.sha256)
            self.assertEqual(prior, 1)
            self.assertTrue(is_test)
            self.assertEqual((status, reason), ("fits", None))
            self.assertEqual(len(blocks), 1)
            self.assertEqual({x["t"] for x in blocks[0]["legs"]}, {t["qb"], t["aaa"]})
            # Each leg's Kalshi price age (the events were just written: fresh, never stale).
            for x in blocks[0]["legs"]:
                self.assertFalse(x["stale_k"])
                self.assertLess(x["age_s"], 20 * 60)
            # A retry finds it answered and does nothing.
            self.assertEqual(combo_job.price_job({"request_id": rid}, table=table, conn=conn).outcome, "skipped")

    def test_two_outcomes_of_one_result_are_refused_with_the_reason(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            rid = self.request(conn, [(t["aaa"], "yes"), (t["bbb"], "yes")])
            run = combo_job.price_job({"request_id": rid}, table=test_table(t["prefix"]), conn=conn)
            self.assertEqual(run.outcome, "refused")
            st, reason = conn.execute("SELECT status, reason FROM combo_requests WHERE id = %s", (rid,)).fetchone()
            self.assertEqual(st, "refused")
            self.assertIn("two outcomes of one result", reason)
            self.assertIsNone(conn.execute("SELECT 1 FROM combo_quotes WHERE request_id = %s", (rid,)).fetchone())

    def test_a_no_on_a_yes_only_event_builds_manually(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            rid = self.request(conn, [(t["qb"], "yes"), (t["aaa"], "no")])
            combo_job.price_job({"request_id": rid}, table=test_table(t["prefix"]), conn=conn)
            status, reason = conn.execute("SELECT combo_status, combo_reason FROM combo_quotes WHERE request_id = %s",
                                          (rid,)).fetchone()
            self.assertEqual(status, "build_manually")
            self.assertIn("YES only", reason)


class Access(ComboSqlCase):
    def test_the_owner_inserts_and_reads_never_updates_anon_sees_nothing(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            with conn.cursor() as cur:
                _act_as(cur, "authenticated", self.owner)
                cur.execute("INSERT INTO combo_requests (legs) VALUES (%s) RETURNING id",
                            (psycopg.types.json.Jsonb([{"market_ticker": t["qb"], "side": "yes"},
                                                       {"market_ticker": t["aaa"], "side": "yes"}]),))
                rid = cur.fetchone()[0]
                cur.execute("SELECT status FROM combo_requests WHERE id = %s", (rid,))
                self.assertEqual(cur.fetchone()[0], "queued")
                cur.execute("SAVEPOINT s")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("UPDATE combo_requests SET status = 'answered' WHERE id = %s", (rid,))
                cur.execute("ROLLBACK TO SAVEPOINT s")
                cur.execute("RESET ROLE")
                _act_as(cur, "anon")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("SELECT 1 FROM combo_requests LIMIT 1")


class RateLimit(ComboSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT 1 FROM migration_log WHERE name = '077_combo_limit_tickers'").fetchone() is None:
                raise unittest.SkipTest("db/077 not pasted")

    def test_the_21st_request_in_10_minutes_is_refused_with_why(self) -> None:
        with _rolled_back() as conn:
            t = self.board(conn)
            have = conn.execute("SELECT count(*) FROM combo_requests WHERE owner_id = %s "
                                "AND created_at > now() - interval '10 minutes'", (self.owner,)).fetchone()[0]
            for _ in range(20 - have):
                self.request(conn, [(t["qb"], "yes"), (t["aaa"], "yes")])
            conn.execute("SAVEPOINT s")
            with self.assertRaisesRegex(psycopg.errors.RaiseException,
                                        r"at most 20 combo prices in 10 minutes; the next one frees up at "
                                        r"\d\d:\d\d:\d\d UTC"):
                self.request(conn, [(t["qb"], "yes"), (t["aaa"], "yes")])
            conn.execute("ROLLBACK TO SAVEPOINT s")


if __name__ == "__main__":
    unittest.main()
