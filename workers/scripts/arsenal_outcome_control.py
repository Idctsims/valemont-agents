"""The outcome check's controls, through the real model (owner, 2026-10-16).

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_outcome_control      (from workers/)

Runs every case in tests/fixtures/arsenal/outcome_controls.json (Thermopylae,
the positive control; Custer, held out of the prompt, must flag; the
Miracle on Ice must not) through verify.outcome_check: the same page
retrieval and the same 3-read vote. Writes nothing (the Haiku calls are
costed in ai_usage as usual). Exits 1 when any case comes out wrong: run it
after any change to the outcome prompt, before it touches the pool.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from arsenal import sources, verify
from core import ledger
from core.paths import load_env

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arsenal" / "outcome_controls.json"


def card(c: dict) -> ledger.ArsenalRefToVerify:
    return ledger.ArsenalRefToVerify(
        int(c["id"]), c["category"], c["title"], c["origin"], c["source_work"], None, None, c["source_kind"],
        c.get("source_page"), "verified", None, c["meaning"], c["deploy_line"])


def cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def main() -> int:
    load_env()
    wrong = 0
    try:
        for case in cases():
            res = verify.outcome_check(card(case["card"]), ask=lambda r, e: verify.ask_outcome_haiku(r, e))
            ok = res.conflict == case["expect_conflict"]
            wrong += not ok
            votes = "".join("Y" if v else "n" for v in res.votes) or "-"
            print(f"{'PASS' if ok else 'FAIL'} {case['name']}: expect {'conflict' if case['expect_conflict'] else 'ok'}, "
                  f"got {'conflict' if res.conflict else 'ok'} (votes {votes}; page {res.page}); {res.note}")
    finally:
        sources.close_http()
        ledger.close_pool()
    print(f"{wrong} wrong")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
