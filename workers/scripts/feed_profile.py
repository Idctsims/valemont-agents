r"""The feed's interest profile by hand (feed/profile.py, feed/why.py).

    ..\venv\Scripts\python.exe -m scripts.feed_profile --rebuild        (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_profile --score
    ..\venv\Scripts\python.exe -m scripts.feed_profile --why
    ..\venv\Scripts\python.exe -m scripts.feed_profile --top 30

--rebuild  rebuilds every pillar's profile now, whatever was built today,
           then rescores the last 72 h (exactly what the 04:00 job does,
           without its once-a-day skip). Prints counts only.
--score    scores stories that finished summarizing and have no score yet.
--why      one feed_why pass (Haiku; the daily cap of 100 calls applies).
           With --again ID,ID,... it forgets that those stories were asked
           and asks once more (at most 8), printing each line or null.
--repo-why --again ID,ID,...  the same for REPO stories (feed/why.py
           repo_ask_again: at most 8, one same-run retry for a refused line;
           the repo cap of 4 calls a day applies), printing each line.
--context  prints the exact venture context feed_why sends Haiku.
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
    for flag in ("--rebuild", "--score", "--why", "--repo-why", "--context"):
        group.add_argument(flag, action="store_true")
    group.add_argument("--top", type=int, metavar="N")
    parser.add_argument("--pillar", choices=("home", "sports"), default="home", help="for --top")
    parser.add_argument("--again", metavar="IDS",
                        help="with --why or --repo-why: story ids to ask again, comma-separated")
    args = parser.parse_args(argv)

    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from feed import profile, why  # after load_env: EMBED_* come from .env

    try:
        if args.rebuild:
            print(f"rebuild: {profile.rebuild().line()}")
        elif args.score:
            print(f"score: {profile.score_new_job()} scores written")
        elif args.why and args.again:
            ids = [int(x) for x in args.again.split(",") if x.strip()]
            titles = {j.story_id: j.title for j in ledger.feed_why_jobs(ids)}
            answers = why.ask_again(ids)
            for sid, line in answers.items():
                print(f"{sid:7}  {titles.get(sid, "?")[:80]}\n         -> {line if line else "null"}")
            for sid, line, reason in why.REFUSED:
                print(f"  refused {sid}: {reason}: {line!r}")
            print(f"why again: {sum(1 for a in answers.values() if a)} line(s), "
                  f"{sum(1 for a in answers.values() if not a)} null ({len(why.REFUSED)} refused by the rules), "
                  f"of {len(answers)}")
        elif args.repo_why:
            if not args.again:
                parser.error("--repo-why needs --again ID,ID,...")
            ids = [int(x) for x in args.again.split(",") if x.strip()]
            answers = why.repo_ask_again(ids)
            for sid, line in answers.items():
                print(f"{sid:7}  -> {line}")
            for sid, line, reason in why.REFUSED:
                print(f"  refused {sid}: {reason}: {line!r}")
            print(f"repo why again: {sum(1 for a in answers.values() if a != why.GENERAL)} concrete, "
                  f"{sum(1 for a in answers.values() if a == why.GENERAL)} general, of {len(answers)}")
        elif args.why:
            print(f"why: {why.why_job()} line(s) kept")
        elif args.context:
            print(why.json.dumps(why.venture_context(ledger.ctx_ventures()), ensure_ascii=False, indent=1))
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
