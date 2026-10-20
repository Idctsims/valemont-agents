"""db/057's egress_samples against the real database (owner, 2026-10-20):
the egress job's day total, one append-only row per run and a final row for
the day just closed; v_egress_days is each day's latest; the owner and the
read-only role read, nobody updates, deletes or truncates.

Every test runs inside ONE rolled-back transaction, on days in 2001 (no real
sample is ever written). Skips until db/057 is pasted.
"""

from __future__ import annotations

import unittest
from datetime import date

import psycopg

from core import ledger
from tests_live.test_feed_rank_sql import RankSqlCase, _rolled_back


class EgressSamples(RankSqlCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with _rolled_back() as conn:
            if conn.execute("SELECT to_regclass('public.egress_samples')").fetchone()[0] is None:
                raise unittest.SkipTest("db/057 not pasted")

    def refused(self, conn: psycopg.Connection, sql: str, params: tuple, message: str,
                kind: type[Exception] = psycopg.errors.RaiseException) -> None:
        with self.assertRaises(kind) as cm, conn.transaction():
            conn.execute(sql, params)
        self.assertIn(message, str(cm.exception))

    def test_the_worker_writes_samples_and_the_view_reads_each_days_latest(self) -> None:
        with _rolled_back() as conn:
            d1, d2 = date(2001, 1, 1), date(2001, 1, 2)
            ledger.egress_record_sample(d1, total_bytes=100, carried_bytes=40, by_job={"feed_ingest": 60}, conn=conn)
            last = ledger.egress_record_sample(d1, total_bytes=900, carried_bytes=40,
                                               by_job={"feed_ingest": 860}, final=True, conn=conn)
            ledger.egress_record_sample(d2, total_bytes=7, carried_bytes=0, by_job={}, conn=conn)
            self.assertEqual(conn.execute(
                "SELECT day, total_bytes, by_job, final FROM v_egress_days WHERE day IN (%s, %s) ORDER BY day",
                (d1, d2)).fetchall(),
                [(d1, 900, {"feed_ingest": 860}, True), (d2, 7, {}, False)])
            self.assertEqual(conn.execute("SELECT created_at = now(), carried_bytes FROM egress_samples WHERE id = %s",
                                          (last,)).fetchone(), (True, 40))

    def test_append_only_and_the_checks(self) -> None:
        with _rolled_back() as conn:
            sid = ledger.egress_record_sample(date(2001, 1, 3), total_bytes=5, carried_bytes=0, by_job={}, conn=conn)
            self.refused(conn, "UPDATE egress_samples SET total_bytes = 1 WHERE id = %s", (sid,), "")
            self.refused(conn, "DELETE FROM egress_samples WHERE id = %s", (sid,), "")
            self.refused(conn, "TRUNCATE egress_samples", (), "TRUNCATE is refused")
            self.refused(conn, "INSERT INTO egress_samples (day, total_bytes, carried_bytes, by_job) "
                               "VALUES ('2001-01-04', 5, 9, '{}')", (), "", psycopg.errors.CheckViolation)
            self.refused(conn, "INSERT INTO egress_samples (day, total_bytes, by_job) VALUES ('2001-01-04', 5, '[]')",
                         (), "", psycopg.errors.CheckViolation)

    def test_access(self) -> None:
        with _rolled_back() as conn:
            for rel in ("egress_samples", "v_egress_days"):
                with self.subTest(rel=rel):
                    self.assertEqual(conn.execute(
                        "SELECT has_table_privilege('anon', %s, 'SELECT,INSERT,UPDATE,DELETE'), "
                        "has_table_privilege('authenticated', %s, 'SELECT'), "
                        "has_table_privilege('authenticated', %s, 'INSERT,UPDATE,DELETE,TRUNCATE'), "
                        "has_table_privilege('valemont_readonly', %s, 'SELECT')", (f"public.{rel}",) * 4).fetchone(),
                        (False, True, False, True))
            self.assertEqual(conn.execute(
                "SELECT relrowsecurity, (SELECT count(*) FROM pg_policies WHERE tablename = 'egress_samples') "
                "FROM pg_class WHERE oid = 'public.egress_samples'::regclass").fetchone(), (True, 1))


if __name__ == "__main__":
    unittest.main()
