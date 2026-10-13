r"""The one-off categorize pass, by hand (feed/categorize.py; after db/042).

    ..\venv\Scripts\python.exe -m scripts.feed_categorize --dry-run      (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_categorize

--dry-run  counts the uncategorized Home stories and the calls it would
           make; no Claude call, nothing written.
(no flag)  categorizes them with Haiku, under the AI budget guard, and
           prints counts per category, ten samples each (random, seed 42:
           id and headline) for the owner's review, and the cost recorded
           in ai_usage.

Writes real rows, as the worker would (stories.category, NULL ones only).
"""

from __future__ import annotations

import argparse
import math
import random
import sys

from core import ledger
from core.paths import load_env
from feed import categorize, category


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count only: no Claude call, nothing written")
    args = parser.parse_args(argv)
    load_env()
    jobs = ledger.feed_uncategorized_home_stories()
    calls = math.ceil(len(jobs) / categorize.GROUP_SIZE)
    if args.dry_run:
        print(f"{len(jobs)} uncategorized Home stories; {calls} Haiku call(s) of up to {categorize.GROUP_SIZE}")
        return 0
    res = categorize.run(jobs)
    print(f"asked {res.asked} of {len(jobs)} in {res.calls} call(s); wrote {res.written}; "
          f"cost ${res.cost_usd:.6f} (ai_usage, purpose {categorize.PURPOSE})")
    if res.stopped:
        print(f"STOPPED: {res.stopped}")
    rng = random.Random(42)
    for c in category.CATEGORIES:
        rows = res.by_category.get(c, [])
        print(f"\n## {category.LABELS[c]} ({c}): {len(rows)}")
        for sid, title in sorted(rng.sample(rows, min(10, len(rows)))):
            print(f"  {sid}\t{title[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
