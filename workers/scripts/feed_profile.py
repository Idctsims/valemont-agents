r"""The feed's interest profile by hand (feed/profile.py, feed/why.py).

    ..\venv\Scripts\python.exe -m scripts.feed_profile --rebuild        (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_profile --score
    ..\venv\Scripts\python.exe -m scripts.feed_profile --why
    ..\venv\Scripts\python.exe -m scripts.feed_profile --top 30

--rebuild  rebuilds every pillar's profile now, whatever was built today,
           then rescores the last 72 h (exactly what the 04:00 job does,
           without its once-a-day skip). Prints counts only.
--score    scores stories that finished summarizing and have no score yet.
--why      one feed_why pass (Haiku; the daily cap of 20 calls applies).
--top N    Home's For you, the first N positions: position, story id,
           floored / cross-pillar flags and the headline. Read-only.

Writes real rows, as the worker would (except --top).
"""

from __future__ import annotations

import argparse
import logging
import sys

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--rebuild", "--score", "--why"):
        group.add_argument(flag, action="store_true")
    group.add_argument("--top", type=int, metavar="N")
    parser.add_argument("--pillar", choices=("home", "sports"), default="home", help="for --top")
    args = parser.parse_args(argv)

    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from feed import profile, why  # after load_env: EMBED_* come from .env

    try:
        if args.rebuild:
            print(f"rebuild: {profile.rebuild().line()}")
        elif args.score:
            print(f"score: {profile.score_new_job()} scores written")
        elif args.why:
            print(f"why: {why.why_job()} line(s) kept")
        else:
            rows = ledger.feed_for_you(args.pillar, max(1, args.top))
            for r in rows:
                flags = ("F" if r.floored else "-") + ("X" if r.cross_pillar else "-")
                print(f"{r.pos:4} {r.id:7} {flags} {r.rank:+7.3f}  {r.title[:90]}")
            print(f"({len(rows)} of For you, {args.pillar})")
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
