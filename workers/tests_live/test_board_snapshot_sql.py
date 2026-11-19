"""db/084 against the real database: M5's board snapshot.

Every test runs inside ONE transaction that is always rolled back and builds
its own test board rows (series E2ETL..., is_test) with the worker's
statements; it reads back only its own event's rows. Skips when the database
is unreachable or db/084 is unpasted.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from core import ledger
from tests_live import test_kalshi_board_sql as board
from tests_live.test_feed_rank_sql import _rolled_back


class Snapshot084(board.BoardSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regprocedure('public.research_board_snapshot_take(text, boolean, integer)')"
                            ).fetchone()[0] is None:
                raise unittest.SkipTest("db/084 not pasted")

    def take_test(self, conn):
        j = conn.execute("SELECT research_board_snapshot_take('am', true)").fetchone()[0]
        self.assertTrue(j["is_test"])
        return j["snapshot_id"]

    def test_a_test_snapshot_copies_eligible_not_started_legs_only(self) -> None:
        with _rolled_back() as conn:
            _, event, _, markets = self.game(conn, start=board.NOW + timedelta(hours=3), n_markets=3)
            _, started, _, _ = self.game(conn, start=board.NOW - timedelta(hours=1), n_markets=2)
            sid = self.take_test(conn)
            ev = conn.execute("SELECT idx, starts_in_min, combo_yes_only, kalshi_age_s FROM research_board_snapshot_events "
                              "WHERE snapshot_id = %s AND event_ticker = %s", (sid, event)).fetchone()
            self.assertIsNotNone(ev)
            self.assertTrue(170 <= ev[1] <= 181, ev)
            self.assertFalse(ev[2])
            self.assertIsNone(conn.execute("SELECT 1 FROM research_board_snapshot_events WHERE snapshot_id = %s "
                                           "AND event_ticker = %s", (sid, started)).fetchone())
            legs = conn.execute("SELECT tail, kind, stat, yes_bid, yes_ask, fee_yes, tier FROM research_board_snapshot_legs "
                                "WHERE snapshot_id = %s AND event_idx = %s ORDER BY tail", (sid, ev[0])).fetchall()
            self.assertEqual([r[0] for r in legs], [m.split("-")[-1] for m in markets])
            self.assertEqual({r[1:] for r in legs}, {("p", "points", 4000, 4200, 171, "k")})

    def test_the_workers_text_runs(self) -> None:
        # A real snapshot of the live board, rolled back; or the slot's skip if the worker already took it.
        with _rolled_back() as conn:
            result = ledger.research_board_snapshot("am", conn=conn)
            self.assertTrue("snapshot_id" in result or result.get("skipped") in ("taken", "window complete"), result)

    def test_prune_tests_only_keeps_real_snapshots_and_rows_are_written_once(self) -> None:
        with _rolled_back() as conn:
            self.game(conn, start=board.NOW + timedelta(hours=3))
            real = conn.execute("SELECT count(*) FROM research_board_snapshots WHERE NOT is_test").fetchone()[0]
            sid = self.take_test(conn)
            with conn.transaction():
                with self.assertRaisesRegex(Exception, "written once"):
                    with conn.transaction():
                        conn.execute("UPDATE research_board_snapshot_legs SET p = 1 WHERE snapshot_id = %s", (sid,))
            self.assertGreaterEqual(conn.execute("SELECT research_board_snapshot_prune()").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM research_board_snapshots WHERE NOT is_test").fetchone()[0], real)
            self.assertIsNone(conn.execute("SELECT 1 FROM research_board_snapshot_legs WHERE snapshot_id = %s", (sid,)).fetchone())

    def test_rls_and_grants(self) -> None:
        with _rolled_back() as conn:
            for t in ("research_board_snapshots", "research_board_snapshot_events", "research_board_snapshot_legs",
                      "research_board_eligibility"):
                with self.subTest(table=t):
                    row = conn.execute(
                        "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname), "
                        "has_table_privilege('anon', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', oid, 'SELECT'), "
                        "has_table_privilege('authenticated', oid, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', oid, 'SELECT') "
                        "FROM pg_class c WHERE oid = %s::regclass", (f"public.{t}",)).fetchone()
                    self.assertEqual(row, (True, 1, False, True, False, True))
            for role in ("anon", "authenticated", "valemont_readonly"):
                for fn in ("research_board_snapshot_take(text, boolean, integer)", "research_board_snapshot_prune(boolean)"):
                    with self.subTest(role=role, fn=fn):
                        self.assertFalse(conn.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                                                      (role, f"public.{fn}")).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
