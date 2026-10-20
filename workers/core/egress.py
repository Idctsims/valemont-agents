"""Approximate database egress, per job run and per day (owner, 2026-10-15).

Supabase Free allows 5 GB a month (~165 MB a day). The worker's reads were
most of it: ~1.7-1.9 GB a day on Oct 13-14, chiefly vectors as text. This
meters what the worker reads so the budget is visible before the dashboard
says so.

How it counts: every connection from the worker's pool uses MeteredCursor.
After each statement it adds the bytes of every returned value as they
crossed the wire (psycopg's PGresult.get_value: the raw bytes, binary values
at their binary size), plus each DataRow's framing (7 bytes and 4 a field). That is
the data, not TLS or protocol chatter, so it reads a little low; writes are
ingress and are not counted. tests_live and db_inspect make their own
connections and are not counted either.

Where it goes:
    per run     core/jobs.run_tracked opens run(job) around each system job:
                the run's bytes join its note ("~120 KB read") in job_health
                and the job's INFO log line.
    per day     totals by job for the owner's date (America/Chicago); the
                `egress` system job writes "today <date> ~X MB read (top
                jobs)" to job_health every EGRESS_INTERVAL_S and carries the
                day's total across a restart by reading its own last note.
                Each run also appends an egress_samples row (db/057), and the
                first run after the rollover a `final` row for the day just
                closed (take_closed): the note alone lost Oct 16-18.
    alert       one push a day when the day's total passes ALERT_MB.
Reads outside a system job (agents, scripts) count as "other".
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import psycopg

OWNER_TZ = ZoneInfo("America/Chicago")
#: One push a day past this (owner, 2026-10-15; Free is ~165 MB a day).
ALERT_MB = 150
MB = 1024 * 1024
#: DataRow framing: type byte, length, field count; then a length per field.
ROW_OVERHEAD = 7
FIELD_OVERHEAD = 4


@dataclass
class RunMeter:
    job: str
    bytes: int = 0

    def note(self) -> str:
        return f"~{self.bytes / 1024:.0f} KB read"


_RUN: ContextVar[RunMeter | None] = ContextVar("egress_run", default=None)


@dataclass
class Day:
    day: date
    by_job: dict[str, int] = field(default_factory=dict)
    #: Bytes counted before this process started (from the last note).
    carried: int = 0
    alerted: bool = False

    @property
    def total(self) -> int:
        return self.carried + sum(self.by_job.values())


_LOCK = threading.Lock()
_DAY: Day | None = None
#: The day just replaced at the Chicago rollover, until the egress job
#: writes its final sample (take_closed).
_CLOSED: Day | None = None


def owner_today(now: datetime | None = None) -> date:
    return (now or datetime.now(OWNER_TZ)).astimezone(OWNER_TZ).date()


def _today() -> Day:
    global _DAY, _CLOSED
    d = owner_today()
    if _DAY is None or _DAY.day != d:
        if _DAY is not None:
            _CLOSED = _DAY
        _DAY = Day(d)
    return _DAY


def take_closed() -> Day | None:
    """The day closed at the last rollover, once (a copy), or None."""
    global _CLOSED
    with _LOCK:
        _today()
        closed, _CLOSED = _CLOSED, None
        return None if closed is None else Day(closed.day, dict(closed.by_job), closed.carried, closed.alerted)


def add(n: int) -> None:
    """Count `n` bytes against the running job (or 'other') and the day."""
    if n <= 0:
        return
    run = _RUN.get()
    if run is not None:
        run.bytes += n
    name = run.job if run is not None else "other"
    with _LOCK:
        day = _today()
        day.by_job[name] = day.by_job.get(name, 0) + n


def result_bytes(cur: psycopg.Cursor[Any]) -> int:
    """The bytes of the statement's result as they crossed the wire."""
    res = cur.pgresult
    if res is None:
        return 0
    rows, cols = res.ntuples, res.nfields
    if not rows or not cols:
        return 0
    data = 0
    for r in range(rows):
        for c in range(cols):
            v = res.get_value(r, c)
            if v is not None:
                data += len(v)
    return data + rows * (ROW_OVERHEAD + FIELD_OVERHEAD * cols)


class MeteredCursor(psycopg.Cursor):  # type: ignore[type-arg]
    """The worker pool's cursor (core/ledger.py): counts every result."""

    def execute(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        out = super().execute(*args, **kwargs)
        try:
            add(result_bytes(self))
        except Exception:  # metering must never fail a query
            pass
        return out


@contextmanager
def run(job: str) -> Iterator[RunMeter]:
    """Attribute every read inside to `job` (core/jobs.run_tracked)."""
    meter = RunMeter(job)
    token = _RUN.set(meter)
    try:
        yield meter
    finally:
        _RUN.reset(token)


# ------------------------------------------------------------- the day's note

_NOTE = re.compile(r"today (\d{4}-\d{2}-\d{2}) ~([\d.]+) MB read")


def carry(previous_note: str | None) -> None:
    """At boot: take the day's total so far from the egress job's last note
    when it is today's (a restart must not reset the day)."""
    m = _NOTE.search(previous_note or "")
    if not m:
        return
    with _LOCK:
        day = _today()
        if m.group(1) == day.day.isoformat() and day.carried == 0:
            day.carried = int(float(m.group(2)) * MB)
            day.alerted = "alerted" in (previous_note or "")


def snapshot() -> Day:
    with _LOCK:
        d = _today()
        return Day(d.day, dict(d.by_job), d.carried, d.alerted)


def day_note(day: Day, top: int = 4) -> str:
    jobs = sorted(day.by_job.items(), key=lambda kv: -kv[1])[:top]
    parts = ", ".join(f"{name} {n / MB:.1f}" for name, n in jobs)
    return (f"today {day.day.isoformat()} ~{day.total / MB:.1f} MB read"
            + (f" ({parts})" if parts else "")
            + (" · alerted" if day.alerted else ""))


def mark_alerted() -> None:
    with _LOCK:
        _today().alerted = True
