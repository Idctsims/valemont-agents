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


def passes(expect: str, verdict: str) -> bool:
    if expect == "unverified":
        return verdict == "unverified"
    return verdict in CLEARED  # "clear" and "corrected" (no regression)


def main() -> int:
    load_env()
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    spent = {"cost": Decimal(0), "calls": 0}
    real_complete = ai.complete

    def counted(**kw: Any) -> Any:
        outer = kw.pop("on_recorded", None)

        def recorded(usage_id: int, model: str, cost: Decimal) -> None:
            spent["cost"] += cost
            spent["calls"] += 1
            if outer:
                outer(usage_id, model, cost)
        return real_complete(on_recorded=recorded, **kw)

    ai.complete = counted  # count this run's judge calls only
    try:
        rows = []
        for c in doc["cards"]:
            v = verify.verify_ref(card_of(c), ask=lambda r, e: verify.ask_haiku(r, e))
            ok = passes(c["expect"], v.verification)
            rows.append(ok)
            print(f"{'PASS' if ok else 'FAIL'} {c['id']:>5} expect {c['expect']:<10} got {v.verification:<10} "
                  f"{c['title'][:48]!r}\n        {(v.note or '')[:260]}")
    finally:
        ai.complete = real_complete
        ledger.close_pool()
    bad = [c for c, ok in zip(doc["cards"], rows) if not ok and c["expect"] != "clear"]
    clear = [ok for c, ok in zip(doc["cards"], rows) if c["expect"] == "clear"]
    print(f"\nknown-bad held: {sum(1 for c, ok in zip(doc['cards'], rows) if c['expect'] == 'unverified' and ok)}"
          f"/{sum(1 for c in doc['cards'] if c['expect'] == 'unverified')}; "
          f"should-clear cleared: {sum(clear)}/{len(clear)}; 1852 kept: "
          f"{next(ok for c, ok in zip(doc['cards'], rows) if c['expect'] == 'corrected')}")
    print(f"cost: {spent['calls']} Haiku calls, ${spent['cost']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
