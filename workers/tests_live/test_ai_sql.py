"""db/020's ai_usage through core/ledger.py, against real Postgres.

ai_usage is append-only (trigger), so a committed test row could never be
removed and would count toward the real monthly budget forever. Instead the
ledger's pool is swapped, for these tests only, for one connection inside a
transaction that is always rolled back: the real SQL runs, nothing is kept.

Skips until db/020 is pasted.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from decimal import Decimal
from typing import Iterator
from unittest import mock

import psycopg

from core import ai, ledger
from core.paths import load_env

load_env()


class _OneTransaction:
    """Quacks like ConnectionPool for `with pool.connection() as conn:`."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        yield self._conn  # no commit: the test rolls everything back


class AiUsageSql(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.getenv("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL is not set")
        try:
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                ready = conn.execute("SELECT to_regclass('public.ai_usage') IS NOT NULL").fetchone()[0]
        except Exception as exc:
            raise unittest.SkipTest(f"database unreachable: {type(exc).__name__}")
        if not ready:
            raise unittest.SkipTest("db/020 not pasted")

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        p = mock.patch.object(ledger, "_pool", lambda: _OneTransaction(self.conn))
        p.start()
        self.addCleanup(p.stop)

    def test_recorded_cost_is_in_month_to_date_spend(self) -> None:
        before = ledger.ai_spend_month_to_date()
        cost = ai.record_usage(purpose="tests_live", model="claude-opus-5-5",
                               usage=ai.Usage(1000, 500, cache_read=200, cache_write_5m=100),
                               critical=False)
        self.assertEqual(ledger.ai_spend_month_to_date() - before, cost)
        row = self.conn.execute(
            "SELECT model, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens, batch, cost_usd "
            "FROM ai_usage WHERE purpose = 'tests_live' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row, ("claude-opus-5-5", 1000, 500, 200, 100, False, cost))

    def test_last_month_does_not_count(self) -> None:
        before = ledger.ai_spend_month_to_date()
        self.conn.execute(
            "INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cost_usd, created_at) "
            "VALUES ('tests_live', 'claude-opus-5-5', 1, 1, 99, now() - interval '40 days')")
        self.assertEqual(ledger.ai_spend_month_to_date(), before)

    def test_month_starts_at_local_midnight_on_the_first(self) -> None:
        start = self.conn.execute(
            f"SELECT {ledger._LOCAL_MONTH_START} AT TIME ZONE 'America/Chicago'").fetchone()[0]
        self.assertEqual((start.day, start.hour, start.minute), (1, 0, 0))

    def test_notification_sent_this_month(self) -> None:
        self.assertFalse(ledger.notification_sent_this_month("tests_live_budget"))
        self.conn.execute(
            "INSERT INTO notifications (owner_id, kind, title, status, sent_at) "
            "SELECT owner_id, 'tests_live_budget', 't', 'sent', now() FROM app_settings")
        self.assertTrue(ledger.notification_sent_this_month("tests_live_budget"))

    def test_cost_fits_the_column(self) -> None:
        # NUMERIC(12,6): a month of heavy Fable use must not overflow.
        cost = ai.cost_usd("claude-fable-5-1", ai.Usage(50 * 10**6, 10 * 10**6))
        self.assertEqual(cost, Decimal("1000.000000"))
        ai.record_usage(purpose="tests_live", model="claude-fable-5-1",
                        usage=ai.Usage(50 * 10**6, 10 * 10**6), critical=True)


if __name__ == "__main__":
    unittest.main()
