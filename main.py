"""Railway entry point. Canary-gated so the fake agent cannot ship by accident.

Set CANARY=true in the environment to register `_fake` and run forever.
Anything else — unset, false, misspelled — exits immediately with a clear
message and an empty registry. The live roster belongs in
`core.orchestrator.build_default()` later; it is not wired here.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
    )
    log = logging.getLogger("valemont.main")

    if os.getenv("CANARY", "").strip().lower() != "true":
        sys.exit(
            "No agents registered. Set CANARY=true to run the fake canary "
            "agent on this worker. Refusing to start empty — the canary must "
            "not be able to ship by accident."
        )

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
