"""Run the Kalshi board pull by hand (Chat 8 Phase 1; db/071).

    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_board --dry-run     (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_board --full
    ..\\venv\\Scripts\\python.exe -m scripts.kalshi_board --refresh   (after --full in the same process only)

--dry-run fetches and parses the whole 36 h board through the job's own code
path and writes NOTHING (no database connection is opened): it prints the job
note, the RSS before, at the end and at the peak, and the NO-side complement
check. --full runs the real job (writes through DATABASE_URL), as the worker
would. Read-only to Kalshi either way.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import memory  # noqa: E402
from core.paths import ENV_FILE  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--full", action="store_true")
    ap.add_argument("--refresh-after", action="store_true", help="with --full: then one refresh run")
    ap.add_argument("--flush", type=int, default=None, help="market rows buffered per write (measurement)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.full:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)
    from sports import kalshi_board as job

    if args.flush:
        job.FLUSH_ROWS = args.flush
    before = memory.rss_mb()
    with memory.PeakSampler() as s:
        run = job.full_job(dry_run=args.dry_run)
    print(run.job_note)
    print(f"rss before {before:.1f} MB, end {s.end_mb:.1f} MB, peak {s.peak_mb:.1f} MB "
          f"(+{s.peak_mb - before:.1f} MB over the start)")
    if args.full and args.refresh_after:
        r = job.refresh_job()
        print("refresh:", r.job_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
