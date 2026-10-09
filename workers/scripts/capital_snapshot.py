"""Write every missing capital snapshot, by hand (db/026).

    ..\\venv\\Scripts\\python.exe -m scripts.capital_snapshot --backfill     (from workers/)

The same path as the worker's capital_snapshot job (core/system_jobs.py
snapshot_capital): each mode with data gets every source's closing value for
every ended day since its first entry that has no snapshot yet. Prints the
rows written per mode and nothing else; 0 means it was already complete.
Paper and live are counted separately, never summed.
"""

from __future__ import annotations

import argparse
import sys

from core import ledger
from core.paths import load_env
from core.system_jobs import snapshot_capital


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backfill", action="store_true", required=True,
                        help="fill every ended day since the first entry (the only mode)")
    parser.parse_args(argv)

    load_env()
    try:
        written = snapshot_capital()
    finally:
        ledger.close_pool()
    if not written:
        print("no mode has data")
    for mode, count in written.items():
        print(f"{mode} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
