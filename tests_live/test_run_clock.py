"""db/016: a run stores the clock offset measured for its tick. Skips until pasted."""

from __future__ import annotations

import unittest

from core import ledger

from .support import LiveLedgerTestCase, tearDownModule  # noqa: F401


class RunClock(LiveLedgerTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        import os
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'runs' AND column_name = 'clock_offset_ms'")
            if cur.fetchone() is None:
                raise unittest.SkipTest("db/016 not pasted yet")

    def test_the_measured_offset_lands_on_the_run(self) -> None:
        sample = ledger.measure_clock()
        run_id = ledger.start_run(self.agent_id, notes="tests_live clock", clock=sample)
        ledger.end_run(run_id, "ok")
        offset, rtt = (self.scalar("SELECT clock_offset_ms FROM runs WHERE id = %s", run_id),
                       self.scalar("SELECT clock_rtt_ms FROM runs WHERE id = %s", run_id))
        self.assertEqual((offset, rtt), (sample.offset_ms, sample.rtt_ms))

    def test_the_offset_is_bounded_by_the_round_trip_sanity(self) -> None:
        sample = ledger.measure_clock()
        self.assertGreaterEqual(sample.rtt_ms, 0)
        self.assertLess(abs(sample.offset_ms), 60_000, "a minute of skew is a broken clock, not a measurement")


if __name__ == "__main__":
    unittest.main()
