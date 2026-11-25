"""Seed the Parlays page's e2e fixtures (Chat 8 Phase 4.5; docs/parlays_page.md §6).

Writes, as the `_e2e_parlays` agent (is_test; db/091), through core/ledger.py:
  - a Safer ticket that hits (3 legs), its CLV (proxy) captured;
  - a Mid ticket that misses (6 legs), its close missed;
  - an open cross-sport Lotto (11 legs, extrapolated fill), its games tomorrow;
  - three slot results: an empty slot, a sport with no eligible market, a refusal at commit.
Commitments are permanent (CLAUDE.md §2): run it once, on the day the owner's
e2e run needs fixtures (`?day=e2e` finds the latest fixture day). It waits
~75 s so the two settled tickets can resolve after their resolves_after.

    ..\\venv\\Scripts\\python.exe -m scripts.seed_e2e_parlays        (from workers/)

Refuses unless the agent is a test agent. Nothing here bets.
"""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from decimal import Decimal

from core import ledger
from core.ledger import Leg, LegOutcome

AGENT = "_e2e_parlays"
WAIT_S = 75


def _reason(side: str, label: str, ask: int, n: int, total: int) -> str:
    mid = Decimal(ask) / 100 - Decimal("0.005")
    return (f"{side.upper()} {label}: ask {ask}¢, mid {mid:.3f}, ask÷mid {Decimal(ask) / 100 / mid:.3f}, "
            f"ranked {n} of {total}; kalshi consensus p {mid:.3f}; 0 ranked higher were not taken")


def _ticket(tier: str, scope: str, sport: str | None, legs: list[tuple[str, str, int]], stake: str,
            starts, fill: str | None = None) -> tuple[dict, list[Leg]]:
    tickers = [f"E2EPARLAY-{tier.upper()}-{i}" for i in range(len(legs))]
    payload = {
        "tier": tier, "scope": scope, "sport": sport, "rule_version": "P2.1", "search": "greedy",
        "capital_at_risk": stake, "contracts": 100, "scored_price": str(Decimal(stake) / 100 - Decimal("0.0168")),
        "taker_fee": "0.0168", "maker_fee_assumed": "0.0084", "fair_p": 0.39, "fair_se": None, "naive_p": "0.385",
        "rel_cost_over_fair": 0.017, "pairs": 0, "measured_pairs": 0, "prior_pairs": 0, "edge_legs": [],
        "combo_status": "fits", "combo_reason": None, "fill_note": fill, "expected_quote": None,
        "expected_quote_note": "pending (M1)", "rho_version": ".4", "rho_sha256": "e2e",
        "leg_reasons": [_reason(side, label, ask, i + 1, 40) for i, (side, label, ask) in enumerate(legs)],
        "legs": [{"market_ticker": t, "side": side, "event_ticker": t, "game_ticker": t, "sport": sport,
                  "tier": "kalshi", "p": "0.5", "ask": str(Decimal(ask) / 100), "mid": "0.5",
                  "fetched_at": starts.isoformat()} for t, (side, _label, ask) in zip(tickers, legs)],
        "kickoffs": {t: starts.isoformat() for t in tickers},
        "invalidation": "0", "stop_rule": "structural_zero", "agent": AGENT,
    }
    return payload, [Leg(subject=t, market="E2EPARLAY", line=Decimal(ask) / 100, direction=side, size=100)
                     for t, (side, _l, ask) in zip(tickers, legs)]


def main() -> int:
    if not ledger.agent_is_test(AGENT):
        print(f"refused: {AGENT} is not a test agent")
        return 2
    aid = ledger.agent_id(AGENT)
    run = ledger.start_run(aid, notes="e2e parlay fixtures (scripts.seed_e2e_parlays)")
    now = ledger.measure_clock().db_time
    soon, tomorrow = now + timedelta(seconds=60), now + timedelta(days=1)

    safer, safer_legs = _ticket("safer", "sport", "Basketball", [
        ("yes", "Test Player: 4+ threes (Test Away at Test Home)", 80),
        ("no", "Test Away wins (Test Away at Test Home)", 85),
        ("yes", "Over 210.5 points (Test Away at Test Home)", 82)], "41.30", soon)
    mid, mid_legs = _ticket("mid", "sport", "Hockey", [
        ("no", "Test Skater: 1+ goals (Test North at Test South)", 78)] + [
        ("no", f"Test Skater {i}: 1+ ast (Test North at Test South)", 76) for i in range(2, 7)], "15.80", soon)
    lotto, lotto_legs = _ticket("lotto", "cross_sport", None, [
        ("no", f"Test Club {i} wins (Test Club {i} vs Test Rival {i})", 76) for i in range(1, 12)], "3.18",
        tomorrow, fill="extrapolated fill")

    made = []
    for payload, legs, closes, resolves in ((safer, safer_legs, soon, soon + timedelta(seconds=10)),
                                            (mid, mid_legs, soon, soon + timedelta(seconds=10)),
                                            (lotto, lotto_legs, tomorrow, tomorrow + timedelta(days=1))):
        c = ledger.commit(agent_id=aid, run_id=run, kind="event_contract", thesis=f"e2e fixture: {payload['tier']}",
                          payload=payload, resolves_after=resolves, legs=legs, closes_at=closes)
        made.append(c.id)
    for detail, msg in (
        ({"slot": "e2e pm", "tier": "safer", "scope": "sport", "sport": "Football", "rule_version": "P2.1",
          "binding": "unmodeled pair"}, "empty: unmodeled pair"),
        ({"slot": "e2e pm", "tier": "mid", "scope": "sport", "sport": "Chess", "rule_version": "P2.1",
          "binding": "rules 1-4: universe"}, "empty: rules 1-4: universe"),
        ({"tier": "lotto", "scope": "sport", "sport": "Tennis", "refused": "game started by the database clock: E2E"},
         "refused at commit"),
    ):
        ledger.emit_event("gate_evaluated", msg, agent_id=aid, run_id=run, detail=detail)
    print(f"committed {made}; waiting {WAIT_S} s to settle the first two")
    time.sleep(WAIT_S)

    ledger.add_resolution(commitment_id=made[0], outcome="hit", pnl=Decimal("1.4213"),
                          leg_outcomes=[LegOutcome(i, "hit", Decimal(1)) for i in range(3)],
                          detail={"unit": "usd", "contracts": 100, "ticket_value": "1", "capital_at_risk": "41.30",
                                  "proceeds": "100", "gross": "58.70",
                                  "legs": [{"ticker": l.subject, "side": l.direction, "side_value": "1"}
                                           for l in safer_legs]})
    ledger.add_resolution(commitment_id=made[1], outcome="miss", pnl=Decimal("-1"),
                          leg_outcomes=[LegOutcome(i, "miss" if i == 0 else "hit", Decimal(0 if i == 0 else 1))
                                        for i in range(6)],
                          detail={"unit": "usd", "contracts": 100, "ticket_value": "0", "capital_at_risk": "15.80",
                                  "proceeds": "0", "gross": "-15.80",
                                  "legs": [{"ticker": l.subject, "side": l.direction,
                                            "side_value": "0" if i == 0 else "1"} for i, l in enumerate(mid_legs)]})
    ledger.add_closing_snapshot(commitment_id=made[0], entry_price=Decimal("0.385"), close_price=Decimal("0.392"),
                                clv=Decimal("0.007"), clv_pct=Decimal("0.0182"),
                                detail={"label": "CLV (proxy)", "rule_version": "P2.1", "age_min_median": 1,
                                        "age_min_max": 3, "legs": []})
    ledger.add_closing_snapshot(commitment_id=made[1], status="missed", attempt="error",
                                reason="unavailable: no candle in the 1:00:00 before kickoff: E2EPARLAY-MID-0")
    ledger.end_run(run, "ok", notes=f"fixtures {made}")
    print(f"done: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
