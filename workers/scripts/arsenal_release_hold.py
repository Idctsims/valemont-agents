"""Release Arsenal holds, on the record (db/056; owner, 2026-10-16).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_release_hold --ids 9 10 11 --reason "..."   (from workers/)
    ...  --dry-run      shows what would be released, writes nothing

Each release is one append-only arsenal_hold_releases row (the hold stays
as history; db/056's arsenal_release_hold refuses a second release). Prints
each hold before and after. Refuses before writing anything when an id is
unknown or already released, so a batch is released whole or not at all.
"""

from __future__ import annotations

import argparse
import sys

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ids", type=int, nargs="+", required=True, help="arsenal_holds ids")
    parser.add_argument("--reason", required=True, help="why (kept on each release)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_env()
    try:
        holds = {h.id: h for h in ledger.arsenal_holds_by_id(args.ids)}
        missing = [i for i in args.ids if i not in holds]
        done = [i for i in args.ids if i in holds and holds[i].released]
        if missing or done:
            print(f"refused, nothing written: unknown {missing or 'none'}; already released {done or 'none'}")
            return 1
        for i in args.ids:
            h = holds[i]
            target = f"ref {h.ref_id}" if h.ref_id is not None else f"word {h.word_id}"
            if args.dry_run:
                print(f"hold {i} ({target}, {h.reason}): would be released")
                continue
            rel = ledger.arsenal_release_hold(i, args.reason)
            print(f"hold {i} ({target}, {h.reason}): released (arsenal_hold_releases {rel})")
    finally:
        ledger.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
