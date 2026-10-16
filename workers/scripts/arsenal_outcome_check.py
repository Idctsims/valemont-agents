"""Item 5's outcome check over the pool, and a hold for every conflict.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_check            (from workers/; a dry run)
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_check --write    (holds written)
    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_check --ids 194  (a control, dry run)

A DRY RUN BY DEFAULT (owner, 2026-10-16, after an intermediate prompt held
three false positives). --write first runs the controls in this same run
(scripts.arsenal_outcome_control: Thermopylae and Custer must flag, the
Miracle on Ice must not) and refuses, writing nothing, unless all of them
pass. Both results are printed and logged.

Every real, undropped, verified or corrected reference (one with an active
hold is skipped) is read against its best source page (arsenal/verify.py
candidates()). Haiku answers outcome_conflict OUTCOME_SAMPLES times, and a
strict majority is a conflict. With --write each conflict becomes an
arsenal_holds row (reason 'outcome_conflict', the note as detail), and the
picker skips it until a release (db/056). Prints one line per reference:
id, title, the votes, the page read, the note.
"""

from __future__ import annotations

import argparse
import logging
import sys

from arsenal import sources, verify
from core import ledger
from core.paths import load_env
from scripts import arsenal_outcome_control

log = logging.getLogger("valemont.arsenal.outcome_check")

REASON = "outcome_conflict"


def main(argv: list[str] | None = None, *, ask: verify.AskOutcome | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="write a hold for each conflict (refused unless the controls pass in this run)")
    parser.add_argument("--samples", type=int, default=verify.OUTCOME_SAMPLES, help="reads per reference")
    parser.add_argument("--ids", type=int, nargs="+",
                        help="only these references, dropped ones included (a control); never with --write")
    args = parser.parse_args(argv)
    if args.ids and args.write:
        parser.error("--ids reads references outside the pool (a control): it never writes")
    ask = ask or (lambda card, e: verify.ask_outcome_haiku(card, e))
    load_env()
    held = 0
    try:
        if args.write:
            wrong, lines = arsenal_outcome_control.run_controls(ask=ask, samples=args.samples)
            for line in lines:
                print(f"control: {line}")
                log.info("arsenal outcome control: %s", line)
            if wrong:
                msg = f"--write refused: {wrong} of {len(lines)} controls wrong in this run; nothing written"
                print(msg)
                log.error("arsenal outcome check: %s", msg)
                return 1
            log.info("arsenal outcome check: all %d controls passed; holds will be written", len(lines))
        if args.ids:
            pool = [ledger.ArsenalPoolRef(r, False) for r in (ledger.arsenal_ref_to_verify(i) for i in args.ids) if r]
        else:
            pool = ledger.arsenal_undropped_checked_refs()
        what = "references by id" if args.ids else "undropped checked references"
        print(f"{len(pool)} {what} ({'holds written' if args.write else 'dry run'})")
        for item in pool:
            r = item.ref
            if item.held:
                print(f"{r.id} {r.title!r}: held (an active hold), skipped")
                continue
            res = verify.outcome_check(r, ask=ask, samples=args.samples)
            votes = "".join("Y" if v else "n" for v in res.votes) or "-"
            mark = "CONFLICT" if res.conflict else "ok"
            line = f"{r.id} {r.title!r}: {mark} votes {votes}; page {res.page}; {res.note}"
            print(line)
            log.info("arsenal outcome check: %s", line)
            if res.conflict:
                held += 1
                if args.write:
                    hold_id = ledger.arsenal_hold(ref_id=r.id, reason=REASON, detail=res.note)
                    print(f"  held: arsenal_holds {hold_id}")
    finally:
        sources.close_http()
        ledger.close_pool()
    print(f"{held} conflict(s){' held' if args.write else ' (dry run: nothing written)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
