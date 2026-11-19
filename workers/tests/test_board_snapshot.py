"""M5's board snapshot job (db/084): the slot from Chicago time, the note, a skip is not a failure."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from consensus import board_snapshot as bs
from core import ledger, system_jobs
from core.jobs import JobSkipped

RESULT = {"snapshot_id": 7, "slot": "am", "slot_date": "2026-11-20", "events": 769, "legs": 12082, "bands": 44,
          "bytes": 924672, "ms": 2061, "board_at": "2026-11-20T15:30:00+00:00", "is_test": False}


class Slot(unittest.TestCase):
    def test_the_slot_is_chicago_time_in_both_offsets(self):
        self.assertEqual(bs.slot_for(datetime(2026, 11, 20, 16, 30, tzinfo=timezone.utc)), "am")   # 10:30 CST
        self.assertEqual(bs.slot_for(datetime(2026, 11, 20, 22, 30, tzinfo=timezone.utc)), "pm")   # 16:30 CST
        self.assertEqual(bs.slot_for(datetime(2026, 7, 20, 15, 30, tzinfo=timezone.utc)), "am")    # 10:30 CDT
        self.assertEqual(bs.slot_for(datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)), "am")    # a late fire keeps its slot


class Job(unittest.TestCase):
    def test_a_snapshot_reports_its_counts_and_bytes(self):
        with mock.patch.object(ledger, "research_board_snapshot", return_value=dict(RESULT)) as call:
            run = bs.snapshot_job(now=lambda: datetime(2026, 11, 20, 16, 30, tzinfo=timezone.utc))
        call.assert_called_once_with("am")
        self.assertEqual(run.job_note, "am 2026-11-20: 12082 legs in 769 events, 44 eligibility rows, 924672 bytes, "
                                       "2061 ms (snapshot 7)")

    def test_a_taken_slot_or_a_finished_window_is_a_skip_not_a_failure(self):
        for skipped in ({"skipped": "taken", "slot": "pm", "slot_date": "2026-11-20"},
                        {"skipped": "window complete", "snapshots": 28}):
            with mock.patch.object(ledger, "research_board_snapshot", return_value=skipped), \
                    self.assertRaises(JobSkipped) as ctx:
                bs.snapshot_job(now=lambda: datetime(2026, 11, 20, 22, 30, tzinfo=timezone.utc))
            self.assertIn(skipped["skipped"], str(ctx.exception))

    def test_the_ledger_refuses_an_unknown_slot_before_any_sql(self):
        with self.assertRaises(ValueError):
            ledger.research_board_snapshot("noon")


class Registration(unittest.TestCase):
    def test_1030_and_1630_chicago_never_at_boot(self):
        job = {j.name: j for j in system_jobs.build_system_jobs({})}[bs.JOB]
        t = job.trigger
        self.assertEqual((str(t.fields[5]), str(t.fields[6]), str(t.timezone)), ("10,16", "30", "America/Chicago"))
        self.assertFalse(job.run_at_boot)
        self.assertEqual(job.misfire_grace, 1800)
        self.assertEqual(job.expected_interval_s, 18 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
