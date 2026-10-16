"""Make (or show) the Arsenal drop for one date, by hand.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_drop --date 2026-10-16      (from workers/)

The same code as the worker's arsenal_drop job (arsenal/drop.py): zero AI
calls unless the verified pool is short, then one top-up first. A date that
already has a drop prints that drop. Prints the drop's titles, categories,
rooms and verification, and the Power Word.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from arsenal import drop
from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="the owner's date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    load_env()
    try:
        run = drop.make_drop(args.date)
        print(run.job_note)
        d = ledger.arsenal_drop_details(run.drop_id) if run.drop_id else None
        if d is not None:
            for c in d.cards:
                print(f"  {c.slot}. {c.title} [{c.category}, {c.room}, {c.verification}]")
            print(f"  Power Word: {d.word} [{d.word_verification}]" + (" (topped up)" if d.topped_up else ""))
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
