"""The Arsenal, Phase 2 (docs/arsenal.md §9; db/059): the worker's side.

* VERIFY ON SAVE (arsenal/arm.py): a saved Arm Me card goes through the
  pool's own verifier, then the outcome check (controls first, the card's
  vote on the record); every step done is skipped on a retry; a failed
  control records nothing; a test card makes no AI call; anything that is
  not an Arm Me card is left alone. The handler is registered and watched.
* THE QUIZ LINE: the brief's one Arsenal line ends "Quiz ready: N on your
  saved Arsenal." when ctx_arsenal_brief carries it (Mondays, at least 4
  saved: arsenal_quiz_line), alone on a Monday with no drop, and nothing
  changes on any other day.
* WEIGHTS WITH SAVES: db/059's arsenal_weights counts a save as two Loves
  (+0.30), real and current saves only, with db/054's signature; the quiz
  line's rule is the Monday plus 4 saved; the pool's never-repeat list reads
  pool keys only. tests_live/test_arsenal_phase2_sql.py runs the SQL itself.

No database, no network, no model: every ledger call is patched.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from arsenal import arm, outcome, sources, verify
from core import brief, ledger, system_jobs
from tests.test_arsenal import ROOT, NoDatabase, controls_ask, ref

MIGRATION_059 = ROOT / "db" / "059_arsenal_phase2.sql"


def state(**kw: Any) -> ledger.ArsenalArmState:
    base = dict(status="arm", verification="pending", is_test=False, checked=False, held=False)
    base.update(kw)
    return ledger.ArsenalArmState(**base)


class VerifyOnSave(NoDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.recorded: list[tuple] = []
        self.verified: list[int] = []
        self.patch("arsenal_record_outcome", lambda i, **kw: self.recorded.append(
            (i, kw["conflict"], kw["votes"], kw["controls"], kw["source"])) or (9, 70 if kw["conflict"] else None))
        self.patch("arsenal_ref_to_verify", lambda i: ref(id=i, title="See's Candies", verification="verified"))
        page = verify.Candidate("wikipedia", "Some page", "https://x.invalid", sources.Excerpt("x", 1, ("",)), ())
        p = mock.patch.object(verify, "candidates", lambda r, http=None: ([page], []))
        p.start()
        self.addCleanup(p.stop)

    def states(self, *seq: ledger.ArsenalArmState) -> None:
        it = iter(seq)
        self.patch("arsenal_arm_state", lambda i: next(it))

    def verify_item(self, result: str):
        def run(target: str, item_id: int, **kw: Any) -> str:
            self.assertEqual(target, "ref")
            self.verified.append(item_id)
            return result
        return mock.patch.object(verify, "verify_item", run)

    def test_pending_is_verified_then_outcome_checked_controls_first(self) -> None:
        self.states(state(), state(verification="corrected"))
        seen: list[str] = []
        base = controls_ask()
        with self.verify_item("corrected"):
            note = arm.verify_saved(711, ask_outcome=lambda c, e: seen.append(c.title) or base(c, e))
        self.assertEqual(self.verified, [711])
        self.assertEqual(seen[:9], ["Thermopylae"] * 3 + ["Custer's Last Stand"] * 3 + ["Miracle on Ice"] * 3)
        self.assertEqual(seen[9:], ["See's Candies"] * 3)
        self.assertEqual(self.recorded, [(711, False, "nnn", "3/3", "worker")])
        self.assertEqual(note, "verify corrected; outcome ok (nnn; controls 3/3)")

    def test_a_conflict_is_recorded_and_held(self) -> None:
        self.states(state(verification="verified"))
        note = arm.verify_saved(711, ask_outcome=controls_ask("See's Candies"))
        self.assertEqual(self.recorded, [(711, True, "YYY", "3/3", "worker")])
        self.assertEqual(note, "outcome conflict, held (YYY; controls 3/3)")

    def test_unverified_is_never_outcome_checked(self) -> None:
        self.states(state(), state(verification="unverified"))
        with self.verify_item("unverified"):
            note = arm.verify_saved(711, ask_outcome=lambda c, e: self.fail("no outcome read for an unverified card"))
        self.assertEqual(note, "verify unverified; unverified: no outcome check")
        self.assertEqual(self.recorded, [])

    def test_a_retry_skips_what_is_done(self) -> None:
        self.states(state(verification="verified", checked=True))
        with self.verify_item("never"):
            note = arm.verify_saved(711, ask_outcome=lambda c, e: self.fail("checked already"))
        self.assertEqual((note, self.verified, self.recorded), ("outcome check already on record", [], []))

    def test_a_failed_control_records_nothing_and_raises_for_the_queue(self) -> None:
        self.states(state(verification="verified"))
        with self.assertRaises(outcome.ControlsFailed):
            arm.verify_saved(711, ask_outcome=lambda c, e: (False, "x"))
        self.assertEqual(self.recorded, [])

    def test_test_cards_and_non_arm_rows_make_no_call(self) -> None:
        for st, expected in ((state(is_test=True), "skipped: test reference"),
                             (state(status="pool", verification="verified"), "skipped: a pool reference, not an Arm Me card"),
                             (None, "skipped: no such reference")):
            with self.subTest(expected=expected):
                self.patch("arsenal_arm_state", lambda i, st=st: st)
                with self.verify_item("never"):
                    self.assertEqual(arm.verify_saved(5, ask_outcome=lambda c, e: self.fail("no AI")), expected)
                self.assertEqual(self.verified, [])

    def test_the_job_reads_its_ref_id(self) -> None:
        self.states(state(is_test=True))
        self.assertEqual(arm.arm_verify_job({"ref_id": 42}).job_note, "ref 42: skipped: test reference")
        with self.assertRaises(ValueError):
            arm.arm_verify_job({"word_id": 3})

    def test_registered_on_the_queue_and_watched_for_failures(self) -> None:
        self.assertEqual(ledger.ARSENAL_ARM_VERIFY_KIND, "arsenal_arm_verify")
        self.assertIn(ledger.ARSENAL_ARM_VERIFY_KIND, system_jobs.QUEUE_HANDLERS)
        job = system_jobs.ARSENAL_ARM_VERIFY
        self.assertFalse(job.scheduled)
        self.assertGreaterEqual(job.expected_interval_s, 365 * 24 * 60 * 60)  # saves are sporadic
        names = [j.name for j in system_jobs.build_system_jobs({})]
        self.assertIn("arsenal_arm_verify", names)
        # The kind the trigger queues is the kind the worker handles.
        self.assertIn("'arsenal_arm_verify'", MIGRATION_059.read_text(encoding="utf-8"))


class QuizLine(unittest.TestCase):
    def test_monday_with_a_drop_ends_with_the_quiz(self) -> None:
        line = brief._arsenal_lines({"power_word": "incumbent", "ref": {"title": "See's Candies", "room": "boardroom"},
                                     "quiz": {"questions": 7}})
        self.assertEqual(line, ["Power Word: incumbent. Strongest reference: See's Candies, for the boardroom. "
                                "Quiz ready: 7 on your saved Arsenal."])

    def test_a_monday_with_no_drop_is_the_quiz_alone(self) -> None:
        self.assertEqual(brief._arsenal_lines({"quiz": {"questions": 10}}), ["Quiz ready: 10 on your saved Arsenal."])

    def test_any_other_day_is_unchanged(self) -> None:
        self.assertEqual(brief._arsenal_lines({"power_word": "incumbent"}), ["Power Word: incumbent."])
        self.assertEqual(brief._arsenal_lines({"power_word": "incumbent", "quiz": {"questions": 0}}),
                         ["Power Word: incumbent."])
        self.assertEqual(brief._arsenal_lines({}), [])

    def test_the_line_is_data_in_the_ai_brief_too(self) -> None:
        self.assertIn("arsenal_brief", brief.DATA_LINE_SECTIONS)
        self.assertIn("arsenal_brief", brief.ONE_LINE_SECTIONS)

    def test_the_rule_is_a_monday_and_four_saved(self) -> None:
        sql = MIGRATION_059.read_text(encoding="utf-8")
        self.assertIn("WHERE extract(isodow FROM p_day) = 1 AND c.refs + c.words >= 4", sql)
        self.assertIn("least(10, 2 * c.refs + c.words)", sql)
        self.assertIn("public.arsenal_quiz_line(public.owner_today(), false)", sql)


class WeightsWithSaves(unittest.TestCase):
    def test_a_save_is_two_loves_real_and_current(self) -> None:
        sql = MIGRATION_059.read_text(encoding="utf-8")
        self.assertIn("+ 0.30 * count(*) FILTER (WHERE t.reaction = 'save')", sql)
        self.assertIn("FROM public.v_arsenal_saved s WHERE NOT s.is_test", sql)
        # db/054's signature: nothing that reads the weights changes.
        self.assertIn("RETURNS TABLE (dim text, key text, base real, loves integer, not_interested integer, weight real)",
                      sql)

    def test_the_worker_reads_the_weights_by_name(self) -> None:
        with mock.patch.object(ledger, "_brief_conn") as conn:
            conn.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
                ("category", "film_tv", 1.3), ("room", "pitch", 1.0)]
            self.assertEqual(ledger.arsenal_weights(), {("category", "film_tv"): 1.3, ("room", "pitch"): 1.0})

    def test_the_pool_never_repeat_list_reads_pool_keys_only(self) -> None:
        with mock.patch.object(ledger, "_brief_conn") as conn:
            execute = conn.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [("a",)]
            self.assertEqual(ledger.arsenal_recent_keys(5), ["a"])
        self.assertIn("origin_kind = 'pool'", execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
