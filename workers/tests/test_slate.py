"""Slates — isolation, exact-duplicate refusal, near-duplicates allowed, the cap."""

from __future__ import annotations

import unittest
from decimal import Decimal as D

from core.ledger import Leg
from tests.support import LedgerTestCase, ScriptedAgent, proposal


class Isolation(LedgerTestCase):

    def test_one_bad_proposal_does_not_strand_the_rest(self) -> None:
        slate = [proposal("A"), proposal("B", thesis="bad"), proposal("C")]
        self.ledger.fail_on("commit", RuntimeError("check violated"),
                            when=lambda call: call.kwargs["thesis"] == "bad")
        outcome = ScriptedAgent(proposals=slate).run_once()

        self.assertEqual(len(outcome.commitment_ids), 2)
        self.assertEqual(outcome.failed, 1)
        self.assertEqual(outcome.status, "error")
        self.assertIsNone(outcome.commitment_id)
        written = [c.kwargs["thesis"] for c in self.ledger.named("commit")]
        self.assertEqual(len(written), 2)
        self.assertNotIn("bad", written)

        [(kind, detail)] = [e for e in self.ledger.events() if e[0] == "slate_committed"]
        self.assertEqual((detail["committed"], detail["proposed"], detail["failed"]), (2, 3, 1))
        self.assertEqual(detail["commitment_ids"], list(outcome.commitment_ids))
        self.assertIn("error", self.ledger.event_kinds())
        self.assertNotIn("committed", self.ledger.event_kinds())

    def test_every_proposal_failing_emits_no_commit_event(self) -> None:
        self.ledger.fail_on("commit", RuntimeError("db down"))
        outcome = ScriptedAgent(proposals=[proposal("A"), proposal("B")]).run_once()
        self.assertEqual((outcome.commitment_ids, outcome.failed), ((), 2))
        kinds = self.ledger.event_kinds()
        self.assertNotIn("committed", kinds)
        self.assertNotIn("slate_committed", kinds)

    def test_none_and_empty_are_both_idle(self) -> None:
        for empty in (None, [], ()):
            with self.subTest(empty=empty):
                self.ledger.calls.clear()
                outcome = ScriptedAgent(proposals=empty).run_once()
                self.assertEqual(outcome.status, "ok")
                self.assertEqual(self.ledger.named("commit", ok=None), [])
                self.assertIn("idle", self.ledger.event_kinds())


class ExactDuplicates(LedgerTestCase):

    def assert_refused_whole(self, slate) -> None:
        outcome = ScriptedAgent(proposals=slate).run_once()
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.commitment_ids, ())
        self.assertEqual(self.ledger.named("commit", ok=None), [],
                         "a refused slate must not attempt a single write")
        self.assertIn("error", self.ledger.event_kinds())

    def test_same_leg_in_two_proposals_is_refused(self) -> None:
        self.assert_refused_whole([proposal("A"), proposal("B"), proposal("A")])

    def test_same_leg_twice_inside_one_proposal_is_refused(self) -> None:
        dup = Leg("A", "points_over", D("25.5"), "over", D(1))
        self.assert_refused_whole([proposal("A", extra_legs=[dup])])

    def test_float_and_decimal_spellings_of_one_line_are_the_same_leg(self) -> None:
        self.assert_refused_whole([proposal("A", line=25.5), proposal("A", line=D("25.5"))])

    def test_trailing_zero_spelling_is_the_same_leg(self) -> None:
        # Was an expectedFailure: identity compared str(Decimal), which keeps
        # trailing zeros, so these wrote two permanent rows for one position.
        self.assert_refused_whole([proposal("A", line=D("25.5")), proposal("A", line=D("25.50"))])

    def test_many_spellings_of_one_line_all_collapse(self) -> None:
        """Every spelling of the same number is the same leg.

        The scale-preserving ones (25.50, 2.55E+1) are what the text comparison
        missed; int-vs-Decimal is the same question one step further out.
        """
        for label, line in [
            ("trailing zeros", D("25.500")),
            ("exponent form", D("2.55E+1")),
            ("float", 25.5),
            ("string", "25.5"),
        ]:
            with self.subTest(spelling=label):
                self.assert_refused_whole(
                    [proposal("A", line=D("25.5")), proposal("A", line=line)]
                )

    def test_integer_line_spellings_collapse(self) -> None:
        for line in (D("25"), D("25.0"), D("25.00"), 25, "25"):
            with self.subTest(spelling=repr(line)):
                self.assert_refused_whole(
                    [proposal("A", line=D("25")), proposal("A", line=line)]
                )

    def test_genuinely_different_lines_still_pass(self) -> None:
        """The fix must not over-collapse: 25.5 and 25.51 are different bets."""
        outcome = ScriptedAgent(
            proposals=[proposal("A", line=D("25.50")), proposal("A", line=D("25.51"))]
        ).run_once()
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(len(outcome.commitment_ids), 2)


class NearDuplicatesAllowed(LedgerTestCase):
    """Core refuses exact duplicates only. These four are legitimate slates."""

    def assert_all_written(self, slate) -> None:
        outcome = ScriptedAgent(proposals=slate).run_once()
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(len(outcome.commitment_ids), len(slate))
        self.assertEqual(outcome.failed, 0)

    def test_same_subject_different_market(self) -> None:
        self.assert_all_written([
            proposal("QB", "passing_yards_over", D("249.5")),
            proposal("QB", "passing_tds_over", D("1.5")),
        ])

    def test_same_market_different_line_ladder(self) -> None:
        self.assert_all_written([
            proposal("QB", "passing_yards_over", D("249.5")),
            proposal("QB", "passing_yards_over", D("274.5")),
        ])

    def test_same_line_opposite_direction(self) -> None:
        self.assert_all_written([
            proposal("QB", "passing_yards", D("249.5"), "over"),
            proposal("QB", "passing_yards", D("249.5"), "under"),
        ])

    def test_different_subject_identical_market_line_direction(self) -> None:
        self.assert_all_written([
            proposal("QB1", "passing_yards_over", D("249.5")),
            proposal("QB2", "passing_yards_over", D("249.5")),
        ])


class SlateCap(LedgerTestCase):

    def test_over_the_cap_writes_zero_rows(self) -> None:
        slate = [proposal(f"P{i}") for i in range(4)]
        outcome = ScriptedAgent(proposals=slate, max_slate_size=3).run_once()

        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.commitment_ids, ())
        self.assertEqual(self.ledger.named("commit", ok=None), [])
        self.assertNotIn("slate_committed", self.ledger.event_kinds())
        [end] = self.ledger.named("end_run")
        self.assertEqual(end.args[1], "error")
        self.assertIn("max_slate_size", end.kwargs["error"])

    def test_exactly_at_the_cap_is_written(self) -> None:
        slate = [proposal(f"P{i}") for i in range(3)]
        outcome = ScriptedAgent(proposals=slate, max_slate_size=3).run_once()
        self.assertEqual(len(outcome.commitment_ids), 3)

    def test_default_cap_is_twenty_five(self) -> None:
        self.assertEqual(ScriptedAgent().slate_cap, 25)
        slate = [proposal(f"P{i}") for i in range(26)]
        outcome = ScriptedAgent(proposals=slate).run_once()
        self.assertEqual(self.ledger.named("commit", ok=None), [])
        self.assertEqual(outcome.commitment_ids, ())


if __name__ == "__main__":
    unittest.main()
