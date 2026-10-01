"""Railway entry point. Canary-gated so the fake agent cannot ship by accident.

Set CANARY=true in the environment to register `_fake` and run forever.
Anything else — unset, false, misspelled — refuses to start with a clear
message and an empty registry. The live roster belongs in
`core.orchestrator.build_default()` later; it is not wired here.

Configuration refusals sleep before exiting so Railway's restart loop is
slow (~once a minute) rather than twice a second, and use exit code 78
(EX_CONFIG) so they are distinguishable from a real crash (exit 1).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import NoReturn

from dotenv import load_dotenv

load_dotenv()

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
    if not canary and roster != "production":
        _refuse(
            log,
            "No agents registered. Set CANARY=true to run the fake canary, or "
            "ROSTER=production to run the approved roster. Refusing to start "
            "empty — neither may ship by accident.",
        )

    if not os.getenv("DATABASE_URL", "").strip():
        _refuse(
            log,
            "DATABASE_URL is not set. Add the Supabase Session pooler string "
            "to this service's environment variables.",
        )

    if roster == "production":
        from core.orchestrator import build_production

        log.info("production roster — _kalshi_probe, nfl_ml (each two-gated)")
        build_production().run_forever()
        return

    from adapters._fake import build
    from core.orchestrator import Orchestrator, Registration, Schedule, every

    agent = build()
    orchestrator = Orchestrator()
    # Fast timers: the scripted horizon is ~45s and abandonment is watchable
    # in under a minute. This is a canary schedule, not a production one.
    orchestrator.register(
        Registration(
            agent=agent,
            schedule=Schedule(
                run=every(seconds=5),
                sweep=every(seconds=10),
            ),
        )
    )
    log.info("canary mode — registering %s only", agent.slug)
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
