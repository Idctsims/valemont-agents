"""Insert one goal for the owner through the worker's ledger path (db/021).

    ..\\venv\\Scripts\\python.exe -m scripts.add_goal --title "Rollover test" --horizon weekly --period 2026-09-28
                                                                    (from workers/)

The owner comes from app_settings. Prints the new goal's id. For checks like
the rollover end-to-end test; everyday goals are added in the app.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--title", required=True)
    parser.add_argument("--horizon", choices=("weekly", "monthly", "long_term"), required=True)
    parser.add_argument("--period", type=date.fromisoformat, default=None,
                        help="period start: a Monday (weekly), the 1st (monthly); omit for long_term")
    parser.add_argument("--area", choices=("business", "personal", "health", "money", "people"))
    args = parser.parse_args(argv)

    load_env()
    try:
        print(ledger.add_goal(title=args.title, horizon=args.horizon,
                              period_start=args.period, area=args.area))
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
