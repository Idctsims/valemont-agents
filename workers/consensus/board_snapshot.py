"""M5's board snapshot (db/084; owner, 2026-11-19: option B, docs/research/m5_step0.md).

At 10:30 and 16:30 Chicago the worker asks Postgres to copy the board's
combo-eligible, not-started legs into the research tables
(`research_board_snapshot_take`). The copy never leaves the database, so it
costs no egress. A real slot is taken at most once a day, and after 28 real
snapshots the function refuses: the 14-day window, which starts the day this
deploys, ends by itself. There is no backfill and no boot run: an earlier
day's board is gone (and anything from 2026-07-01 is the reserve), and a
missed slot stays missed, which the header table shows.

It reads the board and consensus tables only; it never locks them, so the
board and consensus jobs run as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core import ledger
from core.jobs import JobSkipped

JOB = "board_snapshot"
#: Twice a day; the monitor calls it stale after 2x this (36 h), so one missed slot does not alert.
INTERVAL_S = 18 * 60 * 60
CHICAGO = ZoneInfo("America/Chicago")


def slot_for(at: datetime) -> str:
    """'am' for the 10:30 run, 'pm' for the 16:30 one (a late fire keeps its slot)."""
    return "am" if at.astimezone(CHICAGO).hour < 13 else "pm"


@dataclass(frozen=True)
class SnapshotRun:
    result: dict[str, Any]

    @property
    def job_note(self) -> str:
        r = self.result
        return (f"{r['slot']} {r['slot_date']}: {r['legs']} legs in {r['events']} events, "
                f"{r['bands']} eligibility rows, {r['bytes']} bytes, {r['ms']} ms (snapshot {r['snapshot_id']})")


def snapshot_job(*, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> SnapshotRun:
    result = ledger.research_board_snapshot(slot_for(now()))
    if result.get("skipped"):
        # An expected no-op (the slot is taken, or the 14 days are done): a success with its reason.
        raise JobSkipped(f"board snapshot: {result['skipped']} ({', '.join(f'{k} {v}' for k, v in result.items() if k != 'skipped')})")
    return SnapshotRun(result)
