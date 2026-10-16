"""Item 5's outcome check over the pool, and a hold for every conflict.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_check --dry-run   (from workers/)
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_check

Every real, undropped, verified or corrected reference (one already held is
skipped) is read against its best source page (arsenal/verify.py
candidates()). Haiku answers outcome_conflict OUTCOME_SAMPLES times, and a
strict majority is a conflict. Without --dry-run each conflict becomes an
arsenal_holds row (db/055; reason 'outcome_conflict', the note as detail),
and the picker never takes it. Needs db/055 pasted. Prints one line per
reference: id, title, the votes, held or not, the note, the page read.
"""

from __future__ import annotations

import argparse
import sys

from arsenal import sources, verify
from core import ledger
from core.paths import load_env

REASON = "outcome_conflict"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="read and report; write no hold")
    parser.add_argument("--samples", type=int, default=verify.OUTCOME_SAMPLES, help="reads per reference")
    parser.add_argument("--ids", type=int, nargs="+",
                        help="only these references, dropped ones included (a positive control); needs --dry-run")
    args = parser.parse_args(argv)
    if args.ids and not args.dry_run:
        parser.error("--ids reads references outside the pool (a control): use it with --dry-run")
    load_env()
    held = 0
    try:
        if args.ids:
            pool = [ledger.ArsenalPoolRef(r, False) for r in (ledger.arsenal_ref_to_verify(i) for i in args.ids) if r]
        else:
            pool = ledger.arsenal_undropped_checked_refs()
        what = "references by id" if args.ids else "undropped checked references"
        print(f"{len(pool)} {what} ({'dry run' if args.dry_run else 'holds written'})")
        for item in pool:
            r = item.ref
            if item.held:
                print(f"{r.id} {r.title!r}: already held, skipped")
                continue
            res = verify.outcome_check(r, ask=lambda card, e: verify.ask_outcome_haiku(card, e), samples=args.samples)
            votes = "".join("Y" if v else "n" for v in res.votes) or "-"
            mark = "CONFLICT" if res.conflict else "ok"
            print(f"{r.id} {r.title!r}: {mark} votes {votes}; page {res.page}; {res.note}")
            if res.conflict:
                held += 1
                if not args.dry_run:
                    hold_id = ledger.arsenal_hold(ref_id=r.id, reason=REASON, detail=res.note)
                    print(f"  held: arsenal_holds {hold_id}")
    finally:
        sources.close_http()
        ledger.close_pool()
    print(f"{held} conflict(s){'' if args.dry_run else ' held'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
