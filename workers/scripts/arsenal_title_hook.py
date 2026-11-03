"""Run the Screening Room hook by hand (db/062; docs/arsenal.md §10).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_title_hook --hook 12                       (from workers/)
    ...  --hook 12 --rerun --by "owner, 2026-11-04: the model refused"
    ...  --backfill --by "owner Nov 3" [--dry-run]

--hook does what the worker's job does: drafts a queued hook, then verifies
and outcome-checks its cards; a drafted hook only finishes what is left
(each step skips what is done). With --rerun it drafts a FAILED hook again,
and that is the only way one does: the re-run is written to
arsenal_title_hook_reruns (append-only, with --by and the database role) in
the same transaction as the new draft. A drafted hook never re-runs.

--backfill (owner, 2026-11-03): every real title currently Finished with no
hook (finished before db/062) gets its hook, noted "backfill (<by>)", and
ONE arsenal_title_hook job, in one transaction per title, exactly as the
trigger would have written them. The worker drafts them. --dry-run lists
the titles and writes nothing. A title that has a hook is never touched.

Prints ids, titles and states only; never a secret.
"""

from __future__ import annotations

import argparse
import sys

from arsenal import hook
from core import ledger
from core.paths import load_env


def backfill(by: str, *, dry_run: bool) -> int:
    todo = ledger.arsenal_hook_backfill_candidates()
    print(f"{len(todo)} real Finished title(s) with no hook" + (" (dry run: nothing written)" if dry_run else ""))
    for title_id, name, ws_id in todo:
        if dry_run:
            print(f"  title {title_id} {name!r}: would queue (finish {ws_id})")
            continue
        hook_id = ledger.arsenal_hook_backfill(title_id, ws_id, by)
        print(f"  title {title_id} {name!r}: " + (f"hook {hook_id} queued" if hook_id else "has a hook now; skipped"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--hook", type=int, help="arsenal_title_hooks id")
    what.add_argument("--backfill", action="store_true", help="queue every real Finished title with no hook")
    parser.add_argument("--rerun", action="store_true", help="with --hook: draft a FAILED hook again (recorded)")
    parser.add_argument("--by", help="who and why (required with --rerun and --backfill)")
    parser.add_argument("--dry-run", action="store_true", help="with --backfill: list, write nothing")
    args = parser.parse_args(argv)
    by = (args.by or "").strip()
    if (args.rerun or args.backfill) and not by:
        parser.error("--rerun and --backfill need --by")
    if args.rerun and args.backfill:
        parser.error("--rerun goes with --hook")
    if args.dry_run and not args.backfill:
        parser.error("--dry-run goes with --backfill")
    load_env()
    try:
        if args.backfill:
            return backfill(by, dry_run=args.dry_run)
        h = ledger.arsenal_title_hook(args.hook)
        if h is None:
            print(f"refused: no hook {args.hook}")
            return 1
        if args.rerun and h.status != "failed":
            print(f"refused, nothing written: hook {h.id} is {h.status}; only a failed hook is re-run (once per title)")
            return 1
        print(f"hook {h.id} (title {h.title_id}): {h.status} before")
        print(hook.run_hook(h.id, rerun_by=by if args.rerun else None))
        after = ledger.arsenal_title_hook(h.id)
        if after is not None:
            took = (after.settled_at - after.created_at).total_seconds() if after.settled_at else None
            print(f"hook {after.id}: {after.status}; refs {list(after.ref_ids)}; word {after.word_id}"
                  + (f"; {took:.0f} s from finish to {after.status}" if took is not None else "")
                  + (f"; note: {after.note}" if after.note else ""))
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
