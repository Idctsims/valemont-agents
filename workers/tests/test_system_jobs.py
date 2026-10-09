"""System jobs: the tracked wrapper, health monitor, db size alerts, the job
queue, the registry, and main.py's new "system jobs only" mode.

No database, no network: every ledger function used is replaced by an
in-memory fake, the real pool raises, and core.push.send is stubbed.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest import mock

from core import jobs, ledger, push, system_jobs
from core.jobs import SystemJob, describe, run_tracked
from core.ledger import JobHealth, QueuedJob
from core.orchestrator import Orchestrator


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("a system-job test reached the real database pool")


def row(job: str, *, failures: int = 0, stale: bool = False, state: str = "ok",
        error: str | None = None, interval: int = 60) -> JobHealth:
    return JobHealth(job=job, expected_interval_s=interval, last_ok_at=None, last_error=error,
                     consecutive_failures=failures, alert_state=state, stale=stale)


@dataclass
class FakeLedger:
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    health: list[JobHealth] = field(default_factory=list)
    size: tuple[int, list[tuple[str, int]]] = (0, [])
    sent_kinds: set[str] = field(default_factory=set)
    queue: list[QueuedJob] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def _rec(self, name: str, *args: Any) -> None:
        if name in self.fail_on:
            raise ConnectionError(f"{name}: database unreachable")
        self.calls.append((name, args))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    # job health
    def job_register(self, job, interval): self._rec("job_register", job, interval)
    def job_started(self, job): self._rec("job_started", job)
    def job_succeeded(self, job): self._rec("job_succeeded", job)
    def job_failed(self, job, error): self._rec("job_failed", job, error)
    def job_health_rows(self): return list(self.health)
    def set_job_alert_state(self, job, state): self._rec("set_job_alert_state", job, state)
    # db size
    def database_size(self, top: int = 10): return self.size
    def notification_sent_since(self, kind, hours): return kind in self.sent_kinds
    # queue
    def queue_fail_exhausted(self): self._rec("queue_fail_exhausted"); return 0
    def queue_claim(self): return self.queue.pop(0) if self.queue else None
    def queue_done(self, job_id): self._rec("queue_done", job_id)
    def queue_retry(self, job_id, error, delay): self._rec("queue_retry", job_id, error, delay)
    def queue_fail(self, job_id, error): self._rec("queue_fail", job_id, error)


class SystemTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = FakeLedger()
        names = ("job_register", "job_started", "job_succeeded", "job_failed", "job_health_rows",
                 "set_job_alert_state", "database_size", "notification_sent_since",
                 "queue_fail_exhausted", "queue_claim", "queue_done", "queue_retry", "queue_fail")
        for name in names:
            p = mock.patch.object(ledger, name, getattr(self.ledger, name))
            p.start()
            self.addCleanup(p.stop)
        self.pushes: list[push.Message] = []
        self.push_error: Exception | None = None

        def fake_send(message: push.Message, **_: Any) -> list[push.SendResult]:
            if self.push_error:
                raise self.push_error
            self.pushes.append(message)
            return [push.SendResult("owner", 1, "sent", ())]

        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch.object(push, "send", fake_send)):
            p.start()
            self.addCleanup(p.stop)


# ------------------------------------------------------------------ wrapper

class Describe(unittest.TestCase):
    def test_secrets_are_redacted(self) -> None:
        for raw in (
            "connection to postgresql://postgres.abc:hunter2@aws-0.pooler.supabase.com:5432/postgres failed",
            "auth failed: password=hunter2 user=x",
            "header Authorization: Bearer abc.def.ghi rejected",
            "api_key=sk-ant-123 invalid",
        ):
            with self.subTest(raw=raw[:30]):
                text = describe(RuntimeError(raw))
                self.assertNotIn("hunter2", text)
                self.assertNotIn("abc.def.ghi", text)
                self.assertNotIn("sk-ant-123", text)
                self.assertIn("[redacted]", text)

    def test_one_line_and_bounded(self) -> None:
        text = describe(ValueError("a\nb" + "x" * 1000))
        self.assertNotIn("\n", text)
        self.assertLessEqual(len(text), jobs.ERROR_MAX)
        self.assertTrue(text.startswith("ValueError: a b"))


class RunTracked(SystemTestCase):
    def job(self, fn) -> SystemJob:
        from apscheduler.triggers.interval import IntervalTrigger
        return SystemJob("probe", 60, IntervalTrigger(seconds=60), fn)

    def test_success_records_start_then_ok(self) -> None:
        run_tracked(self.job(lambda: None))
        self.assertEqual(self.ledger.names(), ["job_started", "job_succeeded"])

    def test_failure_is_recorded_redacted_and_reraised(self) -> None:
        def boom() -> None:
            raise RuntimeError("password=hunter2 nope")
        with self.assertRaises(RuntimeError):
            run_tracked(self.job(boom))
        self.assertEqual(self.ledger.names(), ["job_started", "job_failed"])
        recorded = self.ledger.calls[1][1][1]
        self.assertNotIn("hunter2", recorded)

    def test_a_dead_database_does_not_swallow_the_jobs_own_error(self) -> None:
        self.ledger.fail_on = {"job_started", "job_failed"}
        with self.assertRaises(KeyError):
            run_tracked(self.job(lambda: {}["missing"]))

    def test_success_that_cannot_be_recorded_raises(self) -> None:
        self.ledger.fail_on = {"job_succeeded"}
        with self.assertRaises(ConnectionError):
            run_tracked(self.job(lambda: None))

    def test_names_are_snake_case(self) -> None:
        from apscheduler.triggers.interval import IntervalTrigger
        with self.assertRaises(ValueError):
            SystemJob("Heart-Beat", 60, IntervalTrigger(seconds=60), lambda: None)


# ---------------------------------------------------------- health monitor

class HealthMonitor(SystemTestCase):
    WATCHED = ["heartbeat", "health_monitor"]

    def test_two_consecutive_failures_alert_once(self) -> None:
        self.ledger.health = [row("heartbeat", failures=2, error="RuntimeError: x")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(len(self.pushes), 1)
        msg = self.pushes[0]
        self.assertEqual((msg.kind, msg.deep_link), ("job_health", "/settings/health"))
        self.assertIn("failed 2 times", msg.body)
        self.assertIn(("set_job_alert_state", ("heartbeat", "alerted")), self.ledger.calls)

    def test_one_failure_is_not_yet_an_incident(self) -> None:
        self.ledger.health = [row("heartbeat", failures=1)]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes, [])

    def test_stale_alerts(self) -> None:
        self.ledger.health = [row("heartbeat", stale=True)]
        system_jobs.check_health(self.WATCHED)
        self.assertIn("has not succeeded on schedule", self.pushes[0].body)

    def test_the_monitor_never_calls_itself_stale(self) -> None:
        # The redeploy case: the old process stopped and the new one waited
        # 90 s, so the monitor's own row is older than 2x60 s on its first
        # pass. That used to push a false "needs attention" + "Recovered" pair.
        self.ledger.health = [row("health_monitor", stale=True)]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes, [])
        self.assertNotIn("set_job_alert_state", self.ledger.names())

    def test_the_monitors_own_failures_still_alert(self) -> None:
        self.ledger.health = [row("health_monitor", failures=2, error="ConnectionError: x")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes[0].title, "health_monitor needs attention")

    def test_other_jobs_are_still_judged_stale_alongside_the_monitor(self) -> None:
        self.ledger.health = [row("health_monitor", stale=True), row("heartbeat", stale=True)]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual([m.title for m in self.pushes], ["heartbeat needs attention"])

    def test_a_self_stale_alert_from_before_the_fix_is_closed(self) -> None:
        self.ledger.health = [row("health_monitor", stale=True, state="alerted")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual([m.title for m in self.pushes], ["Recovered: health_monitor"])

    def test_an_open_incident_does_not_alert_again(self) -> None:
        self.ledger.health = [row("heartbeat", failures=7, state="alerted")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes, [])

    def test_recovery_sends_one_push_and_closes(self) -> None:
        self.ledger.health = [row("heartbeat", state="alerted")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes[0].title, "Recovered: heartbeat")
        self.assertIn(("set_job_alert_state", ("heartbeat", "ok")), self.ledger.calls)

    def test_a_failed_push_leaves_the_incident_open_to_retry(self) -> None:
        self.push_error = push.PushError("no active push subscriptions")
        self.ledger.health = [row("heartbeat", failures=3)]
        system_jobs.check_health(self.WATCHED)
        self.assertNotIn("set_job_alert_state", self.ledger.names())

    def test_an_unscheduled_alerting_job_is_closed_as_recovered(self) -> None:
        # The drill after HEALTH_DRILL is unset: no longer scheduled at all.
        self.ledger.health = [row("health_drill", failures=9, state="alerted")]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes[0].title, "Recovered: health_drill")
        self.assertIn("no longer scheduled", self.pushes[0].body)

    def test_an_unscheduled_quiet_job_is_ignored(self) -> None:
        self.ledger.health = [row("old_job", failures=9, stale=True)]
        system_jobs.check_health(self.WATCHED)
        self.assertEqual(self.pushes, [])


class ExternalWatchdog(SystemTestCase):
    """The 'watchdog' row is written by the Vercel route, which owns its
    alert_state ("worker down"). The monitor alerts only on the row going
    stale (cron stopped calling), with its own state, and never touches
    alert_state, so neither incident can close the other."""

    WATCHED = ["heartbeat", "health_monitor"]

    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.dict(system_jobs._external_alerted, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def check(self, *, stale: bool, state: str = "ok") -> None:
        self.ledger.health = [row("watchdog", stale=stale, state=state, interval=300)]
        system_jobs.check_health(self.WATCHED)

    def test_stale_alerts_once_resumed_pushes_once_and_alert_state_is_never_written(self) -> None:
        self.check(stale=True)
        self.check(stale=True)
        self.check(stale=False)
        self.check(stale=False)
        self.assertEqual([m.kind for m in self.pushes], ["watchdog_stale", "watchdog_resumed"])
        self.assertEqual(self.pushes[0].deep_link, "/settings/health")
        self.assertNotIn("set_job_alert_state", self.ledger.names())

    def test_the_routes_own_worker_down_state_is_not_the_monitors_business(self) -> None:
        # The bug this fixes: the route marked the row 'alerted' (worker down)
        # while cron kept calling. The monitor must neither push nor reset it.
        self.check(stale=False, state="alerted")
        self.assertEqual(self.pushes, [])
        self.assertNotIn("set_job_alert_state", self.ledger.names())

    def test_a_stale_row_alerted_before_a_restart_is_adopted_not_repushed(self) -> None:
        self.ledger.sent_kinds = {"watchdog_stale"}
        self.check(stale=True)
        self.assertEqual(self.pushes, [])
        self.check(stale=False)  # and its recovery is still announced
        self.assertEqual([m.kind for m in self.pushes], ["watchdog_resumed"])

    def test_a_stale_row_never_alerted_is_pushed_after_a_restart(self) -> None:
        self.check(stale=True)
        self.assertEqual([m.kind for m in self.pushes], ["watchdog_stale"])

    def test_a_failed_push_is_retried_next_pass(self) -> None:
        self.push_error = push.PushError("no active push subscriptions")
        self.check(stale=True)
        self.push_error = None
        self.check(stale=True)
        self.assertEqual([m.kind for m in self.pushes], ["watchdog_stale"])

    def test_an_external_row_is_watched_even_though_it_is_not_scheduled_here(self) -> None:
        # Not in WATCHED, yet not treated as an unscheduled job to close out.
        self.check(stale=True)
        self.assertNotIn("no longer scheduled", self.pushes[0].body)


# ----------------------------------------------------------------- db size

class DbSize(SystemTestCase):
    MB = system_jobs.MB

    def test_below_400_is_quiet(self) -> None:
        self.ledger.size = (399 * self.MB, [("kalshi_candles", 300 * self.MB)])
        system_jobs.check_db_size()
        self.assertEqual(self.pushes, [])

    def test_400_pushes_once(self) -> None:
        self.ledger.size = (420 * self.MB, [("kalshi_candles", 300 * self.MB)])
        system_jobs.check_db_size()
        self.assertEqual([m.kind for m in self.pushes], ["db_size_400"])
        self.assertIn("kalshi_candles", self.pushes[0].body)

    def test_450_pushes_its_own_alert_only(self) -> None:
        self.ledger.size = (460 * self.MB, [])
        system_jobs.check_db_size()
        self.assertEqual([m.kind for m in self.pushes], ["db_size_450"])

    def test_already_alerted_this_month_is_quiet(self) -> None:
        self.ledger.size = (420 * self.MB, [])
        self.ledger.sent_kinds = {"db_size_400"}
        system_jobs.check_db_size()
        self.assertEqual(self.pushes, [])


# --------------------------------------------------------------- job queue

class Queue(SystemTestCase):
    def test_noop_completes(self) -> None:
        self.ledger.queue = [QueuedJob(1, "noop", {}, 1)]
        system_jobs.poll_queue()
        self.assertIn(("queue_done", (1,)), self.ledger.calls)

    def test_a_failure_retries_with_backoff(self) -> None:
        def boom(_: Any) -> None:
            raise RuntimeError("flaky")
        self.ledger.queue = [QueuedJob(2, "flaky", {}, 2)]
        system_jobs.poll_queue({"flaky": boom})
        name, (job_id, error, delay) = self.ledger.calls[-1]
        self.assertEqual((name, job_id, delay), ("queue_retry", 2, timedelta(seconds=60)))
        self.assertIn("flaky", error)

    def test_the_fifth_failure_is_final(self) -> None:
        def boom(_: Any) -> None:
            raise RuntimeError("still broken")
        self.ledger.queue = [QueuedJob(3, "flaky", {}, 5)]
        system_jobs.poll_queue({"flaky": boom})
        self.assertEqual(self.ledger.calls[-1][0], "queue_fail")

    def test_an_unknown_kind_fails_at_once(self) -> None:
        self.ledger.queue = [QueuedJob(4, "mystery", {}, 1)]
        system_jobs.poll_queue()
        self.assertEqual(self.ledger.calls[-1], ("queue_fail", (4, "no handler for kind 'mystery'")))

    def test_backoff_doubles(self) -> None:
        self.assertEqual([system_jobs.backoff(n).total_seconds() for n in (1, 2, 3, 4)],
                         [30, 60, 120, 240])

    def test_abandoned_jobs_are_swept_first(self) -> None:
        system_jobs.poll_queue()
        self.assertEqual(self.ledger.names(), ["queue_fail_exhausted"])


# ---------------------------------------------------------------- registry

class Registry(unittest.TestCase):
    def test_default_jobs(self) -> None:
        built = {j.name: j for j in system_jobs.build_system_jobs({})}
        self.assertEqual(set(built), {"heartbeat", "health_monitor", "db_size", "job_queue"})
        self.assertEqual(built["heartbeat"].expected_interval_s, 60)
        self.assertEqual(built["job_queue"].expected_interval_s, 10)
        self.assertEqual(built["db_size"].expected_interval_s, 86400)
        self.assertEqual(str(built["db_size"].trigger.timezone), "America/Chicago")
        self.assertEqual(built["health_monitor"].start_delay_s, 90)

    def test_the_drill_is_opt_in(self) -> None:
        for value in ("", "false", "1", "yes"):
            with self.subTest(value=value):
                names = {j.name for j in system_jobs.build_system_jobs({"HEALTH_DRILL": value})}
                self.assertNotIn("health_drill", names)
        names = {j.name for j in system_jobs.build_system_jobs({"HEALTH_DRILL": "true"})}
        self.assertIn("health_drill", names)
        with self.assertRaises(RuntimeError):
            system_jobs.health_drill()


# ------------------------------------------------------- orchestrator, main

class SystemOnlyBoot(SystemTestCase):
    def test_orchestrator_starts_with_system_jobs_and_no_agents(self) -> None:
        orchestrator = Orchestrator()
        for job in system_jobs.build_system_jobs({}):
            orchestrator.register_system_job(job)
        orchestrator.start()
        self.addCleanup(orchestrator.shutdown, False)
        ids = {j.id for j in orchestrator._scheduler.get_jobs()}
        self.assertEqual(ids, {"system:heartbeat", "system:health_monitor",
                               "system:db_size", "system:job_queue"})
        self.assertEqual(self.ledger.names().count("job_register"), 4)

    def test_disabled_agents_plus_system_jobs_start_rather_than_refuse(self) -> None:
        from tests.support import ScriptedAgent
        from core.orchestrator import Registration, Schedule, every

        ledger_enabled = mock.patch.object(ledger, "agent_enabled", lambda slug: False)
        agent_id = mock.patch.object(ScriptedAgent, "agent_id", 90)
        ledger_enabled.start(); agent_id.start()
        self.addCleanup(ledger_enabled.stop); self.addCleanup(agent_id.stop)
        orchestrator = Orchestrator()
        orchestrator.register(Registration(agent=ScriptedAgent(),
                                           schedule=Schedule(run=every(minutes=1), sweep=every(minutes=1))))
        for job in system_jobs.build_system_jobs({}):
            orchestrator.register_system_job(job)
        orchestrator.start()
        self.addCleanup(orchestrator.shutdown, False)
        self.assertTrue(all(j.id.startswith("system:") for j in orchestrator._scheduler.get_jobs()))

    def test_nothing_at_all_still_refuses(self) -> None:
        with self.assertRaises(RuntimeError):
            Orchestrator().start()

    def test_duplicate_system_job_is_refused(self) -> None:
        orchestrator = Orchestrator()
        job = system_jobs.build_system_jobs({})[0]
        orchestrator.register_system_job(job)
        with self.assertRaises(ValueError):
            orchestrator.register_system_job(job)

    def test_main_without_roster_or_canary_runs_system_jobs_instead_of_refusing(self) -> None:
        sys.modules.pop("main", None)
        with mock.patch("core.paths.load_env"):
            import main
        self.addCleanup(sys.modules.pop, "main", None)
        started: list[Orchestrator] = []
        env = {"DATABASE_URL": "postgresql://unused", "CANARY": "", "ROSTER": "", "HEALTH_DRILL": ""}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(main, "_refuse", side_effect=AssertionError("refused")), \
                mock.patch.object(Orchestrator, "run_forever", lambda self: started.append(self)):
            main.main()
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].agents, [])
        self.assertEqual({j.name for j in started[0].system_jobs},
                         {"heartbeat", "health_monitor", "db_size", "job_queue"})


if __name__ == "__main__":
    unittest.main()
