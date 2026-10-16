"""arsenal_drop: today's drop (3 references, 3 categories, 1 Power Word).

Daily 05:30 Chicago, and at boot (a no-op once today has one). The pick is
db/054's arsenal_pick_drop: zero AI calls. When the verified pool is short
(fewer than 3 categories with a ready reference, or no ready word), ONE
synchronous top-up runs first (pool.topup: Sonnet, then inline
verification), and the drop is marked topped_up. Still short after it: the
job raises, so job_health records it and the health monitor pushes.

The 06:15 Morning Brief reads ctx_arsenal_brief.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from arsenal import pool
from core import ledger

log = logging.getLogger("valemont.arsenal.drop")


def is_short(status: ledger.ArsenalPoolStatus) -> bool:
    return len(status.ready_categories) < 3 or status.words_ready < 1


@dataclass(frozen=True)
class DropRun:
    job_note: str
    drop_id: int | None = None
    topped_up: bool = False


def make_drop(day: date, *, client: Any = None, http: Any = None, ask: Any = None, embed: Any = None) -> DropRun:
    existing = ledger.arsenal_drop_on(day)
    if existing is not None:
        return DropRun(f"{day} has drop {existing}", existing)
    status = ledger.arsenal_pool_status()
    topped = False
    note = ""
    if is_short(status):
        log.warning("arsenal drop %s: pool short (%d categories ready, %d words); topping up", day,
                    len(status.ready_categories), status.words_ready)
        up = pool.topup(status, client=client, http=http, ask=ask, embed=embed)
        topped = True
        note = f"; topped up (run {up.run_id}: {up.refs} refs, {up.words} words, ${up.cost}, {up.verified})"
    drop_id = ledger.arsenal_pick_drop(day, topped_up=topped)
    log.info("arsenal drop %s: %d%s", day, drop_id, note)
    return DropRun(f"{day} drop {drop_id}{note}", drop_id, topped)


def drop_job() -> DropRun:
    return make_drop(ledger.arsenal_owner_today())
