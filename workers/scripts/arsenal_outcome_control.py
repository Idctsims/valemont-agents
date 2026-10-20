"""The outcome check's controls, through the real model (owner, 2026-10-16).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_control      (from workers/)

Runs every case in tests/fixtures/arsenal/outcome_controls.json (Thermopylae,
the positive control; Custer, held out of the prompt, must flag; the
Miracle on Ice must not) through verify.outcome_check: the same page
retrieval and the same 3-read vote. Writes nothing (the Haiku calls are
costed in ai_usage as usual). Exits 1 when any case comes out wrong: run it
after any change to the outcome prompt, before it touches the pool.

The controls live in arsenal/outcome.py (db/057: the worker runs them before
every check it records); this script and arsenal_outcome_check use them.
"""

from __future__ import annotations

import sys

from arsenal import outcome, sources, verify
from core import ledger
from core.paths import load_env

FIXTURE = outcome.FIXTURE
card = outcome.card
cases = outcome.cases
run_controls = outcome.run_controls


def main() -> int:
    load_env()
    try:
        wrong, lines = run_controls(ask=lambda r, e: verify.ask_outcome_haiku(r, e))
    finally:
        sources.close_http()
        ledger.close_pool()
    for line in lines:
        print(line)
    print(f"{wrong} wrong")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
