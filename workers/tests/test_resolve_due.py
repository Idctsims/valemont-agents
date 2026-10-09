"""resolve_due — defer, error, bounded void, and a broken attempt counter."""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal as D

from core.agent import DeferPolicy
from core.ledger import Leg
from tests.support import LedgerTestCase, ScriptedAgent, hit, pending

POLICY = DeferPolicy(max_attempts=3, max_overdue=timedelta(hours=24))


def raises(exc: BaseException):
    def resolver(p):
        raise exc
    return resolver


class ResolveDue(LedgerTestCase):

    def agent(self, resolver=None) -> ScriptedAgent:
        return ScriptedAgent(resolver=resolver, defer_policy=POLICY)

    def test_nothing_due_opens_no_run(self) -> None:
        outcome = self.agent().resolve_due()
        self.assertIsNone(outcome.run_id)
        self.assertEqual(self.ledger.writes(), [])

    def test_verdict_is_persisted_as_given(self) -> None:
        self.ledger.add_due(pending(1))
        outcome = self.agent(lambda p: hit(D("2.5"))).resolve_due()

        self.assertEqual((outcome.due, outcome.resolved), (1, 1))
        [resolution] = self.ledger.named("add_resolution")
        self.assertEqual(resolution.kwargs["outcome"], "hit")
        self.assertEqual(resolution.kwargs["pnl"], D("2.5"))
        self.assertIn("resolved", self.ledger.event_kinds())
        [end] = self.ledger.named("end_run")
        self.assertEqual(end.args[1], "ok")

    def test_defer_records_an_attempt_and_writes_no_resolution(self) -> None:
        self.ledger.add_due(pending(1))
        outcome = self.agent(lambda p: None).resolve_due()

        self.assertEqual((outcome.deferred, outcome.resolved, outcome.voided), (1, 0, 0))
        self.assertEqual(self.ledger.named("add_resolution"), [])
        [attempt] = self.ledger.named("record_resolution_attempt")
        self.assertEqual(attempt.args, (1, "deferred"))
        self.assertEqual(attempt.kwargs["purpose"], "resolve")

    def test_error_is_counted_isolated_and_recorded(self) -> None:
        self.ledger.add_due(pending(1), pending(2))

        def resolver(p):
            if p.id == 1:
                raise RuntimeError("feed exploded")
            return hit()

        agent = self.agent(resolver)
        outcome = agent.resolve_due()

        self.assertEqual((outcome.failed, outcome.resolved), (1, 1))
        self.assertEqual(agent.resolve_calls, [1, 2])
        [attempt] = self.ledger.named("record_resolution_attempt")
        self.assertEqual(attempt.args, (1, "error"))
        self.assertIn("feed exploded", attempt.kwargs["reason"])
        [resolution] = self.ledger.named("add_resolution")
        self.assertEqual(resolution.kwargs["commitment_id"], 2)
        self.assertIn("error", self.ledger.event_kinds())
        [end] = self.ledger.named("end_run")
        self.assertEqual(end.args[1], "error")

    def test_exhausted_attempts_void_without_asking_again(self) -> None:
        legs = (Leg("A", "m", D(1), "over", D(1)), Leg("B", "m", D(2), "over", D(1)))
        self.ledger.add_due(pending(7, attempts=3, legs=legs))
        agent = self.agent(lambda p: self.fail("resolve() called on an exhausted commitment"))
        outcome = agent.resolve_due()

        self.assertEqual(outcome.voided, 1)
        self.assertEqual(agent.resolve_calls, [])
        [void] = self.ledger.named("add_resolution")
        self.assertEqual(void.kwargs["outcome"], "void")
        self.assertIsNone(void.kwargs["pnl"])
        self.assertIs(void.kwargs["detail"]["abandoned"], True)
        self.assertEqual([lo.outcome for lo in void.kwargs["leg_outcomes"]], ["void", "void"])
        self.assertEqual([lo.leg_index for lo in void.kwargs["leg_outcomes"]], [0, 1])
        self.assertIn("voided", self.ledger.event_kinds())

    def test_one_attempt_short_of_the_cap_is_still_asked(self) -> None:
        self.ledger.add_due(pending(7, attempts=2))
        agent = self.agent()
        outcome = agent.resolve_due()
        self.assertEqual((outcome.resolved, outcome.voided), (1, 0))
        self.assertEqual(agent.resolve_calls, [7])

    def test_overdue_cap_voids_however_few_attempts(self) -> None:
        # "However few": one. Zero never voids (see ZeroAttemptsNeverAbandon).
        self.ledger.add_due(pending(8, attempts=1, overdue_by=timedelta(hours=25)))
        outcome = self.agent().resolve_due()
        self.assertEqual(outcome.voided, 1)
        [void] = self.ledger.named("add_resolution")
        self.assertIn("past resolves_after", void.kwargs["detail"]["reason"])

    def test_failed_void_write_is_a_failure_not_a_void(self) -> None:
        self.ledger.add_due(pending(9, attempts=3))
        self.ledger.fail_on("add_resolution", RuntimeError("db down"))
        outcome = self.agent().resolve_due()
        self.assertEqual((outcome.voided, outcome.failed), (0, 1))
        self.assertNotIn("voided", self.ledger.event_kinds())

    def test_failed_resolution_write_is_a_failure(self) -> None:
        self.ledger.add_due(pending(10))
        self.ledger.fail_on("add_resolution", RuntimeError("trigger rejected"))
        outcome = self.agent().resolve_due()
        self.assertEqual((outcome.resolved, outcome.failed), (0, 1))
        self.assertNotIn("resolved", self.ledger.event_kinds())


class BrokenAttemptCounter(LedgerTestCase):
    """If the attempt can't be recorded, fail toward retry — never toward a void."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger.fail_on("record_resolution_attempt", RuntimeError("counter down"))

    def test_defer_survives_and_nothing_is_abandoned(self) -> None:
        self.ledger.add_due(pending(1), pending(2))
        agent = ScriptedAgent(resolver=lambda p: None if p.id == 1 else hit(),
                              defer_policy=POLICY)
        outcome = agent.resolve_due()

        self.assertEqual((outcome.deferred, outcome.resolved, outcome.voided,
                          outcome.failed), (1, 1, 0, 0))
        [resolution] = self.ledger.named("add_resolution")
        self.assertEqual(resolution.kwargs["commitment_id"], 2)

    def test_error_path_survives_too(self) -> None:
        self.ledger.add_due(pending(1))
        agent = ScriptedAgent(resolver=raises(ValueError("bad")), defer_policy=POLICY)
        outcome = agent.resolve_due()
        self.assertEqual((outcome.failed, outcome.voided), (1, 0))
        self.assertEqual(self.ledger.named("add_resolution"), [])


if __name__ == "__main__":
    unittest.main()
