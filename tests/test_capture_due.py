"""capture_due — still-open defer, CloseUnavailable tombstone, budget exhaustion."""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal as D

from core.agent import ClosePrice, CloseUnavailable, DeferPolicy
from core.ledger import Leg
from tests.support import LedgerTestCase, ScriptedAgent, now, pending

POLICY = DeferPolicy(max_attempts=4, max_overdue=timedelta(hours=48))


def closing(commitment_id: int, **kwargs):
    kwargs.setdefault("closes_at", now() - timedelta(minutes=1))
    return pending(commitment_id, **kwargs)


def raises(exc: BaseException):
    def closer(p):
        raise exc
    return closer


class CaptureDue(LedgerTestCase):

    def agent(self, closer=None, *, captures_close: bool = True) -> ScriptedAgent:
        return ScriptedAgent(closer=closer, captures_close=captures_close,
                             capture_policy=POLICY)

    def test_opted_out_agent_costs_nothing(self) -> None:
        self.ledger.add_capture(closing(1))
        outcome = self.agent(captures_close=False).capture_due()
        self.assertIsNone(outcome.run_id)
        self.assertEqual(self.ledger.calls, [])

    def test_nothing_due_opens_no_run(self) -> None:
        outcome = self.agent().capture_due()
        self.assertIsNone(outcome.run_id)
        self.assertEqual(self.ledger.writes(), [])

    def test_still_open_defers_and_counts_a_capture_attempt(self) -> None:
        self.ledger.add_capture(closing(1))
        outcome = self.agent(lambda p: None).capture_due()

        self.assertEqual((outcome.deferred, outcome.captured, outcome.missed), (1, 0, 0))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])
        [attempt] = self.ledger.named("record_resolution_attempt")
        self.assertEqual(attempt.args, (1, "deferred"))
        self.assertEqual(attempt.kwargs["purpose"], "capture")

    def test_capture_freezes_clv_computed_against_the_leg_line(self) -> None:
        self.ledger.add_capture(closing(1))
        outcome = self.agent(lambda p: ClosePrice(price=D("0.55"))).capture_due()

        self.assertEqual(outcome.captured, 1)
        [snap] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(snap.kwargs["entry_price"], D("0.40"))
        self.assertEqual(snap.kwargs["close_price"], D("0.55"))
        self.assertEqual(snap.kwargs["clv"], D("0.15"))
        self.assertEqual(snap.kwargs["clv_pct"], D("0.375"))
        self.assertEqual(snap.kwargs.get("status", "captured"), "captured")
        self.assertEqual(snap.kwargs["detail"]["captured_by"], "_test")
        self.assertIn("captured", self.ledger.event_kinds())

    def test_explicit_entry_price_overrides_the_leg_line(self) -> None:
        legs = (Leg("MKT", "event_contract", D(34), "yes", D(1)),)
        self.ledger.add_capture(closing(1, legs=legs))
        self.agent(lambda p: ClosePrice(price=D("0.40"), entry_price=D("0.34"))).capture_due()
        [snap] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(snap.kwargs["clv"], D("0.06"))

    def test_close_unavailable_tombstones_immediately(self) -> None:
        self.ledger.add_capture(closing(1, attempts=0))
        outcome = self.agent(raises(CloseUnavailable("venue serves no history"))).capture_due()

        self.assertEqual((outcome.missed, outcome.failed), (1, 0))
        [tomb] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(tomb.kwargs["status"], "missed")
        self.assertTrue(tomb.kwargs["reason"].startswith("unavailable:"))
        self.assertIn("venue serves no history", tomb.kwargs["reason"])
        self.assertEqual(self.ledger.named("record_resolution_attempt"), [])
        self.assertIn("close_missed", self.ledger.event_kinds())

    def test_exhausted_attempts_tombstone_without_asking_again(self) -> None:
        self.ledger.add_capture(closing(1, attempts=4))
        agent = self.agent(lambda p: self.fail("capture_close() called after exhaustion"))
        outcome = agent.capture_due()

        self.assertEqual(outcome.missed, 1)
        self.assertEqual(agent.capture_calls, [])
        [tomb] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(tomb.kwargs["status"], "missed")
        self.assertIn("4 attempts", tomb.kwargs["reason"])

    def test_exhausted_overdue_budget_tombstones(self) -> None:
        self.ledger.add_capture(closing(1, attempts=0, overdue_by=timedelta(hours=49)))
        outcome = self.agent().capture_due()
        self.assertEqual(outcome.missed, 1)
        [tomb] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(tomb.kwargs["status"], "missed")

    def test_capture_budget_is_more_patient_than_resolve_budget(self) -> None:
        agent = ScriptedAgent()
        self.assertGreater(agent.capture_policy.max_attempts, agent.defer_policy.max_attempts)
        self.assertGreater(agent.capture_policy.max_overdue, agent.defer_policy.max_overdue)

    def test_transient_error_retries_rather_than_tombstoning(self) -> None:
        self.ledger.add_capture(closing(1))
        outcome = self.agent(raises(TimeoutError("feed slow"))).capture_due()

        self.assertEqual((outcome.failed, outcome.missed), (1, 0))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])
        [attempt] = self.ledger.named("record_resolution_attempt")
        self.assertEqual(attempt.args, (1, "error"))
        self.assertEqual(attempt.kwargs["purpose"], "capture")

    def test_out_of_range_close_is_a_failure_not_a_snapshot(self) -> None:
        self.ledger.add_capture(closing(1))
        outcome = self.agent(lambda p: ClosePrice(price=D("1.5"))).capture_due()
        self.assertEqual((outcome.failed, outcome.captured), (1, 0))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])

    def test_no_entry_price_is_a_failure_not_a_tombstone(self) -> None:
        self.ledger.add_capture(closing(1, legs=(Leg("MKT", "event_contract"),)))
        outcome = self.agent(lambda p: ClosePrice(price=D("0.5"))).capture_due()
        self.assertEqual((outcome.failed, outcome.missed), (1, 0))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])

    def test_failed_tombstone_write_is_a_failure_not_a_miss(self) -> None:
        self.ledger.add_capture(closing(1, attempts=4))
        self.ledger.fail_on("add_closing_snapshot", RuntimeError("db down"))
        outcome = self.agent().capture_due()
        self.assertEqual((outcome.missed, outcome.failed), (0, 1))
        self.assertNotIn("close_missed", self.ledger.event_kinds())

    def test_broken_attempt_counter_fails_toward_retry(self) -> None:
        self.ledger.fail_on("record_resolution_attempt", RuntimeError("counter down"))
        self.ledger.add_capture(closing(1), closing(2))
        outcome = self.agent(
            lambda p: None if p.id == 1 else ClosePrice(price=D("0.5"))
        ).capture_due()
        self.assertEqual((outcome.deferred, outcome.captured, outcome.missed), (1, 1, 0))
        [snap] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(snap.kwargs["commitment_id"], 2)


if __name__ == "__main__":
    unittest.main()
