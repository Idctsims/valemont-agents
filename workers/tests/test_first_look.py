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
     `AbandonWithoutAttempt` at zero, before touching the ledger.

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

    def test_close_unavailable_whose_attempt_cannot_be_recorded_stays_due(self) -> None:
        # The attempt must be on record before the tombstone. If that write
        # fails, nothing is tombstoned: the row is asked again next sweep.
        self.ledger.add_capture(closing(3, attempts=0))
        self.ledger.fail_on("record_resolution_attempt", RuntimeError("counter down"))

        def unavailable(p):
            raise CloseUnavailable("ticker delisted")

        outcome = self.agent(unavailable).capture_due()
        self.assertEqual((outcome.missed, outcome.failed), (0, 1))
        self.assertEqual(self.ledger.named("add_closing_snapshot", ok=None), [])
