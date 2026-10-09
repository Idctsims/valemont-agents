"""db/025: every answer is written with the attempt that produced it.

db/024 refuses a void with no 'resolve' attempt, and a 'missed' snapshot with
no 'capture' attempt, strictly. The ledger satisfies it by construction:
`add_resolution` and `add_closing_snapshot` write the attempt in the same
transaction as the answer, before it. So a first-look void is accepted, and
a write that fails leaves no orphan attempt behind.

Fixtures are real `_test` rows, sealed afterwards like every due fixture.
Skips until db/025 is pasted.
"""

from __future__ import annotations

import os
import unittest

import psycopg

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401

ATTEMPTS = (
    "SELECT result, purpose FROM resolution_attempts "
    "WHERE commitment_id = %s ORDER BY id"
)


class AnsweredAttempts(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            check = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'resolution_attempts_result_check'"
            ).fetchone()
        if check is None or "answered" not in check[0]:
            raise unittest.SkipTest("db/025 not pasted")

    def attempts(self, commitment_id: int) -> list[tuple[str, str]]:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            return [tuple(r) for r in conn.execute(ATTEMPTS, (commitment_id,)).fetchall()]

    def test_a_first_look_void_is_accepted_with_its_attempt(self) -> None:
        committed = self.commit_due()
        self.assertEqual(self.attempts(committed.id), [])
        ledger.add_resolution(
            commitment_id=committed.id, outcome="void",
            leg_outcomes=[ledger.LegOutcome(0, "void", None)], pnl=None,
            detail={"reason": "tests_live db/025: adapter void on first look"},
        )
        self.assertEqual(self.attempts(committed.id), [("answered", "resolve")])
        self.assertEqual(
            self.scalar("SELECT outcome FROM resolutions WHERE commitment_id = %s", committed.id), "void"
        )

    def test_a_refused_resolution_leaves_no_orphan_attempt(self) -> None:
        committed = self.commit_due()
        ledger.add_resolution(
            commitment_id=committed.id, outcome="hit",
            leg_outcomes=[ledger.LegOutcome(0, "hit", "1")], pnl="1",
        )
        with self.assertRaises(psycopg.Error):
            ledger.add_resolution(
                commitment_id=committed.id, outcome="void",
                leg_outcomes=[ledger.LegOutcome(0, "void", None)], pnl=None,
            )
        # One transaction: the second answer was refused, so its attempt was too.
        self.assertEqual(self.attempts(committed.id), [("answered", "resolve")])

    def test_a_captured_close_carries_its_attempt(self) -> None:
        committed = self.commit_due(with_close=True)
        ledger.add_closing_snapshot(
            commitment_id=committed.id, entry_price="0.40", close_price="0.45",
            clv="0.05", clv_pct="0.125",
        )
        self.assertEqual(self.attempts(committed.id), [("answered", "capture")])

    def test_close_unavailable_on_first_look_tombstones_with_its_attempt(self) -> None:
        committed = self.commit_due(with_close=True)
        ledger.add_closing_snapshot(
            commitment_id=committed.id, status="missed",
            reason="unavailable: tests_live db/025", attempt="error",
            attempt_reason="unavailable: tests_live db/025",
        )
        self.assertEqual(self.attempts(committed.id), [("error", "capture")])

    def test_an_answered_attempt_is_never_written_alone(self) -> None:
        committed = self.commit_due()
        with self.assertRaises(ledger.LedgerError):
            ledger.record_resolution_attempt(committed.id, "answered")
        self.assertEqual(self.attempts(committed.id), [])


if __name__ == "__main__":
    unittest.main()
