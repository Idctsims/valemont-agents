"""arsenal_drop: today's drop (3 references, 3 categories, 1 Power Word).

Every 30 minutes from 05:30 to 21:30 Chicago, and at boot; a no-op (one
small read) once today has a drop. The pick is db/054's arsenal_pick_drop:
zero AI calls.

When the verified pool is short (fewer than 3 categories with a ready
reference, or no ready word):
  * a pool batch is open, or was submitted in the last DEFER_FOR: DEFER.
    Log it and return; no top-up (owner, 2026-10-16: the day's drop comes
    from the batch pool). The next half-hourly run tries again; from
    LAST_CALL (21:00 Chicago) it no longer waits.
  * otherwise ONE synchronous top-up (pool.topup: Sonnet, then each new
    item verified INLINE by verify.verify_item, never through job_queue),
    then the pick, marked topped_up.
A pick that still fails after a top-up logs an ERROR naming the top-up's
verification counts and raises: job_health records it and the health
monitor pushes. Never a silent empty day.

The 06:15 Morning Brief reads ctx_arsenal_brief.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from arsenal import pool
from core import ledger

log = logging.getLogger("valemont.arsenal.drop")

#: A batch submitted this recently is still worth waiting for.
DEFER_FOR = timedelta(hours=2)
OWNER_TZ = ZoneInfo("America/Chicago")
#: The last two half-hourly runs (21:00, 21:30) never defer: a short day
#: tops up rather than end empty.
LAST_CALL = time(21, 0)


def is_short(status: ledger.ArsenalPoolStatus) -> bool:
    return len(status.ready_categories) < 3 or status.words_ready < 1


def defer_reason(status: ledger.ArsenalPoolStatus, now: datetime) -> str | None:
    """Why a short pool waits for its batch instead of topping up, or None.
    From LAST_CALL (Chicago) it never waits: the day gets its drop."""
    if now.astimezone(OWNER_TZ).time() >= LAST_CALL:
        return None
    if status.open_batch is not None:
        return f"batch {status.open_batch.batch_id} is open"
    if status.last_submitted_at is not None and now - status.last_submitted_at < DEFER_FOR:
        return f"a batch was submitted {int((now - status.last_submitted_at).total_seconds() // 60)} min ago"
    return None


class DropFailed(RuntimeError):
    """The pool could not make a drop even after the top-up."""


@dataclass(frozen=True)
class DropRun:
    job_note: str
    drop_id: int | None = None
    topped_up: bool = False
    deferred: bool = False


def make_drop(day: date, *, client: Any = None, http: Any = None, ask: Any = None, embed: Any = None,
              now: datetime | None = None) -> DropRun:
    existing = ledger.arsenal_drop_on(day)
    if existing is not None:
        return DropRun(f"{day} has drop {existing}", existing)
    status = ledger.arsenal_pool_status()
    topped = False
    note = ""
    if is_short(status):
        wait = defer_reason(status, now or datetime.now(timezone.utc))
        if wait:
            log.warning("arsenal drop %s: pool short (%d categories ready, %d words); deferred: %s", day,
                        len(status.ready_categories), status.words_ready, wait)
            return DropRun(f"{day} deferred: pool short and {wait}", deferred=True)
        log.warning("arsenal drop %s: pool short (%d categories ready, %d words); topping up", day,
                    len(status.ready_categories), status.words_ready)
        up = pool.topup(status, client=client, http=http, ask=ask, embed=embed)
        topped = True
        note = f"; topped up (run {up.run_id}: {up.refs} refs, {up.words} words, ${up.cost}, {up.verified})"
    try:
        drop_id = ledger.arsenal_pick_drop(day, topped_up=topped)
    except Exception as exc:
        if not topped:
            raise
        log.error("arsenal drop %s: NO DROP after the top-up%s: %s", day, note, exc)
        raise DropFailed(f"{day}: no drop after the top-up{note}: {exc}") from exc
    log.info("arsenal drop %s: %d%s", day, drop_id, note)
    return DropRun(f"{day} drop {drop_id}{note}", drop_id, topped)


def drop_job() -> DropRun:
    return make_drop(ledger.arsenal_owner_today())
