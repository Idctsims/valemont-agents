"""The Parlays page's two worker jobs (Chat 8 Phase 4.5; docs/parlays_page.md).

    parlay_legs_capture  every 15 min: copies each recent parlay leg's display
                         fields (player, stat, line, team, period, game) from
                         the board into parlay_leg_display (db/091), before
                         the board prunes them 6 h after the game starts. A
                         leg is committed before its game starts, so a 15
                         minute cadence never misses one. Write-once.
    parlay_hit_push      every 5 min: one push per ticket that settled as a
                         hit (owner-delegated call, Nov 24: hits only; a bust
                         would be noise). Only hits settled in the last 12 h,
                         so a deploy never pushes old ones. Pushed, then
                         recorded in parlay_hit_pushes (append-only, once per
                         ticket): a failure between the two is a duplicate
                         push at worst, never a lost one.

Nothing here writes a ledger row, and nothing bets (CLAUDE.md §1). Paper.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable

from core import ledger, push

log = logging.getLogger("valemont.parlay_watch")

CAPTURE_JOB = "parlay_legs_capture"
CAPTURE_INTERVAL_S = 15 * 60
HIT_JOB = "parlay_hit_push"
HIT_INTERVAL_S = 5 * 60
#: A hit older than this is never pushed (a deploy or an outage does not replay history).
HIT_WINDOW = timedelta(hours=12)
PAGE = "/betting/parlays"

TIER_NAMES = {"safer": "Safer", "mid": "Mid", "lotto": "Lotto"}


def capture_job() -> int:
    n = ledger.parlay_legs_capture()
    log.info("parlay_legs_capture: %d legs captured", n)
    return n


def _usd(x: Decimal) -> str:
    return f"${x:,.2f}"


def hit_message(hit: ledger.ParlayHit) -> push.Message:
    """'Parlay hit: Mid ×6.32' / 'NHL · 9 legs · paid $100.00 on a $15.83 paper stake'."""
    tier = TIER_NAMES.get(hit.tier, hit.tier.title())
    if hit.scope == "cross_sport":
        tier += " cross-sport"
    mult = (hit.paid / hit.stake) if hit.stake else Decimal(0)
    where = "Cross-sport" if hit.scope == "cross_sport" else (hit.sport or "")
    return push.Message(
        kind="parlay_hit",
        title=f"Parlay hit: {tier} ×{mult:.2f}",
        body=f"{where} · {hit.legs} legs · paid {_usd(hit.paid)} on a {_usd(hit.stake)} paper stake",
        deep_link=f"{PAGE}?day={hit.day.isoformat()}",
        tag=f"parlay-hit-{hit.commitment_id}",
    )


@dataclass(frozen=True)
class HitRun:
    due: int
    pushed: int

    @property
    def job_note(self) -> str:
        return f"{self.pushed} of {self.due} hits pushed"

    def __str__(self) -> str:
        return self.job_note


def hit_push_job(*, send: Callable[[push.Message], Any] | None = None) -> HitRun:
    """Pushes each due hit once. A failed send raises (job_health records it;
    the next run retries, because nothing was recorded)."""
    send = send or push.send
    due = ledger.parlay_hits_due(HIT_WINDOW)
    pushed = 0
    for hit in due:
        results = send(hit_message(hit))
        nid = None
        for r in results or ():
            nid = getattr(r, "notification_id", None) or nid
        ledger.parlay_hit_pushed(hit.commitment_id, nid)
        pushed += 1
    run = HitRun(due=len(due), pushed=pushed)
    log.info("parlay_hit_push: %s", run)
    return run
