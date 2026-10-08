"""db/019 through core/ledger.py: the five queries workers/core/push.py uses,
against real Postgres (tests/ stubs them and cannot see SQL).

These are app tables, not the ledger: rows are mutable and deletable, so this
module cleans up after itself instead of quarantining. The fixture device has
an endpoint on a `.invalid` domain, so even a worker send that raced the test
could never deliver anywhere; it would fail and stamp failed_at.

Skips until db/019 is pasted and when auth.users is empty.
"""

from __future__ import annotations

import os
import unittest
import uuid

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


class PushLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            ready = _sql("SELECT to_regclass('public.push_subscriptions') IS NOT NULL")[0][0]
            users = _sql("SELECT id::text FROM auth.users ORDER BY created_at LIMIT 1")
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if not ready:
            raise unittest.SkipTest("db/019 not pasted")
        if not users:
            raise unittest.SkipTest("auth.users is empty")
        cls.owner = users[0][0]

    def setUp(self) -> None:
        self.endpoint = f"https://push.example.invalid/tests_live/{uuid.uuid4()}"
        self.sub_id = _sql(
            "INSERT INTO push_subscriptions (owner_id, endpoint, p256dh, auth, device_label) "
            "VALUES (%s, %s, 'p', 'a', 'tests_live') RETURNING id",
            self.owner, self.endpoint,
        )[0][0]
        self.addCleanup(_sql, "DELETE FROM push_subscriptions WHERE id = %s", self.sub_id)
        self.addCleanup(_sql, "DELETE FROM notifications WHERE kind = 'tests_live' AND owner_id = %s",
                        self.owner)

    def _row(self) -> tuple:
        return _sql("SELECT active, last_success_at IS NOT NULL, failed_at IS NOT NULL "
                    "FROM push_subscriptions WHERE id = %s", self.sub_id)[0]

    def test_active_subscriptions_include_the_fixture_and_filter_by_owner(self) -> None:
        mine = ledger.active_push_subscriptions(self.owner)
        fixture = [s for s in mine if s.id == self.sub_id]
        self.assertEqual(len(fixture), 1)
        self.assertEqual((fixture[0].owner_id, fixture[0].endpoint, fixture[0].device_label),
                         (self.owner, self.endpoint, "tests_live"))
        self.assertEqual(ledger.active_push_subscriptions(str(uuid.uuid4())), [])
        self.assertIn(self.sub_id, [s.id for s in ledger.active_push_subscriptions()])

    def test_delivered_stamps_last_success(self) -> None:
        ledger.mark_push_delivered(self.sub_id)
        self.assertEqual(self._row(), (True, True, False))

    def test_a_transient_failure_keeps_it_active(self) -> None:
        ledger.mark_push_failed(self.sub_id, deactivate=False)
        self.assertEqual(self._row(), (True, False, True))
        self.assertIn(self.sub_id, [s.id for s in ledger.active_push_subscriptions(self.owner)])

    def test_gone_deactivates_and_drops_it_from_the_active_set(self) -> None:
        ledger.mark_push_failed(self.sub_id, deactivate=True)
        self.assertEqual(self._row(), (False, False, True))
        self.assertNotIn(self.sub_id, [s.id for s in ledger.active_push_subscriptions(self.owner)])

    def test_notification_goes_from_queued_to_finished(self) -> None:
        nid = ledger.record_notification(owner_id=self.owner, kind="tests_live", title="t",
                                         body=None, deep_link="/onboarding")
        self.assertEqual(_sql("SELECT status, sent_at IS NULL FROM notifications WHERE id = %s", nid),
                         [("queued", True)])
        ledger.finish_notification(nid, status="partial", error="push.example.invalid/…abc123: 410")
        self.assertEqual(
            _sql("SELECT status, sent_at IS NOT NULL, error FROM notifications WHERE id = %s", nid),
            [("partial", True, "push.example.invalid/…abc123: 410")],
        )


if __name__ == "__main__":
    unittest.main()
