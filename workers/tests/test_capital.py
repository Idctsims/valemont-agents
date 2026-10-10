"""Capital snapshots (db/026): scheduling, the backfill range, and the
paper/live split.

No database: the four ledger calls the job makes are faked. The SQL itself
(capital_value_as_of, the insert, the guards, the view) is tested live in
tests_live/test_capital_sql.py.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

from core import ledger, system_jobs
from core.jobs import run_tracked

TODAY = date(2026, 10, 9)
CHICAGO = ZoneInfo("America/Chicago")


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("a capital test reached the real database pool")


@dataclass
class FakeCapital:
    """Modes with data and their first local day; records every write."""

    first: dict[str, date] = field(default_factory=dict)
    today: date = TODAY
    writes: list[tuple[str, date, date]] = field(default_factory=list)
    health: list[tuple[str, str]] = field(default_factory=list)

    def capital_local_today(self) -> date:
        return self.today

    def capital_first_day(self, mode: str) -> date | None:
        return self.first.get(mode)

    def write_capital_snapshots(self, mode: str, first: date, last: date) -> int:
        self.writes.append((mode, first, last))
        return (last - first).days + 1

    def job_started(self, job: str) -> None:
        self.health.append(("started", job))

    def job_succeeded(self, job: str) -> None:
        self.health.append(("ok", job))

    def job_failed(self, job: str, error: str) -> None:
        self.health.append(("failed", job))


class CapitalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeCapital()
        for name in ("capital_local_today", "capital_first_day", "write_capital_snapshots",
                     "job_started", "job_succeeded", "job_failed"):
            p = mock.patch.object(ledger, name, getattr(self.fake, name))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ledger, "_pool", _refuse_database)
        p.start()
        self.addCleanup(p.stop)


class Scheduling(unittest.TestCase):
    def job(self):
        [job] = [j for j in system_jobs.build_system_jobs({}) if j.name == "capital_snapshot"]
        return job

    def test_daily_at_0005_chicago_and_at_boot(self) -> None:
        job = self.job()
        self.assertEqual(job.expected_interval_s, 24 * 60 * 60)
        self.assertEqual(str(job.trigger.timezone), "America/Chicago")
        self.assertTrue(job.run_at_boot, "the boot run is the backfill")
        self.assertIs(job.fn, system_jobs.snapshot_capital)

    def test_it_fires_at_0005_local_across_the_dst_change(self) -> None:
        trigger = self.job().trigger
        # The night before, and the night of, the 2026-11-01 fall-back.
        for start, expected in (
            (datetime(2026, 10, 9, 12, 0, tzinfo=CHICAGO), datetime(2026, 10, 10, 0, 5, tzinfo=CHICAGO)),
            (datetime(2026, 10, 31, 12, 0, tzinfo=CHICAGO), datetime(2026, 11, 1, 0, 5, tzinfo=CHICAGO)),
            (datetime(2026, 11, 1, 12, 0, tzinfo=CHICAGO), datetime(2026, 11, 2, 0, 5, tzinfo=CHICAGO)),
        ):
            with self.subTest(start=start):
                fire = trigger.get_next_fire_time(None, start)
                self.assertEqual(fire.astimezone(timezone.utc), expected.astimezone(timezone.utc))
                local = fire.astimezone(CHICAGO)
                self.assertEqual((local.hour, local.minute), (0, 5))


class Span(unittest.TestCase):
    def test_first_day_through_yesterday(self) -> None:
        self.assertEqual(system_jobs.capital_span(date(2026, 10, 1), TODAY),
                         (date(2026, 10, 1), date(2026, 10, 8)))

    def test_yesterday_alone_when_the_data_began_yesterday(self) -> None:
        y = TODAY - timedelta(days=1)
        self.assertEqual(system_jobs.capital_span(y, TODAY), (y, y))

    def test_nothing_while_the_first_day_has_not_ended(self) -> None:
        self.assertIsNone(system_jobs.capital_span(TODAY, TODAY))

    def test_never_today_or_later(self) -> None:
        for first in (date(2025, 1, 1), date(2026, 10, 8)):
            _, last = system_jobs.capital_span(first, TODAY)
            self.assertLess(last, TODAY)


class Snapshot(CapitalTestCase):
    def test_a_mode_with_no_data_writes_nothing(self) -> None:
        self.assertEqual(system_jobs.snapshot_capital(), {})
        self.assertEqual(self.fake.writes, [])

    def test_backfill_covers_every_ended_day_since_the_first_entry(self) -> None:
        self.fake.first = {"paper": date(2026, 9, 30)}
        written = system_jobs.snapshot_capital()
        self.assertEqual(self.fake.writes, [("paper", date(2026, 9, 30), date(2026, 10, 8))])
        self.assertEqual(written, {"paper": 9})

    def test_paper_only_today_never_touches_live(self) -> None:
        self.fake.first = {"paper": date(2026, 10, 9)}
        self.assertEqual(system_jobs.snapshot_capital(), {"paper": 0})
        self.assertEqual(self.fake.writes, [])
        self.assertNotIn("live", system_jobs.snapshot_capital())

    def test_paper_and_live_are_written_separately_with_their_own_ranges(self) -> None:
        self.fake.first = {"paper": date(2026, 9, 1), "live": date(2026, 10, 7)}
        written = system_jobs.snapshot_capital()
        self.assertEqual(self.fake.writes, [
            ("paper", date(2026, 9, 1), date(2026, 10, 8)),
            ("live", date(2026, 10, 7), date(2026, 10, 8)),
        ])
        # Reported per mode; there is no combined figure to report.
        self.assertEqual(written, {"paper": 38, "live": 2})

    def test_runs_tracked_so_it_shows_in_job_health(self) -> None:
        [job] = [j for j in system_jobs.build_system_jobs({}) if j.name == "capital_snapshot"]
        self.fake.first = {"paper": date(2026, 10, 1)}
        run_tracked(job)
        self.assertEqual(self.fake.health, [("started", "capital_snapshot"), ("ok", "capital_snapshot")])

    def test_a_failed_write_is_recorded_and_raised(self) -> None:
        [job] = [j for j in system_jobs.build_system_jobs({}) if j.name == "capital_snapshot"]
        self.fake.first = {"paper": date(2026, 10, 1)}

        def boom(*a: Any) -> int:
            raise ConnectionError("database unreachable")

        with mock.patch.object(ledger, "write_capital_snapshots", boom):
            with self.assertRaises(ConnectionError):
                run_tracked(job)
        self.assertEqual(self.fake.health[-1], ("failed", "capital_snapshot"))


class LedgerGuards(unittest.TestCase):
    def test_an_unknown_mode_or_empty_range_is_refused_before_the_database(self) -> None:
        with mock.patch.object(ledger, "_pool", _refuse_database):
            with self.assertRaises(ledger.LedgerError):
                ledger.write_capital_snapshots("blended", date(2026, 10, 1), date(2026, 10, 2))  # type: ignore[arg-type]
            with self.assertRaises(ledger.LedgerError):
                ledger.write_capital_snapshots("paper", date(2026, 10, 2), date(2026, 10, 1))

    def test_the_modes_are_exactly_paper_and_live(self) -> None:
        self.assertEqual(ledger.CAPITAL_MODES, ("paper", "live"))


if __name__ == "__main__":
    unittest.main()
