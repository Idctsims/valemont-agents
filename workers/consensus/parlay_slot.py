"""The parlay slot's re-quote (Chat 8 Phase 4.4; docs/parlay_generator_plan.md §4.4).

One re-quote per slot, shared by the three tiers (owner, Nov 20):

    10:33 / 16:33 Chicago   parlay_requote (this module, a system job):
                            the candidate reader, then every candidate event
                            re-read from Kalshi (latest start first, the
                            re-quote's own 0.10 s pace floor), then consensus
                            p recomputed on the fresh mids; kept in worker
                            memory under the slot's key
    10:45 / 16:45 Chicago   parlay_safer, parlay_mid, parlay_lotto (adapters/
                            parlays.py) each take that slot's quotes; none of
                            them calls Kalshi

While the re-quote runs, `kb.SLOT_REQUOTE` is set and the board refresh
yields (JobSkipped: a success with a note, no alert). The job never runs at
boot; its misfire grace is 5 minutes, so a re-quote that cannot start by :38
does not run, and the slot is then empty with binding rule `fresh` (the tiers
find no quotes for it).

**Gated like the agents.** It does nothing (JobSkipped) unless the worker
runs ROSTER=production and at least one parlay agent is enabled: before the
owner switches the parlays on, it costs no Kalshi call and no refresh skip.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from consensus import parlay_legs as pl
from core import ledger
from core.jobs import JobSkipped
from venues.kalshi import board as kb

log = logging.getLogger("valemont.parlay_slot")

JOB = "parlay_requote"
OWNER_TZ = ZoneInfo("America/Chicago")
#: The stamped commit times (pre-registration §2) and the re-quote's start (owner, Nov 20).
SLOT_HOURS = (10, 16)
REQUOTE_MINUTE = 33
COMMIT_MINUTE = 45
#: A re-quote that cannot start within this of :33 does not run (owner, Nov 20).
MISFIRE_GRACE_S = 300
#: Twice a day; the longest gap is 16:33 -> 10:33 (18 h). The monitor calls it stale after 2x.
INTERVAL_S = 18 * 60 * 60
AGENTS = ("parlay_safer", "parlay_mid", "parlay_lotto")


def slot_key(at: datetime) -> tuple[date, str]:
    """(the Chicago date, 'am' | 'pm') of the slot `at` belongs to."""
    local = at.astimezone(OWNER_TZ)
    return local.date(), ("am" if local.hour < 13 else "pm")


@dataclass
class SlotQuotes:
    """One slot's re-quote: the candidate markets (consensus p recomputed on the
    fresh mids), the fresh quotes, and the run's figures."""

    key: tuple[date, str]
    markets: list[pl.Market]
    quotes: dict[str, pl.Quote]
    cut: dict[str, int]
    requote: pl.Requote
    started_at: datetime
    finished_at: datetime
    read_seconds: float
    normalized: set[str] = field(default_factory=set)

    def summary(self) -> dict[str, Any]:
        rq = self.requote
        return {"slot": f"{self.key[0].isoformat()} {self.key[1]}", "candidate_markets": len(self.markets),
                "events_asked": rq.events_asked, "events_skipped": rq.events_skipped, "cap": rq.cap,
                "calls": rq.calls, "limited_429": rq.limited, "share_429": round(rq.share_429, 4),
                "seconds": round(rq.seconds, 1), "bytes": rq.bytes, "pace_end": round(rq.pace, 2),
                "read_seconds": round(self.read_seconds, 1), "started_at": self.started_at.isoformat(),
                "finished_at": self.finished_at.isoformat()}


#: The slot store, in worker memory: the latest re-quote per slot key, at most two kept.
_STORE: dict[tuple[date, str], SlotQuotes] = {}
_STORE_LOCK = threading.Lock()


def put(data: SlotQuotes) -> None:
    with _STORE_LOCK:
        _STORE[data.key] = data
        for k in sorted(_STORE)[:-2]:
            del _STORE[k]


def get(key: tuple[date, str]) -> SlotQuotes | None:
    with _STORE_LOCK:
        return _STORE.get(key)


@dataclass(frozen=True)
class RequoteResult:
    job_note: str


def gate_open(environ: dict[str, str] | None = None,
              enabled: Callable[[str], bool] = ledger.agent_enabled) -> str | None:
    """None when the re-quote should run, else why not (the JobSkipped note)."""
    env = os.environ if environ is None else environ
    if env.get("ROSTER", "").strip().lower() != "production":
        return "ROSTER is not production: no parlay agent runs"
    if not any(enabled(slug) for slug in AGENTS):
        return "no parlay agent is enabled (agents.enabled)"
    return None


def requote_slot(*, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 read: Callable[[], tuple[list[dict[str, Any]], dict[str, int]]] = ledger.parlay_candidates,
                 requote: Callable[..., pl.Requote] = pl.requote) -> SlotQuotes:
    """The re-quote itself, with the refresh held off for its whole length."""
    started = now()
    kb.SLOT_REQUOTE.set()
    try:
        t0 = time.monotonic()
        rows, cut = read()
        read_s = time.monotonic() - t0
        markets = pl.markets_from_rows(rows)
        rq = requote(pl.requote_order(markets))
        fresh = pl.with_fresh_p(markets, rq.quotes)
        data = SlotQuotes(key=slot_key(started), markets=fresh, quotes=dict(rq.quotes), cut=dict(cut), requote=rq,
                          started_at=started, finished_at=now(), read_seconds=read_s,
                          normalized=pl.normalized_events(fresh, rq.quotes))
    finally:
        kb.SLOT_REQUOTE.clear()
    put(data)
    return data


def requote_job() -> RequoteResult:
    """The parlay_requote system job (10:33 and 16:33 Chicago, never at boot)."""
    closed = gate_open()
    if closed is not None:
        raise JobSkipped(closed)
    data = requote_slot()
    s = data.summary()
    log.info("parlay re-quote %s: %s", s["slot"], s)
    return RequoteResult(
        f"{s['slot']}: {s['candidate_markets']} markets, {s['events_asked']} events, {s['calls']} calls, "
        f"429s {s['limited_429']} ({s['share_429']:.2%}), {s['seconds']} s")
