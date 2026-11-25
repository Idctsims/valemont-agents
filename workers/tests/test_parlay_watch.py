"""The Parlays page's worker jobs (db/091; docs/parlays_page.md): the leg capture,
one push per hit (never a bust, never an old hit, never twice), the brief's line."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from consensus import parlay_watch as pw
from core import brief, ledger, push, system_jobs

HIT = ledger.ParlayHit(commitment_id=20070, tier="mid", scope="sport", sport="Hockey", legs=9,
                       stake=Decimal("15.83"), paid=Decimal("100.00"), day=date(2026, 11, 23))
CROSS = ledger.ParlayHit(commitment_id=20081, tier="lotto", scope="cross_sport", sport=None, legs=13,
                         stake=Decimal("3.18"), paid=Decimal("100.00"), day=date(2026, 11, 24))


class Message(unittest.TestCase):
    def test_the_multiplier_stake_and_day_link(self):
        m = pw.hit_message(HIT)
        self.assertEqual(m.title, "Parlay hit: Mid ×6.32")
        self.assertEqual(m.body, "Hockey · 9 legs · paid $100.00 on a $15.83 paper stake")
        self.assertEqual(m.deep_link, "/betting/parlays?day=2026-11-23")
        self.assertEqual((m.kind, m.tag), ("parlay_hit", "parlay-hit-20070"))

    def test_a_cross_sport_lotto_says_so(self):
        m = pw.hit_message(CROSS)
        self.assertEqual(m.title, "Parlay hit: Lotto cross-sport ×31.45")
        self.assertTrue(m.body.startswith("Cross-sport · 13 legs"))


class HitPush(unittest.TestCase):
    def test_each_due_hit_is_pushed_then_recorded_once(self):
        sent = []
        result = push.SendResult("owner", 991, "sent", ())
        with mock.patch.object(ledger, "parlay_hits_due", return_value=[HIT, CROSS]) as due, \
                mock.patch.object(ledger, "parlay_hit_pushed") as rec:
            run = pw.hit_push_job(send=lambda m: sent.append(m) or [result])
        due.assert_called_once_with(timedelta(hours=12))
        self.assertEqual([m.tag for m in sent], ["parlay-hit-20070", "parlay-hit-20081"])
        self.assertEqual(rec.call_args_list, [mock.call(20070, 991), mock.call(20081, 991)])
        self.assertEqual(run.job_note, "2 of 2 hits pushed")

    def test_a_failed_send_raises_and_records_nothing(self):
        def boom(_m):
            raise push.PushError("no active push subscriptions")
        with mock.patch.object(ledger, "parlay_hits_due", return_value=[HIT]), \
                mock.patch.object(ledger, "parlay_hit_pushed") as rec, self.assertRaises(push.PushError):
            pw.hit_push_job(send=boom)
        rec.assert_not_called()

    def test_nothing_due_pushes_nothing(self):
        with mock.patch.object(ledger, "parlay_hits_due", return_value=[]), \
                mock.patch.object(ledger, "parlay_hit_pushed") as rec:
            run = pw.hit_push_job(send=lambda m: self.fail("sent"))
        rec.assert_not_called()
        self.assertEqual(run.job_note, "0 of 0 hits pushed")

    def test_the_due_query_is_real_hits_in_the_window_not_yet_pushed(self):
        sql = ledger.PARLAY_HITS_DUE_SQL
        for part in ("r.outcome = 'hit'", "now() - %(window)s", "NOT a.is_test", "c.mode = 'paper'",
                     "NOT EXISTS (SELECT 1 FROM parlay_hit_pushes"):
            self.assertIn(part, sql)


class Capture(unittest.TestCase):
    def test_the_capture_job_calls_the_database_once(self):
        with mock.patch.object(ledger, "parlay_legs_capture", return_value=12) as call:
            self.assertEqual(pw.capture_job(), 12)
        call.assert_called_once_with()


class Registration(unittest.TestCase):
    def test_both_jobs_on_their_intervals(self):
        jobs = {j.name: j for j in system_jobs.build_system_jobs({})}
        self.assertEqual(jobs[pw.CAPTURE_JOB].expected_interval_s, 15 * 60)
        self.assertEqual(jobs[pw.HIT_JOB].expected_interval_s, 5 * 60)


class BriefLine(unittest.TestCase):
    LINES = ["Parlays (paper), yesterday: Safer 6 tickets, Mid 6 tickets.",
             "Settled since yesterday's brief: Safer 2 hit, 5 missed (−$89.22 paper).", "a third line"]

    def test_the_lines_are_the_data_at_most_two(self):
        payload = {"parlays_brief": {"lines": self.LINES}}
        self.assertEqual(brief.template_lines(payload, "parlays_brief", "2026-11-24"), self.LINES[:2])
        self.assertIn("parlays_brief", brief.DATA_LINE_SECTIONS)

    def test_no_data_no_section(self):
        self.assertEqual(brief.template_lines({"parlays_brief": None}, "parlays_brief", "2026-11-24"), [])
        self.assertEqual(brief.template_lines({}, "parlays_brief", "2026-11-24"), [])


if __name__ == "__main__":
    unittest.main()
