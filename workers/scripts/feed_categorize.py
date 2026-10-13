r"""The one-off categorize pass, by hand (feed/categorize.py; after db/042).

    ..\venv\Scripts\python.exe -m scripts.feed_categorize --dry-run      (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_categorize
    ..\venv\Scripts\python.exe -m scripts.feed_categorize --recategorize sports_business,betting [--dry-run]

--dry-run  counts the stories and the calls it would make; no Claude call,
           nothing written.
(no flag)  categorizes the uncategorized Home stories with Haiku, under the
           AI budget guard, and prints counts per category, ten samples each
           (random, seed 42: id and headline) for the owner's review, and the
           cost recorded in ai_usage.
--recategorize C1,C2
           re-runs exactly the Home stories now in those categories, with the
           current definitions (owner, 2026-10-13): the id set is read once,
           a story moves only if its category is still the one read
           (compare-and-set), every move is logged and printed
           (id, old -> new, headline), and the cost is its own ai_usage
           purpose (feed_recategorize).
--money    the same re-run over the stories the money question decides
           (owner, 2026-10-13): Home stories with a sport, and those now in
           sports_business; betting is left out (ledger.feed_money_jobs).

Writes real rows, as the worker would.
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
    parser.add_argument("--recategorize", metavar="C1,C2",
                        help="re-run the stories now in these categories (comma-separated)")
    parser.add_argument("--money", action="store_true",
                        help="re-run the stories the money question decides (with a sport, or sports_business)")
    args = parser.parse_args(argv)
    if args.money and args.recategorize:
        parser.error("--money and --recategorize are separate re-runs")
    load_env()
    current: dict[int, str] | None = None
    if args.money:
        pairs = ledger.feed_money_jobs()
        jobs = [j for j, _ in pairs]
        current = {j.story_id: cur for j, cur in pairs}
        what = "Home stories the money question decides (with a sport, or in sports_business; not betting)"
    elif args.recategorize:
        cats = [c.strip() for c in args.recategorize.split(",") if c.strip()]
        unknown = [c for c in cats if category.valid(c) is None]
        if not cats or unknown:
            parser.error(f"unknown categories: {', '.join(unknown) or '(none given)'}")
        pairs = ledger.feed_category_jobs(cats)
        jobs = [j for j, _ in pairs]
        current = {j.story_id: cur for j, cur in pairs}
        what = f"Home stories now in {', '.join(cats)}"
    else:
        jobs = ledger.feed_uncategorized_home_stories()
        what = "uncategorized Home stories"
    calls = math.ceil(len(jobs) / categorize.GROUP_SIZE)
    if args.dry_run:
        print(f"{len(jobs)} {what}; {calls} Haiku call(s) of up to {categorize.GROUP_SIZE}")
        return 0
    res = categorize.run(jobs, current=current)
    purpose = categorize.RECAT_PURPOSE if current is not None else categorize.PURPOSE
    print(f"asked {res.asked} of {len(jobs)} in {res.calls} call(s); wrote {res.written}; "
          f"cost ${res.cost_usd:.6f} (ai_usage, purpose {purpose})")
    if res.stopped:
        print(f"STOPPED: {res.stopped}")
    if current is not None:
        titles = {j.story_id: j.title for j in jobs}
        print(f"\n{len(res.moves)} moved, {res.unchanged} unchanged")
        for sid, old, new in res.moves:
            print(f"  {sid}\t{old} -> {new}\t{titles[sid][:100]}")
        return 0
    rng = random.Random(42)
    for c in category.CATEGORIES:
        rows = res.by_category.get(c, [])
        print(f"\n## {category.LABELS[c]} ({c}): {len(rows)}")
        for sid, title in sorted(rng.sample(rows, min(10, len(rows)))):
            print(f"  {sid}\t{title[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
