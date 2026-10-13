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
    goals_rollover   weekly  Monday 00:01 and the 1st 00:01, America/Chicago,
                             and once at boot: carries every ended week and
                             month not yet rolled (db/021), oldest first.
                             Idempotent, so the boot run is what saves a
                             midnight the worker was down for.
    goals_monday_push weekly Monday 07:00 America/Chicago, never at boot:
                             "Set your week", linking to /goals.
    capital_snapshot daily   00:05 America/Chicago, and once at boot: each
                             mode with data gets every source's closing value
                             (db/026 capital_value_as_of) for every ended day
                             since its first entry that has none yet. Nightly
                             that is yesterday; at boot it is the backfill; a
                             missed night repairs itself on the next pass.
                             Paper and live are written separately, never summed.
    morning_brief    daily   06:15, then every 15 min to 09:30, and at boot:
                             today's brief from the registry's brief sections
                             (core/brief.py). AI first; the deterministic
                             fallback from 09:30, so a brief always exists.
    morning_brief_push daily 09:45, and at boot: "Morning brief" -> /brief,
                             once per date (brief_deliveries); "Late ·" after
                             09:50, skipped after 18:00.
    feed_ingest      30 min  and 20 s after boot: syncs workers/feed/sources.toml
                             (first run only), fetches every active source,
                             dedupes and clusters into stories (feed/ingest.py).
                             Then runs feed_summarize, whatever the outcome.
    feed_summarize   chained Haiku summaries for new stories (feed/summarize.py).
                             Never scheduled on its own (SystemJob.scheduled
                             False) but registered and watched: stale after an
                             hour means ingest has stopped calling it.
    feed_score       chained after feed_summarize: scores stories that
                             finished summarizing and have no score yet,
                             against the latest interest profiles (feed/profile.py).
    feed_why         chained after feed_score and after feed_profile: the
                             "Why it matters" line for Home's top 8 in For
                             you, asked once per story, at most 20 Haiku calls
                             a day, NULL unless the link is concrete (feed/why.py).
    feed_profile     daily   04:00 America/Chicago, and at boot unless a
                             profile was already built today: rebuilds each
                             pillar's mean vector, profile and learned weights,
                             then rescores every kept story of the last 72 h.
                             Then runs feed_why.
    feed_prune       daily   03:15 America/Chicago, never at boot: clears item
                             embeddings older than 72 h; deletes filtered
                             stories (with their items), seeded items and
                             similarity-log rows older than 30 days, kept
                             stories unreacted for 90 days, superseded scores
                             after 7 days and old profiles after 90. A story
                             with any reaction is never deleted (db/039).

Alerts go through core/push.py with deep link /settings/health.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core import brief, ledger, push
from core.jobs import SystemJob, describe, run_tracked
from feed import ingest as feed_ingest
from feed import profile as feed_profile_mod
from feed import summarize as feed_summarize
from feed import why as feed_why

log = logging.getLogger("valemont.system")

HEALTH_LINK = "/settings/health"
GOALS_LINK = "/goals"
OWNER_TZ = "America/Chicago"
WEEK_S = 7 * 24 * 60 * 60

#: Rows other processes keep. The Vercel watchdog writes 'watchdog' each time
#: cron-job.org calls it, so the worker notices if that external check dies.
#: Watched for staleness only, with their own incident state (_check_external).
EXTERNAL_JOBS = frozenset({"watchdog"})

FAILURES_TO_ALERT = 2

#: The health monitor's own job name. It is exempt from its own staleness
#: check (see check_health).
MONITOR = "health_monitor"

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
        # The monitor never judges its own staleness: if this line runs, the
        # monitor is running. Across a redeploy its row is legitimately old
        # (the old process stopped, the new one waits start_delay_s), which
        # used to push a false "needs attention" + "Recovered" pair on every
        # deploy. A monitor that has truly stopped can't report itself; the
        # external watchdog covers that. Its own failures still count.
        stale = row.stale and row.job != MONITOR
        unhealthy = failing or stale
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


# ------------------------------------------------------- summary backlog alert

#: None = not yet known in this process (after a restart).
_backlog_alerted: bool | None = None
BACKLOG_KIND = "feed_summary_backlog"
BACKLOG_REALERT_HOURS = 24


def check_summary_backlog() -> int:
    """The summarizer's backlog (owner, 2026-10-12): ONE push when more than
    BACKLOG_ALERT kept stories older than BACKLOG_MIN_AGE_H are unsummarized,
    re-armed once it falls back to BACKLOG_ALERT or fewer. After a restart a
    push sent within BACKLOG_REALERT_HOURS is adopted, not repeated. Returns
    the count."""
    global _backlog_alerted
    n = ledger.feed_summary_backlog(feed_summarize.BACKLOG_MIN_AGE_H, feed_summarize.MAX_ATTEMPTS)
    over = n > feed_summarize.BACKLOG_ALERT
    if _backlog_alerted is None:
        _backlog_alerted = over and ledger.notification_sent_since(BACKLOG_KIND, BACKLOG_REALERT_HOURS)
    if over and not _backlog_alerted:
        if _alert(push.Message(
            kind=BACKLOG_KIND,
            title="Feed summaries are behind",
            body=(f"{n} stories older than {feed_summarize.BACKLOG_MIN_AGE_H} h have no summary yet. "
                  "Check feed_summarize on the health page (capped, or the AI budget)."),
            deep_link=HEALTH_LINK, tag="feed-backlog",
        )):
            _backlog_alerted = True
            log.warning("alerted: summary backlog %d", n)
    elif not over and _backlog_alerted:
        _backlog_alerted = False
        log.info("summary backlog back to %d: alert re-armed", n)
    return n


def monitor_pass(scheduled: Iterable[str]) -> None:
    """The health monitor's minute: every job's health, then the backlog."""
    check_health(scheduled)
    check_summary_backlog()


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


# ---------------------------------------------------------------------- goals

#: A soft cap: the web allows an 11th weekly goal and warns about it.
WEEKLY_GOAL_CAP = 10
ROLLOVER_HORIZONS: tuple[ledger.RolloverHorizon, ...] = ("weekly", "monthly")
#: Far more ended periods than a real outage leaves behind. Reaching it means
#: the due list is not shrinking, which is a bug to surface, not to loop on.
ROLLOVER_MAX_STEPS = 200


def roll_over_goals() -> int:
    """Carry every ended period not yet rolled, oldest first, for both
    horizons. Returns goals carried. Carrying a period can make the next one
    due (its new rows are open), so this asks again after each carry."""
    total = 0
    for horizon in ROLLOVER_HORIZONS:
        for _ in range(ROLLOVER_MAX_STEPS):
            due = ledger.goal_periods_to_roll(horizon)
            if not due:
                break
            carried = ledger.carry_over_goals(horizon, due[0])
            if carried == 0:
                raise RuntimeError(
                    f"goals rollover: {horizon} {due[0]} is listed as due but nothing carried")
            log.info("goals: carried %d %s goal(s) from %s", carried, horizon, due[0])
            total += carried
        else:
            raise RuntimeError(
                f"goals rollover: {horizon} still has periods due after {ROLLOVER_MAX_STEPS} steps")
    return total


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def monday_message(counts: ledger.WeekGoalCounts) -> push.Message:
    slots = max(0, WEEKLY_GOAL_CAP - counts.held)
    return push.Message(
        kind="goals_monday",
        title="Set your week",
        body=f"{counts.carried_in} carried in. {_plural(slots, 'slot')} left.",
        deep_link=GOALS_LINK,
        tag="goals-week",
    )


def goals_monday_push() -> None:
    """Roll first (00:01 may have been missed; it is idempotent), then push.
    No device to push to raises, so the job records a failure."""
    roll_over_goals()
    push.send(monday_message(ledger.this_week_goal_counts()))


# -------------------------------------------------------------------- capital

def capital_span(first: date, today: date) -> tuple[date, date] | None:
    """The ended days a mode's history covers: its first day through
    yesterday (owner's timezone). None when its first day is today, because
    no day with data has ended yet."""
    yesterday = today - timedelta(days=1)
    return None if first > yesterday else (first, yesterday)


def snapshot_capital() -> dict[str, int]:
    """Snapshot every mode that has data, separately. Returns rows written
    per mode. Idempotent: days already snapshotted are skipped."""
    today = ledger.capital_local_today()
    written: dict[str, int] = {}
    for mode in ledger.CAPITAL_MODES:
        first = ledger.capital_first_day(mode)
        if first is None:
            continue  # no data in this mode: nothing to snapshot, no rows at all
        span = capital_span(first, today)
        written[mode] = 0 if span is None else ledger.write_capital_snapshots(mode, *span)
    if any(written.values()):
        log.info("capital: wrote %s", ", ".join(f"{m} {n}" for m, n in written.items()))
    return written


# ----------------------------------------------------------------------- feed

FEED_INTERVAL_S = 30 * 60

#: Run by feed_ingest_job, never by the scheduler (scheduled=False). The
#: trigger is required by SystemJob and unused.
FEED_SUMMARIZE = SystemJob("feed_summarize", FEED_INTERVAL_S, IntervalTrigger(seconds=FEED_INTERVAL_S),
                           feed_summarize.summarize_job, scheduled=False)
#: Chained the same way: feed_score after feed_summarize, feed_why after it.
FEED_SCORE = SystemJob("feed_score", FEED_INTERVAL_S, IntervalTrigger(seconds=FEED_INTERVAL_S),
                       feed_profile_mod.score_new_job, scheduled=False)
FEED_WHY = SystemJob("feed_why", FEED_INTERVAL_S, IntervalTrigger(seconds=FEED_INTERVAL_S),
                     feed_why.why_job, scheduled=False)


def _chain(*jobs: SystemJob) -> None:
    """Run each chained job in turn; one failing never stops the next. Each
    records its own outcome in job_health (run_tracked)."""
    for job in jobs:
        try:
            run_tracked(job)
        except Exception:
            pass  # recorded in job_health and logged by run_tracked


def feed_ingest_job() -> None:
    """Ingest, then summarize, score and write why-lines for whatever ingest
    produced, even if ingest failed partway (its stories so far are worth
    it). A chained failure is recorded in its own job_health row and does not
    mark ingest failed; an ingest failure still propagates."""
    try:
        feed_ingest.ingest_job()
    finally:
        _chain(FEED_SUMMARIZE, FEED_SCORE, FEED_WHY)


def feed_profile_job() -> None:
    """Rebuild the profiles and rescore (skipped when built today), then the
    why-lines for whatever entered Home's top 8."""
    try:
        feed_profile_mod.profile_job()
    finally:
        _chain(FEED_WHY)


def feed_prune() -> dict[str, int]:
    counts = ledger.feed_prune()
    log.info("feed prune: %s", ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


# ---------------------------------------------------------------------- drill

def health_drill() -> None:
    raise RuntimeError("health drill: this job fails on purpose (HEALTH_DRILL=true)")


# ------------------------------------------------------------------- registry

def brief_trigger() -> OrTrigger:
    """Every quarter-hour attempt from 06:15 to 09:30 (core/brief.retry_times)."""
    by_hour: dict[int, list[int]] = {}
    for t in brief.retry_times():
        by_hour.setdefault(t.hour, []).append(t.minute)
    return OrTrigger([
        CronTrigger(hour=h, minute=",".join(str(m) for m in ms), timezone=OWNER_TZ)
        for h, ms in sorted(by_hour.items())
    ])


def build_system_jobs(env: Mapping[str, str] = os.environ) -> list[SystemJob]:
    jobs = [
        SystemJob("heartbeat", 60, IntervalTrigger(seconds=60), heartbeat),
        SystemJob("db_size", 24 * 60 * 60,
                  CronTrigger(hour=3, minute=0, timezone="America/Chicago"),
                  check_db_size, misfire_grace=3600),
        SystemJob("job_queue", 10, IntervalTrigger(seconds=10), poll_queue, misfire_grace=9),
        SystemJob("goals_rollover", WEEK_S,
                  OrTrigger([
                      CronTrigger(day_of_week="mon", hour=0, minute=1, timezone=OWNER_TZ),
                      CronTrigger(day=1, hour=0, minute=1, timezone=OWNER_TZ),
                  ]),
                  roll_over_goals, misfire_grace=3600),
        SystemJob("goals_monday_push", WEEK_S,
                  CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=OWNER_TZ),
                  goals_monday_push, misfire_grace=3600, run_at_boot=False),
        SystemJob("capital_snapshot", 24 * 60 * 60,
                  CronTrigger(hour=0, minute=5, timezone=OWNER_TZ),
                  snapshot_capital, misfire_grace=3600),
        SystemJob("morning_brief", 24 * 60 * 60, brief_trigger(), brief.morning_brief_job, misfire_grace=600),
        SystemJob("morning_brief_push", 24 * 60 * 60,
                  CronTrigger(hour=brief.PUSH_AT.hour, minute=brief.PUSH_AT.minute, timezone=OWNER_TZ),
                  brief.morning_brief_push_job, misfire_grace=600),
        SystemJob("feed_ingest", FEED_INTERVAL_S, IntervalTrigger(seconds=FEED_INTERVAL_S),
                  feed_ingest_job, misfire_grace=600, start_delay_s=20),
        FEED_SUMMARIZE,
        FEED_SCORE,
        FEED_WHY,
        SystemJob("feed_profile", 24 * 60 * 60, CronTrigger(hour=4, minute=0, timezone=OWNER_TZ),
                  feed_profile_job, misfire_grace=3600, start_delay_s=60),
        SystemJob("feed_prune", 24 * 60 * 60, CronTrigger(hour=3, minute=15, timezone=OWNER_TZ),
                  feed_prune, misfire_grace=3600, run_at_boot=False),
    ]
    if env.get("HEALTH_DRILL", "").strip().lower() == "true":
        log.warning("HEALTH_DRILL=true: scheduling health_drill, which always fails")
        jobs.append(SystemJob("health_drill", 60, IntervalTrigger(seconds=60), health_drill))
    # The monitor watches every job above, including itself.
    names = [j.name for j in jobs] + [MONITOR]
    jobs.append(SystemJob(MONITOR, 60, IntervalTrigger(seconds=60),
                          lambda: monitor_pass(names), start_delay_s=90))
    return jobs
