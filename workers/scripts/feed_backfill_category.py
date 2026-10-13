r"""Backfill stories.category from Haiku topics (db/042; Chat 3 Phase 3).

    ..\venv\Scripts\python.exe -m scripts.feed_backfill_category              (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_backfill_category --dry-run

Every kept, real Home story with topics and no category gets one from the
deterministic mapping in feed/category.py (never from a guess). Stories with
no topics are left for the summary call. Safe to re-run: it fills only NULL
categories, so a story the summary call categorized is never changed.

Prints counts per category only (no titles).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from core import ledger
from core.paths import load_env
from feed import category


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = parser.parse_args(argv)
    load_env()
    rows = ledger.feed_uncategorized_home_stories()
    pairs = [(sid, c) for sid, topics in rows if (c := category.from_topics(topics)) is not None]
    counts = Counter(c for _, c in pairs)
    written = 0 if args.dry_run else ledger.feed_set_categories(pairs)
    print(f"{len(rows)} uncategorized Home stories; {len(pairs)} with topics; "
          f"{'would write' if args.dry_run else 'wrote'} {len(pairs) if args.dry_run else written}")
    for c in category.CATEGORIES:
        print(f"  {c:16} {counts.get(c, 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
