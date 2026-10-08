"""System jobs: work the worker does for itself, as opposed to agents.

An agent has the observe/thesis/commit/resolve loop and records itself in
`runs`. A system job (heartbeat, the health monitor, the db size check, the
queue poller) is a plain function on a clock, and records itself in
`job_health`: one row per job, updated in place (db/020).

Every system job runs through `run_tracked`, which:

  1. marks the job started,
  2. runs it,
  3. on success, stamps last_ok_at and resets consecutive_failures;
     on failure, records a redacted, truncated error and counts it,
     then RE-RAISES, so the scheduler's error listener logs it as well.

Failures are recorded and never swallowed. A failure to record (the
database itself is down) is logged and the job's own exception still
propagates: the health monitor cannot see that case, which is what the
external watchdog (apps/web /api/watchdog) is for.

Lives in core, not adapters (CLAUDE.md §3): adapters are domain logic, and
scheduling, persistence and error handling are core's, written once.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from apscheduler.triggers.base import BaseTrigger

from core import ledger

log = logging.getLogger("valemont.jobs")

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Things that must never reach job_health.last_error or a push: database
#: URLs with credentials, key=value secrets, and bearer tokens.
#: Order matters: "Authorization: Bearer <token>" must lose the token itself,
#: not just the word "Bearer", so the bearer pattern runs first and the
#: key=value pattern swallows an optional "Bearer " prefix.
_SECRETS = (
    re.compile(r"\bBearer\s+\S+", re.I),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@[^\s]+", re.I),
    re.compile(r"\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*(Bearer\s+)?\S+", re.I),
)
ERROR_MAX = 300


def describe(exc: BaseException) -> str:
    """`TypeName: message`, secrets redacted, one line, at most ERROR_MAX chars."""
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    return text if len(text) <= ERROR_MAX else text[: ERROR_MAX - 1] + "…"


@dataclass(frozen=True)
class SystemJob:
    name: str
    #: What the health monitor holds it to: stale after 2× this.
    expected_interval_s: int
    trigger: BaseTrigger
    fn: Callable[[], None]
    #: A system job that fires late is still worth running; a minute is the
    #: shortest interval here, so tolerate most of one.
    misfire_grace: int = 50
    #: Seconds after boot for the first run. 0 runs it at once, which also
    #: proves it on every deploy. The health monitor waits longer, so it never
    #: judges a job that has not had its first chance to run.
    start_delay_s: int = 0

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"system job names are snake_case, got {self.name!r}")
        if self.expected_interval_s <= 0:
            raise ValueError("expected_interval_s must be positive")


def run_tracked(job: SystemJob) -> None:
    """Run `job`, recording the outcome in job_health. Re-raises on failure."""
    try:
        ledger.job_started(job.name)
    except Exception:
        log.error("%s: could not record start (database unreachable?)", job.name, exc_info=True)
    try:
        job.fn()
    except Exception as exc:
        detail = describe(exc)
        log.error("%s failed: %s", job.name, detail)
        try:
            ledger.job_failed(job.name, detail)
        except Exception:
            log.error("%s: could not record the failure either", job.name, exc_info=True)
        raise
    try:
        ledger.job_succeeded(job.name)
    except Exception:
        log.error("%s: ran, but could not record success", job.name, exc_info=True)
        raise
