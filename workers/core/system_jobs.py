"""The worker's own jobs, always scheduled, whatever ROSTER or CANARY says.

    heartbeat        60 s    does nothing; run_tracked's ok stamp IS the beat.
                             The Vercel watchdog reads this row from outside.
    health_monitor   60 s    pushes when a job fails twice running, or its
                             last_ok_at is older than 2 × its interval; one
                             push per incident, one when it recovers.
    db_size          daily   03:00 America/Chicago, and once at boot: logs
                             total + top tables; pushes at 400 MB and again
                             at 450 MB (free tier cap: 500 MB).
    job_queue        10 s    claims queued work with FOR UPDATE SKIP LOCKED,
                             retries with backoff, gives up after 5 attempts.
    health_drill     60 s    HEALTH_DRILL=true only: always fails, to prove
                             the alert path end to end.

Alerts go through core/push.py with deep link /settings/health.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, Callable, Iterable, Mapping

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core import ledger, push
from core.jobs import SystemJob, describe

log = logging.getLogger("valemont.system")

HEALTH_LINK = "/settings/health"

#: Rows other processes keep. The Vercel watchdog writes 'watchdog' each time
#: cron-job.org calls it, so the worker notices if that external check dies.
#: Watched for staleness only, with their own incident state (_check_external).
EXTERNAL_JOBS = frozenset({"watchdog"})

FAILURES_TO_ALERT = 2

MB = 1024 * 1024
DB_SIZE_THRESHOLDS_MB = (400, 450)
#: Re-alert a threshold at most once a month while the database stays above it.
DB_SIZE_REALERT_HOURS = 24 * 30

QUEUE_BATCH = 20
QUEUE_BACKOFF_BASE = timedelta(seconds=30)  # 30 s, 60 s, 2 min, 4 min


# ------------------------------------------------------------------ heartbeat

def heartbeat() -> None:
    """Nothing to do: the job_health ok stamp written by run_tracked is the beat."""


# ------------------------------------------------------------- health monitor

def _alert(message: push.Message) -> bool:
    """Send; True only if at least one device took it."""
    try:
        results = push.send(message)
    except push.PushError as exc:
        log.error("health alert not sent: %s", exc)
        return False
    return any(r.status in ("sent", "partial") for r in results)


#: Incident state for EXTERNAL rows, held by the monitor itself. Their
#: job_health.alert_state belongs to the process that writes the row (the
#: watchdog route uses it for "worker down"); sharing it would let one
#: incident close the other with the wrong message. None = not yet known in
#: this process (after a restart).
_external_alerted: dict[str, bool] = {}

#: An external stale alert sent within this window is adopted after a worker
#: restart instead of being pushed again.
EXTERNAL_REALERT_HOURS = 24


def _check_external(row: ledger.JobHealth) -> None:
    """A row kept by another process: alert when it stops reporting, once per
    incident, and once when it resumes. Never touches its alert_state."""
    stale_kind, resumed_kind = f"{row.job}_stale", f"{row.job}_resumed"
    alerted = _external_alerted.get(row.job)
    if alerted is None:
        # First pass in this process: adopt an incident alerted before a
        # restart rather than pushing it twice. If it already recovered, the
        # resumed push for it is lost with the restart; accepted.
        alerted = row.stale and ledger.notification_sent_since(stale_kind, EXTERNAL_REALERT_HOURS)
        _external_alerted[row.job] = alerted

    if row.stale and not alerted:
        if _alert(push.Message(
            kind=stale_kind,
            title=f"{row.job} is not reporting",
            body=(f"Nothing has called {row.job} on schedule. Until it resumes, "
                  "nothing outside Railway is watching the worker."),
            deep_link=HEALTH_LINK, tag=f"job-{row.job}",
        )):
            _external_alerted[row.job] = True
            log.warning("alerted: external %s stale", row.job)
    elif not row.stale and alerted:
        if _alert(push.Message(
            kind=resumed_kind,
            title=f"{row.job} reporting again",
            body=f"{row.job} is being called on schedule again.",
            deep_link=HEALTH_LINK, tag=f"job-{row.job}",
        )):
            _external_alerted[row.job] = False
            log.info("recovered: external %s", row.job)


def check_health(scheduled: Iterable[str]) -> None:
    """One pass of the monitor over every job_health row this process answers for."""
    watched = set(scheduled)
    for row in ledger.job_health_rows():
        if row.job in EXTERNAL_JOBS:
            _check_external(row)
            continue
        if row.job not in watched:
            # A job that was alerting and is no longer scheduled at all (the
            # drill, once HEALTH_DRILL is unset): close the incident.
            if row.alert_state == "alerted" and _alert(push.Message(
                kind="job_health",
                title=f"Recovered: {row.job}",
                body=f"{row.job} is no longer scheduled. Alert closed.",
                deep_link=HEALTH_LINK,
                tag=f"job-{row.job}",
            )):
                ledger.set_job_alert_state(row.job, "ok")
            continue

        failing = row.consecutive_failures >= FAILURES_TO_ALERT
        unhealthy = failing or row.stale
        if unhealthy and row.alert_state == "ok":
            why = (f"failed {row.consecutive_failures} times in a row"
                   if failing else "has not succeeded on schedule")
            body = f"{row.job} {why}."
            if row.last_error:
                body += f" Last error: {row.last_error}"
            if _alert(push.Message(kind="job_health", title=f"{row.job} needs attention",
                                   body=body[:240], deep_link=HEALTH_LINK, tag=f"job-{row.job}")):
                ledger.set_job_alert_state(row.job, "alerted")
                log.warning("alerted: %s", body)
        elif not unhealthy and row.alert_state == "alerted":
            if _alert(push.Message(kind="job_health", title=f"Recovered: {row.job}",
                                   body=f"{row.job} is running normally again.",
                                   deep_link=HEALTH_LINK, tag=f"job-{row.job}")):
                ledger.set_job_alert_state(row.job, "ok")
                log.info("recovered: %s", row.job)


# -------------------------------------------------------------------- db size

def check_db_size() -> None:
    total, tables = ledger.database_size()
    top = ", ".join(f"{name} {size / MB:.1f}" for name, size in tables[:5])
    log.info("database %.1f MB; largest (MB): %s", total / MB, top)
    for threshold in sorted(DB_SIZE_THRESHOLDS_MB, reverse=True):
        if total < threshold * MB:
            continue
        kind = f"db_size_{threshold}"
        if not ledger.notification_sent_since(kind, DB_SIZE_REALERT_HOURS):
            _alert(push.Message(
                kind=kind,
                title=f"Database at {total / MB:.0f} MB",
                body=f"Past the {threshold} MB mark of the 500 MB free tier. Largest: {top}.",
                deep_link=HEALTH_LINK,
                tag="db-size",
            ))
        break  # only the highest threshold crossed


# ------------------------------------------------------------------ job queue

#: kind -> handler(payload). Real handlers arrive with the features that need
#: them (slip parsing, Arm Me, Lookbook tagging...). 'noop' exists to test.
QueueHandler = Callable[[Mapping[str, Any]], None]
QUEUE_HANDLERS: dict[str, QueueHandler] = {"noop": lambda payload: None}


def backoff(attempt: int) -> timedelta:
    """Delay before retry number `attempt` + 1: 30 s, 60 s, 2 min, 4 min."""
    return QUEUE_BACKOFF_BASE * (2 ** (attempt - 1))


def poll_queue(handlers: Mapping[str, QueueHandler] = QUEUE_HANDLERS) -> None:
    abandoned = ledger.queue_fail_exhausted()
    if abandoned:
        log.warning("job_queue: failed %d job(s) abandoned on their last attempt", abandoned)
    for _ in range(QUEUE_BATCH):
        job = ledger.queue_claim()
        if job is None:
            return
        handler = handlers.get(job.kind)
        if handler is None:
            ledger.queue_fail(job.id, f"no handler for kind {job.kind!r}")
            log.error("job_queue #%d: no handler for %r", job.id, job.kind)
            continue
        try:
            handler(job.payload)
        except Exception as exc:
            detail = describe(exc)
            if job.attempts >= ledger.QUEUE_MAX_ATTEMPTS:
                ledger.queue_fail(job.id, detail)
                log.error("job_queue #%d %s failed for good: %s", job.id, job.kind, detail)
            else:
                ledger.queue_retry(job.id, detail, backoff(job.attempts))
                log.warning("job_queue #%d %s attempt %d failed, retrying: %s",
                            job.id, job.kind, job.attempts, detail)
            continue
        ledger.queue_done(job.id)


# ---------------------------------------------------------------------- drill

def health_drill() -> None:
    raise RuntimeError("health drill: this job fails on purpose (HEALTH_DRILL=true)")


# ------------------------------------------------------------------- registry

def build_system_jobs(env: Mapping[str, str] = os.environ) -> list[SystemJob]:
    jobs = [
        SystemJob("heartbeat", 60, IntervalTrigger(seconds=60), heartbeat),
        SystemJob("db_size", 24 * 60 * 60,
                  CronTrigger(hour=3, minute=0, timezone="America/Chicago"),
                  check_db_size, misfire_grace=3600),
        SystemJob("job_queue", 10, IntervalTrigger(seconds=10), poll_queue, misfire_grace=9),
    ]
    if env.get("HEALTH_DRILL", "").strip().lower() == "true":
        log.warning("HEALTH_DRILL=true: scheduling health_drill, which always fails")
        jobs.append(SystemJob("health_drill", 60, IntervalTrigger(seconds=60), health_drill))
    # The monitor watches every job above, including itself.
    names = [j.name for j in jobs] + ["health_monitor"]
    jobs.append(SystemJob("health_monitor", 60, IntervalTrigger(seconds=60),
                          lambda: check_health(names), start_delay_s=90))
    return jobs
