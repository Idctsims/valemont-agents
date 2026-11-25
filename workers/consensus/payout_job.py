"""The build_payout queue job (db/094; E1; docs/payout_target.md).

The web inserts a payout_requests row; its trigger queues ONE job_queue row
`build_payout` {request_id}; the 10 s poller runs this. It reads the board's
combo candidates (ledger.payout_candidates: leg_consensus_for over every
combo-eligible game not started, 7.5-9.3 s on production on Nov 25), keeps the
read for CACHE_S so a second request in a row skips it, searches
(consensus/payout.py) and writes the answer and the settle in one
transaction. Anything that breaks settles `failed` with the error, then raises
so job_health and the queue see it (a retry finds the request settled and does
nothing).

Read-only to Kalshi: nothing here bets (CLAUDE.md §1).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping

import psycopg

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import payout
from core import ledger
from core.jobs import describe

log = logging.getLogger("valemont.consensus.payout")

#: How long one board read serves later requests (a search's legs must be fresh: 20 min).
CACHE_S = 120.0
_cache: dict[bool, tuple[float, list[pl.Market], dict[str, datetime]]] = {}


def board(tests: bool, *, conn: psycopg.Connection | None = None,
          clock: Callable[[], float] = time.monotonic) -> tuple[list[pl.Market], dict[str, datetime], bool]:
    """(markets, {market: Kalshi read time}, from the cache?). tests_live passes `conn`, which bypasses the cache."""
    hit = _cache.get(tests)
    if conn is None and hit is not None and clock() - hit[0] < CACHE_S:
        return hit[1], hit[2], True
    rows, at = ledger.payout_candidates(tests=tests, conn=conn)
    markets = pl.markets_from_rows(rows)
    if conn is None:
        _cache[tests] = (clock(), markets, at)
    return markets, at, False


@dataclass
class PayoutRun:
    request_id: int
    outcome: str = "skipped"
    stake: Decimal | None = None
    payout: Decimal | None = None
    target: Decimal | None = None
    tickets: int = 0
    nearest: bool = False
    candidates: int = 0
    cached: bool = False
    ms: int = 0
    reason: str | None = None

    @property
    def job_note(self) -> str:
        if self.outcome != "answered":
            return f"payout {self.request_id} {self.outcome}" + (f": {self.reason}" if self.reason else "")
        return (f"payout {self.request_id} answered: ${self.stake} -> ${self.payout} (target {self.target}), "
                f"{self.tickets} ticket(s){' (nearest)' if self.nearest else ''} from {self.candidates} "
                f"candidates; board {'cached' if self.cached else 'read'}; {self.ms} ms")


def build_job(payload: Mapping[str, Any], *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
              table: cr.RhoTable | None = None, conn: psycopg.Connection | None = None) -> PayoutRun:
    """One request. `conn` and `table` exist for tests_live (one rolled-back
    transaction, a test table); production passes neither."""
    request_id = int(payload["request_id"])
    run = PayoutRun(request_id)
    req = ledger.payout_request(request_id, conn=conn)
    if req is None or req.status != "queued":
        run.reason = "gone" if req is None else f"already {req.status}"
        return run
    run.stake, run.payout = req.stake, req.payout
    t0 = time.perf_counter()
    t = table or cr.load_table()
    try:
        markets, at, run.cached = board(req.is_test, conn=conn)
        at_now = now()
        legs = payout.legs_from(markets, at, at_now)
        ans = payout.search(req.stake, req.payout, legs, t)
        series = [c.m.series_ticker for p in ans.picks for c in p.legs]
        periods = ledger.board_periods(series, conn=conn)
        tickets = [payout.ticket_json(p, req.stake, periods, at_now) for p in ans.picks]
    except Exception as exc:
        run.outcome, run.reason = "failed", describe(exc)
        ledger.payout_fail(request_id, f"the search failed: {describe(exc)}", conn=conn)
        raise
    run.ms = int((time.perf_counter() - t0) * 1000)
    ledger.payout_answer(request_id, {
        "tickets": tickets, "nearest": ans.nearest or not tickets, "target_price": ans.target,
        "candidates": ans.candidates, "rho_version": t.sha256, "compute_ms": run.ms,
    }, conn=conn)
    run.outcome, run.target, run.tickets = "answered", ans.target, len(tickets)
    run.nearest, run.candidates = ans.nearest or not tickets, ans.candidates
    log.info("%s", run.job_note)
    return run


@dataclass
class PruneRun:
    deleted: int

    @property
    def job_note(self) -> str:
        return f"payout requests pruned (7 days): {self.deleted}"


def prune_job() -> PruneRun:
    return PruneRun(ledger.payout_prune())
