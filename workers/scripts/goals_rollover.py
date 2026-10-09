"""Carry one ended period's open goals into the next, by hand (db/021).

    ..\\venv\\Scripts\\python.exe -m scripts.goals_rollover --horizon weekly --from 2026-09-28
                                                                    (from workers/)

The same SQL function the worker's goals_rollover job and the web's
ensureRollover call. Prints the number of goals inserted and nothing else:
0 means they were already carried. The database refuses a period that has not
ended in the owner's timezone, and a date that is not a Monday (weekly) or
the 1st (monthly).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--horizon", choices=("weekly", "monthly"), required=True)
    parser.add_argument("--from", dest="from_date", type=date.fromisoformat, required=True,
                        help="start of the ended period: a Monday, or the 1st of a month")
    args = parser.parse_args(argv)

    load_env()
    try:
        print(ledger.carry_over_goals(args.horizon, args.from_date))
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
