r"""A test move proposal for the owner's phone check (db/042; Chat 3 Phase 3).

    ..\venv\Scripts\python.exe -m scripts.feed_test_proposal        (from workers/)

Creates "Test Player <stamp>" plays_for "Test Team A <stamp>" and an OPEN
trade to "Test Team B <stamp>", all is_test, and prints the proposal id. It
shows on /settings/feed under Proposed changes with a `test` tag; Confirm
moves the test player to Team B. No real entity is touched: test entities are
never followed, never a move target, never in Wags' context
(ctx_sports_feed). The rows are permanent, like every row here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from core import ledger
from core.paths import load_env


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    load_env()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    pid = ledger.feed_add_test_move(stamp)
    print(f"test proposal {pid}: Test Player {stamp}, Test Team A {stamp} -> Test Team B {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
