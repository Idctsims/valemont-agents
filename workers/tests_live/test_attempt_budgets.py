"""The two sweeps must not spend each other's attempt budget.

This is the test the stub suite cannot write. The defect was four missing words
in a SQL predicate, and the only thing that can confirm the fix is Postgres
executing the query.

The bug: `due_for_resolution` counted every `resolution_attempts` row while
`due_for_capture` filtered to `purpose = 'capture'`. Resolution and capture carry
separate `DeferPolicy` budgets, so a commitment waiting legitimately on an open
market drew down BOTH — burning 12 resolution attempts while capture was still
correctly waiting, then voiding. A void is permanent
(`resolutions.commitment_id` is UNIQUE), so the commitment died of bookkeeping
with every component working as designed.
"""

from __future__ import annotations

import unittest

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401


class AttemptBudgetsAreIndependent(LiveLedgerTestCase):
    # Every read takes the WHOLE due set (limit sized from the live count),
    # so the answer cannot depend on how many permanent `_test` rows have
    # built up ahead of the fixture. These tests are about which attempts
    # are counted, not about paging; paging is test_sweep_order.py. The
    # production queries are unchanged: this is their own `limit` argument.
    # Before 2026-10-09 they read the default page of 100 and went red once
    # 109 leaked fixtures sat ahead of them.

    def whole(self, sweep: str) -> list[ledger.PendingCommitment]:
        if sweep == "capture":
            return ledger.due_for_capture(self.agent_id, limit=self.due_count("capture") + 10)
        return ledger.due_for_resolution(self.agent_id, limit=self.due_count("resolve") + 10)
    def test_capture_attempts_do_not_count_against_resolution(self) -> None:
        """The bug, stated as a test. This failed before the fix."""
        committed = self.commit_due(with_close=True)

        for _ in range(5):
            ledger.record_resolution_attempt(
                committed.id, "deferred", "market still open", purpose="capture"
            )

        resolving = self.find(self.whole("resolve"), committed.id)
        capturing = self.find(self.whole("capture"), committed.id)

        self.assertEqual(
            resolving.attempts, 0,
            "five CAPTURE attempts were charged to the RESOLUTION budget — this "
            "is the bug: the commitment would void while capture was still "
            "legitimately waiting, and a void cannot be undone",
        )
        self.assertEqual(capturing.attempts, 5)

    def test_resolution_attempts_do_not_count_against_capture(self) -> None:
        """The mirror image. The capture query always filtered; prove it still does."""
        committed = self.commit_due(with_close=True)

        for _ in range(3):
            ledger.record_resolution_attempt(
                committed.id, "deferred", "not knowable yet", purpose="resolve"
            )

        resolving = self.find(self.whole("resolve"), committed.id)
        capturing = self.find(self.whole("capture"), committed.id)

        self.assertEqual(resolving.attempts, 3)
        self.assertEqual(capturing.attempts, 0)

    def test_both_budgets_count_in_parallel_without_interfering(self) -> None:
        committed = self.commit_due(with_close=True)

        for _ in range(4):
            ledger.record_resolution_attempt(committed.id, "deferred", "x", purpose="capture")
        for _ in range(2):
            ledger.record_resolution_attempt(committed.id, "error", "y", purpose="resolve")
        ledger.record_resolution_attempt(committed.id, "deferred", "z", purpose="capture")

        resolving = self.find(self.whole("resolve"), committed.id)
        capturing = self.find(self.whole("capture"), committed.id)

        self.assertEqual(resolving.attempts, 2, "resolution saw capture's attempts")
        self.assertEqual(capturing.attempts, 5, "capture saw resolution's attempts")

    def test_the_realistic_scenario_that_used_to_void(self) -> None:
        """A market that sits open past its close, with default crypto patience.

        Twelve capture deferrals is an ordinary wait on a thin market. Before the
        fix, the resolution sweep saw twelve attempts and its default policy
        (12 attempts) declared the commitment expired.
        """
        from core.agent import DeferPolicy

        committed = self.commit_due(with_close=True)
        for _ in range(12):
            ledger.record_resolution_attempt(
                committed.id, "deferred", "market still open", purpose="capture"
            )

        pending = self.find(self.whole("resolve"), committed.id)
        verdict = DeferPolicy().expired(pending)
        self.assertIsNone(
            verdict,
            f"the resolution sweep would abandon this commitment ({verdict}) "
            f"purely because capture was waiting — permanent, unrecoverable "
            f"data loss from bookkeeping",
        )


class AttemptsAreScopedPerCommitment(LiveLedgerTestCase):
    """The purpose filter must not have broken per-commitment scoping."""

    def test_one_commitments_attempts_do_not_leak_to_another(self) -> None:
        first = self.commit_due()
        second = self.commit_due()

        for _ in range(3):
            ledger.record_resolution_attempt(first.id, "deferred", "x", purpose="resolve")

        rows = ledger.due_for_resolution(self.agent_id, limit=self.due_count("resolve") + 10)
        self.assertEqual(self.find(rows, first.id).attempts, 3)
        self.assertEqual(self.find(rows, second.id).attempts, 0)


if __name__ == "__main__":
    unittest.main()
