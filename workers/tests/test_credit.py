"""The Anthropic account's credit outage (core/ai.py, owner 2026-11-03), and
the stranded Arm Me saves sweep (arsenal/arm.py) that the same outage made
urgent.

  * the API's "credit balance is too low" 400 becomes CreditExhausted on
    every SDK path (messages, batches);
  * ONE push per outage, ONE "restored" push on the first success after,
    across a worker restart in the middle of it (the state is read once);
  * run_tracked records CREDIT_NOTE, not the SDK's text; the health monitor
    sends no per-job "needs attention" for it;
  * the job_queue defers such a job an hour and gives its attempt back;
  * a quota belongs only on a 'submitted' pool run (db/061);
  * the sweep finishes stranded saves, keeps going past one failure, raises
    at the end, and stops at once on the credit error.

No database, no network: ledger functions are fakes, the pool raises,
push.send is stubbed.
"""

from __future__ import annotations

import os
import unittest
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock

from apscheduler.triggers.interval import IntervalTrigger

from arsenal import arm
from core import ai, ledger, push, system_jobs
from core.jobs import SystemJob, run_tracked
from core.ledger import JobHealth, QueuedJob


def _refuse_database(*a: Any, **k: Any) -> Any:
    raise AssertionError("a credit test reached the real database pool")


class CreditError(Exception):
    """Shaped like anthropic.BadRequestError for the credit case."""
    status_code = 400

    def __init__(self) -> None:
        super().__init__("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                         "'message': 'Your credit balance is too low to access the Anthropic API. Please go "
                         "to Plans & Billing to upgrade or purchase credits.'}}")


class OtherBadRequest(Exception):
    status_code = 400


def ok_response(**kw: Any) -> Any:
    return SimpleNamespace(model=kw.get("model", "claude-haiku-5-5"), content=[],
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0, cache_creation=None))


class Client:
    """messages.create / batches.* that fail with `error` while it is set."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.messages = SimpleNamespace(create=self.create, batches=SimpleNamespace(
            create=self._batch_create, retrieve=self._retrieve, results=self._results))

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def create(self, **kw: Any) -> Any:
        self._maybe_fail()
        return ok_response(**kw)

    def _batch_create(self, **kw: Any) -> Any:
        self._maybe_fail()
        return SimpleNamespace(id="msgbatch_x")

    def _retrieve(self, batch_id: str) -> Any:
        self._maybe_fail()
        return SimpleNamespace(processing_status="ended")

    def _results(self, batch_id: str) -> Any:
        def gen() -> Any:
            self._maybe_fail()  # lazily, on iteration, as the SDK may
            yield from ()
        return gen()


class CreditCase(unittest.TestCase):
    def setUp(self) -> None:
        self.pushes: list[push.Message] = []
        self.delivered = True
        #: What the notifications table would say (ledger.ai_credit_outage_open).
        self.db_open = False
        self.db_reads = 0

        def fake_send(message: push.Message, **_: Any) -> list[push.SendResult]:
            self.pushes.append(message)
            if self.delivered:
                self.db_open = message.kind == ai.CREDIT_EMPTY_KIND
            return [push.SendResult("owner", 1, "sent" if self.delivered else "failed", ())]

        def outage_open() -> bool:
            self.db_reads += 1
            return self.db_open

        patches = [
            mock.patch.object(ledger, "_pool", _refuse_database),
            mock.patch.object(push, "send", fake_send),
            mock.patch.object(ledger, "ai_credit_outage_open", outage_open),
            mock.patch.object(ledger, "ai_spend_month_to_date", lambda: Decimal(0)),
            mock.patch.object(ledger, "record_ai_usage", lambda **row: 1),
            mock.patch.dict(os.environ, {"AI_MONTHLY_BUDGET_USD": "20", "ANTHROPIC_API_KEY": ""}),
            # A fresh process each test.
            mock.patch.object(ai, "_credit_empty", None),
            mock.patch.object(ai, "_credit_ok_seen", False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = Client()

    def call(self) -> Any:
        return ai.complete(purpose="test_call", model="claude-haiku-5-5",
                           messages=[{"role": "user", "content": "hi"}], client=self.client)

    def kinds(self) -> list[str]:
        return [m.kind for m in self.pushes]


class Outage(CreditCase):
    def test_the_credit_error_becomes_credit_exhausted_with_the_sdk_error_as_cause(self) -> None:
        self.client.error = CreditError()
        with self.assertRaises(ai.CreditExhausted) as caught:
            self.call()
        self.assertIsInstance(caught.exception.__cause__, CreditError)
        self.assertEqual(str(caught.exception), ai.CREDIT_NOTE)

    def test_one_push_per_outage_however_many_calls_fail(self) -> None:
        self.client.error = CreditError()
        for _ in range(5):
            with self.assertRaises(ai.CreditExhausted):
                self.call()
        self.assertEqual(self.kinds(), [ai.CREDIT_EMPTY_KIND])
        self.assertEqual(self.pushes[0].title, "Anthropic credit balance empty — top up")
        self.assertEqual(self.pushes[0].deep_link, "/settings/health")

    def test_the_first_success_after_pushes_restored_once(self) -> None:
        self.client.error = CreditError()
        with self.assertRaises(ai.CreditExhausted):
            self.call()
        self.client.error = None
        self.call()
        self.call()
        self.assertEqual(self.kinds(), [ai.CREDIT_EMPTY_KIND, ai.CREDIT_RESTORED_KIND])
        self.assertEqual(self.pushes[1].title, "Anthropic credit restored")

    def test_a_second_outage_pushes_again(self) -> None:
        for _ in range(2):
            self.client.error = CreditError()
            with self.assertRaises(ai.CreditExhausted):
                self.call()
            self.client.error = None
            self.call()
        self.assertEqual(self.kinds(), [ai.CREDIT_EMPTY_KIND, ai.CREDIT_RESTORED_KIND] * 2)

    def test_a_success_with_no_outage_reads_nothing_and_pushes_nothing(self) -> None:
        self.call()
        self.assertEqual((self.pushes, self.db_reads), ([], 0))

    def test_other_errors_pass_through_untouched(self) -> None:
        self.client.error = OtherBadRequest("Error code: 400 - max_tokens too large")
        with self.assertRaises(OtherBadRequest):
            self.call()
        self.client.error = RuntimeError("credit balance is too low")  # no 400 status
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(self.pushes, [])

    def test_an_undelivered_push_is_tried_again_on_the_next_failure(self) -> None:
        self.delivered = False
        self.client.error = CreditError()
        with self.assertRaises(ai.CreditExhausted):
            self.call()
        self.delivered = True
        with self.assertRaises(ai.CreditExhausted):
            self.call()
        with self.assertRaises(ai.CreditExhausted):
            self.call()
        self.assertEqual(self.kinds(), [ai.CREDIT_EMPTY_KIND, ai.CREDIT_EMPTY_KIND])

    def test_every_batch_path_is_covered(self) -> None:
        self.client.error = CreditError()
        req = [ai.BatchRequest("refs", {"messages": [{"role": "user", "content": "x"}]})]
        with self.assertRaises(ai.CreditExhausted):
            ai.submit_batch(requests=req, model="claude-haiku-5-5", client=self.client)
        with self.assertRaises(ai.CreditExhausted):
            ai.batch_status("msgbatch_x", client=self.client)
        with self.assertRaises(ai.CreditExhausted):
            ai.batch_results("msgbatch_x", purpose="p", model="claude-haiku-5-5", client=self.client)
        self.assertEqual(self.kinds(), [ai.CREDIT_EMPTY_KIND])


class Restart(CreditCase):
    """The state is read once per process, so a restart mid-outage neither
    repeats the push nor loses the "restored" one."""

    def test_an_outage_already_pushed_before_the_restart_is_not_pushed_again(self) -> None:
        self.db_open = True
        self.client.error = CreditError()
        with self.assertRaises(ai.CreditExhausted):
            self.call()
        self.assertEqual(self.pushes, [])

    def test_the_monitor_sync_closes_an_outage_a_call_already_survived(self) -> None:
        self.db_open = True
        self.call()  # before the monitor's first pass: nothing known, nothing read
        self.assertEqual((self.pushes, self.db_reads), ([], 0))
        ai.credit_sync()
        self.assertEqual(self.kinds(), [ai.CREDIT_RESTORED_KIND])
        ai.credit_sync()
        self.assertEqual(self.db_reads, 1, "read once per process")

    def test_the_monitor_sync_then_a_success_restores(self) -> None:
        self.db_open = True
        ai.credit_sync()
        self.assertEqual(self.pushes, [])
        self.call()
        self.assertEqual(self.kinds(), [ai.CREDIT_RESTORED_KIND])


class Cause(unittest.TestCase):
    def test_found_through_a_wrapper(self) -> None:
        inner = ai.CreditExhausted(ai.CREDIT_NOTE)
        try:
            try:
                raise inner
            except ai.CreditExhausted as exc:
                raise RuntimeError("summarize failed") from exc
        except RuntimeError as outer:
            self.assertIs(ai.credit_cause(outer), inner)
        self.assertIsNone(ai.credit_cause(RuntimeError("x")))
        self.assertIsNone(ai.credit_cause(None))

    def test_the_note_is_recognized(self) -> None:
        self.assertTrue(ai.credit_note(ai.CREDIT_NOTE))
        self.assertFalse(ai.credit_note("BadRequestError: Error code: 400"))
        self.assertFalse(ai.credit_note(None))


# ------------------------------------------------------------- jobs side

def health(job: str, *, failures: int, error: str | None, state: str = "ok") -> JobHealth:
    return JobHealth(job=job, expected_interval_s=1800, last_ok_at=None, last_error=error,
                     consecutive_failures=failures, alert_state=state, stale=False)


class JobsSide(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.health: list[JobHealth] = []
        self.queue: list[QueuedJob] = []
        self.pushes: list[push.Message] = []

        def rec(name: str) -> Any:
            return lambda *a, **k: self.calls.append((name, a))

        fakes = {
            "job_started": rec("job_started"), "job_succeeded": rec("job_succeeded"),
            "job_failed": rec("job_failed"), "set_job_alert_state": rec("set_job_alert_state"),
            "job_health_rows": lambda: list(self.health),
            "queue_fail_exhausted": lambda: 0,
            "queue_claim": lambda: self.queue.pop(0) if self.queue else None,
            "queue_done": rec("queue_done"), "queue_retry": rec("queue_retry"),
            "queue_fail": rec("queue_fail"), "queue_defer": rec("queue_defer"),
        }
        patches = [mock.patch.object(ledger, "_pool", _refuse_database),
                   mock.patch.object(push, "send", lambda m, **_: self.pushes.append(m)
                                     or [push.SendResult("owner", 1, "sent", ())])]
        patches += [mock.patch.object(ledger, n, f) for n, f in fakes.items()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def failing(self) -> None:
        try:
            raise ai.CreditExhausted(ai.CREDIT_NOTE) from CreditError()
        except ai.CreditExhausted as exc:
            raise RuntimeError("summarize: group failed") from exc

    def test_run_tracked_names_the_cause(self) -> None:
        with self.assertRaises(RuntimeError):
            run_tracked(SystemJob("feed_summarize", 1800, IntervalTrigger(seconds=1800), self.failing))
        self.assertIn(("job_failed", ("feed_summarize", ai.CREDIT_NOTE)), self.calls)

    def test_the_monitor_sends_no_per_job_alert_for_the_credit(self) -> None:
        self.health = [health("feed_summarize", failures=5, error=ai.CREDIT_NOTE),
                       health("feed_why", failures=2, error="RuntimeError: other")]
        system_jobs.check_health(["feed_summarize", "feed_why"])
        self.assertEqual([m.title for m in self.pushes], ["feed_why needs attention"])
        self.assertNotIn(("set_job_alert_state", ("feed_summarize", "alerted")), self.calls)

    def test_an_alerted_job_failing_on_credit_stays_alerted_then_recovers(self) -> None:
        self.health = [health("feed_summarize", failures=3, error=ai.CREDIT_NOTE, state="alerted")]
        system_jobs.check_health(["feed_summarize"])
        self.assertEqual(self.pushes, [])
        self.health = [health("feed_summarize", failures=0, error=None, state="alerted")]
        system_jobs.check_health(["feed_summarize"])
        self.assertEqual([m.title for m in self.pushes], ["Recovered: feed_summarize"])

    def test_the_queue_defers_an_hour_and_gives_the_attempt_back(self) -> None:
        self.queue = [QueuedJob(7, "arsenal_arm_verify", {"ref_id": 1}, 5)]  # its last attempt
        system_jobs.poll_queue({"arsenal_arm_verify": lambda payload: self.failing()})
        self.assertIn(("queue_defer", (7, ai.CREDIT_NOTE, timedelta(hours=1))), self.calls)
        names = [c[0] for c in self.calls]
        self.assertNotIn("queue_fail", names)
        self.assertNotIn("queue_retry", names)

    def test_other_failures_keep_the_old_retry(self) -> None:
        def boom(payload: Any) -> None:
            raise RuntimeError("wikipedia down")
        self.queue = [QueuedJob(8, "arsenal_verify", {"ref_id": 2}, 1)]
        system_jobs.poll_queue({"arsenal_verify": boom})
        self.assertEqual([c[0] for c in self.calls], ["queue_retry"])

    def test_a_failed_credit_read_never_stops_the_monitor(self) -> None:
        self.health = [health("feed_why", failures=2, error="RuntimeError: other")]
        with mock.patch.object(ai, "_credit_empty", None), \
                mock.patch.object(ledger, "ai_credit_outage_open", side_effect=ConnectionError("down")), \
                mock.patch.object(system_jobs, "check_summary_backlog", lambda: 0):
            system_jobs.monitor_pass(["feed_why"])
            self.assertIsNone(ai._credit_empty, "unknown still: the next pass reads again")
        self.assertEqual([m.title for m in self.pushes], ["feed_why needs attention"])


# ----------------------------------------------------------------- quotas

class Quotas(unittest.TestCase):
    def test_quotas_only_on_a_submitted_run(self) -> None:
        with mock.patch.object(ledger, "_pool", _refuse_database):
            with self.assertRaises(ledger.LedgerError):
                ledger.arsenal_log_run("batch", "collected", run_id=1, quotas={"sports": 3})


# ---------------------------------------------------------- stranded saves

class StrandedSaves(unittest.TestCase):
    def setUp(self) -> None:
        self.asked: list[tuple[int, int]] = []
        self.ids: list[int] = []

        def stranded(limit: int, min_age_h: int) -> list[int]:
            self.asked.append((limit, min_age_h))
            return list(self.ids)

        for p in (mock.patch.object(ledger, "_pool", _refuse_database),
                  mock.patch.object(ledger, "arsenal_stranded_saves", stranded)):
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_stranded(self) -> None:
        self.assertEqual(arm.sweep_job(verify_one=lambda i: "x").job_note, "no stranded saves")
        self.assertEqual(self.asked, [(arm.SWEEP_LIMIT, arm.SWEEP_MIN_AGE_H)])

    def test_each_is_finished_and_noted(self) -> None:
        self.ids = [11, 12]
        run = arm.sweep_job(verify_one=lambda i: f"verify verified; outcome ok")
        self.assertIn("2 stranded save(s) finished", run.job_note)
        self.assertIn("ref 11:", run.job_note)

    def test_one_failure_never_stops_the_rest_and_raises_at_the_end(self) -> None:
        self.ids = [11, 12, 13]
        seen: list[int] = []

        def one(ref_id: int) -> str:
            seen.append(ref_id)
            if ref_id == 12:
                raise RuntimeError("wikipedia down")
            return "ok"

        with self.assertRaises(arm.SweepFailed) as caught:
            arm.sweep_job(verify_one=one)
        self.assertEqual(seen, [11, 12, 13])
        self.assertIn("1 of 3", str(caught.exception))

    def test_the_credit_error_stops_the_run_at_once(self) -> None:
        self.ids = [11, 12]
        seen: list[int] = []

        def one(ref_id: int) -> str:
            seen.append(ref_id)
            raise ai.CreditExhausted(ai.CREDIT_NOTE)

        with self.assertRaises(ai.CreditExhausted):
            arm.sweep_job(verify_one=one)
        self.assertEqual(seen, [11])

    def test_the_query_takes_only_real_unsettled_saves_with_no_live_job(self) -> None:
        sql = ledger.ARSENAL_STRANDED_SAVES_SQL
        for needle in ("v_arsenal_saved", "r.status = 'arm'", "NOT r.is_test", "r.verification = 'pending'",
                       "arsenal_outcome_checks", "q.status IN ('queued', 'running')", "'arsenal_arm_verify'"):
            self.assertIn(needle, sql)

    def test_scheduled_every_six_hours(self) -> None:
        built = {j.name: j for j in system_jobs.build_system_jobs({})}
        self.assertEqual(built[arm.SWEEP_JOB].expected_interval_s, 6 * 60 * 60)
        self.assertTrue(built[arm.SWEEP_JOB].scheduled)


if __name__ == "__main__":
    unittest.main()
