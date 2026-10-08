"""db/020 through core/ledger.py: job_health, job_queue, database_size and
notification_sent_since against real Postgres (tests/ stubs them and is
blind to SQL).

App tables, not the ledger: rows are mutable, so this module deletes what it
wrote. Job names are prefixed `tests_live_` so nothing collides with the
worker's own jobs. A deployed worker's queue poller could claim a test row
first; the queue test then skips rather than fails, since that is not a
broken invariant.

Skips until db/020 is pasted.
"""

from __future__ import annotations

import os
import unittest
from datetime import timedelta

import psycopg

from core import ledger
from core.paths import load_env

from .support import tearDownModule  # noqa: F401

load_env()


def _sql(sql: str, *params: object) -> list[tuple]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows


class SystemJobsSql(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            ready = _sql("SELECT to_regclass('public.job_health') IS NOT NULL")[0][0]
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if not ready:
            raise unittest.SkipTest("db/020 not pasted")

    def setUp(self) -> None:
        self.addCleanup(_sql, "DELETE FROM job_health WHERE job LIKE 'tests\\_live\\_%%'")
        self.addCleanup(_sql, "DELETE FROM job_queue WHERE payload ->> 'tests_live' = 'true'")
        self.addCleanup(_sql, "DELETE FROM notifications WHERE kind = 'tests_live_probe'")

    def _health(self, job: str) -> ledger.JobHealth:
        [row] = [r for r in ledger.job_health_rows() if r.job == job]
        return row

    # -- job_health ------------------------------------------------------------

    def test_the_lifecycle_updates_one_row_in_place(self) -> None:
        job = "tests_live_probe"
        ledger.job_register(job, 60)
        ledger.job_register(job, 60)  # idempotent
        ledger.job_started(job)
        ledger.job_failed(job, "RuntimeError: first")
        ledger.job_failed(job, "RuntimeError: second")
        row = self._health(job)
        self.assertEqual((row.consecutive_failures, row.last_error, row.alert_state),
                         (2, "RuntimeError: second", "ok"))
        ledger.job_succeeded(job)
        row = self._health(job)
        self.assertEqual((row.consecutive_failures, row.last_error), (0, None))
        self.assertIsNotNone(row.last_ok_at)
        self.assertFalse(row.stale)
        ledger.set_job_alert_state(job, "alerted")
        self.assertEqual(self._health(job).alert_state, "alerted")
        self.assertEqual(_sql("SELECT count(*) FROM job_health WHERE job = %s", job)[0][0], 1)

    def test_staleness_is_judged_on_the_database_clock(self) -> None:
        job = "tests_live_stale"
        ledger.job_register(job, 5)
        ledger.job_succeeded(job)
        self.assertFalse(self._health(job).stale)
        _sql("UPDATE job_health SET last_ok_at = now() - interval '11 seconds' WHERE job = %s", job)
        self.assertTrue(self._health(job).stale)

    def test_reregistering_changes_the_interval_not_the_state(self) -> None:
        job = "tests_live_reregister"
        ledger.job_register(job, 60)
        ledger.job_failed(job, "x")
        ledger.job_register(job, 120)
        row = self._health(job)
        self.assertEqual((row.expected_interval_s, row.consecutive_failures), (120, 1))

    def test_errors_are_capped_at_500(self) -> None:
        job = "tests_live_long_error"
        ledger.job_register(job, 60)
        ledger.job_failed(job, "x" * 2000)
        self.assertEqual(len(self._health(job).last_error), 500)

    # -- database size -------------------------------------------------------------

    def test_database_size_reports_total_and_tables(self) -> None:
        total, tables = ledger.database_size(top=50)
        self.assertGreater(total, 0)
        names = [t for t, _ in tables]
        self.assertIn("commitments", names)
        self.assertEqual([s for _, s in tables], sorted((s for _, s in tables), reverse=True))

    # -- notification dedupe -----------------------------------------------------

    def test_notification_sent_since(self) -> None:
        self.assertFalse(ledger.notification_sent_since("tests_live_probe", 24))
        _sql("INSERT INTO notifications (owner_id, kind, title, status, sent_at) "
             "SELECT owner_id, 'tests_live_probe', 't', 'sent', now() FROM app_settings")
        self.assertTrue(ledger.notification_sent_since("tests_live_probe", 24))

    # -- job_queue ---------------------------------------------------------------

    def test_claim_retry_and_finish(self) -> None:
        [(job_id,)] = _sql("INSERT INTO job_queue (kind, payload, run_after) "
                           "VALUES ('noop', '{\"tests_live\": true}', now() - interval '1 second') "
                           "RETURNING id")
        claimed = ledger.queue_claim()
        if claimed is None or claimed.id != job_id:
            raise unittest.SkipTest("another poller claimed the test job first")
        self.assertEqual((claimed.kind, claimed.attempts), ("noop", 1))
        self.assertEqual(_sql("SELECT status, locked_at IS NOT NULL FROM job_queue WHERE id = %s",
                              job_id), [("running", True)])

        ledger.queue_retry(job_id, "RuntimeError: flaky", timedelta(seconds=30))
        status, future, error = _sql(
            "SELECT status, run_after > now() + interval '20 seconds', last_error "
            "FROM job_queue WHERE id = %s", job_id)[0]
        self.assertEqual((status, future, error), ("queued", True, "RuntimeError: flaky"))
        self.assertNotEqual(getattr(ledger.queue_claim(), "id", None), job_id,
                            "a job must not be claimed before its backoff ends")

        _sql("UPDATE job_queue SET run_after = now() WHERE id = %s", job_id)
        again = ledger.queue_claim()
        if again is None or again.id != job_id:
            raise unittest.SkipTest("another poller claimed the test job first")
        self.assertEqual(again.attempts, 2)
        ledger.queue_done(job_id)
        self.assertEqual(_sql("SELECT status, finished_at IS NOT NULL, locked_at FROM job_queue "
                              "WHERE id = %s", job_id), [("done", True, None)])

    def test_a_job_abandoned_on_its_last_attempt_is_failed(self) -> None:
        [(job_id,)] = _sql(
            "INSERT INTO job_queue (kind, payload, status, attempts, locked_at) "
            "VALUES ('noop', '{\"tests_live\": true}', 'running', 5, now() - interval '11 minutes') "
            "RETURNING id")
        self.assertGreaterEqual(ledger.queue_fail_exhausted(), 1)
        self.assertEqual(_sql("SELECT status FROM job_queue WHERE id = %s", job_id), [("failed",)])


if __name__ == "__main__":
    unittest.main()
