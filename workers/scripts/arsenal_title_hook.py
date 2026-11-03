"""Run one Screening Room hook by hand (db/062; docs/arsenal.md §10).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_title_hook --hook 12                       (from workers/)
    ...  --hook 12 --rerun --by "owner, 2026-11-04: the model refused"

Without --rerun it does what the worker's job does: drafts a queued hook,
then verifies and outcome-checks its cards; a drafted hook only finishes
what is left (each step skips what is done). With --rerun it drafts a
FAILED hook again, and that is the only way one does: the re-run is written
to arsenal_title_hook_reruns (append-only, with --by and the database role)
in the same transaction as the new draft. A drafted hook never re-runs: once
per title. Prints the result and the hook's state after; never a secret.
"""

from __future__ import annotations

import argparse
import sys

from arsenal import hook
from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hook", type=int, required=True, help="arsenal_title_hooks id")
    parser.add_argument("--rerun", action="store_true", help="draft a FAILED hook again (recorded)")
    parser.add_argument("--by", help="who and why, kept on the re-run row (required with --rerun)")
    args = parser.parse_args(argv)
    if args.rerun and not (args.by or "").strip():
        parser.error("--rerun needs --by")
    load_env()
    try:
        h = ledger.arsenal_title_hook(args.hook)
        if h is None:
            print(f"refused: no hook {args.hook}")
            return 1
        if args.rerun and h.status != "failed":
            print(f"refused, nothing written: hook {h.id} is {h.status}; only a failed hook is re-run (once per title)")
            return 1
        print(f"hook {h.id} (title {h.title_id}): {h.status} before")
        print(hook.run_hook(h.id, rerun_by=args.by.strip() if args.rerun else None))
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
