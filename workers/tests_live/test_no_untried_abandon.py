"""db/024: the database refuses to abandon a commitment nobody tried.

A void resolution needs a 'resolve' attempt on record; a 'missed' close
snapshot needs a 'capture' attempt. The purpose has to match: a capture
attempt does not license a void, and the reverse. Ordinary outcomes and
captured snapshots need no attempt. No exemption for test agents: every
row here belongs to `_test`, and the rule bites just the same.

Fixtures are real `_test` rows (permanent, quarantined by is_test) and are
sealed afterwards like every due fixture (support.seal records its own
attempts first). Skips until db/024 is pasted.
"""

from __future__ import annotations

import os
import unittest

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

REFUSED = "attempt on record"


class NoUntriedAbandon(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            present = conn.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN "
                "('resolutions_no_untried_void', 'closing_snapshots_no_untried_missed')"
            ).fetchone()[0]
        if present != 2:
            raise unittest.SkipTest("db/024 not pasted")

    def void(self, commitment_id: int) -> None:
        ledger.add_resolution(
            commitment_id=commitment_id, outcome="void",
            leg_outcomes=[ledger.LegOutcome(0, "void", None)], pnl=None,
            detail={"abandoned": True, "reason": "tests_live db/024"},
        )

    def miss(self, commitment_id: int) -> None:
        ledger.add_closing_snapshot(commitment_id=commitment_id, status="missed", reason="tests_live db/024")

    def test_the_rows_here_are_test_rows_and_get_no_exemption(self) -> None:
        self.assertTrue(ledger.agent_is_test("_test"))
        committed = self.commit_due()
        with self.assertRaisesRegex(psycopg.errors.RaiseException, REFUSED):
            self.void(committed.id)

    def test_a_void_needs_a_resolve_attempt(self) -> None:
        committed = self.commit_due()
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "no resolve attempt"):
            self.void(committed.id)
        ledger.record_resolution_attempt(committed.id, "deferred", "tests_live", purpose="capture")
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "no resolve attempt"):
            self.void(committed.id)  # a capture attempt does not count
        ledger.record_resolution_attempt(committed.id, "deferred", "tests_live", purpose="resolve")
        self.void(committed.id)
        self.assertEqual(
            self.scalar("SELECT outcome FROM resolutions WHERE commitment_id = %s", committed.id), "void"
        )

    def test_a_missed_close_needs_a_capture_attempt(self) -> None:
        committed = self.commit_due(with_close=True)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "no capture attempt"):
            self.miss(committed.id)
        ledger.record_resolution_attempt(committed.id, "deferred", "tests_live", purpose="resolve")
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "no capture attempt"):
            self.miss(committed.id)  # a resolve attempt does not count
        ledger.record_resolution_attempt(committed.id, "deferred", "tests_live", purpose="capture")
        self.miss(committed.id)
        self.assertEqual(
            self.scalar("SELECT status FROM closing_snapshots WHERE commitment_id = %s", committed.id), "missed"
        )

    def test_a_real_outcome_needs_no_attempt(self) -> None:
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
        )

    def test_a_captured_close_needs_no_attempt(self) -> None:
        committed = self.commit_due(with_close=True)
        ledger.add_closing_snapshot(
            commitment_id=committed.id, entry_price="0.40", close_price="0.45",
            clv="0.05", clv_pct="0.125",
        )

    def test_raw_sql_gets_no_way_around_it(self) -> None:
        committed = self.commit_due(with_close=True)
        self.refuses(
            "INSERT INTO resolutions (commitment_id, outcome) VALUES (%s, 'void')",
            committed.id, containing=REFUSED,
        )
        self.refuses(
            "INSERT INTO closing_snapshots (commitment_id, status, reason) VALUES (%s, 'missed', 'raw')",
            committed.id, containing=REFUSED,
        )


if __name__ == "__main__":
    unittest.main()
