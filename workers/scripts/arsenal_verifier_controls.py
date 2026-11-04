"""The verifier yield controls (owner, 2026-11-04): run the CURRENT verifier
on the frozen control set, write nothing to the Arsenal, report each verdict.

    ..\\venv\\Scripts\\python.exe -m scripts.arsenal_verifier_controls        (from workers/)

tests/fixtures/arsenal/verifier_controls.json, frozen before any change:
  * known-bad (expect "unverified"): must stay unverified;
  * should-clear (expect "clear"): should end verified or corrected;
  * 1852 (expect "corrected"): must stay cleared (no regression).
It runs verify.verify_ref only: no settle, no outcome check, no hold. The
Haiku judge's calls are real and write their ai_usage rows (that is the
cost it reports). Exits 1 when any known-bad card clears or 1852 does not.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from arsenal import verify
from core import ai, ledger
from core.paths import load_env

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "arsenal" / "verifier_controls.json"
CLEARED = ("verified", "corrected")


def card_of(c: dict[str, Any]) -> ledger.ArsenalRefToVerify:
    return ledger.ArsenalRefToVerify(
        int(c["id"]), c["category"], c["title"], c["origin"], c["source_work"], c.get("quote"), c.get("quote_by"),
        c["source_kind"], c.get("source_page"), "pending", None, c["meaning"], c["deploy_line"])


def passes(c: dict[str, Any], v: verify.RefVerdict) -> bool:
    expect = c["expect"]
    if expect == "unverified":
        return v.verification == "unverified"
    if expect == "trimmed":
        # The guarded trim (owner, 2026-11-04): corrected, rewritten without
        # the invented flourish; never verified as-is.
        return (v.verification == "corrected" and bool(v.origin)
                and c["flourish"].lower() not in (v.origin or "").lower())
    return v.verification in CLEARED  # "clear" and "corrected" (no regression)


#: The bar (owner, 2026-11-04, after the judge trial): should-clear >= 3/10
#: in both runs, with known-bad 7/7, 1852 kept and trims 2/2.
SHOULD_CLEAR_MIN = 3


def main(argv: list[str] | None = None) -> int:
    load_env()
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    spent: dict[str, Any] = {"cost": Decimal(0), "calls": 0, "by_model": {}}
    real_complete = ai.complete

    def counted(**kw: Any) -> Any:
        outer = kw.pop("on_recorded", None)

        def recorded(usage_id: int, model: str, cost: Decimal) -> None:
            spent["cost"] += cost
            spent["calls"] += 1
            n, c = spent["by_model"].get(model, (0, Decimal(0)))
            spent["by_model"][model] = (n + 1, c + cost)
            if outer:
                outer(usage_id, model, cost)
        return real_complete(on_recorded=recorded, **kw)

    ai.complete = counted  # count this run's judge calls only
    try:
        rows = []
        for c in doc["cards"]:
            v = verify.verify_ref(card_of(c), ask=lambda r, e: verify.ask_haiku(r, e))
            ok = passes(c, v)
            rows.append(ok)
            rewritten = f"\n        rewritten: {v.origin[:200]}" if c["expect"] == "trimmed" and v.origin else ""
            print(f"{'PASS' if ok else 'FAIL'} {c['id']:>5} expect {c['expect']:<10} got {v.verification:<10} "
                  f"{c['title'][:48]!r}\n        {(v.note or '')[:260]}{rewritten}")
    finally:
        ai.complete = real_complete
        ledger.close_pool()

    def tally(expect: str) -> tuple[int, int]:
        got = [ok for c, ok in zip(doc["cards"], rows) if c["expect"] == expect]
        return sum(got), len(got)

    held, n_bad = tally("unverified")
    cleared, n_clear = tally("clear")
    kept, _ = tally("corrected")
    trims, n_trim = tally("trimmed")
    done = held == n_bad and cleared >= SHOULD_CLEAR_MIN and kept == 1 and trims == n_trim
    print(f"\nknown-bad held: {held}/{n_bad}; should-clear cleared: {cleared}/{n_clear} (need {SHOULD_CLEAR_MIN}); "
          f"1852 kept: {bool(kept)}; trims: {trims}/{n_trim}  =>  done-when {'MET' if done else 'NOT met'}")
    by = ", ".join(f"{m} {n} calls ${c}" for m, (n, c) in sorted(spent["by_model"].items()))
    print(f"cost: {spent['calls']} judge calls, ${spent['cost']} ({by})")
    return 0 if held == n_bad and kept == 1 and trims == n_trim else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
