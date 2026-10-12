r"""The feed by hand (feed/ingest.py, feed/summarize.py).

    ..\venv\Scripts\python.exe -m scripts.feed_run --sync          (from workers/)
    ..\venv\Scripts\python.exe -m scripts.feed_run --ingest
    ..\venv\Scripts\python.exe -m scripts.feed_run --summarize
    ..\venv\Scripts\python.exe -m scripts.feed_run --prune

--sync       upserts workers/feed/sources.toml into `sources` (the boot sync).
--ingest     one ingest run over every active source, exactly as the job does
             (sync first). It does NOT summarize: run --summarize for that, so
             the cost of a summary pass is always a separate, deliberate step.
--summarize  one summary pass (at most 60 stories, the daily cap applies).
--prune      the nightly prune now.

Prints counts only. Writes real rows, as the worker would.
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
    for flag in ("--sync", "--ingest", "--summarize", "--prune"):
        group.add_argument(flag, action="store_true")
    args = parser.parse_args(argv)

    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from feed import ingest, summarize  # after load_env: EMBED_* come from .env

    try:
        if args.sync:
            upserted, deactivated = ingest.sync_sources()
            print(f"sources: {upserted} upserted, {deactivated} deactivated")
        elif args.ingest:
            stats = ingest.ingest_job()
            print(f"ingest: {stats.line()}")
            for s in stats.sources:
                print(f"  {s.slug:24} {s.status:13} fetched {s.fetched:3}  new {s.new_stories:3}  "
                      f"joined {s.joined:3}  filtered {s.filtered:3}  seeded {s.seeded:3}  "
                      f"known {s.known:3}  decode_failed {s.decode_failed}"
                      + (f"  error: {s.error}" if s.error else ""))
        elif args.summarize:
            r = summarize.summarize_job()
            print(f"summarize: {r.accepted} accepted, {r.rejected} rejected; "
                  f"{r.retried} retried for copying, {r.retry_passed} passed the retry")
        else:
            print(f"prune: {ledger.feed_prune()}")
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
