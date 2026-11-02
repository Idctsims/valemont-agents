"""What the nightly feed prune would delete now, by step and table. Deletes nothing.

    ..\\venv\\Scripts\\python.exe -m scripts.feed_prune --dry-run      (from workers/)

ledger.feed_prune_dry_run: SELECTs only, in a READ ONLY transaction. The two
story rules print the stories and every row that cascades with them
(content_items, story_scores, feed_similarity_log), and protected_trailers,
which must be 0 (a story carrying a title's trailer is never deleted). The
other steps print the rows each would touch. Counts only, no titles.

The real prune is the worker's feed_prune job (03:15 America/Chicago); this
script has no delete mode on purpose.
"""

from __future__ import annotations

import argparse
import sys

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", required=True, help="count only (the only mode)")
    parser.parse_args(argv)
    load_env()
    try:
        counts = ledger.feed_prune_dry_run()
    finally:
        ledger.close_pool()
    print(f"kept stories after {ledger.KEPT_STORY_DAYS} days, filtered after {ledger.FILTERED_STORY_DAYS} "
          "(protected never: reactions, proposals, pushes, repo surfacings, trailers)")
    for step, tables in counts.items():
        print(f"{step}: " + ", ".join(f"{t} {n}" for t, n in tables.items()))
    bad = [s for s, t in counts.items() if t.get("protected_trailers")]
    if bad:
        print(f"STOP: trailer links would go in {bad}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
