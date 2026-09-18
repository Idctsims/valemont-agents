"""Drive the fake agent end to end against the real database.

    python scripts/run_fake.py                 deterministic, ~60s, exact rows
    python scripts/run_fake.py --orchestrator  same agent under APScheduler

The default mode drives `BaseAgent` directly on a fixed sequence so every row
is predictable before you start. It is not a mock: the ledger, the schema, the
triggers and the bounded-defer policy are all real. The only thing faked is
the market data.

`--orchestrator` runs the identical agent through `core.orchestrator` on fast
timers, which is the shape that will run on Railway. Use it to prove the
scheduler path; use the default to check the rows.

The harness lives here rather than in `core.orchestrator.build_default()` so a
test agent cannot accidentally ship to production.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from adapters._fake import HORIZON, SCRIPT, build
from core import ledger
from core.orchestrator import Orchestrator, Registration, Schedule, every

load_dotenv()

log = logging.getLogger("valemont.run_fake")

#: Sweeps are cheap; the wait is dominated by HORIZON. Enough passes to let
#: #2 defer once and #4 exhaust its three attempts and get abandoned.
MAX_SWEEPS = 8
SWEEP_PAUSE = timedelta(seconds=2)


def drive() -> int:
    """Commit the whole script, wait out the horizon, then sweep to completion."""
    agent = build()
    log.info("agent %s (id=%s, is_test=%s)", agent.slug, agent.agent_id,
             ledger.agent_is_test(agent.slug))
    log.info("patience: %d attempts / %s",
             agent.defer_policy.max_attempts, agent.defer_policy.max_overdue)

    print(f"\n-- committing {len(SCRIPT)} scripted commitments --")
    committed: list[int] = []
    for _ in range(len(SCRIPT)):
        outcome = agent.run_once()
        if outcome.commitment_id is None:
            print(f"   run {outcome.run_id}: idle ({outcome.error or 'no commitment'})")
            continue
        committed.append(outcome.commitment_id)
        print(f"   run {outcome.run_id}: committed #{outcome.commitment_id}")

    # The script is exhausted; this tick should go idle, which is the path a
    # real agent takes most of the time.
    idle = agent.run_once()
    print(f"   run {idle.run_id}: idle (script exhausted) -> "
          f"status={idle.status}, committed={idle.committed}")

    if not committed:
        print("\nNothing committed. Nothing to sweep.")
        return 1

    wait = HORIZON.total_seconds() + 2
    print(f"\n-- waiting {wait:.0f}s for resolves_after to pass --")
    print("   (resolutions before then are rejected by the resolutions_timing trigger)")
    time.sleep(wait)

    print("\n-- sweeping --")
    for i in range(1, MAX_SWEEPS + 1):
        s = agent.resolve_due()
        if s.due == 0:
            print(f"   sweep {i}: nothing due — done")
            break
        print(f"   sweep {i}: due={s.due} resolved={s.resolved} "
              f"deferred={s.deferred} voided={s.voided} failed={s.failed}")
        if s.voided:
            print("            ^ bounded defer fired — #4 abandoned as void")
        time.sleep(SWEEP_PAUSE.total_seconds())
    else:
        print(f"   still not settled after {MAX_SWEEPS} sweeps — that is a bug")
        return 1

    print(f"\nCommitment ids this run: {committed}")
    print("Query them with the SQL in the run notes.")
    return 0


def orchestrated(seconds: int) -> int:
    """Same agent, real scheduler, fast timers. Proves the 24/7 shape."""
    agent = build()
    orchestrator = Orchestrator()
    orchestrator.register(
        Registration(
            agent=agent,
            schedule=Schedule(run=every(seconds=5), sweep=every(seconds=10)),
        )
    )
    orchestrator.start()
    print(f"\nRunning under APScheduler for {seconds}s. Ctrl-C to stop early.\n")
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        orchestrator.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orchestrator", action="store_true",
        help="run under APScheduler instead of driving the loop directly",
    )
    parser.add_argument(
        "--seconds", type=int, default=150,
        help="how long to run in --orchestrator mode (default: 150)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
    )

    try:
        return orchestrated(args.seconds) if args.orchestrator else drive()
    except Exception:
        log.exception("harness failed")
        return 1
    finally:
        ledger.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
