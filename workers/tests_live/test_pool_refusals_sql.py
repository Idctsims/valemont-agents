"""db/064's arsenal_pool_runs.refusals against the real database: the shape
ledger.arsenal_log_run builds (zero counts dropped) is stored on a collected
row; the database refuses it on a submitted row and in a bad shape; the
ledger refuses refusals off a collected row before the database sees them.
The rows are inserted directly so they can be TEST rows (arsenal_log_run has
no test flag). Every test runs in ONE rolled-back transaction. Skips until
db/064."""

from __future__ import annotations

import unittest

import psycopg

from core import ledger
from tests_live.test_arsenal_sql import ArsenalSqlCase
from tests_live.test_feed_rank_sql import _rolled_back


class Refusals(ArsenalSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'arsenal_pool_runs' "
                            "AND column_name = 'refusals'").fetchone() is None:
                raise unittest.SkipTest("db/064 not pasted")

    def submitted(self, conn: psycopg.Connection) -> int:
        return int(conn.execute("INSERT INTO arsenal_pool_runs (kind, status, model, refs_requested, words_requested, "
                                "is_test) VALUES ('topup', 'submitted', 'claude-sonnet-5-5', 6, 2, true) "
                                "RETURNING id").fetchone()[0])

    def test_the_collector_writes_them_on_the_collected_row(self) -> None:
        with _rolled_back() as conn:
            run = self.submitted(conn)
            refusals = {"finance_business": {"duplicate_key": 4, "near_dup": 0}, "words": {"duplicate_key": 1},
                        "unknown": {"invalid_field": 2}}
            row = conn.execute("INSERT INTO arsenal_pool_runs (kind, status, run_id, refusals, is_test) "
                               "VALUES ('topup', 'collected', %s, %s, true) RETURNING id",
                               (run, psycopg.types.json.Jsonb(
                                   {c: {r: n for r, n in rs.items() if n} for c, rs in refusals.items()}))
                               ).fetchone()[0]
            got = conn.execute("SELECT refusals FROM arsenal_pool_runs WHERE id = %s", (row,)).fetchone()[0]
            self.assertEqual(got, {"finance_business": {"duplicate_key": 4}, "words": {"duplicate_key": 1},
                                   "unknown": {"invalid_field": 2}})
            # The ledger refuses refusals on any other status before the database sees them.
            with self.assertRaises(ledger.LedgerError):
                ledger.arsenal_log_run("topup", "submitted", refusals={"sports": {"other": 1}}, conn=conn)

    def test_the_check_refuses_another_status_and_another_shape(self) -> None:
        with _rolled_back() as conn:
            run = self.submitted(conn)
            for status, value in (("submitted", '{"sports": {"other": 1}}'), ("collected", '{"cooking": {"other": 1}}'),
                                  ("collected", '{"sports": {"bored": 1}}'), ("collected", '{"sports": {"near_dup": -1}}')):
                with self.subTest(status=status, value=value), self.assertRaises(psycopg.errors.CheckViolation), \
                        conn.transaction():
                    conn.execute("INSERT INTO arsenal_pool_runs (kind, status, run_id, model, refusals, is_test) "
                                 "VALUES ('topup', %s, %s, 'claude-sonnet-5-5', %s::jsonb, true)",
                                 (status, run if status == "collected" else None, value))


if __name__ == "__main__":
    unittest.main()
