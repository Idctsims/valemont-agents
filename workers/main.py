"""Railway entry point.

The worker always runs its system jobs (core/system_jobs.py: heartbeat,
health monitor, db size, job queue). Agents are added on top, governed by
ROSTER and CANARY exactly as before:

    ROSTER=production   the approved roster (core.orchestrator.build_production)
    CANARY=true         the fake canary, every CANARY_INTERVAL_S seconds
                        (default 60, minimum 5; anything else refuses)
    neither             no agents; system jobs only. Not a refusal: the
                        heartbeat and health monitor are worth running alone.

Still refused, as configuration errors: both set, an unknown ROSTER, a bad
CANARY_INTERVAL_S, a missing DATABASE_URL. Refusals sleep before exiting so
Railway's restart loop is slow (~once a minute) rather than twice a second,
and use exit code 78 (EX_CONFIG) so they are distinguishable from a crash.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import NoReturn

from core.paths import load_env

load_env()

#: sysexits.h EX_CONFIG — config refusal, not a crash.
CONFIG_EXIT = 78
#: How long to sit before exiting so Railway does not thrash the container.
REFUSAL_SLEEP_SECONDS = 60


def _refuse(log: logging.Logger, reason: str) -> NoReturn:
    log.error("%s", reason)
    log.error(
        "config refusal (exit %d) — sleeping %ds before exit to slow the "
        "restart loop",
        CONFIG_EXIT,
        REFUSAL_SLEEP_SECONDS,
    )
    time.sleep(REFUSAL_SLEEP_SECONDS)
    raise SystemExit(CONFIG_EXIT)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
    )
    log = logging.getLogger("valemont.main")

    canary = os.getenv("CANARY", "").strip().lower() == "true"
    roster = os.getenv("ROSTER", "").strip().lower()
    if canary and roster:
        _refuse(log, "Both CANARY=true and ROSTER are set. Pick one: the canary "
                     "and the production roster never share a worker.")
    if roster and roster != "production":
        _refuse(log, f"ROSTER={roster!r} is not recognised. The only value is "
                     "'production'.")

    if not os.getenv("DATABASE_URL", "").strip():
        _refuse(
            log,
            "DATABASE_URL is not set. Add the Supabase Session pooler string "
            "to this service's environment variables.",
        )

    from core.orchestrator import Orchestrator, Registration
    from core.system_jobs import build_system_jobs

    if roster == "production":
        from core.orchestrator import build_production

        log.info("production roster — _kalshi_probe, nfl_ml (each two-gated)")
        orchestrator = build_production()
    elif canary:
        from adapters._fake import CanaryConfigError, build, canary_interval_s, canary_schedule

        # Validated before build(), which is the first database call.
        try:
            interval_s = canary_interval_s()
        except CanaryConfigError as exc:
            _refuse(log, str(exc))

        agent = build()
        orchestrator = Orchestrator()
        # One cadence for run, sweep and capture (CANARY_INTERVAL_S, default 60 s).
        # This is a canary schedule, not a production one.
        orchestrator.register(Registration(agent=agent, schedule=canary_schedule(interval_s)))
        log.info("canary mode — registering %s only, every %ds", agent.slug, interval_s)
    else:
        log.info("no ROSTER and no CANARY — no agents; system jobs only")
        orchestrator = Orchestrator()

    for job in build_system_jobs():
        orchestrator.register_system_job(job)
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
