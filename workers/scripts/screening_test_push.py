"""A TEST release push (Chat 4 Phase 4; db/053; owner, 2026-10-15).

    ..\\venv\\Scripts\\python.exe -m scripts.screening_test_push              (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.screening_test_push --now
    ..\\venv\\Scripts\\python.exe -m scripts.screening_test_push --title-id 123

By default it ARMS the push for the next 08:30 America/Chicago: one
`screening_test_release_push` job in job_queue, which the worker's poller
sends at 08:30 through the real path (screening/rec_jobs.release_message,
core/push.py), "Tonight (test): <title> on <service>", opening the title. With
--now it sends at once, from here. Either way it is recorded is_test in
release_pushes and release_alerts: exempt from the one-a-day rule and the
never-twice rule, and never blocking the real push. The title is --title-id,
else the newest Want or Watching title (one streaming tonight first). Needs a
subscribed device (/onboarding). Prints ids only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import ledger
from core.paths import load_env
from core.push import PushError


def next_0830(now: datetime) -> datetime:
    """The next 08:30 in America/Chicago after `now` (aware)."""
    tz = ZoneInfo("America/Chicago")
    local = now.astimezone(tz)
    at = local.replace(hour=8, minute=30, second=0, microsecond=0)
    if at <= local:
        at = (local + timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)
    return at


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now", action="store_true", help="send now instead of arming it for 08:30")
    ap.add_argument("--title-id", type=int, help="a titles.id (default: the newest Want or Watching title)")
    args = ap.parse_args(argv)
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from screening import rec_jobs

    try:
        if ledger.screening_test_push_title(args.title_id) is None:
            print("No title to announce: shelve a title as Want or Watching first (or pass a real --title-id).",
                  file=sys.stderr)
            return 1
        payload = {"title_id": args.title_id} if args.title_id else {}
        if args.now:
            try:
                push_id = rec_jobs.test_release_push(payload)
            except PushError as exc:
                print(f"Not sent: {exc}", file=sys.stderr)
                return 1
            print(f"test release push sent; release_pushes row {push_id} (is_test)")
            return 0
        at = next_0830(datetime.now(ZoneInfo("UTC")))
        job = ledger.queue_enqueue(rec_jobs.TEST_PUSH_KIND, payload, at)
        print(f"armed: job_queue #{job} sends the test release push at {at:%Y-%m-%d %H:%M} Chicago")
        return 0
    finally:
        ledger.close_pool()


if __name__ == "__main__":
    sys.exit(main())
