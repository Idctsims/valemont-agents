"""The price_combo queue job (db/076; Chat 8 Phase 3; docs/correlation.md).

The web inserts a combo_requests row; its trigger queues ONE job_queue row
`price_combo` {request_id}; the 10 s poller runs this. It reads the legs'
board rows and consensus (ledger.combo_legs: leg_consensus_for), prices them
(combo.py) and writes the quote and the answer in one transaction. An
impossible combo is settled `refused` with its reason; anything else that
breaks settles `failed` with the error, then raises so job_health and the
queue see it (a retry finds the request settled and does nothing).

Read-only to Kalshi: nothing here bets (CLAUDE.md §1).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import psycopg

from consensus import combo, correlation as cr
from core import ledger
from core.jobs import describe

log = logging.getLogger("valemont.consensus.combo")


@dataclass
class ComboRun:
    request_id: int
    outcome: str = "skipped"
    legs: int = 0
    games: int = 0
    naive: float | None = None
    fair: float | None = None
    se: float | None = None
    method: str = ""
    ms: int = 0
    unmodeled: int = 0
    priors: int = 0
    reason: str | None = None

    @property
    def job_note(self) -> str:
        if self.outcome != "answered":
            return f"combo {self.request_id} {self.outcome}" + (f": {self.reason}" if self.reason else "")
        se = f" ± {self.se:.6f}" if self.se is not None else ""
        return (f"combo {self.request_id} answered: {self.legs} legs in {self.games} games, naive {self.naive:.6f}, "
                f"fair {self.fair:.6f}{se} ({self.method}); unmodeled pairs {self.unmodeled}, prior/borrowed "
                f"{self.priors}; {self.ms} ms")


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def leg_rows(rows: list[tuple[Any, ...]]) -> dict[str, combo.LegIn]:
    out: dict[str, combo.LegIn] = {}
    for (t, ev, game, series, sport, league, kind, payout, subject, stat, line, label, team, tier, p, ask, no_ask,
         fee_yes, fee_no, stale, excl, eligible, yes_only, size_max, starts) in rows:
        out[t] = combo.LegIn(
            market_ticker=t, side="yes", event_ticker=ev, game_ticker=game, series_ticker=series, sport=sport,
            league=league, market_kind=kind, payout=payout, subject=subject, stat=stat, line=_f(line), label=label,
            team=team, tier=tier, p_yes=_f(p), yes_ask=_f(ask), no_ask=_f(no_ask), fee_yes=_f(fee_yes),
            fee_no=_f(fee_no), stale=bool(stale), mutually_exclusive=excl, combo_eligible=bool(eligible),
            combo_yes_only=bool(yes_only), combo_size_max=size_max, starts_at=starts)
    return out


def quote_row(q: combo.Quote, table: cr.RhoTable, ms: int) -> dict[str, Any]:
    return {
        "naive_p": q.naive_p, "fair_p": min(1.0, max(0.0, q.fair_p)), "se": q.se,
        "blocks": [combo.block_json(b) for b in q.blocks],
        "kalshi_naive_cost": None if q.kalshi_naive_cost is None else round(q.kalshi_naive_cost, 8),
        "combo_status": q.combo_status, "combo_reason": q.combo_reason, "rho_version": table.sha256,
        "unmodeled_pairs": q.unmodeled_pairs, "prior_pairs": q.prior_pairs, "compute_ms": ms,
    }


def price_job(payload: Mapping[str, Any], *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
              table: cr.RhoTable | None = None, conn: psycopg.Connection | None = None) -> ComboRun:
    """One request. `conn` and `table` exist for tests_live (one rolled-back
    transaction, a test table); production passes neither."""
    request_id = int(payload["request_id"])
    run = ComboRun(request_id)
    req = ledger.combo_request(request_id, conn=conn)
    if req is None or req.status != "queued":
        run.reason = "gone" if req is None else f"already {req.status}"
        return run
    t0 = time.perf_counter()
    t = table or cr.load_table()
    try:
        rows = leg_rows(ledger.combo_legs([x["market_ticker"] for x in req.legs], conn=conn))
        legs = combo.validate(req.legs, rows, now())
        q = combo.price(legs, t)
    except cr.Refused as why:
        run.outcome, run.reason = "refused", str(why)
        ledger.combo_settle(request_id, "refused", str(why), conn=conn)
        return run
    except Exception as exc:
        run.outcome, run.reason = "failed", describe(exc)
        ledger.combo_settle(request_id, "failed", f"the pricer failed: {describe(exc)}", conn=conn)
        raise
    run.ms = int((time.perf_counter() - t0) * 1000)
    ledger.combo_answer(request_id, quote_row(q, t, run.ms), conn=conn)
    run.outcome, run.legs, run.games = "answered", len(legs), len(q.blocks)
    run.naive, run.fair, run.se = q.naive_p, q.fair_p, q.se
    run.method = "+".join(sorted({b.method for b in q.blocks}))
    run.unmodeled, run.priors = q.unmodeled_pairs, q.prior_pairs
    log.info("%s", run.job_note)
    return run


@dataclass
class PruneRun:
    deleted: int

    @property
    def job_note(self) -> str:
        return f"combo requests pruned (7 days): {self.deleted}"


def prune_job() -> PruneRun:
    return PruneRun(ledger.combo_prune())
