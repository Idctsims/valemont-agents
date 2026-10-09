"""Goals (db/021) on the worker: the rollover and Monday push jobs, their cron
times, the boot catch-up loop, the push copy, and scripts.goals_rollover.

No database, no network: the ledger fakes come from test_system_jobs, the real
pool raises, and core.push.send is stubbed. The SQL itself (period checks,
idempotency, RLS) is tests_live/test_goals_sql.py.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

from apscheduler.triggers.combining import OrTrigger

from core import ledger, push, system_jobs
from core.jobs import run_tracked
from core.ledger import WeekGoalCounts
from core.orchestrator import Orchestrator
from tests.test_system_jobs import SystemTestCase

CHI = ZoneInfo("America/Chicago")


def jobs() -> dict[str, Any]:
    return {j.name: j for j in system_jobs.build_system_jobs({})}


def next_fire(trigger: Any, after: datetime) -> datetime:
    return trigger.get_next_fire_time(None, after).astimezone(CHI)


# ------------------------------------------------------------------ schedule

class Schedule(SystemTestCase):
    def test_rollover_fires_monday_0001_and_the_first_0001_chicago(self) -> None:
        trigger = jobs()["goals_rollover"].trigger
        self.assertIsInstance(trigger, OrTrigger)
        cases = [
            # Friday -> the coming Monday 00:01
            (datetime(2026, 10, 9, 12, 0, tzinfo=CHI), datetime(2026, 10, 12, 0, 1, tzinfo=CHI)),
            # Wednesday 28 Oct -> Sunday 1 Nov 00:01 (the 1st beats Monday 2 Nov)
            (datetime(2026, 10, 28, 9, 0, tzinfo=CHI), datetime(2026, 11, 1, 0, 1, tzinfo=CHI)),
            # Monday 00:00:30 -> 00:01 the same Monday
            (datetime(2026, 10, 12, 0, 0, 30, tzinfo=CHI), datetime(2026, 10, 12, 0, 1, tzinfo=CHI)),
        ]
        for after, expected in cases:
            with self.subTest(after=after.isoformat()):
                self.assertEqual(next_fire(trigger, after), expected)

    def test_rollover_is_local_midnight_across_dst(self) -> None:
        # DST ends 1 Nov 2026: Monday 2 Nov 00:01 CST is 06:01 UTC, not 05:01.
        trigger = jobs()["goals_rollover"].trigger
        fire = trigger.get_next_fire_time(None, datetime(2026, 11, 1, 12, 0, tzinfo=CHI))
        self.assertEqual(fire.astimezone(timezone.utc), datetime(2026, 11, 2, 6, 1, tzinfo=timezone.utc))

    def test_monday_push_fires_monday_0700_chicago(self) -> None:
        trigger = jobs()["goals_monday_push"].trigger
        self.assertEqual(next_fire(trigger, datetime(2026, 10, 9, 12, 0, tzinfo=CHI)),
                         datetime(2026, 10, 12, 7, 0, tzinfo=CHI))
        # Monday 07:30 -> next Monday, not the same day
        self.assertEqual(next_fire(trigger, datetime(2026, 10, 12, 7, 30, tzinfo=CHI)),
                         datetime(2026, 10, 19, 7, 0, tzinfo=CHI))

    def test_rollover_runs_at_boot_and_the_push_never_does(self) -> None:
        built = jobs()
        self.assertTrue(built["goals_rollover"].run_at_boot)
        self.assertEqual(built["goals_rollover"].start_delay_s, 0)
        self.assertFalse(built["goals_monday_push"].run_at_boot)
        for name in ("goals_rollover", "goals_monday_push"):
            self.assertEqual(built[name].expected_interval_s, 7 * 24 * 3600)

    def test_wiring_runs_rollover_now_and_waits_for_monday_to_push(self) -> None:
        orchestrator = Orchestrator()
        for job in system_jobs.build_system_jobs({}):
            orchestrator.register_system_job(job)
        # Paused, so nothing fires while the next run times are read.
        orchestrator._scheduler.start(paused=True)
        self.addCleanup(orchestrator._scheduler.shutdown, False)
        before = datetime.now(timezone.utc)
        orchestrator._wire_system()
        rollover = orchestrator._scheduler.get_job("system:goals_rollover").next_run_time
        monday = orchestrator._scheduler.get_job("system:goals_monday_push").next_run_time
        self.assertLess(abs(rollover - before), timedelta(seconds=5))
        expected = jobs()["goals_monday_push"].trigger.get_next_fire_time(None, before)
        self.assertEqual(monday, expected)
        local = monday.astimezone(CHI)
        self.assertEqual((local.isoweekday(), local.hour, local.minute), (1, 7, 0))


# ------------------------------------------------------------------ rollover

class FakePeriods:
    """Ended periods with uncarried open goals, per horizon. Carrying one can
    make the next one due, as the real SQL does when the next period has also
    ended."""

    def __init__(self, due: dict[str, list[date]], ended_through: date,
                 carried: int = 2) -> None:
        self.due = {h: sorted(d) for h, d in due.items()}
        self.ended_through = ended_through
        self.carried = carried
        self.calls: list[tuple[str, date]] = []

    def goal_periods_to_roll(self, horizon: str) -> list[date]:
        return list(self.due.get(horizon, []))

    def carry_over_goals(self, horizon: str, from_date: date) -> int:
        self.calls.append((horizon, from_date))
        periods = self.due[horizon]
        periods.remove(from_date)
        step = timedelta(days=7) if horizon == "weekly" else None
        nxt = from_date + step if step else (from_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        following = nxt + step if step else (nxt.replace(day=28) + timedelta(days=4)).replace(day=1)
        if following <= self.ended_through and nxt not in periods:
            periods.append(nxt)
            periods.sort()
        return self.carried


class Rollover(SystemTestCase):
    def use(self, fake: Any) -> None:
        for name in ("goal_periods_to_roll", "carry_over_goals"):
            p = mock.patch.object(ledger, name, getattr(fake, name))
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_due_carries_nothing(self) -> None:
        fake = FakePeriods({}, ended_through=date(2026, 10, 12))
        self.use(fake)
        self.assertEqual(system_jobs.roll_over_goals(), 0)
        self.assertEqual(fake.calls, [])

    def test_last_week_is_carried(self) -> None:
        # Monday 12 Oct: the week of 5 Oct has ended, 12 Oct has not.
        fake = FakePeriods({"weekly": [date(2026, 10, 5)]}, ended_through=date(2026, 10, 12))
        self.use(fake)
        self.assertEqual(system_jobs.roll_over_goals(), 2)
        self.assertEqual(fake.calls, [("weekly", date(2026, 10, 5))])

    def test_boot_after_two_missed_mondays_catches_up_oldest_first(self) -> None:
        # Down since before 28 Sep; booting on 13 Oct. The week of 28 Sep is
        # due; carrying it makes 5 Oct due (that week ended too); 12 Oct has not.
        fake = FakePeriods({"weekly": [date(2026, 9, 28)]}, ended_through=date(2026, 10, 13))
        self.use(fake)
        self.assertEqual(system_jobs.roll_over_goals(), 4)
        self.assertEqual(fake.calls, [("weekly", date(2026, 9, 28)), ("weekly", date(2026, 10, 5))])

    def test_both_horizons_on_the_first(self) -> None:
        fake = FakePeriods({"weekly": [date(2026, 9, 21)], "monthly": [date(2026, 9, 1)]},
                           ended_through=date(2026, 10, 1))
        self.use(fake)
        system_jobs.roll_over_goals()
        self.assertEqual(fake.calls, [("weekly", date(2026, 9, 21)), ("monthly", date(2026, 9, 1))])

    def test_a_second_run_is_a_no_op(self) -> None:
        fake = FakePeriods({"weekly": [date(2026, 10, 5)]}, ended_through=date(2026, 10, 12))
        self.use(fake)
        system_jobs.roll_over_goals()
        self.assertEqual(system_jobs.roll_over_goals(), 0)
        self.assertEqual(len(fake.calls), 1)

    def test_a_due_period_that_carries_nothing_fails_loudly(self) -> None:
        fake = FakePeriods({"weekly": [date(2026, 10, 5)]}, ended_through=date(2026, 10, 12), carried=0)
        self.use(fake)
        with self.assertRaisesRegex(RuntimeError, "listed as due but nothing carried"):
            system_jobs.roll_over_goals()

    def test_a_due_list_that_never_shrinks_is_bounded(self) -> None:
        stuck = mock.Mock(goal_periods_to_roll=lambda h: [date(2026, 10, 5)] if h == "weekly" else [],
                          carry_over_goals=lambda h, d: 1)
        self.use(stuck)
        with self.assertRaisesRegex(RuntimeError, "still has periods due"):
            system_jobs.roll_over_goals()

    def test_the_job_lands_in_job_health(self) -> None:
        self.use(FakePeriods({}, ended_through=date(2026, 10, 12)))
        run_tracked(jobs()["goals_rollover"])
        self.assertEqual(self.ledger.names(), ["job_started", "job_succeeded"])

    def test_a_missing_migration_is_a_recorded_failure(self) -> None:
        def missing(horizon: str) -> list[date]:
            raise RuntimeError('function goal_periods_to_roll(unknown) does not exist')
        p = mock.patch.object(ledger, "goal_periods_to_roll", missing)
        p.start()
        self.addCleanup(p.stop)
        with self.assertRaises(RuntimeError):
            run_tracked(jobs()["goals_rollover"])
        self.assertEqual(self.ledger.names(), ["job_started", "job_failed"])


# ----------------------------------------------------------------- the push

class MondayPush(SystemTestCase):
    def counts(self, open: int, done: int = 0, carried_in: int = 0) -> None:
        p = mock.patch.object(ledger, "this_week_goal_counts",
                              lambda: WeekGoalCounts(open=open, done=done, carried_in=carried_in))
        p.start()
        self.addCleanup(p.stop)

    def test_copy(self) -> None:
        self.counts(open=3, carried_in=3)
        system_jobs.goals_monday_push()
        (msg,) = self.pushes
        self.assertEqual((msg.kind, msg.title, msg.deep_link, msg.tag),
                         ("goals_monday", "Set your week", "/goals", "goals-week"))
        self.assertEqual(msg.body, "3 carried in. 7 slots left.")

    def test_copy_singular_and_empty(self) -> None:
        for (open_, done, carried), body in {
            (0, 0, 0): "0 carried in. 10 slots left.",
            (9, 0, 2): "2 carried in. 1 slot left.",
            (8, 2, 1): "1 carried in. 0 slots left.",
        }.items():
            with self.subTest(body=body):
                msg = system_jobs.monday_message(WeekGoalCounts(open=open_, done=done, carried_in=carried))
                self.assertEqual(msg.body, body)

    def test_held_is_open_plus_done(self) -> None:
        # The seeded week: 8 open (1 carried in) + 2 done. Dropped and
        # moved-on goals are excluded by the query, so they never reach here.
        counts = WeekGoalCounts(open=8, done=2, carried_in=1)
        self.assertEqual(counts.held, 10)
        self.assertEqual(system_jobs.monday_message(counts).body, "1 carried in. 0 slots left.")

    def test_over_the_cap_never_goes_negative(self) -> None:
        msg = system_jobs.monday_message(WeekGoalCounts(open=12, done=0, carried_in=12))
        self.assertEqual(msg.body, "12 carried in. 0 slots left.")

    def test_rolls_over_before_counting(self) -> None:
        order: list[str] = []
        with mock.patch.object(system_jobs, "roll_over_goals", lambda: order.append("roll") or 0), \
                mock.patch.object(ledger, "this_week_goal_counts",
                                  lambda: order.append("count") or WeekGoalCounts(0, 0, 0)):
            system_jobs.goals_monday_push()
        self.assertEqual(order, ["roll", "count"])
        self.assertEqual(len(self.pushes), 1)

    def test_no_device_is_a_recorded_failure(self) -> None:
        self.counts(open=0)
        self.push_error = push.PushError("no active push subscriptions")
        with self.assertRaises(push.PushError):
            run_tracked(jobs()["goals_monday_push"])
        self.assertEqual(self.ledger.names(), ["job_started", "job_failed"])


# ------------------------------------------------------------------- script

class Script(SystemTestCase):
    def test_prints_the_count_only(self) -> None:
        from scripts import goals_rollover

        seen: list[tuple[str, date]] = []
        with mock.patch.object(goals_rollover, "load_env"), \
                mock.patch.object(ledger, "close_pool"), \
                mock.patch.object(ledger, "carry_over_goals",
                                  lambda h, d: seen.append((h, d)) or 1):
            out = io.StringIO()
            with redirect_stdout(out):
                code = goals_rollover.main(["--horizon", "weekly", "--from", "2026-09-28"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "1\n")
        self.assertEqual(seen, [("weekly", date(2026, 9, 28))])

    def test_refuses_long_term_and_bad_dates(self) -> None:
        from scripts import goals_rollover

        for argv in (["--horizon", "long_term", "--from", "2026-09-28"],
                     ["--horizon", "weekly", "--from", "28/09/2026"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                goals_rollover.main(argv)
