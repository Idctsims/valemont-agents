"""Scheduler and agent registry. One persistent process, one place timing lives.

Crypto ticks on an interval, equities wake on market hours, props fire against
slate times. APScheduler holds those rules. This process is the thing that is
alive 24/7 on Railway.

Two jobs per agent, deliberately separate:

    <slug>:run      the observe/thesis/commit cycle, on the agent's schedule
    <slug>:sweep    resolution, on its own cadence

They are split because they answer to different clocks. A crypto agent might
form a view every fifteen minutes while its positions resolve hourly; an
equities agent wakes at the open and resolves at the close. Wiring resolution
to the commit schedule would tie two unrelated rhythms together and make one
of them wrong.

Failure posture, since this runs unattended at 3am:

  * A job that raises does not kill the scheduler. `BaseAgent` already catches
    and records; this module adds an APScheduler error listener as the last
    net, so even a bug in core is visible rather than silent.
  * `max_instances=1` and `coalesce=True`: a slow tick is skipped, never
    stacked. Two concurrent runs of one agent could write two commitments off
    one observation.
  * `misfire_grace_time` bounds how late a missed job may still fire. After a
    process restart, a wake-up from an hour ago is stale — the market moved.
  * SIGTERM (which is what Railway sends) drains in-flight jobs, then closes
    the pool.
"""

from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from types import FrameType
from typing import Any, Callable, Iterable, Sequence

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, JobEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core import ledger
from core.agent import BaseAgent, DeferPolicy

__all__ = [
    "Orchestrator",
    "Schedule",
    "Registration",
    "every",
    "cron",
]

log = logging.getLogger("valemont.orchestrator")

#: A job that hasn't fired within this long of its scheduled time is stale
#: rather than late. The observation it would have acted on has expired.
DEFAULT_MISFIRE_GRACE = 120

#: How long to let in-flight jobs finish on SIGTERM before exiting anyway.
#: Railway's grace period before SIGKILL is short; do not exceed it.
SHUTDOWN_TIMEOUT = timedelta(seconds=20)


# ---------------------------------------------------------------------------
# Schedule configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Schedule:
    """When an agent runs and when its commitments get swept.

    `run` may be None for an agent that only resolves — a useful state for
    winding one down without orphaning its open commitments.
    """

    run: BaseTrigger | None
    sweep: BaseTrigger
    #: Close-capture cadence. Required for an agent with captures_close=True,
    #: ignored otherwise. Run it TIGHTER than the sweep: a missed resolution
    #: retries until it voids, but a missed close is gone for good.
    capture: BaseTrigger | None = None
    #: Bounds lateness independently per agent: a crypto tick is stale in
    #: minutes, a props sweep against a game slate is not.
    misfire_grace: int = DEFAULT_MISFIRE_GRACE


def every(
    *,
    seconds: int = 0,
    minutes: int = 0,
    hours: int = 0,
    jitter: int | None = None,
) -> IntervalTrigger:
    """A fixed interval. `jitter` spreads load so agents don't all fire at :00."""
    return IntervalTrigger(
        seconds=seconds, minutes=minutes, hours=hours, jitter=jitter
    )


def cron(expression: str, timezone: str = "UTC") -> CronTrigger:
    """A crontab expression. UTC by default — see CLAUDE.md §5 on timestamps.

    Pass an exchange timezone explicitly for market-hours agents ('America/
    New_York'), so daylight saving is handled by the library rather than by
    someone remembering twice a year.
    """
    return CronTrigger.from_crontab(expression, timezone=timezone)


@dataclass(frozen=True, slots=True)
class Registration:
    """One agent, its schedule, and its patience. The whole per-agent config.

    Adding a fourth agent is one of these plus one file in `adapters/`. If it
    ever takes more than that, `core/` isn't doing its job (CLAUDE.md §3).
    """

    agent: BaseAgent
    schedule: Schedule
    #: Overrides the adapter's own default. Patience is domain-specific: a
    #: postponed NFL game is legitimately unresolvable for a week, a crypto
    #: feed silent for an hour is broken.
    defer_policy: DeferPolicy | None = None
    #: Patience for close capture. Separate from defer_policy because the
    #: failure costs differ: an unresolved commitment can wait, an uncaptured
    #: close cannot be recovered.
    capture_policy: DeferPolicy | None = None
    #: Most commitments this agent may write in one tick. Overrides the
    #: adapter's own default. A board-committing adapter must raise it
    #: deliberately; core refuses the whole slate rather than half-writing one.
    max_slate_size: int | None = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Owns the scheduler, the registry, and the shutdown path.

    Not a supervisor. It starts agents on a clock and records what happened;
    it does not read their output, override them, or make decisions for them.
    The chief of staff doesn't either (CLAUDE.md §4) — nothing in this system
    has authority over an agent's thesis.
    """

    def __init__(self, timezone: str = "UTC") -> None:
        self._scheduler = BackgroundScheduler(
            timezone=timezone,
            job_defaults={
                # A slow run must never overlap itself: two concurrent ticks
                # could commit twice off one observation.
                "max_instances": 1,
                # Catching up on a backlog of missed ticks is worse than
                # skipping them. Run once, now, with current data.
                "coalesce": True,
                "misfire_grace_time": DEFAULT_MISFIRE_GRACE,
            },
        )
        self._registry: dict[str, Registration] = {}
        self._stopping = threading.Event()
        self._scheduler.add_listener(
            self._on_job_problem, EVENT_JOB_ERROR | EVENT_JOB_MISSED
        )

    # -- registry -----------------------------------------------------------

    def register(self, registration: Registration) -> None:
        """Add an agent. Raises rather than silently replacing a duplicate."""
        slug = registration.agent.slug
        if slug in self._registry:
            raise ValueError(
                f"{slug!r} is already registered. Two registrations for one "
                f"agent would double its commitments."
            )
        if registration.defer_policy is not None:
            registration.agent.defer_policy = registration.defer_policy
        if registration.capture_policy is not None:
            registration.agent.capture_policy = registration.capture_policy
        if registration.max_slate_size is not None:
            registration.agent.slate_cap = registration.max_slate_size
        if registration.agent.captures_close and registration.schedule.capture is None:
            raise ValueError(
                f"{slug!r} sets captures_close=True but its Schedule has no "
                f"capture trigger. An opted-in agent with no capture job would "
                f"accumulate commitments whose close is silently never taken — "
                f"and a close missed is a measurement lost for good."
            )
        if registration.schedule.capture is not None and not registration.agent.captures_close:
            log.warning(
                "%s has a capture schedule but captures_close=False — "
                "the capture job will not be scheduled", slug,
            )
        self._registry[slug] = registration
        log.info(
            "registered %s (run=%s, sweep=%s, patience=%d attempts / %s)",
            slug,
            registration.schedule.run or "none",
            registration.schedule.sweep,
            registration.agent.defer_policy.max_attempts,
            registration.agent.defer_policy.max_overdue,
        )
        if registration.agent.slate_cap != BaseAgent.max_slate_size:
            log.info("  %s slate cap: %d", slug, registration.agent.slate_cap)

    def register_all(self, registrations: Iterable[Registration]) -> None:
        for registration in registrations:
            self.register(registration)

    @property
    def agents(self) -> Sequence[BaseAgent]:
        return [r.agent for r in self._registry.values()]

    # -- job bodies ---------------------------------------------------------

    def _run_agent(self, slug: str) -> None:
        """Body of a `:run` job. Never raises — BaseAgent has already recorded."""
        if self._stopping.is_set():
            log.info("shutting down, skipping %s run", slug)
            return
        agent = self._registry[slug].agent
        outcome = agent.run_once()
        if outcome.status == "error":
            log.error("%s run %s errored: %s", slug, outcome.run_id, outcome.error)

    def _sweep_agent(self, slug: str) -> None:
        """Body of a `:sweep` job."""
        if self._stopping.is_set():
            log.info("shutting down, skipping %s sweep", slug)
            return
        agent = self._registry[slug].agent
        outcome = agent.resolve_due()
        if outcome.due:
            log.info(
                "%s sweep: due=%d resolved=%d deferred=%d voided=%d failed=%d",
                slug, outcome.due, outcome.resolved, outcome.deferred,
                outcome.voided, outcome.failed,
            )
        if outcome.voided:
            log.warning(
                "%s abandoned %d commitment(s) — check resolve(), not the data",
                slug, outcome.voided,
            )

    def _capture_agent(self, slug: str) -> None:
        """Body of a `:capture` job."""
        if self._stopping.is_set():
            log.info("shutting down, skipping %s capture", slug)
            return
        agent = self._registry[slug].agent
        outcome = agent.capture_due()
        if outcome.due:
            log.info(
                "%s capture: due=%d captured=%d deferred=%d missed=%d failed=%d",
                slug, outcome.due, outcome.captured, outcome.deferred,
                outcome.missed, outcome.failed,
            )
        if outcome.missed:
            log.warning(
                "%s permanently lost the close on %d commitment(s) — "
                "unrecoverable, check capture cadence", slug, outcome.missed,
            )

    # -- lifecycle ----------------------------------------------------------

    def _wire(self) -> int:
        """Schedule every agent that passes BOTH gates. Returns how many did.

        Gate 1 is code: a `Registration` exists and its `enabled` is True.
        Gate 2 is data: `agents.enabled` is true in the database. Either can
        stop an agent; neither alone can start one. That lets an agent be
        parked with a pasted migration, without a deploy, and stops a
        registration that shipped early from trading until the row says so.
        """
        wired = 0
        for slug, registration in self._registry.items():
            if not registration.enabled:
                log.warning(
                    "%s is registered but disabled in its Registration — "
                    "not scheduling", slug,
                )
                continue
            if not ledger.agent_enabled(slug):
                log.warning(
                    "%s is registered but agents.enabled is false in the "
                    "database — not scheduling", slug,
                )
                continue
            wired += 1

            schedule = registration.schedule
            if schedule.run is not None:
                self._scheduler.add_job(
                    self._run_agent,
                    trigger=schedule.run,
                    args=[slug],
                    id=f"{slug}:run",
                    name=f"{slug} run",
                    misfire_grace_time=schedule.misfire_grace,
                    replace_existing=True,
                )
            else:
                log.info("%s has no run schedule — resolve-only", slug)

            self._scheduler.add_job(
                self._sweep_agent,
                trigger=schedule.sweep,
                args=[slug],
                id=f"{slug}:sweep",
                name=f"{slug} sweep",
                misfire_grace_time=schedule.misfire_grace,
                replace_existing=True,
            )

            if registration.agent.captures_close and schedule.capture is not None:
                self._scheduler.add_job(
                    self._capture_agent,
                    trigger=schedule.capture,
                    args=[slug],
                    id=f"{slug}:capture",
                    name=f"{slug} capture",
                    misfire_grace_time=schedule.misfire_grace,
                    replace_existing=True,
                )
        return wired

    def start(self) -> None:
        """Prove the database works, wire the jobs, then start ticking.

        The connection check is first and deliberate. A worker that starts
        cleanly and only discovers at the first commit that it cannot reach
        Supabase is exactly the silent 3am failure this project is built to
        avoid — better to refuse to boot.
        """
        if not self._registry:
            raise RuntimeError("No agents registered. Nothing to orchestrate.")

        for slug in self._registry:
            # Resolves through the ledger, so an agent whose row is missing (or
            # whose migration was never pasted) fails here, at boot, by name.
            self._registry[slug].agent.agent_id

        if self._wire() == 0:
            # A worker with no jobs would sit "up" forever doing nothing, and
            # look healthy while doing it. Refuse instead, by name.
            raise RuntimeError(
                "Every registered agent is disabled "
                f"({', '.join(self._registry)}) — in its Registration or in "
                "agents.enabled. Nothing to schedule; refusing to start idle."
            )
        self._scheduler.start()
        log.info(
            "orchestrator up — %d agent(s), %d job(s)",
            len(self._registry), len(self._scheduler.get_jobs()),
        )
        for job in self._scheduler.get_jobs():
            log.info("  %-24s next: %s", job.id, job.next_run_time)

    def shutdown(self, drain: bool = True) -> None:
        """Stop taking work, let in-flight jobs finish, close the pool.

        Idempotent: SIGTERM followed by SIGINT, or a double Ctrl-C, must not
        raise on the way out.
        """
        if self._stopping.is_set():
            log.info("shutdown already in progress")
            return
        self._stopping.set()
        log.info("shutting down (drain=%s)", drain)

        try:
            self._scheduler.shutdown(wait=drain)
        except Exception:
            log.error("scheduler did not shut down cleanly", exc_info=True)

        try:
            ledger.close_pool()
        except Exception:
            log.error("connection pool did not close cleanly", exc_info=True)

        log.info("orchestrator down")

    def run_forever(self) -> None:
        """Start, install signal handlers, and block until told to stop.

        Railway sends SIGTERM. Ctrl-C sends SIGINT. Both drain and exit 0 —
        a clean stop is not a crash and should not look like one in the logs.
        """
        self.start()
        stopped = threading.Event()

        def _handle(signum: int, _frame: FrameType | None) -> None:
            log.info("caught %s", signal.Signals(signum).name)
            stopped.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):
                # Not on the main thread, or the platform disallows it. Worth
                # saying out loud: without this, SIGTERM kills mid-write.
                log.warning("could not install %s handler", sig.name)

        try:
            stopped.wait()
        finally:
            self.shutdown()

    # -- last net -----------------------------------------------------------

    def _on_job_problem(self, event: JobEvent) -> None:
        """Log what APScheduler noticed that the agent didn't.

        `BaseAgent.run_once` catches its own exceptions, so an error arriving
        here means core itself failed — or the ledger is unreachable and the
        agent could not even record its failure. Either way it must be loud.
        """
        if event.code == EVENT_JOB_MISSED:
            log.warning(
                "job %s missed its window and was skipped as stale", event.job_id
            )
            return
        log.error(
            "job %s escaped its own error handling — this is a core bug or a "
            "dead database", event.job_id,
            exc_info=getattr(event, "exception", None),
        )


# ---------------------------------------------------------------------------
# Default wiring
# ---------------------------------------------------------------------------

def build_default() -> Orchestrator:
    """The live roster. Grows one `Registration` at a time.

    Harness agents are deliberately absent: the fake agent is wired in
    `scripts/run_fake.py`, so nothing can accidentally ship one to production.
    """
    from adapters.crypto import CRYPTO_DEFER_POLICY, CryptoAgent

    orchestrator = Orchestrator()
    orchestrator.register(
        Registration(
            agent=CryptoAgent(),
            schedule=Schedule(
                # Quarter-hourly. The rule reads hourly candles, so ticking
                # faster would re-examine the same bar; jitter keeps us off
                # the exact minute boundary the whole internet polls on.
                run=every(minutes=15, jitter=45),
                # Resolution is on its own clock: positions are judged six
                # hours out, so a half-hourly sweep bounds how long a resolved
                # position sits unscored without hammering the feed.
                sweep=every(minutes=30, jitter=60),
                misfire_grace=300,
            ),
            # Explicit even though it matches the adapter's own default —
            # CLAUDE.md §9.2 wants patience chosen per agent, and a default
            # that happens to be right is still a default nobody chose.
            defer_policy=CRYPTO_DEFER_POLICY,
        )
    )
    # Step 5 adds equities here. One file, one Registration.
    return orchestrator


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
    )
    build_default().run_forever()
