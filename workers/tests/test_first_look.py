"""Invariant: no commitment is ever voided, or has its close tombstoned
`missed`, with zero recorded attempts.

Both writes are permanent (UNIQUE per commitment). Before this rule, a row
that sat unseen past `max_overdue` (a due set larger than one sweep page)
was given up on at its first appearance, without a single call into the
adapter. Now:

  1. `DeferPolicy.expired` never answers at zero attempts, however overdue;
  2. so the first appearance is a real attempt (and a late first look at a
     close can still capture: capture reads candle history);
  3. and the write itself refuses: `_abandon` and `_miss` raise
     `AbandonWithoutAttempt` at zero, before touching the ledger;
  4. db/024 enforces it in the database, strictly, and core satisfies it by
     construction: every answer (resolution or snapshot) is written with the
     attempt that produced it, in one transaction (db/025).

The due-set ORDER that stops rows starving in the first place is SQL, so it
is tested live: tests_live/test_sweep_order.py.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

from core.agent import AbandonWithoutAttempt, ClosePrice, CloseUnavailable, DeferPolicy
from tests.support import LedgerTestCase, ScriptedAgent, now, pending

POLICY = DeferPolicy(max_attempts=4, max_overdue=timedelta(hours=24))
VERY_OVERDUE = timedelta(days=30)


def closing(commitment_id: int, **kwargs):
    kwargs.setdefault("closes_at", now() - timedelta(minutes=1))
    return pending(commitment_id, **kwargs)


class Policy(LedgerTestCase):
    def test_zero_attempts_never_expire_however_overdue(self) -> None:
        for what in ("resolution", "close"):
            with self.subTest(what=what):
                self.assertIsNone(POLICY.expired(pending(1, attempts=0, overdue_by=VERY_OVERDUE), what))

    def test_one_attempt_and_overdue_does_expire(self) -> None:
        self.assertIsNotNone(POLICY.expired(pending(1, attempts=1, overdue_by=timedelta(hours=25))))


class Resolution(LedgerTestCase):
    def test_an_unseen_overdue_row_gets_a_first_look_not_a_void(self) -> None:
        self.ledger.add_due(pending(7, attempts=0, overdue_by=VERY_OVERDUE))
        agent = ScriptedAgent(resolver=lambda p: None, defer_policy=POLICY)
        outcome = agent.resolve_due()

        self.assertEqual(agent.resolve_calls, [7])
        self.assertEqual((outcome.voided, outcome.deferred), (0, 1))
        self.assertEqual(self.ledger.named("add_resolution"), [])
        [attempt] = self.ledger.named("record_resolution_attempt")
        self.assertEqual(attempt.args[:2], (7, "deferred"))

    def test_and_if_it_answers_it_resolves(self) -> None:
        self.ledger.add_due(pending(7, attempts=0, overdue_by=VERY_OVERDUE))
        outcome = ScriptedAgent(defer_policy=POLICY).resolve_due()
        self.assertEqual((outcome.resolved, outcome.voided), (1, 0))

    def test_the_void_write_refuses_at_zero_attempts(self) -> None:
        agent = ScriptedAgent(defer_policy=POLICY)
        with self.assertRaises(AbandonWithoutAttempt):
            agent._abandon(pending(7, attempts=0, overdue_by=VERY_OVERDUE), "test", run_id=1)
        self.assertEqual(self.ledger.named("add_resolution", ok=None), [])


class Capture(LedgerTestCase):
    def agent(self, closer=None) -> ScriptedAgent:
        return ScriptedAgent(closer=closer, captures_close=True, capture_policy=POLICY)

    def test_a_late_first_look_captures_from_history(self) -> None:
        # Seen for the first time 30 days after its close. The old rule
        # tombstoned it unseen; now capture_close is asked, and an adapter
        # that reads candle history can still price it.
        self.ledger.add_capture(closing(3, attempts=0, overdue_by=VERY_OVERDUE))
        agent = self.agent(lambda p: ClosePrice(price=D("0.55")))
        outcome = agent.capture_due()
        self.assertEqual(agent.capture_calls, [3])
        self.assertEqual((outcome.captured, outcome.missed), (1, 0))

    def test_a_late_first_look_that_defers_is_counted_not_tombstoned(self) -> None:
        self.ledger.add_capture(closing(3, attempts=0, overdue_by=VERY_OVERDUE))
        outcome = self.agent(lambda p: None).capture_due()
        self.assertEqual((outcome.deferred, outcome.missed), (1, 0))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])

    def test_the_tombstone_write_refuses_at_zero_attempts(self) -> None:
        agent = self.agent()
        with self.assertRaises(AbandonWithoutAttempt):
            agent._miss(closing(3, attempts=0), "test", run_id=1)
        self.assertEqual(self.ledger.named("add_closing_snapshot", ok=None), [])

    def test_close_unavailable_whose_write_fails_lands_neither_and_stays_due(self) -> None:
        # The look and the tombstone are one transaction. If it fails, neither
        # lands: no tombstone, no attempt, and the row is asked again.
        self.ledger.add_capture(closing(3, attempts=0))
        self.ledger.fail_on("add_closing_snapshot", RuntimeError("database down"))

        def unavailable(p):
            raise CloseUnavailable("ticker delisted")

        outcome = self.agent(unavailable).capture_due()
        self.assertEqual((outcome.missed, outcome.failed), (0, 1))
        self.assertEqual(self.ledger.named("add_closing_snapshot"), [])
        self.assertEqual(self.ledger.attempts[(3, "capture")], 0)

    def test_a_captured_close_carries_its_attempt(self) -> None:
        self.ledger.add_capture(closing(3, attempts=0))
        self.agent(lambda p: ClosePrice(price=D("0.55"))).capture_due()
        [snapshot] = self.ledger.named("add_closing_snapshot")
        self.assertEqual(snapshot.kwargs["attempt"], "answered")
        self.assertEqual(self.ledger.attempts[(3, "capture")], 1)
        self.assertEqual(self.ledger.named("record_resolution_attempt", ok=None), [])

    def test_an_exhausted_budget_tombstones_on_the_attempts_already_there(self) -> None:
        self.ledger.add_capture(closing(3, attempts=4, overdue_by=timedelta(hours=1)))
        self.agent(lambda p: self.fail("asked after exhaustion")).capture_due()
        [tomb] = self.ledger.named("add_closing_snapshot")
        self.assertIsNone(tomb.kwargs["attempt"])  # nothing asked, nothing invented
        self.assertEqual(self.ledger.attempts[(3, "capture")], 4)


class EveryAnswerCarriesItsAttempt(LedgerTestCase):
    """db/024 stays strict: no void without a 'resolve' attempt, whoever
    writes it. Core makes that hold by construction, writing the attempt that
    produced an answer in the same transaction as the answer. The stub ledger
    models the trigger, so "accepted" below means db/024 would accept it."""

    def verdict(self, outcome: str):
        from core.agent import Verdict
        from core.ledger import LegOutcome
        return Verdict(outcome=outcome, pnl=None if outcome == "void" else D("0"),
                       leg_outcomes=[LegOutcome(leg_index=0, outcome=outcome, actual=None)])

    def test_an_adapter_void_on_its_first_resolve_is_accepted(self) -> None:
        self.ledger.add_due(pending(7, attempts=0))
        agent = ScriptedAgent(resolver=lambda p: self.verdict("void"), defer_policy=POLICY)
        outcome = agent.resolve_due()

        self.assertEqual(agent.resolve_calls, [7])
        self.assertEqual((outcome.resolved, outcome.voided, outcome.failed), (1, 0, 0))
        [resolution] = self.ledger.named("add_resolution")
        self.assertEqual(resolution.kwargs["outcome"], "void")
        self.assertEqual(resolution.kwargs["attempt"], "answered")
        self.assertEqual(self.ledger.attempts[(7, "resolve")], 1)
        self.assertIn("resolved", self.ledger.event_kinds())

    def test_every_outcome_carries_its_attempt(self) -> None:
        for i, outcome in enumerate(("hit", "miss", "partial", "push", "void"), start=20):
            with self.subTest(outcome=outcome):
                self.ledger.add_due(pending(i, attempts=0))
                ScriptedAgent(resolver=lambda p, o=outcome: self.verdict(o),
                              defer_policy=POLICY).resolve_due()
                [resolution] = [c for c in self.ledger.named("add_resolution")
                                if c.kwargs["commitment_id"] == i]
                self.assertEqual(resolution.kwargs["attempt"], "answered")
                self.assertEqual(self.ledger.attempts[(i, "resolve")], 1)
                self.ledger.due.clear()

    def test_the_stub_really_refuses_an_untried_void(self) -> None:
        # Without this, "accepted" above could be a stub that accepts anything.
        from core import ledger
        self.ledger.add_due(pending(8, attempts=0))
        with self.assertRaisesRegex(ledger.LedgerError, "db/024"):
            ledger.add_resolution(commitment_id=8, outcome="void", leg_outcomes=[], attempt=None)
        self.assertEqual(self.ledger.attempts[(8, "resolve")], 0)

    def test_core_abandonment_asks_nothing_and_invents_no_attempt(self) -> None:
        self.ledger.add_due(pending(9, attempts=4, overdue_by=timedelta(hours=1)))
        outcome = ScriptedAgent(resolver=lambda p: self.fail("asked after exhaustion"),
                                defer_policy=POLICY).resolve_due()
        self.assertEqual(outcome.voided, 1)
        [void] = self.ledger.named("add_resolution")
        self.assertIsNone(void.kwargs["attempt"])
        self.assertEqual(self.ledger.attempts[(9, "resolve")], 4)

    def test_an_answered_attempt_is_never_written_alone(self) -> None:
        from core import ledger
        with self.assertRaises(ledger.LedgerError):
            ledger.record_resolution_attempt(7, "answered")
