"""All database access lives here. If SQL appears anywhere else, that's a bug.

Deliberately thin and boring. Every write an agent makes goes through one of
these functions, which is what lets us guarantee shape and keep the Supabase
switch to a single environment variable.

Two rules this module exists to enforce:

1.  **The database sets `committed_at`.** No function here accepts it. There is
    no backdating path, not even an accidental one.
2.  **Core never computes an outcome.** `add_resolution` persists the outcome
    and pnl the adapter decided on. A paper position marks to market, a prop
    pays a multiplier, an event contract settles at par — that arithmetic is
    domain knowledge and lives in the adapter's `resolve()`. If this module
    ever grows an `if kind == ...`, the abstraction has failed.
3.  **Test rows never reach a track record.** Commitments and events cannot be
    deleted, so harness agents are quarantined by `agents.is_test` instead.
    Any read that computes a score must include `TRACK_RECORD_FILTER`.

Numerics are `Decimal` end to end. Floats are accepted and converted via
`str()` so 0.1 stays 0.1, but prefer passing `Decimal` from the caller.
"""

from __future__ import annotations

from array import array
from contextlib import contextmanager
import logging
import os
import re
import struct
import time
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Final, Iterator, Literal, Mapping, Sequence

import psycopg
from psycopg.adapt import Loader
from psycopg.pq import Format
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core import egress

log = logging.getLogger("valemont.ledger")

__all__ = [
    "Leg",
    "LegOutcome",
    "Commitment",
    "PendingCommitment",
    "LedgerError",
    "TRACK_RECORD_FILTER",
    "agent_id",
    "agent_is_test",
    "ClockSample",
    "measure_clock",
    "start_run",
    "end_run",
    "commit",
    "add_resolution",
    "emit_event",
    "record_resolution_attempt",
    "due_for_resolution",
    "open_commitments",
    "Factor",
    "ClosingSnapshot",
    "add_closing_snapshot",
    "record_selection",
    "due_for_capture",
    "ModelVersion",
    "record_model_version",
    "latest_model_version",
    "archive_market",
    "archive_settlement",
    "archive_candles",
    "archive_trades",
    "register_preregistration",
    "preregistration_stamp",
    "PushSubscription",
    "active_push_subscriptions",
    "record_notification",
    "finish_notification",
    "mark_push_delivered",
    "mark_push_failed",
    "notification_sent_since",
    "notification_sent_this_month",
    "record_ai_usage",
    "ai_spend_month_to_date",
    "JobHealth",
    "job_register",
    "job_started",
    "job_succeeded",
    "job_failed",
    "job_health_rows",
    "set_job_alert_state",
    "database_size",
    "QueuedJob",
    "queue_claim",
    "queue_done",
    "queue_retry",
    "queue_fail",
    "queue_fail_exhausted",
    "WeekGoalCounts",
    "owner_id",
    "carry_over_goals",
    "goal_periods_to_roll",
    "add_goal",
    "this_week_goal_counts",
    "CAPITAL_MODES",
    "capital_local_today",
    "capital_first_day",
    "write_capital_snapshots",
    "BriefSection",
    "BriefRow",
    "brief_sections",
    "context_snapshot",
    "owner_local_now",
    "latest_brief",
    "insert_brief",
    "brief_cost",
    "brief_generation_lock",
    "brief_delivered",
    "deliver_brief_once",
    "close_pool",
]


# ---------------------------------------------------------------------------
# Vocabulary — mirrors the CHECK constraints in db/001_schema.sql. Kept as
# Literals so a typo is a type error here rather than a 3am exception there.
# ---------------------------------------------------------------------------

Kind = Literal["paper_position", "prop_slip", "event_contract"]
Direction = Literal["over", "under", "long", "short", "yes", "no"]
#: `settled` = the venue paid out a value strictly between 0 and 1 — a tie at
#: $0.50, or Kalshi's "last fair price" for a long postponement or an inactive
#: prop player (db/008). Not a push (no stake came back) and never a void.
LegResult = Literal["hit", "miss", "push", "void", "settled"]
Outcome = Literal["hit", "miss", "partial", "push", "void"]
RunStatus = Literal["ok", "error"]
EventKind = Literal[
    "woke",
    "observing",
    "thesis",
    "committed",
    "slate_committed",
    "idle",
    "resolving",
    "resolved",
    "deferred",
    "voided",
    "capturing",
    "captured",
    "close_missed",
    "selected",
    "gate_evaluated",
    "error",
]

#: What an attempt came to. `deferred` and `error` produced no answer and count
#: against the bounded-defer budget. `answered` is the attempt that produced
#: the resolution or snapshot, written in the same transaction as it (db/025).
AttemptResult = Literal["deferred", "error", "answered"]

#: Which sweep an attempt belongs to. Capture and resolution share the counter
#: because they share the bounded-retry discipline exactly.
AttemptPurpose = Literal["resolve", "capture"]

#: Anything numeric a caller may hand us for a NUMERIC column.
Numeric = Decimal | int | float | str


class LedgerError(RuntimeError):
    """Raised when the ledger is asked for something that cannot exist."""


#: Every query that computes a track record — hit rate, running pnl, calibration,
#: anything the chief of staff reports — MUST include this predicate, with
#: `agents` joined as `a`. Harness agents write real rows that cannot be
#: deleted (the append-only triggers see to that), so they are filtered at read
#: time or not at all. This is a constant rather than a comment so that a
#: future scoring query has something to reach for instead of remembering.
TRACK_RECORD_FILTER: Final = "a.is_test = false"


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Leg:
    """One leg, as it stands at commit time. Frozen here and in the database.

    The same five fields carry all four domains:

        paper position   line = entry price    direction = long | short
        prop leg         line = the prop line  direction = over | under
        event contract   line = entry price    direction = yes  | no

    `line` is always in the units the outcome will be measured in, so
    ``actual - line`` is meaningful without knowing the domain. Event-contract
    prices are decimal probability (0.34), never cents.
    """

    subject: str                        # 'BTC-USD', 'NVDA', 'Ja Morant', ticker
    market: str                         # 'spot_long', 'points_over', ...
    line: Numeric | None = None
    direction: Direction | None = None
    size: Numeric | None = None


@dataclass(frozen=True, slots=True)
class Factor:
    """One named, signed adjustment making up a thesis.

    The unit of self-calibration. Each commitment carries the factors that
    produced it, so the ledger can later answer "when `injury` fired, did those
    commitments beat the close?" — and a factor that never earns its keep gets
    cut on evidence rather than on taste.

    `name` is snake_case, enforced by a CHECK in the database. Free text would
    let `injury`, `injuries` and `Injury` fragment into three factors, and every
    average would then be computed over a third of the evidence.

    `value` is signed and in the same units as the market price, so a
    probability market's factors sum meaningfully against `clv`.

    `leg_index` of None applies the factor to the whole commitment; otherwise
    to that one leg — a six-leg slip with one hurt player needs the difference.
    """

    name: str
    value: Numeric
    leg_index: int | None = None
    detail: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ClosingSnapshot:
    """The third observation, as the database recorded it."""

    id: int
    commitment_id: int
    captured_at: datetime
    status: Literal["captured", "missed"]
    clv: Decimal | None = None
    clv_pct: Decimal | None = None
    #: How late the captured "close" actually is, in seconds. Set by the
    #: database trigger, never by application code — a worker computing it from
    #: its own clock would write a wrong lag on a skewed container, and a wrong
    #: lag is worse than none because it would be trusted.
    capture_lag_seconds: Decimal | None = None


@dataclass(frozen=True, slots=True)
class LegOutcome:
    """What actually happened to one leg. Written exactly once, by `resolve()`.

    `leg_index` is 0-based and matches the order the legs were passed to
    `commit()`.
    """

    leg_index: int
    outcome: LegResult
    actual: Numeric | None = None


@dataclass(frozen=True, slots=True)
class Commitment:
    """A commitment as the database recorded it.

    `committed_at` comes back from the database rather than going in. That is
    the whole point.
    """

    id: int
    committed_at: datetime
    resolves_after: datetime


@dataclass(frozen=True, slots=True)
class PendingCommitment:
    """A commitment that is due and has no resolution row yet.

    Carries everything an adapter's `resolve()` needs to decide the outcome
    without reaching for the database itself.
    """

    id: int
    agent_id: int
    agent_slug: str
    #: True when this came from a harness agent. Carried on the row rather than
    #: left implicit so no consumer can average it in without having seen it.
    is_test: bool
    kind: Kind
    thesis: str
    confidence: Decimal | None
    payload: dict[str, Any]
    committed_at: datetime
    resolves_after: datetime
    legs: tuple[Leg, ...]

    #: When this market's opinion becomes final, or None when the domain has no
    #: meaningful close. Read as "earliest moment worth looking", not "look at
    #: exactly this" — see db/004 and `BaseAgent.capture_close`.
    closes_at: datetime | None = None

    #: How many times `resolve()` has already declined to answer for this one.
    #: Feeds the bounded-defer policy in `core.agent`.
    attempts: int = 0
    #: How far past `resolves_after` we are, by the *database* clock. Measured
    #: server-side on purpose: the patience threshold must not depend on a
    #: worker's clock being right.
    overdue_by: timedelta = timedelta(0)


# ---------------------------------------------------------------------------
# Connection pool — one per process, opened on first use.
# ---------------------------------------------------------------------------

_MIN_SIZE: Final = 1
_MAX_SIZE: Final = 4

_POOL: ConnectionPool | None = None


def _pool() -> ConnectionPool:
    global _POOL
    if _POOL is None:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise LedgerError(
                "DATABASE_URL is not set. Copy .env.example to .env and fill "
                "in the Supabase Session pooler string (port 5432)."
            )
        # open=False then .open() — psycopg_pool deprecates opening inside the
        # constructor. wait=True means an unreachable database fails loudly
        # here, not at the first write in the middle of the night.
        pool = ConnectionPool(
            url,
            min_size=_MIN_SIZE,
            max_size=_MAX_SIZE,
            open=False,
            # Every read is metered (core/egress.py; owner, 2026-10-15).
            kwargs={"application_name": "valemont-agents", "cursor_factory": egress.MeteredCursor},
        )
        pool.open(wait=True, timeout=30.0)
        _POOL = pool
    return _POOL


def close_pool() -> None:
    """Shut the pool down. Called by the orchestrator on SIGTERM."""
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


def _num(value: Numeric | None) -> Decimal | None:
    """Coerce to Decimal without routing through binary float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

_AGENTS: dict[str, tuple[int, bool, bool]] = {}


def _agent(slug: str) -> tuple[int, bool, bool]:
    """(id, is_test, enabled) for a slug. Cached; the roster does not change at runtime."""
    cached = _AGENTS.get(slug)
    if cached is not None:
        return cached

    with _pool().connection() as conn:
        row = conn.execute(
            "SELECT id, is_test, enabled FROM agents WHERE slug = %s", (slug,)
        ).fetchone()

    if row is None:
        raise LedgerError(
            f"No agent registered with slug {slug!r}. Add it to the agents "
            f"table via a numbered migration in db/, not by hand."
        )
    _AGENTS[slug] = (row[0], row[1], row[2])
    return _AGENTS[slug]


def agent_id(slug: str) -> int:
    """Resolve an agent slug to its id."""
    return _agent(slug)[0]


def agent_is_test(slug: str) -> bool:
    """Whether this agent's rows are quarantined from the track record.

    Exists so a harness can refuse to run against a real agent. Test rows
    cannot be deleted, so pointing the fake agent at `crypto` by accident
    would permanently contaminate the record — the check has to happen before
    the first write, not after.
    """
    return _agent(slug)[1]


def agent_enabled(slug: str) -> bool:
    """Whether the database permits this agent to be scheduled.

    One of two gates; the orchestrator requires both (a `Registration` in code,
    and this flag). Read once at boot and cached with the rest of the roster,
    so flipping it in the database takes effect on the next worker restart,
    not mid-run.
    """
    return _agent(slug)[2]


# ---------------------------------------------------------------------------
# Runs — one row per agent wake-up.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClockSample:
    """The worker-to-database clock offset, measured once per tick.

    `offset_ms` = database time − worker time at the midpoint of a timed
    `SELECT clock_timestamp()` round trip. Positive means the database clock
    is ahead. Uncertainty is ± `rtt_ms` / 2. A worker timestamp `w`
    corresponds to roughly `w + offset_ms` on the database's clock. That
    figure is an **estimate**, and is always labelled one: worker time is
    never presented as database time.
    """

    offset_ms: Decimal
    rtt_ms: Decimal
    worker_mid: datetime
    db_time: datetime

    def to_db_estimate(self, worker_time: datetime) -> datetime:
        return worker_time + timedelta(milliseconds=float(self.offset_ms))


def measure_clock() -> ClockSample:
    """`clock_timestamp()`, not `now()`: `now()` is frozen at transaction
    start. In this fresh, one-statement transaction the two agree to
    microseconds, but clock_timestamp() is the reading taken in flight."""
    with _pool().connection() as conn:
        t0 = time.time()
        row = conn.execute("SELECT clock_timestamp()").fetchone()
        t1 = time.time()
    assert row is not None
    db_time: datetime = row[0]
    worker_mid = datetime.fromtimestamp((t0 + t1) / 2, timezone.utc)
    offset = Decimal(str(round((db_time - worker_mid).total_seconds() * 1000, 3)))
    rtt = Decimal(str(round((t1 - t0) * 1000, 3)))
    return ClockSample(offset_ms=offset, rtt_ms=rtt, worker_mid=worker_mid, db_time=db_time)


def start_run(agent_id: int, notes: str | None = None, clock: ClockSample | None = None) -> int:
    """Open a run. `started_at` is the database's now(), as everything is.

    `clock`, when given, is stored on the row (db/016). If db/016 is not
    pasted yet, the run still opens without it and the omission is logged
    at ERROR. A worker that cannot start runs records nothing at all, which
    is worse than a run missing its clock reading.
    """
    if clock is not None:
        try:
            with _pool().connection() as conn:
                row = conn.execute(
                    """
                    INSERT INTO runs (agent_id, status, notes, clock_offset_ms, clock_rtt_ms)
                    VALUES (%s, 'running', %s, %s, %s)
                    RETURNING id
                    """,
                    (agent_id, notes, clock.offset_ms, clock.rtt_ms),
                ).fetchone()
            assert row is not None
            return row[0]
        except psycopg.errors.UndefinedColumn:
            log.error("runs has no clock columns — paste db/016; clock offset NOT stored on this run")
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO runs (agent_id, status, notes)
            VALUES (%s, 'running', %s)
            RETURNING id
            """,
            (agent_id, notes),
        ).fetchone()
    assert row is not None
    return row[0]


def end_run(
    run_id: int,
    status: RunStatus,
    error: str | None = None,
    notes: str | None = None,
) -> None:
    """Close a run.

    `runs` is mutable on purpose — it is bookkeeping, not a commitment. The
    append-only guarantee covers `commitments` and `events`, where it matters.

    `notes` is only overwritten when a value is supplied, so a note left at
    `start_run` survives.
    """
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE runs
               SET status   = %s,
                   ended_at = now(),
                   error    = %s,
                   notes    = COALESCE(%s, notes)
             WHERE id = %s
            """,
            (status, error, notes, run_id),
        )


# ---------------------------------------------------------------------------
# Commitments — the core write. Commitment and legs land together or not at all.
# ---------------------------------------------------------------------------

def commit(
    *,
    agent_id: int,
    run_id: int,
    kind: Kind,
    thesis: str,
    payload: dict[str, Any],
    resolves_after: datetime,
    legs: Sequence[Leg],
    confidence: Numeric | None = None,
    closes_at: datetime | None = None,
    factors: Sequence[Factor] = (),
) -> Commitment:
    """Write a commitment, its legs and its factors in ONE transaction.

    There is deliberately no `committed_at` parameter. The database stamps it
    and hands it back. A half-written commitment — a thesis with no legs, or
    legs with no parent — would be a corrupt track record, so both inserts
    share a transaction and roll back together.

    `resolves_after` must be an aware datetime in the future. The
    `resolves_in_future` CHECK rejects anything else, surfacing as a loud
    psycopg error rather than a silently backdated row.

    Raises:
        LedgerError: if `legs` is empty or `resolves_after` is naive.
    """
    if not legs:
        raise LedgerError(
            "A commitment with no legs is not a commitment. Even a single "
            "paper position is one leg."
        )
    if resolves_after.tzinfo is None:
        raise LedgerError(
            "resolves_after must be timezone-aware. Four agents span crypto, "
            "US market hours, game slates and event settlement — naive "
            "timestamps will burn us."
        )
    if closes_at is not None and closes_at.tzinfo is None:
        raise LedgerError("closes_at must be timezone-aware.")

    with _pool().connection() as conn, conn.cursor() as cur:
        row = cur.execute(
            """
            INSERT INTO commitments
                (agent_id, run_id, kind, thesis, confidence,
                 payload, resolves_after, closes_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, committed_at, resolves_after
            """,
            (
                agent_id,
                run_id,
                kind,
                thesis,
                _num(confidence),
                Jsonb(payload),
                resolves_after,
                closes_at,
            ),
        ).fetchone()
        assert row is not None
        commitment_id: int = row[0]

        if factors:
            # Same transaction as the commitment: a thesis whose attribution
            # landed separately could be half-recorded, and a factor written
            # after the fact is exactly the revision §2 forbids.
            cur.executemany(
                """
                INSERT INTO commitment_factors
                    (commitment_id, leg_index, name, value, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (
                        commitment_id,
                        factor.leg_index,
                        factor.name,
                        _num(factor.value),
                        Jsonb(factor.detail or {}),
                    )
                    for factor in factors
                ],
            )

        cur.executemany(
            """
            INSERT INTO legs
                (commitment_id, leg_index, subject, market,
                 line, direction, size)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    commitment_id,
                    index,
                    leg.subject,
                    leg.market,
                    _num(leg.line),
                    leg.direction,
                    _num(leg.size),
                )
                for index, leg in enumerate(legs)
            ],
        )

    return Commitment(id=row[0], committed_at=row[1], resolves_after=row[2])


# ---------------------------------------------------------------------------
# Resolutions — written after the fact. Never edits the commitment.
# ---------------------------------------------------------------------------

def add_resolution(
    *,
    commitment_id: int,
    outcome: Outcome,
    leg_outcomes: Sequence[LegOutcome],
    pnl: Numeric | None = None,
    detail: dict[str, Any] | None = None,
    attempt: AttemptResult | None = "answered",
    attempt_reason: str | None = None,
) -> int:
    """Record what happened. One transaction: the attempt, leg outcomes, then
    the seal.

    **The attempt is part of the answer (db/024, db/025).** A resolution
    answers a `resolve()` call, so by default the `'resolve'` attempt that
    produced it is written first, in the same transaction. That is what lets
    db/024's trigger stay strict without special cases: an adapter that
    returns a void on its very first look has that look on record, and the
    void is accepted. Pass ``attempt=None`` only when nothing was asked —
    core's abandonment of an exhausted budget — so that void must stand on
    the attempts already recorded, which is exactly what db/024 checks.

    `outcome` and `pnl` are the adapter's verdict, persisted as given. This
    function does not know what a hit is — see the module docstring.

    **The pnl contract (CLAUDE.md §9 — binding).** `pnl` is *return on declared
    risk*, a dimensionless decimal — an R-multiple::

        pnl = (proceeds - capital_at_risk) / capital_at_risk
        capital_at_risk = |entry - invalidation| × size

    **Every commitment declares an invalidation level at commit time**, every
    domain, every direction. The denominator is the distance to what the agent
    said would prove it wrong — not notional, not a margin number, and never
    a worst case the instrument merely permits. §9 is §2 applied to risk:
    declare what would refute you, before you find out.

    Not dollars. Not a payout multiplier. Not a percentage — ``0.05``, never
    ``5``.

    ============  ==================================================
    ``-1.0``      the stop was hit and the thesis was fully wrong.
                  Means exactly that in every domain. No clamp, no
                  special case — the denominator makes it true
    ``+2.5``      two and a half times the declared risk
    ``0``         push, or a void that returned the stake — write
                  ``0``, not ``None``
    ``None``      not scored: the return never became computable, or
                  no invalidation was declared. Never a break-even
    ============  ==================================================

    Long and short are the same expression with a sign, which is the point:
    ``(exit-entry)/(entry-stop)`` and ``(entry-exit)/(stop-entry)``. A prop's
    stake and an event contract's price are stops at zero — structural, not
    chosen — so those two shapes were always R-multiples already.

    Two obligations on the caller:

    *   **Bound the stop.** A denominator the agent picks is one it could
        shrink to inflate its own multiple. `core.agent.declared_risk()`
        enforces §9.0's band and raises rather than record ``+500R``.
    *   **Truncate the exit at the stop** when the stop was hit during the
        holding period (§9.1), or a position that blew through it records
        ``-2.5`` and breaks the floor. That is modeling the declared exit,
        not clamping.

    Normalizing throws away position size on purpose. Put the domain-native
    figures in `detail` so nothing is lost and a size-weighted metric stays
    reconstructible later::

        detail={"unit": "usd", "entry": 60000, "invalidation": 58200,
                "exit": 61800, "capital_at_risk": 1800.00,
                "proceeds": 3600.00, "stop_hit": False,
                "fill": "assumed_at_stop"}

    `core.agent.directional_return()` does the arithmetic for a long or a
    short; `return_on_risk()` is the primitive underneath.

    Legs are writable exactly once (`legs_frozen`), and the resolution's timing
    is checked against `resolves_after` (`resolutions_timing`). Both surface as
    loud psycopg errors, which is the behaviour we want: a second resolution
    attempt should fail, not overwrite.

    Returns:
        The new resolution row's id.
    """
    with _pool().connection() as conn, conn.cursor() as cur:
        if attempt is not None:
            _insert_attempt(cur, commitment_id, attempt, attempt_reason, "resolve")
        if leg_outcomes:
            cur.executemany(
                """
                UPDATE legs
                   SET actual = %s, outcome = %s
                 WHERE commitment_id = %s AND leg_index = %s
                """,
                [
                    (_num(lo.actual), lo.outcome, commitment_id, lo.leg_index)
                    for lo in leg_outcomes
                ],
            )
            if cur.rowcount == 0:
                raise LedgerError(
                    f"Commitment {commitment_id} has no leg matching the "
                    f"outcomes supplied. leg_index is 0-based and must match "
                    f"the order passed to commit()."
                )

        # The resolution row goes last: it is the marker that says this
        # commitment is sealed. resolved_at defaults to now().
        row = cur.execute(
            """
            INSERT INTO resolutions (commitment_id, outcome, pnl, detail)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (commitment_id, outcome, _num(pnl), Jsonb(detail or {})),
        ).fetchone()

    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# Events — the activity stream. Build the stream first, the visuals last.
# ---------------------------------------------------------------------------

def emit_event(
    kind: EventKind,
    message: str | None = None,
    *,
    agent_id: int | None = None,
    run_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one row to the activity stream.

    Emission is core's job — `BaseAgent` calls this at each transition and
    adapters never do. Returns nothing on purpose: nothing downstream should
    need an event's id.
    """
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO events (agent_id, run_id, kind, message, detail)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (agent_id, run_id, kind, message, Jsonb(detail or {})),
        )


# ---------------------------------------------------------------------------
# Resolution attempts — the bounded-defer counter.
# ---------------------------------------------------------------------------

def add_closing_snapshot(
    *,
    commitment_id: int,
    entry_price: Numeric | None = None,
    close_price: Numeric | None = None,
    clv: Numeric | None = None,
    clv_pct: Numeric | None = None,
    status: Literal["captured", "missed"] = "captured",
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
    attempt: AttemptResult | None = "answered",
    attempt_reason: str | None = None,
) -> ClosingSnapshot:
    """Record the market's final opinion. One per commitment, ever.

    Like `add_resolution`, the `'capture'` attempt that produced the snapshot
    is written first, in the same transaction: ``'answered'`` for a captured
    close, ``'error'`` (with its reason) when the adapter declared the close
    unavailable on that look. ``attempt=None`` only for core's tombstone of an
    exhausted budget, which asks nothing and must stand on the attempts
    already on record (db/024).

    `clv` is passed in already computed rather than derived here or on read.
    A formula living in a query can be changed, and changing it silently
    rewrites every historical measurement — freezing the number at capture
    time is the same principle that freezes the commitment itself. Use
    `core.agent.closing_line_value()` to produce it.

    A `missed` snapshot is a tombstone, not a failure to write: it records that
    the close could not be captured and stops the capture sweep asking forever.
    Absent and unrecoverable are otherwise indistinguishable, and the close
    happens exactly once.

    The database enforces that a snapshot cannot predate `closes_at`, cannot
    exist for a commitment that declared no close, and cannot be written twice.
    All three surface as loud psycopg errors.

    `capture_lag_seconds` is filled in by the trigger and returned. It is not a
    parameter: how late a capture was is a measured fact, not something the
    caller gets to assert. A close captured thirty minutes late on a market
    that kept moving is a late price wearing the name "close", and the lag is
    what makes that detectable afterwards instead of invisible.
    """
    if status == "missed" and not reason:
        raise LedgerError(
            "a missed snapshot must say why — it is a record of permanent "
            "data loss, and an unexplained one is no better than an absent row."
        )

    with _pool().connection() as conn, conn.cursor() as cur:
        if attempt is not None:
            _insert_attempt(cur, commitment_id, attempt, attempt_reason, "capture")
        row = cur.execute(
            """
            INSERT INTO closing_snapshots
                (commitment_id, status, entry_price, close_price,
                 clv, clv_pct, reason, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, commitment_id, captured_at, status, clv, clv_pct,
                      capture_lag_seconds
            """,
            (
                commitment_id,
                status,
                _num(entry_price),
                _num(close_price),
                _num(clv),
                _num(clv_pct),
                reason,
                Jsonb(detail or {}),
            ),
        ).fetchone()

    assert row is not None
    return ClosingSnapshot(
        id=row[0], commitment_id=row[1], captured_at=row[2],
        status=row[3], clv=row[4], clv_pct=row[5],
        capture_lag_seconds=row[6],
    )


def record_selection(
    commitment_id: int,
    selected: bool,
    note: str | None = None,
) -> int:
    """Record the operator's pick on a commitment.

    Deliberately not a column on `commitments`. The slate is committed at T and
    picked at T+30min, so a column would need an UPDATE against an immutable
    table — but the stronger reason is that the pick is itself a commitment. A
    database trigger rejects a selection made after the close, because a pick
    recorded once the line has moved is hindsight, not judgement, and would
    silently inflate any measurement of whether the operator beats the model.

    `selected=False` is a real answer and worth storing: declining is a
    decision, and it must not collapse into the same absent row as never
    having looked.
    """
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO selections (commitment_id, selected, note)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (commitment_id, selected, note),
        ).fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# Model versions — append-only fits (db/010). Written by a weekly fit job, read
# by `form_thesis()`. Never edited: a bad fit is superseded, not repaired.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ModelVersion:
    """One fitted model, exactly as the fit job recorded it."""

    id: int
    agent_id: int
    model: str
    fitted_at: datetime
    data_through: datetime
    params: dict[str, Any]
    diagnostics: dict[str, Any]


def record_model_version(
    *,
    agent_id: int,
    model: str,
    data_through: datetime,
    params: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    usable: bool = True,
    reason: str | None = None,
) -> int:
    """Append one fit. `fitted_at` is the database's; `data_through` is the fence.

    `data_through` is the latest kickoff whose data the fit saw. The database
    refuses a fit whose fence is later than the moment it was recorded, and a
    fit marked unusable without a reason (db/010).
    """
    if data_through.tzinfo is None:
        raise LedgerError("data_through must be timezone-aware.")
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO model_versions
                (agent_id, model, data_through, params, diagnostics, usable, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (agent_id, model, data_through, Jsonb(params),
             Jsonb(diagnostics or {}), usable, reason),
        ).fetchone()
    assert row is not None
    return row[0]


def latest_model_version(
    agent_id: int, model: str, before: datetime
) -> ModelVersion | None:
    """The newest usable fit whose data ends strictly before `before`.

    `before` is the commit instant. Filtering on `data_through < before` is the
    walk-forward fence at read time: a thesis formed at t can never load a fit
    that saw t or anything after it (preregistration_nfl.md §4.2). Returns None
    when no such fit exists; the caller stands down rather than guessing.
    """
    if before.tzinfo is None:
        raise LedgerError("before must be timezone-aware.")
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT id, agent_id, model, fitted_at, data_through, params, diagnostics
              FROM model_versions
             WHERE agent_id = %s AND model = %s AND usable
               AND data_through < %s
             ORDER BY data_through DESC, id DESC
             LIMIT 1
            """,
            (agent_id, model, before),
        ).fetchone()
    if row is None:
        return None
    return ModelVersion(
        id=row[0], agent_id=row[1], model=row[2], fitted_at=row[3],
        data_through=row[4], params=row[5], diagnostics=row[6],
    )


# ---------------------------------------------------------------------------
# Pre-registration stamps (db/017). Append-only; the database sets the time.
# ---------------------------------------------------------------------------

def register_preregistration(*, document: str, section: str, content_sha256: str,
                             note: str | None = None) -> None:
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO preregistrations (document, section, content_sha256, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (document, section, content_sha256) DO NOTHING
            """,
            (document, section, content_sha256, note),
        )


def preregistration_stamp(*, document: str, section: str,
                          content_sha256: str) -> tuple[datetime, datetime] | None:
    """(registered_at, the database's now()) for this exact text, or None."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT registered_at, now() FROM preregistrations
             WHERE document = %s AND section = %s AND content_sha256 = %s
            """,
            (document, section, content_sha256),
        ).fetchone()
    return None if row is None else (row[0], row[1])


# ---------------------------------------------------------------------------
# Web Push (db/019). App tables, not the ledger: mutable by design, owned by
# an auth.users id. The worker reaches them through DATABASE_URL like every
# other table (it bypasses RLS); apps/web reaches them as the signed-in owner.
# workers/core/push.py is the only caller.
# ---------------------------------------------------------------------------

NotificationStatus = Literal["sent", "partial", "failed"]


@dataclass(frozen=True)
class PushSubscription:
    """One device. `endpoint`, `p256dh` and `auth` are capabilities: never log
    them in full (push.py truncates the endpoint)."""

    id: int
    owner_id: str
    endpoint: str
    p256dh: str
    auth: str
    device_label: str | None


def active_push_subscriptions(owner_id: str | None = None) -> list[PushSubscription]:
    with _pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT id, owner_id::text, endpoint, p256dh, auth, device_label
              FROM push_subscriptions
             WHERE active AND (%(owner)s::uuid IS NULL OR owner_id = %(owner)s::uuid)
             ORDER BY owner_id, id
            """,
            {"owner": owner_id},
        ).fetchall()
    return [PushSubscription(*r) for r in rows]


def record_notification(*, owner_id: str, kind: str, title: str, body: str | None,
                        deep_link: str | None) -> int:
    """Log a notification as `queued`, before any device is tried."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO notifications (owner_id, kind, title, body, deep_link)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (owner_id, kind, title, body, deep_link),
        ).fetchone()
    return int(row[0])


def finish_notification(notification_id: int, *, status: NotificationStatus,
                        error: str | None) -> None:
    with _pool().connection() as conn:
        conn.execute(
            "UPDATE notifications SET status = %s, error = %s, sent_at = now() WHERE id = %s",
            (status, error, notification_id),
        )


def mark_push_delivered(subscription_id: int) -> None:
    with _pool().connection() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET last_success_at = now() WHERE id = %s",
            (subscription_id,),
        )


def mark_push_failed(subscription_id: int, *, deactivate: bool) -> None:
    """Record a failed delivery. `deactivate` for 404/410: the push service
    says the subscription is gone and will never work again."""
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE push_subscriptions
               SET failed_at = now(), active = active AND NOT %s
             WHERE id = %s
            """,
            (deactivate, subscription_id),
        )


def notification_sent_since(kind: str, hours: int) -> bool:
    """Has a `kind` notification left `queued` in the last `hours`? Used to
    send a threshold alert (db size, AI budget) once, not every run."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM notifications
                 WHERE kind = %s AND status <> 'queued'
                   AND created_at > now() - make_interval(hours => %s))
            """,
            (kind, hours),
        ).fetchone()
    return bool(row[0])


#: The owner's month, not UTC's: budgets and "once a month" alerts reset at
#: local midnight on the 1st (app_settings.timezone, America/Chicago default).
_LOCAL_MONTH_START = """
    (date_trunc('month', now() AT TIME ZONE coalesce(
        (SELECT timezone FROM app_settings), 'America/Chicago'))
     AT TIME ZONE coalesce((SELECT timezone FROM app_settings), 'America/Chicago'))
"""


def notification_sent_this_month(kind: str) -> bool:
    """Has a `kind` notification left `queued` since the 1st, owner's time?"""
    with _pool().connection() as conn:
        row = conn.execute(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM notifications
                 WHERE kind = %s AND status <> 'queued'
                   AND created_at >= {_LOCAL_MONTH_START})
            """,
            (kind,),
        ).fetchone()
    return bool(row[0])


# ---------------------------------------------------------------------------
# AI usage (db/020): append-only. core/ai.py is the only writer here; the web
# twin (Chat 2) writes as the owner through RLS.
# ---------------------------------------------------------------------------

def record_ai_usage(*, purpose: str, model: str, tokens_in: int, tokens_out: int,
                    cache_read_tokens: int, cache_write_tokens: int, batch: bool,
                    cost_usd: Decimal, critical: bool) -> int:
    """Append one row; returns its id."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO ai_usage (purpose, model, tokens_in, tokens_out, cache_read_tokens,
                                  cache_write_tokens, batch, cost_usd, critical)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (purpose, model, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens,
             batch, cost_usd, critical),
        ).fetchone()
    return int(row[0])


def ai_spend_month_to_date() -> Decimal:
    """Sum of ai_usage.cost_usd since the 1st of the owner's month."""
    with _pool().connection() as conn:
        row = conn.execute(
            f"SELECT coalesce(sum(cost_usd), 0) FROM ai_usage WHERE created_at >= {_LOCAL_MONTH_START}"
        ).fetchone()
    return Decimal(row[0])


# ---------------------------------------------------------------------------
# Job health (db/020): latest state only, one row per job, UPDATE in place.
# Written by core/jobs.py's wrapper; read by the health monitor. Every
# staleness judgement is made with the DATABASE clock, never the worker's
# (Kalshi adapter notes: no local now() for deadlines).
# ---------------------------------------------------------------------------

AlertState = Literal["ok", "alerted"]


@dataclass(frozen=True)
class JobHealth:
    job: str
    expected_interval_s: int
    last_ok_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    alert_state: AlertState
    #: Server-computed: last_ok_at (or, if never ok, the last update) is older
    #: than twice the expected interval.
    stale: bool


def job_register(job: str, expected_interval_s: int) -> None:
    """Create the job's row, or update its interval. Never resets its state.
    A retired job (db/023) that is scheduled again is un-retired: it is live."""
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO job_health (job, expected_interval_s) VALUES (%s, %s)
            ON CONFLICT (job) DO UPDATE SET expected_interval_s = EXCLUDED.expected_interval_s,
                                            retired_at = NULL
            """,
            (job, expected_interval_s),
        )


def job_started(job: str) -> None:
    with _pool().connection() as conn:
        conn.execute(
            "UPDATE job_health SET last_started_at = now(), updated_at = now() WHERE job = %s",
            (job,),
        )


def job_succeeded(job: str, note: str | None = None) -> None:
    """A good run. `note` (already redacted) records a deliberate skip, e.g.
    'skipped: AI budget spent', in last_error without counting a failure."""
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_health
               SET last_ok_at = now(), consecutive_failures = 0, last_error = left(%s, 500),
                   updated_at = now()
             WHERE job = %s
            """,
            (note, job),
        )


def job_note(job: str) -> str | None:
    """A job's latest note (job_health.last_error: a success note or the
    error): the egress job reads its own to carry the day's total."""
    with _pool().connection() as conn:
        row = conn.execute("SELECT last_error FROM job_health WHERE job = %s", (job,)).fetchone()
    return row[0] if row else None


def job_failed(job: str, error: str) -> None:
    """`error` must already be redacted and short (core/jobs.py does both)."""
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_health
               SET consecutive_failures = consecutive_failures + 1,
                   last_error = left(%s, 500), updated_at = now()
             WHERE job = %s
            """,
            (error, job),
        )


def job_health_rows() -> list[JobHealth]:
    with _pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT job, expected_interval_s, last_ok_at, last_error, consecutive_failures,
                   alert_state,
                   now() - coalesce(last_ok_at, updated_at)
                       > make_interval(secs => 2 * expected_interval_s) AS stale
              FROM job_health
             -- Retired jobs (db/023) are history, not health: kept, not watched.
             WHERE retired_at IS NULL
             ORDER BY job
            """
        ).fetchall()
    return [JobHealth(*r) for r in rows]


def set_job_alert_state(job: str, state: AlertState) -> None:
    with _pool().connection() as conn:
        conn.execute(
            "UPDATE job_health SET alert_state = %s, updated_at = now() WHERE job = %s",
            (state, job),
        )


# ---------------------------------------------------------------------------
# Database size (the free tier's 500 MB cap)
# ---------------------------------------------------------------------------

def database_size(top: int = 10) -> tuple[int, list[tuple[str, int]]]:
    """(total bytes, [(table, total relation bytes)] for the `top` largest)."""
    with _pool().connection() as conn:
        total = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        tables = conn.execute(
            """
            SELECT c.relname, pg_total_relation_size(c.oid)
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
             ORDER BY 2 DESC LIMIT %s
            """,
            (top,),
        ).fetchall()
    return int(total), [(name, int(size)) for name, size in tables]


# ---------------------------------------------------------------------------
# Job queue (db/020): the app enqueues, the worker claims with
# FOR UPDATE SKIP LOCKED, so two pollers never take the same row.
# ---------------------------------------------------------------------------

#: A `running` row older than this belongs to a worker that died mid-job.
QUEUE_STUCK_AFTER = timedelta(minutes=10)
QUEUE_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class QueuedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int  # including this one


def queue_claim() -> QueuedJob | None:
    """Claim the next due job (or one abandoned mid-run), counting an attempt."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE job_queue
               SET status = 'running', locked_at = now(), attempts = attempts + 1
             WHERE id = (
                 SELECT id FROM job_queue
                  WHERE attempts < %(max)s
                    AND ((status = 'queued' AND run_after <= now())
                      OR (status = 'running' AND locked_at < now() - %(stuck)s))
                  ORDER BY run_after, id
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED)
            RETURNING id, kind, payload, attempts
            """,
            {"max": QUEUE_MAX_ATTEMPTS, "stuck": QUEUE_STUCK_AFTER},
        ).fetchone()
    return None if row is None else QueuedJob(*row)


def queue_done(job_id: int) -> None:
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_queue
               SET status = 'done', locked_at = NULL, finished_at = now(), last_error = NULL
             WHERE id = %s
            """,
            (job_id,),
        )


def queue_retry(job_id: int, error: str, delay: timedelta) -> None:
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_queue
               SET status = 'queued', locked_at = NULL, last_error = left(%s, 500),
                   run_after = now() + %s
             WHERE id = %s
            """,
            (error, delay, job_id),
        )


def queue_fail(job_id: int, error: str) -> None:
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_queue
               SET status = 'failed', locked_at = NULL, finished_at = now(),
                   last_error = left(%s, 500)
             WHERE id = %s
            """,
            (error, job_id),
        )


def queue_fail_exhausted() -> int:
    """Fail `running` rows abandoned on their last allowed attempt; returns how many."""
    with _pool().connection() as conn:
        cur = conn.execute(
            """
            UPDATE job_queue
               SET status = 'failed', locked_at = NULL, finished_at = now(),
                   last_error = 'abandoned mid-run on the final attempt'
             WHERE status = 'running' AND attempts >= %s AND locked_at < now() - %s
            """,
            (QUEUE_MAX_ATTEMPTS, QUEUE_STUCK_AFTER),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Goals (db/021): an app table, edited in place, except its carry history.
# The carry rules live in SQL (carry_over_goals, goal_periods_to_roll), so the
# worker, apps/web's ensureRollover and scripts/goals_rollover.py share one
# copy. The worker's DATABASE_URL role bypasses RLS: owner_id is passed or
# read from app_settings, never taken from auth.uid().
# ---------------------------------------------------------------------------

#: The horizons that have periods, and so roll over. long_term never does.
RolloverHorizon = Literal["weekly", "monthly"]
GoalHorizon = Literal["weekly", "monthly", "long_term"]


@dataclass(frozen=True)
class WeekGoalCounts:
    """The owner's current week (Monday, owner's timezone). Goals moved on to
    a later week and dropped goals are in none of the three."""

    open: int
    done: int
    #: Open goals that arrived by carry-over.
    carried_in: int

    @property
    def held(self) -> int:
        """Goals holding one of the week's slots: open + done. Dropped and
        moved-on goals are already outside all three counts."""
        return self.open + self.done


def owner_id() -> str:
    """The one owner (app_settings, seeded by db/020)."""
    with _pool().connection() as conn:
        row = conn.execute("SELECT owner_id::text FROM app_settings").fetchone()
    if row is None:
        raise LedgerError("app_settings has no row; db/020 seeds it with the owner id")
    return row[0]


def carry_over_goals(horizon: RolloverHorizon, from_date: date) -> int:
    """Carry the open goals of one ENDED period into the next. Returns rows
    inserted; 0 when they were already carried. The database refuses a period
    that has not ended in the owner's timezone, or a misaligned date."""
    with _pool().connection() as conn:
        row = conn.execute("SELECT carry_over_goals(%s, %s)", (horizon, from_date)).fetchone()
    return int(row[0])


def goal_periods_to_roll(horizon: RolloverHorizon) -> list[date]:
    """Ended periods still holding an open, uncarried goal, oldest first."""
    with _pool().connection() as conn:
        rows = conn.execute("SELECT goal_periods_to_roll(%s)", (horizon,)).fetchall()
    return [r[0] for r in rows]


def add_goal(*, title: str, horizon: GoalHorizon, period_start: date | None,
             area: str | None = None, notes: str | None = None,
             owner: str | None = None) -> str:
    """Insert one goal for `owner` (default: the app_settings owner). Returns its id."""
    owner = owner or owner_id()
    with _pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO goals (owner_id, title, horizon, period_start, area, notes)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text
            """,
            (owner, title, horizon, period_start, area, notes),
        ).fetchone()
    return row[0]


#: The slot rule, in SQL: a goal holds one of the week's ten slots when it is
#: open or done in this period. Dropped goals and goals moved on to a later
#: week (a carried row points at them) hold none. apps/web's holdsSlot()
#: (src/lib/goals/types.ts) is the same rule for the count, the strip and the
#: 11th-goal warning. tests_live/test_goals_sql.py runs this exact text.
WEEK_GOAL_COUNTS_SQL: Final = """
    SELECT count(*) FILTER (WHERE g.status = 'open'),
           count(*) FILTER (WHERE g.status = 'done'),
           count(*) FILTER (WHERE g.status = 'open' AND g.carried_from IS NOT NULL)
      FROM goals g
     WHERE g.owner_id = (SELECT owner_id FROM app_settings)
       AND g.horizon = 'weekly'
       AND g.period_start = date_trunc('week', goal_local_today())::date
       AND NOT EXISTS (SELECT 1 FROM goals c WHERE c.carried_from = g.id)
"""


def this_week_goal_counts() -> WeekGoalCounts:
    """Open, done and carried-in weekly goals for the owner's current week."""
    with _pool().connection() as conn:
        row = conn.execute(WEEK_GOAL_COUNTS_SQL).fetchone()
    return WeekGoalCounts(*(int(v) for v in row))


# ---------------------------------------------------------------------------
# Capital (db/026): money tables, append-only, every row carries mode. Values
# come from one SQL function, capital_value_as_of(mode, at), so the worker's
# snapshots and the web's v_capital_today read the same numbers. Paper and
# live are separate rows everywhere here; nothing sums across modes.
# ---------------------------------------------------------------------------

CapitalMode = Literal["paper", "live"]
CAPITAL_MODES: Final[tuple[CapitalMode, ...]] = ("paper", "live")


def capital_local_today() -> date:
    """Today in the owner's timezone, by the database's clock (db/021)."""
    with _pool().connection() as conn:
        row = conn.execute("SELECT goal_local_today()").fetchone()
    return row[0]


def capital_first_day(mode: CapitalMode) -> date | None:
    """The local date of the earliest data any source has for `mode`; None
    when the mode has none (no snapshots are written for it)."""
    with _pool().connection() as conn:
        row = conn.execute(
            """
            SELECT (capital_first_at(%s) AT TIME ZONE coalesce(
                       (SELECT timezone FROM app_settings LIMIT 1), 'America/Chicago'))::date
            """,
            (mode,),
        ).fetchone()
    return row[0]


#: Every day in [first, last] gets each source's closing value as of the end
#: of that local day. Days already written are skipped before the insert (so
#: a nightly pass over the whole history burns no ids), and ON CONFLICT covers
#: a race with another writer. db/026's trigger refuses any day not yet ended.
#: tests_live/test_capital_sql.py runs this exact text.
CAPITAL_SNAPSHOT_SQL: Final = """
    INSERT INTO capital_snapshots (owner_id, snap_date, mode, source, value)
    SELECT s.owner_id, d.day, %(mode)s, v.source, v.value
      FROM app_settings s
     CROSS JOIN LATERAL (
           SELECT g::date AS day
             FROM generate_series(%(first)s::date, %(last)s::date, interval '1 day') g
          ) d
     CROSS JOIN LATERAL capital_value_as_of(%(mode)s, capital_day_end(d.day)) v
     WHERE NOT EXISTS (
           SELECT 1 FROM capital_snapshots x
            WHERE x.owner_id = s.owner_id AND x.snap_date = d.day
              AND x.mode = %(mode)s AND x.source = v.source)
    ON CONFLICT (owner_id, snap_date, mode, source) DO NOTHING
"""


def write_capital_snapshots(mode: CapitalMode, first: date, last: date) -> int:
    """Write the closing value of every source for each day first..last
    (inclusive) not yet snapshotted. Returns rows inserted."""
    if mode not in CAPITAL_MODES:
        raise LedgerError(f"unknown capital mode {mode!r}")
    if last < first:
        raise LedgerError(f"capital snapshot range is empty: {first} > {last}")
    with _pool().connection() as conn:
        cur = conn.execute(CAPITAL_SNAPSHOT_SQL, {"mode": mode, "first": first, "last": last})
        return cur.rowcount


# ---------------------------------------------------------------------------
# Morning brief (db/029, db/030). The sections come from the context-provider
# registry (brief_section = true, in sort order); the content from
# context_snapshot(), the same SQL Wags reads. briefs and brief_deliveries are
# append-only: a regenerated brief is a new row, the latest per date wins
# (v_briefs_latest), and a date is delivered at most once.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BriefSection:
    key: str
    title: str
    sort: int


@contextmanager
def _brief_conn(conn: psycopg.Connection | None) -> Iterator[psycopg.Connection]:
    """The given connection (a tests_live transaction that is rolled back),
    else one from the pool. Production never passes one."""
    if conn is not None:
        yield conn
    else:
        with _pool().connection() as pooled:
            yield pooled


#: tests_live/test_brief_sql.py runs this exact text.
BRIEF_SECTIONS_SQL: Final = """
    SELECT key, label, sort FROM context_providers
     WHERE enabled AND brief_section
     ORDER BY sort, key
"""

#: THE selector of "today's brief" for the worker (the push job, the script):
#: v_briefs_latest, which never contains a test brief (is_test). The web's
#: real views read the same view; ctx_brief() does too.
LATEST_BRIEF_SQL: Final = """
    SELECT id, brief_date, body, fallback, model, word_count, created_at
      FROM v_briefs_latest WHERE kind = %s AND brief_date = %s
"""


def brief_sections(*, conn: psycopg.Connection | None = None) -> list[BriefSection]:
    with _brief_conn(conn) as c:
        rows = c.execute(BRIEF_SECTIONS_SQL).fetchall()
    return [BriefSection(*r) for r in rows]


def context_snapshot(*, conn: psycopg.Connection | None = None) -> dict[str, Any]:
    """Every enabled provider's slice plus the clock (db/029). Not recorded:
    context_snapshots is Wags's record of what it knew."""
    with _brief_conn(conn) as c:
        return c.execute("SELECT context_snapshot()").fetchone()[0]


def owner_local_now(*, conn: psycopg.Connection | None = None) -> datetime:
    """The owner's wall-clock time, by the DATABASE's clock (never the
    worker's: Kalshi adapter notes). Naive, in America/Chicago (app_settings)."""
    with _brief_conn(conn) as c:
        return c.execute(
            "SELECT now() AT TIME ZONE coalesce((SELECT timezone FROM app_settings LIMIT 1), 'America/Chicago')"
        ).fetchone()[0]


@dataclass(frozen=True)
class BriefRow:
    id: int
    brief_date: date
    body: dict[str, Any]
    fallback: bool
    model: str | None
    word_count: int
    created_at: datetime


def latest_brief(kind: str, day: date, *, conn: psycopg.Connection | None = None) -> BriefRow | None:
    """The newest REAL brief of `kind` for `day`. A test brief never counts,
    however new (LATEST_BRIEF_SQL reads v_briefs_latest)."""
    with _brief_conn(conn) as c:
        row = c.execute(LATEST_BRIEF_SQL, (kind, day)).fetchone()
    return BriefRow(*row) if row else None


def insert_brief(*, kind: str, day: date, body: Mapping[str, Any], model: str | None,
                 ai_usage_id: int | None, fallback: bool, voice_version: str,
                 word_count: int, conn: psycopg.Connection | None = None) -> int:
    """A new brief row (never an edit). period_* is the owner's local day."""
    with _brief_conn(conn) as c:
        return c.execute(
            """
            INSERT INTO briefs (period_start, period_end, body, kind, brief_date, model,
                                ai_usage_id, fallback, voice_version, word_count)
            SELECT (%(day)s::timestamp AT TIME ZONE tz), ((%(day)s::date + 1)::timestamp AT TIME ZONE tz),
                   %(body)s, %(kind)s, %(day)s, %(model)s, %(usage)s, %(fallback)s, %(voice)s, %(words)s
              FROM (SELECT coalesce((SELECT timezone FROM app_settings LIMIT 1), 'America/Chicago') AS tz) z
            RETURNING id
            """,
            {"day": day, "body": Jsonb(dict(body)), "kind": kind, "model": model, "usage": ai_usage_id,
             "fallback": fallback, "voice": voice_version, "words": word_count},
        ).fetchone()[0]


@contextmanager
def brief_generation_lock(kind: str, day: date, *, conn: psycopg.Connection | None = None) -> Iterator[None]:
    """Hold a database lock while one process makes `day`'s brief.

    The generate job and the push job both make a brief when none exists, and
    at boot they start together: on 2026-10-10 17:48 both called Claude and
    wrote two briefs a second apart. Whoever holds this lock re-checks
    latest_brief() before generating (core/brief.ensure_brief), so the second
    one finds the first one's row. A session lock on its own pooled
    connection (the brief is written on others); in tests_live, the given
    connection's transaction-scoped lock.
    """
    key = f"brief_generate:{kind}:{day}"
    if conn is not None:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        yield
        return
    with _pool().connection() as held:
        held.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
        try:
            yield
        finally:
            held.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))


def brief_cost(brief_id: int) -> Decimal | None:
    """The cost of the AI call a brief was accepted from (None for a fallback)."""
    with _pool().connection() as conn:
        row = conn.execute(
            "SELECT u.cost_usd FROM briefs b JOIN ai_usage u ON u.id = b.ai_usage_id WHERE b.id = %s",
            (brief_id,),
        ).fetchone()
    return row[0] if row else None


def brief_delivered(kind: str, day: date, *, conn: psycopg.Connection | None = None) -> bool:
    with _brief_conn(conn) as c:
        return bool(c.execute(
            "SELECT EXISTS (SELECT 1 FROM brief_deliveries WHERE kind = %s AND brief_date = %s)",
            (kind, day),
        ).fetchone()[0])


def deliver_brief_once(*, kind: str, day: date, brief_id: int, late: bool,
                       send: Callable[[], int | None],
                       conn: psycopg.Connection | None = None) -> int | None:
    """Push a brief at most once per kind and date.

    In one transaction: take a lock on (kind, date), return None if a
    delivery is already recorded, else call `send()` (which pushes and returns
    the notification id) and record the delivery. A send that raises rolls
    the claim back, so a failed push can be retried; two workers, or a cron
    fire and a boot catch-up, can never both push. The database refuses a
    delivery of a test brief (db/030 brief_deliveries_match).
    """
    with _brief_conn(conn) as c, c.transaction():
        c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"brief_delivery:{kind}:{day}",))
        if c.execute(
            "SELECT EXISTS (SELECT 1 FROM brief_deliveries WHERE kind = %s AND brief_date = %s)",
            (kind, day),
        ).fetchone()[0]:
            return None
        notification_id = send()
        return c.execute(
            """
            INSERT INTO brief_deliveries (brief_id, kind, brief_date, notification_id, late)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (brief_id, kind, day, notification_id, late),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Feed (db/032, db/033): sources, items, stories, rules, the calibration log.
# App tables the worker edits in place, except content_items, which is frozen
# after insert but for clearing its embedding. Real and test rows never mix:
# every story/candidate query takes `is_test`, and the database refuses an
# item whose is_test differs from its source's or story's.
# Embeddings travel as pgvector text ('[0.1,0.2,...]') cast to halfvec.
# ---------------------------------------------------------------------------

FeedFetchStatus = Literal["ok", "not_modified"]


def _vec_text(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6g}" for x in vector) + "]"


class _HalfvecBinaryLoader(Loader):
    """pgvector's halfvec in binary (halfvec_send: int16 dim, int16 unused,
    then dim IEEE half floats, network order) straight to float32. ~770 bytes
    on the wire for 384 dimensions against ~4.7 KB as text (egress fix 2,
    2026-10-15)."""
    format = Format.BINARY

    def load(self, data: Any) -> "array[float]":
        b = bytes(data)
        dim = struct.unpack_from(">H", b, 0)[0]
        return array("f", struct.unpack_from(f">{dim}e", b, 4))


_HALFVEC_OID: int | None = None
_VECTOR_READY: "weakref.WeakSet[psycopg.Connection]" = weakref.WeakSet()


def _binary_vectors(c: psycopg.Connection, cur: psycopg.Cursor | None = None) -> None:
    """Let `c` (and `cur`, a cursor already open on it) read halfvec columns
    in binary (execute(..., binary=True)): registered once per connection;
    the type's oid looked up once per process. A cursor copies the
    connection's adapters when it is created, so one opened before this call
    must be given too. Every vector read in this module goes through it."""
    global _HALFVEC_OID
    if c in _VECTOR_READY:
        if cur is not None:
            cur.adapters.register_loader(_HALFVEC_OID, _HalfvecBinaryLoader)  # type: ignore[arg-type]
        return
    if _HALFVEC_OID is None:
        # From the catalog, not ::regtype: a role without USAGE on the
        # extensions schema (valemont_readonly) can still look it up.
        _HALFVEC_OID = int(c.execute(
            "SELECT t.oid FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'extensions' AND t.typname = 'halfvec'").fetchone()[0])
    c.adapters.register_loader(_HALFVEC_OID, _HalfvecBinaryLoader)
    if cur is not None:
        cur.adapters.register_loader(_HALFVEC_OID, _HalfvecBinaryLoader)
    _VECTOR_READY.add(c)


def _vec_parse(text: Any) -> tuple[float, ...] | None:
    if text is None:
        return None
    if isinstance(text, (list, tuple, array)):
        return tuple(float(x) for x in text)
    return tuple(float(x) for x in str(text).strip("[]").split(",") if x)


def _vec_array(text: Any) -> "array[float] | None":
    """A vector as float32 (array 'f'): 4 bytes a dimension, against about 32
    for a tuple of Python floats. The embeddings are halfvec (16-bit) in the
    database, so float32 loses nothing (Chat 3 Phase 1 hotfix: 2,766
    candidate vectors took 32.5 MB as tuples, about 4 MB as arrays)."""
    if text is None:
        return None
    if isinstance(text, (list, tuple, array)):
        return array("f", text)
    return array("f", [float(x) for x in str(text).strip("[]").split(",") if x])


@dataclass(frozen=True)
class FeedSource:
    id: int
    slug: str
    name: str
    pillars: tuple[str, ...]
    sport: str | None
    kind: str
    url: str
    priority: int
    include_categories: tuple[str, ...] | None
    etag: str | None
    last_modified: str | None
    #: None until the first successful fetch: that is "first fetch".
    last_ok_at: datetime | None
    consecutive_failures: int
    alerted: bool
    is_test: bool
    #: A Google News query's `when:` window (db/032 default 2); the ingest
    #: drops items older than this plus 2 days.
    gnews_window_days: int = 2
    #: First-party (db/045): the "Official" tag; summarized first.
    official: bool = False
    #: A page or hn source's config (feed/watch.py), else None.
    config: dict[str, Any] | None = None


_FEED_SOURCE_COLS = """
    id, slug, name, pillars, sport, kind, url, priority, include_categories, etag, last_modified,
    last_ok_at, consecutive_failures, alerted_at IS NOT NULL, is_test, gnews_window_days, official, config
"""


def _feed_source(row: Sequence[Any]) -> FeedSource:
    r = list(row)
    r[3] = tuple(r[3])
    r[8] = tuple(r[8]) if r[8] else None
    return FeedSource(*r)


def feed_sync_sources(specs: Sequence[Any], *, conn: psycopg.Connection | None = None) -> tuple[int, int]:
    """Upsert every spec by slug (feed/sources.py SourceSpec); set active =
    false on every real source whose slug is no longer listed. Never deletes.
    A changed URL resets the conditional-GET state. Returns (upserted,
    deactivated)."""
    with _brief_conn(conn) as c, c.transaction():
        for s in specs:
            c.execute(
                """
                INSERT INTO sources (slug, name, pillars, sport, kind, url, priority, active,
                                     include_categories, gnews_window_days, notes, official, config)
                VALUES (%(slug)s, %(name)s, %(pillars)s, %(sport)s, %(kind)s, %(url)s, %(priority)s,
                        %(active)s, %(cats)s, %(window)s, %(notes)s, %(official)s, %(config)s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name, pillars = EXCLUDED.pillars, sport = EXCLUDED.sport,
                    kind = EXCLUDED.kind, priority = EXCLUDED.priority, active = EXCLUDED.active,
                    include_categories = EXCLUDED.include_categories,
                    gnews_window_days = EXCLUDED.gnews_window_days, notes = EXCLUDED.notes,
                    official = EXCLUDED.official, config = EXCLUDED.config,
                    etag = CASE WHEN sources.url = EXCLUDED.url THEN sources.etag END,
                    last_modified = CASE WHEN sources.url = EXCLUDED.url THEN sources.last_modified END,
                    url = EXCLUDED.url
                 WHERE NOT sources.is_test
                """,
                {"slug": s.slug, "name": s.name, "pillars": list(s.pillars), "sport": s.sport,
                 "kind": s.kind, "url": s.url, "priority": s.priority, "active": s.active,
                 "cats": list(s.include_categories) if s.include_categories else None,
                 "window": s.window_days, "notes": s.notes,
                 "official": bool(getattr(s, "official", False)),
                 "config": Jsonb(s.config) if getattr(s, "config", None) is not None else None},
            )
        gone = c.execute(
            "UPDATE sources SET active = false WHERE active AND NOT is_test AND NOT (slug = ANY(%s))",
            ([s.slug for s in specs],),
        ).rowcount
    return len(specs), gone


def feed_active_sources(*, conn: psycopg.Connection | None = None) -> list[FeedSource]:
    """Active real sources the 30-minute ingest fetches, lowest priority
    number first. Not the `github` source: the daily repos job owns it
    (db/049, feed/repos.py); nor the `tmdb` one: the recommendations job
    owns it (db/053, screening/rec_jobs.py)."""
    with _brief_conn(conn) as c:
        rows = c.execute(f"SELECT {_FEED_SOURCE_COLS} FROM sources WHERE active AND NOT is_test "
                         "AND kind NOT IN ('github', 'tmdb') ORDER BY priority, slug").fetchall()
    return [_feed_source(r) for r in rows]


def feed_source(slug: str, *, conn: psycopg.Connection | None = None) -> FeedSource:
    with _brief_conn(conn) as c:
        row = c.execute(f"SELECT {_FEED_SOURCE_COLS} FROM sources WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LedgerError(f"no source with slug {slug!r}")
    return _feed_source(row)


def feed_rules(*, conn: psycopg.Connection | None = None) -> list[tuple[int, str, str | None, str]]:
    """Active rules as (id, kind, pillar, pattern)."""
    with _brief_conn(conn) as c:
        return [tuple(r) for r in c.execute(
            "SELECT id, kind, pillar, pattern FROM feed_rules WHERE active ORDER BY id").fetchall()]


def feed_source_ok(source_id: int, *, status: FeedFetchStatus, etag: str | None,
                   last_modified: str | None, conn: psycopg.Connection | None = None) -> None:
    """A good fetch: failures reset, the one-shot alert re-armed."""
    with _brief_conn(conn) as c:
        c.execute(
            """
            UPDATE sources
               SET last_fetched_at = now(), last_ok_at = now(), last_status = %s, last_error = NULL,
                   consecutive_failures = 0, alerted_at = NULL,
                   etag = left(%s, 500), last_modified = left(%s, 100)
             WHERE id = %s
            """,
            (status, etag, last_modified, source_id),
        )


def feed_source_failed(source_id: int, error: str, *,
                       conn: psycopg.Connection | None = None) -> tuple[int, bool]:
    """Count a failed fetch. `error` must already be redacted (core/jobs.describe).
    Returns (consecutive failures, already alerted for this outage)."""
    with _brief_conn(conn) as c:
        row = c.execute(
            """
            UPDATE sources
               SET last_fetched_at = now(), last_status = 'error', last_error = left(%s, 500),
                   consecutive_failures = consecutive_failures + 1
             WHERE id = %s
         RETURNING consecutive_failures, alerted_at IS NOT NULL
            """,
            (error, source_id),
        ).fetchone()
    return int(row[0]), bool(row[1])


def feed_source_alerted(source_id: int, *, conn: psycopg.Connection | None = None) -> None:
    with _brief_conn(conn) as c:
        c.execute("UPDATE sources SET alerted_at = now() WHERE id = %s", (source_id,))


def feed_known(source_id: int, external_ids: Sequence[str], urls: Sequence[str], *,
               conn: psycopg.Connection | None = None) -> tuple[set[str], set[str]]:
    """Exact dedupe, level 1: which of these external ids this source already
    has, and which of these canonical URLs exist anywhere."""
    with _brief_conn(conn) as c:
        ext = {r[0] for r in c.execute(
            "SELECT external_id FROM content_items WHERE source_id = %s AND external_id = ANY(%s)",
            (source_id, list(external_ids))).fetchall()}
        known_urls = {r[0] for r in c.execute(
            "SELECT url FROM content_items WHERE url = ANY(%s)", (list(urls),)).fetchall()} if urls else set()
    return ext, known_urls


@dataclass(frozen=True)
class FeedItemIn:
    external_id: str
    url: str | None
    google_url: str | None
    title: str
    teaser: str | None
    published_at: datetime
    decode_failed: bool
    #: 'date' when the source states only a day (db/045).
    precision: str = "time"
    #: Kept by a tech source's first fetch whatever its age (db/045): first
    #: seen at deploy, so never counted in the latency report.
    backfill: bool = False


def feed_insert_seeded(source: FeedSource, items: Sequence[FeedItemIn], *,
                       conn: psycopg.Connection | None = None) -> int:
    """Record items as seen without ingesting them (an undated source's first
    fetch). No story, no embedding; never shown. Returns rows written."""
    written = 0
    with _brief_conn(conn) as c, c.transaction():
        for it in items:
            written += c.execute(
                """
                INSERT INTO content_items (source_id, external_id, url, google_url, title, teaser,
                                           published_at, fetched_at, seeded, decode_failed, is_test,
                                           published_precision, backfill)
                -- LEAST: the worker's clock can run ahead of the database's
                -- now(), and db/032 refuses published_at > fetched_at.
                VALUES (%s, %s, %s, %s, %s, %s, LEAST(%s, now()), now(), true, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (source.id, it.external_id, it.url, it.google_url, it.title, it.teaser,
                 it.published_at, it.decode_failed, source.is_test, it.precision, it.backfill),
            ).rowcount
    return written


#: The story candidates an item is compared with, one row per MEMBER: stories
#: of the same world (real or test) FIRST seen within `hours` (feed/cluster.py:
#: a story stops absorbing items 48 h after it began, however recently its
#: last item came), each member's title and title-only embedding, and whether
#: the story is a tech story (db/045: a tech item joins only tech stories, any
#: other item never one, so a join can never put a story in two worlds).
#: tests_live/test_feed_ledger.py runs this exact text.
FEED_CANDIDATES_SQL: Final = """
    SELECT st.id, st.item_count, i.title, i.title_embedding,
           CASE WHEN 'tech' = ANY (st.pillars) THEN 'tech'
                WHEN 'screening' = ANY (st.pillars) THEN 'screening' ELSE 'main' END,
           i.id
      FROM stories st
      JOIN content_items i ON i.story_id = st.id
     WHERE st.is_test = %(is_test)s
       AND st.first_seen_at > now() - make_interval(hours => %(hours)s)
       AND i.title_embedding IS NOT NULL
       AND (%(since)s::timestamptz IS NULL OR i.fetched_at > %(since)s::timestamptz)
       AND i.id <> ALL (%(skip)s::bigint[])
     ORDER BY st.id, i.id
"""

#: The candidate window without a vector (feed/candidates.py, every ingest
#: run; egress fix 1, 2026-10-15): each story of the window's id, item count
#: and world. A few dozen bytes a story, against
#: ~4.8 KB a member for FEED_CANDIDATES_SQL.
FEED_CANDIDATE_WINDOW_SQL: Final = """
    SELECT st.id, st.item_count,
           CASE WHEN 'tech' = ANY (st.pillars) THEN 'tech'
                WHEN 'screening' = ANY (st.pillars) THEN 'screening' ELSE 'main' END
      FROM stories st
     WHERE st.is_test = %(is_test)s
       AND st.first_seen_at > now() - make_interval(hours => %(hours)s)
"""


@dataclass(frozen=True)
class CandidateStory:
    story_id: int
    item_count: int
    #: (title, title-only embedding as float32) per member.
    members: tuple[tuple[str, "array[float]"], ...]
    #: Its world (db/045, db/051): 'tech', 'screening' or 'main' (Home and
    #: Sports). Only an item of the same world may join it.
    world: str = "main"
    #: Each member's content_items.id, in the order of `members` (the
    #: in-memory cache keys on them; empty from a stub).
    item_ids: tuple[int, ...] = ()

    @property
    def tech(self) -> bool:
        return self.world == "tech"


def feed_candidates(hours: int, *, is_test: bool = False, since: datetime | None = None,
                    skip: Sequence[int] = (), meter: list[int] | None = None,
                    conn: psycopg.Connection | None = None) -> list[CandidateStory]:
    """Rows are read one at a time (no fetchall), each vector parsed straight
    into float32, so the text rows and the parsed vectors are never all held
    at once in Python objects. `since` (database time) keeps only members
    fetched after it, `skip` leaves out item ids the caller already holds:
    the cache's incremental read (feed/candidates.py). `meter`, when given,
    gets the approximate bytes read added to its first element (the text of
    every fetched value)."""
    stories: dict[int, tuple[int, str, list[tuple[str, array[float]]], list[int]]] = {}
    read = 0
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute(FEED_CANDIDATES_SQL, {"is_test": is_test, "hours": hours, "since": since, "skip": list(skip)},
                    binary=True)
        for sid, count, title, vec, world, item_id in cur:
            read += _row_bytes((sid, count, title, vec, world, item_id))
            s = stories.setdefault(sid, (count, str(world), [], []))
            s[2].append((title, _vec_array(vec)))
            s[3].append(int(item_id))
    if meter is not None:
        meter[0] += read
    return [CandidateStory(sid, count, tuple(members), world, tuple(ids))
            for sid, (count, world, members, ids) in stories.items()]


@dataclass(frozen=True)
class CandidateWindow:
    #: The database's clock when the window was read.
    db_now: datetime
    #: story id -> (item count, world) for every story first seen in the window.
    stories: dict[int, tuple[int, str]]


def feed_candidate_window(hours: int, *, is_test: bool = False, meter: list[int] | None = None,
                          conn: psycopg.Connection | None = None) -> CandidateWindow:
    """FEED_CANDIDATE_WINDOW_SQL: the window's stories, no vectors."""
    out: dict[int, tuple[int, str]] = {}
    read = 0
    with _brief_conn(conn) as c, c.cursor() as cur:
        db_now = c.execute("SELECT now()").fetchone()[0]
        cur.execute(FEED_CANDIDATE_WINDOW_SQL, {"is_test": is_test, "hours": hours})
        for sid, count, world in cur:
            read += _row_bytes((sid, count, world))
            out[int(sid)] = (int(count), str(world))
    if meter is not None:
        meter[0] += read
    return CandidateWindow(db_now, out)


def _row_bytes(values: Sequence[Any]) -> int:
    """Approximate wire bytes of a fetched row: a binary halfvec (an array
    from _HalfvecBinaryLoader) at its wire size, 4 + 2 a dimension; anything
    else at its text length. str() of a float32 array is ~20 characters a
    dimension, which overstated the boot load 8x (26 MB logged, 3.2 MB on
    the wire by core/egress.py, 2026-10-16)."""
    return sum(4 + 2 * len(v) if isinstance(v, array) else len(str(v)) for v in values if v is not None)


@dataclass(frozen=True)
class SimilarityRow:
    story_id: int
    kind: Literal["join", "near_miss", "cap_overflow"]
    cosine: float
    fuzz: float
    joined_by: str | None = None


def _log_similarity(c: psycopg.Connection, item_id: int, rows: Sequence[SimilarityRow]) -> None:
    for r in rows:
        c.execute(
            """
            INSERT INTO feed_similarity_log (item_id, story_id, kind, cosine, fuzz, joined_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (item_id, r.story_id, r.kind, max(-1.0, min(1.0, r.cosine)), r.fuzz, r.joined_by),
        )


def _insert_item(c: psycopg.Connection, source: FeedSource, it: FeedItemIn, story_id: int,
                 vector: Sequence[float], title_vector: Sequence[float]) -> int | None:
    row = c.execute(
        """
        INSERT INTO content_items (source_id, external_id, url, google_url, title, teaser,
                                   published_at, fetched_at, story_id, embedding, title_embedding,
                                   decode_failed, is_test, published_precision, backfill)
        VALUES (%s, %s, %s, %s, %s, %s, LEAST(%s, now()), now(), %s, %s::extensions.halfvec,
                %s::extensions.halfvec, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (source.id, it.external_id, it.url, it.google_url, it.title, it.teaser, it.published_at,
         story_id, _vec_text(vector), _vec_text(title_vector), it.decode_failed, source.is_test,
         it.precision, it.backfill),
    ).fetchone()
    return int(row[0]) if row else None


def feed_new_story(source: FeedSource, it: FeedItemIn, vector: Sequence[float],
                   title_vector: Sequence[float], *,
                   filtered_reason: str | None, seed: tuple[float, int] | None,
                   near_misses: Sequence[SimilarityRow] = (),
                   conn: psycopg.Connection | None = None) -> tuple[int, int] | None:
    """Found a story with this item as its first member (and lead, when it has
    a url). One transaction. Returns (story id, item id), or None when the
    item turned out to exist already (a race on the exact-dedupe keys)."""
    try:
        return _new_story(source, it, vector, title_vector, filtered_reason=filtered_reason, seed=seed,
                          near_misses=near_misses, conn=conn)
    except _ItemExists:
        return None


class _ItemExists(Exception):
    """Raised inside the transaction to roll back a story whose item already existed."""


def _new_story(source: FeedSource, it: FeedItemIn, vector: Sequence[float],
               title_vector: Sequence[float], *,
               filtered_reason: str | None, seed: tuple[float, int] | None,
               near_misses: Sequence[SimilarityRow], conn: psycopg.Connection | None) -> tuple[int, int]:
    with _brief_conn(conn) as c, c.transaction():
        story_id = c.execute(
            """
            INSERT INTO stories (pillars, sport, title, centroid, filtered_reason, seed_score,
                                 seed_rule_id, is_test)
            VALUES (%s, %s, %s, %s::extensions.halfvec, %s, %s, %s, %s)
            RETURNING id
            """,
            (list(source.pillars), source.sport, it.title, _vec_text(vector), filtered_reason,
             seed[0] if seed else None, seed[1] if seed else None, source.is_test),
        ).fetchone()[0]
        item_id = _insert_item(c, source, it, story_id, vector, title_vector)
        if item_id is None:
            # Nothing to found: undo the story (same transaction, so nothing leaks).
            raise _ItemExists()
        if it.url is not None:
            c.execute("UPDATE stories SET lead_item_id = %s WHERE id = %s", (item_id, story_id))
        _log_similarity(c, item_id, near_misses)
    return story_id, item_id


def feed_join_story(source: FeedSource, it: FeedItemIn, vector: Sequence[float],
                    title_vector: Sequence[float], story_id: int, *,
                    seed: tuple[float, int] | None, logged: Sequence[SimilarityRow],
                    conn: psycopg.Connection | None = None) -> int | None:
    """Add this item to `story_id`: running-mean centroid, count, last seen,
    pillars union, sport if unset, best seed score, and the lead (lowest
    source priority, then earliest published, then lowest id; url required).
    The story's title follows its lead. One transaction, the story row locked.
    Returns the item id, or None when the item already existed."""
    with _brief_conn(conn) as c, c.transaction():
        _binary_vectors(c)
        row = c.execute(
            "SELECT centroid, item_count FROM stories WHERE id = %s FOR UPDATE", (story_id,), binary=True
        ).fetchone()
        if row is None:
            raise LedgerError(f"story {story_id} vanished before the join")
        centroid, count = _vec_parse(row[0]) or tuple(vector), int(row[1])
        item_id = _insert_item(c, source, it, story_id, vector, title_vector)
        if item_id is None:
            return None
        mean = [(a * count + b) / (count + 1) for a, b in zip(centroid, vector)]
        c.execute(
            """
            UPDATE stories
               SET centroid = %(mean)s::extensions.halfvec,
                   item_count = item_count + 1,
                   last_seen_at = greatest(last_seen_at, now()),
                   pillars = ARRAY(SELECT DISTINCT unnest(pillars || %(pillars)s::text[]) ORDER BY 1),
                   sport = coalesce(sport, %(sport)s),
                   seed_score = CASE WHEN %(seed)s::real IS NOT NULL
                                      AND (seed_score IS NULL OR %(seed)s::real > seed_score)
                                     THEN %(seed)s::real ELSE seed_score END,
                   seed_rule_id = CASE WHEN %(seed)s::real IS NOT NULL
                                        AND (seed_score IS NULL OR %(seed)s::real > seed_score)
                                       THEN %(seed_rule)s ELSE seed_rule_id END
             WHERE id = %(story)s
            """,
            {"mean": _vec_text(mean), "pillars": list(source.pillars), "sport": source.sport,
             "seed": seed[0] if seed else None, "seed_rule": seed[1] if seed else None, "story": story_id},
        )
        # The lead: the best linkable member by (priority, published, id).
        c.execute(
            """
            UPDATE stories st
               SET lead_item_id = best.id, title = best.title
              FROM (SELECT i.id, i.title
                      FROM content_items i JOIN sources s ON s.id = i.source_id
                     WHERE i.story_id = %(story)s AND i.url IS NOT NULL
                     ORDER BY s.priority, i.published_at, i.id
                     LIMIT 1) best
             WHERE st.id = %(story)s AND st.lead_item_id IS DISTINCT FROM best.id
            """,
            {"story": story_id},
        )
        _log_similarity(c, item_id, logged)
    return item_id


@dataclass(frozen=True)
class SummaryJob:
    story_id: int
    title: str
    teaser: str | None
    source_name: str
    sport: str | None
    attempts: int
    source_slug: str = ""
    #: For the rank order (feed/summarize.order_by_rank, owner 2026-10-14):
    #: the story's pillars, its centroid (float32), the lead's published time,
    #: and whether its title names a followed person or derived team.
    pillars: tuple[str, ...] = ()
    vec: Sequence[float] = ()
    published_at: datetime | None = None
    followed: bool = False


#: The summarizer's CANDIDATE POOL (owner, 2026-10-14: each run takes its
#: stories in For you rank order, feed/summarize.order_by_rank; this SQL
#: order is the pool's and the tie-break): kept, real, linkable stories not
#: yet summarized and with a try left. FIRST the stories whose title names a followed person or
#: a derived team (`priority`, a POSIX regex from feed/rank.priority_pattern;
#: owner decision 10, 2026-10-12: moves are read only from summaries), THEN
#: newest PUBLISHED first: the order Home shows them in (v_feed_stories by
#: lead_published_at). Ordering by first_seen_at instead summarized an
#: arbitrary slice of a big ingest, where every story shares one first-seen
#: time: after the 2026-10-12 re-ingest only 1 of Home's first 30 cards had a
#: summary.
FEED_SUMMARY_QUEUE_SQL: Final = """
    SELECT st.id, st.title, i.teaser, s.name, st.sport, st.summary_attempts, s.slug,
           st.pillars, CASE WHEN %(vectors)s THEN st.centroid END, i.published_at,
           coalesce(lower(st.title) ~ %(priority)s::text, false)
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE st.summary IS NULL AND st.filtered_reason IS NULL AND NOT st.is_test
       AND st.summary_attempts < %(max_attempts)s
       AND NOT ('tech' = ANY (st.pillars)) AND NOT ('screening' = ANY (st.pillars))
     ORDER BY coalesce(lower(st.title) ~ %(priority)s::text, false) DESC, i.published_at DESC, st.id DESC
     LIMIT %(limit)s
"""

#: The TECH summary queue (db/045; feed/tech.py): kept, real, linkable tech
#: stories not yet summarized, with a try left. OFFICIAL FIRST (the owner's
#: speed rule: an official item is summarized in the cycle it was found),
#: then newest published. The Home/Sports queue above leaves tech out.
FEED_TECH_SUMMARY_QUEUE_SQL: Final = """
    SELECT st.id, st.title, i.teaser, s.name, s.official, st.summary_attempts,
           i.backfill, i.published_at, i.fetched_at, s.slug, CASE WHEN %(vectors)s THEN st.centroid END,
           i.published_precision
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE st.summary IS NULL AND st.filtered_reason IS NULL AND NOT st.is_test
       AND st.summary_attempts < %(max_attempts)s
       AND 'tech' = ANY (st.pillars)
     ORDER BY s.official DESC, i.published_at DESC, st.id DESC
     LIMIT %(limit)s
"""


@dataclass(frozen=True)
class TechSummaryJob:
    story_id: int
    title: str
    teaser: str | None
    source_name: str
    official: bool
    attempts: int
    #: The lead item (db/046 push eligibility): a first-fetch backfill, when
    #: it was published, and when it was first seen.
    backfill: bool = False
    published_at: datetime | None = None
    first_seen_at: datetime | None = None
    #: For the rank order: the lead source's slug and the story's centroid.
    source_slug: str = ""
    vec: Sequence[float] = ()
    #: The lead item's published_precision; 'none' (undated) never pushes (db/048).
    precision: str = "time"


def feed_tech_summary_queue(limit: int, max_attempts: int, *, vectors: bool = False,
                            conn: psycopg.Connection | None = None) -> list[TechSummaryJob]:
    """`vectors`: with each centroid (binary). Off by default (egress fix 3):
    the order needs them only when there is more work than room
    (feed/tech.py; feed_story_centroids fetches them then)."""
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute(FEED_TECH_SUMMARY_QUEUE_SQL, {"limit": limit, "max_attempts": max_attempts, "vectors": vectors},
                    binary=True)
        return [TechSummaryJob(int(r[0]), r[1], r[2], r[3], bool(r[4]), int(r[5]), bool(r[6]), r[7], r[8],
                               r[9] or "", _vec_array(r[10]) or array("f"), r[11] or "time") for r in cur]


#: The 48-hour retirement (owner, 2026-10-14): a kept story first seen more
#: than `hours` ago that never got a summary leaves the queue as
#: headline-only, the way Chat 3 retires a story out of attempts
#: (summary_attempts = max). summarized_at stays NULL, so no daily cap counts
#: it; feed_score then scores it like any retired story.
FEED_RETIRE_STALE_SQL: Final = """
    UPDATE stories SET summary_attempts = %(max_attempts)s
     WHERE summary IS NULL AND filtered_reason IS NULL AND NOT is_test
       AND summary_attempts < %(max_attempts)s AND lead_item_id IS NOT NULL
       AND first_seen_at <= now() - make_interval(hours => %(hours)s)
       AND ('tech' = ANY (pillars)) = %(tech)s
       AND NOT ('screening' = ANY (pillars))
"""


def feed_retire_stale(hours: int, max_attempts: int, *, tech: bool,
                      conn: psycopg.Connection | None = None) -> int:
    """Retire this pillar group's unsummarized stories older than `hours`.
    Returns rows retired."""
    with _brief_conn(conn) as c, c.transaction():
        return c.execute(FEED_RETIRE_STALE_SQL, {"hours": hours, "max_attempts": max_attempts, "tech": tech}).rowcount

_LOCAL_DAY_START = """
    (date_trunc('day', now() AT TIME ZONE coalesce((SELECT timezone FROM app_settings), 'America/Chicago'))
     AT TIME ZONE coalesce((SELECT timezone FROM app_settings), 'America/Chicago'))
"""


def feed_summary_queue(limit: int, max_attempts: int, priority: str | None = None, *, vectors: bool = False,
                       conn: psycopg.Connection | None = None) -> list[SummaryJob]:
    """`priority` None: publish order only. Streamed row by row. `vectors`:
    with each centroid (binary, float32). Off by default (egress fix 3): the
    rank order needs them only when the pool is bigger than the run's room
    (feed/summarize.py; feed_story_centroids fetches them then)."""
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute(FEED_SUMMARY_QUEUE_SQL,
                    {"limit": limit, "max_attempts": max_attempts, "priority": priority, "vectors": vectors},
                    binary=True)
        return [SummaryJob(int(r[0]), r[1], r[2], r[3], r[4], int(r[5]), r[6], tuple(r[7]),
                           _vec_array(r[8]) or array("f"), r[9], bool(r[10])) for r in cur]


def feed_story_centroids(ids: Sequence[int], *,
                         conn: psycopg.Connection | None = None) -> "dict[int, array[float]]":
    """story id -> centroid (float32, binary): the summarizers' rank order
    when there is more work than room (egress fix 3)."""
    out: dict[int, array[float]] = {}
    if not ids:
        return out
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute("SELECT id, centroid FROM stories WHERE id = ANY (%s) AND centroid IS NOT NULL",
                    (list(ids),), binary=True)
        for sid, vec in cur:
            out[int(sid)] = _vec_array(vec)
    return out


#: The summary backlog the health monitor watches (owner, 2026-10-12): kept,
#: real stories still waiting for a summary (a try left) that were first seen
#: more than `min_age_h` hours ago. Trailers (db/051) are never summarized,
#: so they are never a backlog.
FEED_SUMMARY_BACKLOG_SQL: Final = """
    SELECT count(*) FROM stories
     WHERE summary IS NULL AND filtered_reason IS NULL AND NOT is_test
       AND summary_attempts < %(max_attempts)s
       AND first_seen_at < now() - make_interval(hours => %(min_age_h)s)
       AND NOT ('screening' = ANY (pillars))
"""


def feed_summary_backlog(min_age_h: int, max_attempts: int, *,
                         conn: psycopg.Connection | None = None) -> int:
    with _brief_conn(conn) as c:
        return int(c.execute(FEED_SUMMARY_BACKLOG_SQL,
                             {"min_age_h": min_age_h, "max_attempts": max_attempts}).fetchone()[0])


def feed_summarized_today(*, conn: psycopg.Connection | None = None) -> int:
    """Real HOME and SPORTS stories sent to Haiku since local midnight (each
    counted once, whether or not its summary was accepted). Tech has its own
    count (owner, 2026-10-14: separate caps per pillar group)."""
    with _brief_conn(conn) as c:
        return int(c.execute(
            f"SELECT count(*) FROM stories WHERE NOT is_test AND NOT ('tech' = ANY (pillars)) "
            f"AND summarized_at >= {_LOCAL_DAY_START}"
        ).fetchone()[0])


#: Tech's own daily count (owner, 2026-10-14): COMMUNITY tech stories sent to
#: Haiku since local midnight. Official ones are never held by a daily cap,
#: so they are not counted.
FEED_TECH_COMMUNITY_TODAY_SQL: Final = f"""
    SELECT count(*) FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND 'tech' = ANY (st.pillars) AND NOT s.official
       AND st.summarized_at >= {_LOCAL_DAY_START}
"""


def feed_tech_community_summarized_today(*, conn: psycopg.Connection | None = None) -> int:
    with _brief_conn(conn) as c:
        return int(c.execute(FEED_TECH_COMMUNITY_TODAY_SQL).fetchone()[0])


@dataclass(frozen=True)
class SummaryOut:
    story_id: int
    #: None: the attempt failed validation; only the attempt is counted.
    summary: str | None
    entities: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    sport: str | None = None
    drop: str | None = None
    #: Set only when the drop filter is live: 'drop:<category>'.
    filtered_reason: str | None = None
    #: Attempts this run used: 1, or 2 with the same-run targeted retry.
    attempts: int = 1
    #: A multi-sport source (sources.toml sport_from = "model", owner D8): the
    #: summarizer's sport replaces the story's; the story keeps its own when
    #: the summarizer said none. Otherwise the story's own sport wins.
    sport_from_model: bool = False
    #: The /news category (feed/category.py, db/042). Kept if already set.
    category: str | None = None
    #: Tech only (feed/tech.py, db/045): the answer to the free-credits
    #: question. Kept if already set.
    claimable: bool | None = None


def feed_save_summaries(results: Sequence[SummaryOut], ai_usage_id: int | None, *,
                        conn: psycopg.Connection | None = None) -> None:
    """One transaction for a group's results. Every story gets an attempt
    counted and summarized_at stamped; accepted ones get their summary."""
    with _brief_conn(conn) as c, c.transaction():
        for r in results:
            if r.summary is None:
                c.execute(
                    """
                    UPDATE stories SET summary_attempts = least(summary_attempts + %s, 5), summarized_at = now()
                     WHERE id = %s
                    """,
                    (r.attempts, r.story_id),
                )
                continue
            # A repo story keeps its GitHub topics (db/049, feed/repos.py):
            # they are what a Not interested on a repo trains.
            c.execute(
                """
                UPDATE stories
                   SET summary = %s, entities = %s,
                       topics = CASE WHEN category = 'repos' AND cardinality(topics) > 0 THEN topics ELSE %s END,
                       sport = CASE WHEN %s THEN coalesce(%s, sport) ELSE coalesce(sport, %s) END,
                       category = coalesce(category, %s),
                       claimable_offer = coalesce(claimable_offer, %s),
                       drop_category = %s, filtered_reason = coalesce(filtered_reason, %s),
                       summary_attempts = least(summary_attempts + %s, 5), summarized_at = now(),
                       ai_usage_id = %s
                 WHERE id = %s
                """,
                (r.summary, list(r.entities), list(r.topics), r.sport_from_model, r.sport, r.sport,
                 r.category, r.claimable, r.drop, r.filtered_reason, r.attempts, ai_usage_id, r.story_id),
            )


# ---------------------------------------------------------------------------
# The free-credits push (db/045 tech_credit_pushes; feed/tech.py). The cap
# (3 a Chicago day) and the never-twice rule are the database's: its trigger
# refuses a 4th and a repeat. The worker asks first, under the trigger's own
# advisory lock, then pushes and records in the same transaction, so a push
# that raises records nothing and a refused one is never sent.
# ---------------------------------------------------------------------------

_TECH_PUSH_LOCK = "SELECT pg_advisory_xact_lock(hashtext('tech_credit_pushes'))"


def tech_credit_push(story_id: int, send: Callable[[], int | None], *,
                     conn: psycopg.Connection | None = None) -> int | None:
    """One real free-credits push for a story, or None when the cap is
    reached or the story was pushed already. `send()` pushes and returns the
    notification id; it runs only once the database has said yes."""
    with _brief_conn(conn) as c, c.transaction():
        c.execute(_TECH_PUSH_LOCK)
        if not c.execute("SELECT tech_credit_push_allowed(%s)", (story_id,)).fetchone()[0]:
            return None
        notification_id = send()
        return int(c.execute(
            "INSERT INTO tech_credit_pushes (story_id, notification_id) VALUES (%s, %s) RETURNING id",
            (story_id, notification_id)).fetchone()[0])


def tech_credit_test_push(story_id: int, send: Callable[[], int | None], *,
                          conn: psycopg.Connection | None = None) -> int:
    """A forced TEST push (scripts/tech_test_push.py): recorded is_test, never
    counted toward the cap, may repeat."""
    with _brief_conn(conn) as c, c.transaction():
        c.execute(_TECH_PUSH_LOCK)
        notification_id = send()
        return int(c.execute(
            "INSERT INTO tech_credit_pushes (story_id, notification_id, is_test) VALUES (%s, %s, true) RETURNING id",
            (story_id, notification_id)).fetchone()[0])


def tech_credit_push_room(*, conn: psycopg.Connection | None = None) -> int:
    with _brief_conn(conn) as c:
        return int(c.execute("SELECT tech_credit_push_room()").fetchone()[0])


def tech_story_for_test_push(story_id: int | None = None, *,
                             conn: psycopg.Connection | None = None) -> tuple[int, str, str | None] | None:
    """(id, title, summary) of a real, kept, linkable tech story: `story_id`
    if it is one, else the newest summarized one, pricing_credits first."""
    with _brief_conn(conn) as c:
        row = c.execute(
            """
            SELECT v.id, v.title, v.summary FROM v_feed_stories v
             WHERE 'tech' = ANY (v.pillars) AND (%(id)s::bigint IS NULL OR v.id = %(id)s::bigint)
               AND (%(id)s::bigint IS NOT NULL OR v.summary IS NOT NULL)
             ORDER BY (v.category = 'pricing_credits') DESC NULLS LAST, v.first_seen_at DESC, v.id DESC
             LIMIT 1
            """, {"id": story_id}).fetchone()
    return (int(row[0]), row[1], row[2]) if row else None


#: The one-off categorize pass (db/042; feed/categorize.py, owner
#: 2026-10-12 23:38): kept, real Home stories with no category yet, with
#: what Haiku reads (headline, summary when there is one, outlet).
FEED_UNCATEGORIZED_SQL: Final = """
    SELECT st.id, st.title, st.summary, s.name, st.sport, st.entities
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.category IS NULL
       AND 'home' = ANY (st.pillars)
     ORDER BY st.id
"""


@dataclass(frozen=True)
class CategorizeJob:
    story_id: int
    title: str
    summary: str | None
    source_name: str
    #: stories.sport: with one, the business-of-sports question decides
    #: (category.decide).
    sport: str | None = None
    #: stories.entities as the summary named them (category.sports_entity).
    entities: tuple[str, ...] = ()


def feed_uncategorized_home_stories(*, conn: psycopg.Connection | None = None) -> list[CategorizeJob]:
    with _brief_conn(conn) as c:
        return [CategorizeJob(int(r[0]), r[1], r[2], r[3], r[4], tuple(r[5] or ()))
                for r in c.execute(FEED_UNCATEGORIZED_SQL).fetchall()]


#: Every real sports league, team, program, tour and broadcaster (db/044),
#: by name and alias, lowercased: what category.sports_entity matches a
#: story's entities to (owner, 2026-10-13). People and companies are out.
FEED_SPORTS_ENTITY_NAMES_SQL: Final = """
    SELECT lower(e.name) FROM entities e
     WHERE NOT e.is_test AND e.kind IN ('league', 'team', 'program', 'tour', 'broadcaster')
    UNION
    SELECT a FROM entities e, unnest(e.aliases) AS a
     WHERE NOT e.is_test AND e.kind IN ('league', 'team', 'program', 'tour', 'broadcaster')
"""


def feed_sports_entity_names(*, conn: psycopg.Connection | None = None) -> frozenset[str]:
    with _brief_conn(conn) as c:
        return frozenset(" ".join(str(r[0]).split()) for r in c.execute(FEED_SPORTS_ENTITY_NAMES_SQL).fetchall())


def feed_set_categories(pairs: Sequence[tuple[int, str]], *, conn: psycopg.Connection | None = None) -> int:
    """Set each story's category, only where it is still NULL (a re-run, or a
    summary that categorized the story meanwhile, never changes one). One
    transaction. Returns rows written."""
    written = 0
    with _brief_conn(conn) as c, c.transaction():
        for sid, category in pairs:
            written += c.execute("UPDATE stories SET category = %s WHERE id = %s AND category IS NULL",
                                 (category, sid)).rowcount
    return written


#: The re-run's id set (owner, 2026-10-13: sports_business and betting under
#: the tightened definitions): kept, real Home stories now in the named
#: categories, with their current category, read once before any call.
FEED_CATEGORIZED_SQL: Final = """
    SELECT st.id, st.title, st.summary, s.name, st.sport, st.entities, st.category
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.category = ANY (%s)
       AND 'home' = ANY (st.pillars)
     ORDER BY st.id
"""


def feed_category_jobs(categories: Sequence[str], *, conn: psycopg.Connection | None = None
                       ) -> list[tuple[CategorizeJob, str]]:
    """(job, its category now) for every story in `categories`."""
    with _brief_conn(conn) as c:
        return [(CategorizeJob(int(r[0]), r[1], r[2], r[3], r[4], tuple(r[5] or ())), r[6])
                for r in c.execute(FEED_CATEGORIZED_SQL, (list(categories),)).fetchall()]


#: The business-of-sports re-run's CANDIDATES (owner, 2026-10-13): kept,
#: real, categorized Home stories with a sport, or now in sports_business,
#: film_music or other. Betting is left out (its own chip). The id set is
#: these filtered by category.decide's gate on the current category
#: (categorize.money_set: film_music and other need a sports entity).
FEED_MONEY_SET_SQL: Final = """
    SELECT st.id, st.title, st.summary, s.name, st.sport, st.entities, st.category
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.category IS NOT NULL
       AND st.category <> 'betting'
       AND (st.sport IS NOT NULL OR st.category IN ('sports_business', 'film_music', 'other'))
       AND 'home' = ANY (st.pillars)
     ORDER BY st.id
"""


#: A re-run over named stories (owner, 2026-10-13: the shipped README's
#: misfits): kept, real, categorized Home stories with these ids.
FEED_JOBS_BY_ID_SQL: Final = """
    SELECT st.id, st.title, st.summary, s.name, st.sport, st.entities, st.category
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.category IS NOT NULL
       AND 'home' = ANY (st.pillars) AND st.id = ANY (%s)
     ORDER BY st.id
"""


def feed_category_jobs_by_id(ids: Sequence[int], *, conn: psycopg.Connection | None = None
                             ) -> list[tuple[CategorizeJob, str]]:
    """(job, its category now) for each named story that qualifies."""
    with _brief_conn(conn) as c:
        return [(CategorizeJob(int(r[0]), r[1], r[2], r[3], r[4], tuple(r[5] or ())), r[6])
                for r in c.execute(FEED_JOBS_BY_ID_SQL, (list(ids),)).fetchall()]


def feed_money_jobs(*, conn: psycopg.Connection | None = None) -> list[tuple[CategorizeJob, str]]:
    """(job, its category now): the business-of-sports re-run's candidates."""
    with _brief_conn(conn) as c:
        return [(CategorizeJob(int(r[0]), r[1], r[2], r[3], r[4], tuple(r[5] or ())), r[6])
                for r in c.execute(FEED_MONEY_SET_SQL).fetchall()]


def feed_recategorize(moves: Sequence[tuple[int, str, str]], *, conn: psycopg.Connection | None = None) -> int:
    """(story id, old, new): set `new` only where the category is still `old`
    (compare-and-set: one changed meanwhile, by a summary or by hand, is left
    alone). One transaction. Returns rows written."""
    written = 0
    with _brief_conn(conn) as c, c.transaction():
        for sid, old, new in moves:
            written += c.execute("UPDATE stories SET category = %s WHERE id = %s AND category = %s",
                                 (new, sid, old)).rowcount
    return written


#: The nightly prune, in order; tests_live runs these texts. A story with ANY
#: reaction, named by a move proposal, or given a free-credits push is never
#: deleted (db/039, db/042, db/045: feed_reactions.story_id,
#: entity_link_proposals.story_id and tech_credit_pushes.story_id are ON
#: DELETE RESTRICT, so a delete that reached one would fail the whole prune;
#: every story delete here excludes them first).
FEED_PRUNE_SQL: Final = {
    "embeddings_cleared": """
        UPDATE content_items SET embedding = NULL, title_embedding = NULL
         WHERE (embedding IS NOT NULL OR title_embedding IS NOT NULL)
           AND fetched_at < now() - interval '72 hours'
    """,
    "filtered_stories_deleted": """
        DELETE FROM stories st
         WHERE st.filtered_reason IS NOT NULL AND st.last_seen_at < now() - interval '30 days'
           AND NOT EXISTS (SELECT 1 FROM feed_reactions r WHERE r.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM entity_link_proposals p WHERE p.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM tech_credit_pushes t WHERE t.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM repo_surfacings rs WHERE rs.story_id = st.id)
    """,
    # Kept stories, 90 days after they were last seen, unless reacted to or
    # named by a move proposal, a push or a repo surfacing (db/049: the
    # surfacing record is append-only and refuses the delete).
    "unreacted_stories_deleted": """
        DELETE FROM stories st
         WHERE st.filtered_reason IS NULL AND st.last_seen_at < now() - interval '90 days'
           AND NOT EXISTS (SELECT 1 FROM feed_reactions r WHERE r.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM entity_link_proposals p WHERE p.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM tech_credit_pushes t WHERE t.story_id = st.id)
           AND NOT EXISTS (SELECT 1 FROM repo_surfacings rs WHERE rs.story_id = st.id)
    """,
    # Superseded scores (a newer row exists for the story and pillar) after 7 days.
    "story_scores_superseded_deleted": """
        DELETE FROM story_scores s
         WHERE s.scored_at < now() - interval '7 days'
           AND EXISTS (SELECT 1 FROM story_scores n
                        WHERE n.story_id = s.story_id AND n.pillar = s.pillar
                          AND (n.scored_at, n.id) > (s.scored_at, s.id))
    """,
    # Old profiles after 90 days; the latest per pillar always stays.
    "interest_profiles_deleted": """
        DELETE FROM interest_profiles p
         WHERE p.built_at < now() - interval '90 days'
           AND EXISTS (SELECT 1 FROM interest_profiles n
                        WHERE n.pillar = p.pillar AND (n.built_at, n.id) > (p.built_at, p.id))
    """,
    "seeded_items_deleted": """
        DELETE FROM content_items
         WHERE seeded AND fetched_at < now() - interval '30 days'
    """,
    "similarity_log_deleted": """
        DELETE FROM feed_similarity_log WHERE created_at < now() - interval '30 days'
    """,
}


def feed_prune(*, conn: psycopg.Connection | None = None) -> dict[str, int]:
    with _brief_conn(conn) as c, c.transaction():
        return {name: c.execute(sql).rowcount for name, sql in FEED_PRUNE_SQL.items()}


# ---------------------------------------------------------------------------
# Feed ranking (db/039; feed/rank.py, feed/profile.py, feed/why.py). The
# worker writes profiles and scores; the owner's reactions come from the web.
# Test stories never enter a profile or a score (every read filters is_test).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FollowRow:
    name: str
    tier: int
    pillar: str | None
    floor: bool
    aliases: tuple[str, ...]
    sport: str | None
    sport_wide: bool
    kind: str | None = None


#: Every follow, plus (owner D3, db/042) a DERIVED tier-2 Sports follow for
#: the current team of each tier-1 person (v_entity_links_current): "his team"
#: moves with him when a move is confirmed. Test entities never appear.
FEED_FOLLOWS_SQL: Final = """
    SELECT e.name, f.tier, f.pillar, f.floor, e.aliases, e.sport, e.sport_wide, e.kind
      FROM follows f JOIN entities e ON e.id = f.entity_id
     WHERE NOT e.is_test
    UNION
    SELECT t.name, 2, 'sports', false, t.aliases, t.sport, t.sport_wide, t.kind
      FROM follows f
      JOIN entities p ON p.id = f.entity_id AND p.kind = 'person' AND NOT p.is_test
      JOIN v_entity_links_current l ON l.from_entity_id = p.id
      JOIN entities t ON t.id = l.to_entity_id AND NOT t.is_test
     WHERE f.tier = 1
     ORDER BY 2, 1, 3 NULLS FIRST
"""


def feed_follows(*, conn: psycopg.Connection | None = None) -> list[FollowRow]:
    with _brief_conn(conn) as c:
        rows = c.execute(FEED_FOLLOWS_SQL).fetchall()
    return [FollowRow(r[0], int(r[1]), r[2], r[3], tuple(r[4]), r[5], r[6], r[7]) for r in rows]


# ---------------------------------------------------------------- move proposals
# db/042. The summarizer's `moves` become proposals (feed/moves.py) only when
# both entities resolve; the owner decides on /settings/feed.

@dataclass(frozen=True)
class MoveEntity:
    id: int
    kind: str
    name: str
    aliases: tuple[str, ...]
    sport: str | None
    followed: bool


@dataclass(frozen=True)
class CurrentLink:
    from_id: int
    to_id: int
    kind: str


@dataclass(frozen=True)
class MoveContext:
    entities: tuple[MoveEntity, ...]
    links: tuple[CurrentLink, ...]
    #: People with an open proposal (at most one each, db/042).
    open_persons: frozenset[int]
    #: (person, kind, to) dismissed within DISMISS_QUIET_D days (owner D5).
    recent_dismissals: frozenset[tuple[int, str, int | None]]


def feed_move_context(quiet_days: int, *, conn: psycopg.Connection | None = None) -> MoveContext:
    """Real entities only: a test entity is never a move's person or target."""
    with _brief_conn(conn) as c:
        ents = c.execute("""
            SELECT e.id, e.kind, e.name, e.aliases, e.sport,
                   EXISTS (SELECT 1 FROM follows f WHERE f.entity_id = e.id)
              FROM entities e WHERE NOT e.is_test
        """).fetchall()
        links = c.execute("""
            SELECT l.from_entity_id, l.to_entity_id, l.kind FROM v_entity_links_current l
              JOIN entities e ON e.id = l.from_entity_id WHERE NOT e.is_test
        """).fetchall()
        opened = c.execute(
            "SELECT person_entity_id FROM entity_link_proposals WHERE decision IS NULL AND NOT is_test").fetchall()
        dismissed = c.execute("""
            SELECT person_entity_id, kind, to_entity_id FROM entity_link_proposals
             WHERE decision = 'dismissed' AND NOT is_test
               AND decided_at > now() - make_interval(days => %s)
        """, (quiet_days,)).fetchall()
    return MoveContext(
        tuple(MoveEntity(int(r[0]), r[1], r[2], tuple(r[3]), r[4], bool(r[5])) for r in ents),
        tuple(CurrentLink(int(r[0]), int(r[1]), r[2]) for r in links),
        frozenset(int(r[0]) for r in opened),
        frozenset((int(r[0]), r[1], int(r[2]) if r[2] is not None else None) for r in dismissed),
    )


@dataclass(frozen=True)
class ProposalIn:
    person_id: int
    kind: str
    link_kind: str
    from_id: int | None
    to_id: int | None
    story_id: int


def feed_insert_proposal(p: ProposalIn, *, conn: psycopg.Connection | None = None) -> int | None:
    """The new proposal's id, or None when the database refused it as a
    duplicate open proposal or a stale `from` (the world changed between the
    read and the write). Anything else raises."""
    with _brief_conn(conn) as c:
        try:
            with c.transaction():
                row = c.execute("""
                    INSERT INTO entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id,
                                                       to_entity_id, story_id)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """, (p.person_id, p.kind, p.link_kind, p.from_id, p.to_id, p.story_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            return None
        except psycopg.errors.RaiseException as exc:
            if "is not the current" in str(exc):
                return None
            raise
    return int(row[0])


def feed_add_test_move(suffix: str, *, conn: psycopg.Connection | None = None) -> int:
    """scripts/feed_test_proposal.py: "Test Player <s>" plays_for "Test Team
    A <s>", and an open trade to "Test Team B <s>" (all is_test), for the
    owner's phone check of Confirm. The id of the proposal."""
    s = suffix.lower()
    with _brief_conn(conn) as c, c.transaction():
        ids = []
        for kind, name in (("person", f"Test Player {s}"), ("team", f"Test Team A {s}"), ("team", f"Test Team B {s}")):
            ids.append(int(c.execute(
                "INSERT INTO entities (kind, name, aliases, sport, is_test) VALUES (%s, %s, %s, 'nba', true) RETURNING id",
                (kind, name, [name.lower()])).fetchone()[0]))
        person, a, b = ids
        c.execute("""
            INSERT INTO entity_links (from_entity_id, to_entity_id, kind, valid_from, confirmed_by_owner)
            VALUES (%s, %s, 'plays_for', owner_today() - 1, true)
        """, (person, a))
        return int(c.execute("""
            INSERT INTO entity_link_proposals (person_entity_id, kind, link_kind, from_entity_id, to_entity_id, is_test)
            VALUES (%s, 'trade', 'plays_for', %s, %s, true) RETURNING id
        """, (person, a, b)).fetchone()[0])


def feed_seed_phrases(*, conn: psycopg.Connection | None = None) -> dict[str, dict[str, list[str]]]:
    """app_settings.seed_interests -> 'feed' (db/039): {pillar: {'love': [...],
    'not_interested': [...]}}. Empty when unset."""
    with _brief_conn(conn) as c:
        row = c.execute("SELECT seed_interests -> 'feed' FROM app_settings LIMIT 1").fetchone()
    feed = (row[0] if row else None) or {}
    return {p: {k: [str(x) for x in (v or {}).get(k, []) if str(x).strip()]
                for k in ("love", "not_interested")}
            for p, v in feed.items() if isinstance(v, dict)}


#: The mean story vector for a pillar: kept real stories first seen in the
#: window. Averaged in the database (pgvector's avg over vector), so seven
#: days of vectors never travel to the worker.
FEED_MEAN_SQL: Final = """
    SELECT extensions.avg(st.centroid::extensions.vector)::extensions.halfvec, count(*)
      FROM stories st
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.centroid IS NOT NULL
       AND %(pillar)s = ANY (st.pillars)
       AND st.first_seen_at > now() - make_interval(days => %(days)s)
"""


def feed_mean_vector(pillar: str, days: int, *,
                     conn: psycopg.Connection | None = None) -> tuple[tuple[float, ...] | None, int]:
    with _brief_conn(conn) as c:
        _binary_vectors(c)
        text, n = c.execute(FEED_MEAN_SQL, {"pillar": pillar, "days": days}, binary=True).fetchone()
    return _vec_parse(text), int(n)


@dataclass(frozen=True)
class RankStory:
    id: int
    pillars: tuple[str, ...]
    sport: str | None
    title: str
    entities: tuple[str, ...]
    topics: tuple[str, ...]
    source_slug: str | None
    #: stories.centroid as float32 (array 'f'; empty when NULL). The rebuild
    #: loads every kept story of the window: 1,105 centroids took 13.0 MB as
    #: tuples of Python floats (memory audit, 2026-10-13), about 1.7 MB as this.
    vec: Sequence[float]


def _rank_story(r: Sequence[Any]) -> RankStory:
    return RankStory(int(r[0]), tuple(r[1]), r[2], r[3], tuple(r[4] or ()), tuple(r[5] or ()), r[6],
                     _vec_array(r[7]) or array("f"))


_RANK_STORY_COLS = "st.id, st.pillars, st.sport, st.title, st.entities, st.topics, s.slug, st.centroid"


@dataclass(frozen=True)
class ReactionSignal:
    story: RankStory
    reaction: str | None
    opened: bool


#: Every real story with a reaction in this pillar, with its latest reaction
#: (v_feed_reaction_latest) and whether it was opened.
FEED_SIGNALS_SQL: Final = f"""
    SELECT {_RANK_STORY_COLS}, r.reaction, r.opened
      FROM v_feed_reaction_latest r
      JOIN stories st ON st.id = r.story_id
      LEFT JOIN content_items i ON i.id = st.lead_item_id
      LEFT JOIN sources s ON s.id = i.source_id
     WHERE r.pillar = %(pillar)s AND NOT r.is_test AND st.centroid IS NOT NULL
     ORDER BY st.id
"""


def feed_reaction_signals(pillar: str, *, conn: psycopg.Connection | None = None) -> list[ReactionSignal]:
    """Streamed row by row (no fetchall), vectors straight to float32."""
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute(FEED_SIGNALS_SQL, {"pillar": pillar}, binary=True)
        return [ReactionSignal(_rank_story(r[:8]), r[8], bool(r[9])) for r in cur]


#: Stories to score: kept, real, linkable, first seen in the window, with a
#: vector, and done with summarizing (summarized, or out of attempts), so
#: their entities and topics are final. `unscored` keeps only stories with no
#: score row at all (new stories between rebuilds).
FEED_TO_SCORE_SQL: Final = f"""
    SELECT {_RANK_STORY_COLS}
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE NOT st.is_test AND st.filtered_reason IS NULL AND st.centroid IS NOT NULL
       AND i.url IS NOT NULL
       AND st.first_seen_at > now() - make_interval(hours => %(hours)s)
       AND (st.summarized_at IS NOT NULL OR st.summary_attempts >= %(max_attempts)s)
       AND (NOT %(unscored)s
            OR NOT EXISTS (SELECT 1 FROM story_scores x WHERE x.story_id = st.id)
            -- A story that GAINED a pillar (an item from a new pillar joined):
            -- one of its scorable pillars has no native score yet, so its
            -- cross-in flag there is stale, or it is missing from that
            -- pillar altogether (owner, 2026-10-15; story 15462).
            OR EXISTS (SELECT 1 FROM unnest(st.pillars) AS p
                        WHERE p = ANY (%(pillars)s::text[])
                          AND NOT EXISTS (SELECT 1 FROM v_story_scores_latest sc
                                           WHERE sc.story_id = st.id AND sc.pillar = p
                                             AND NOT sc.cross_pillar)))
     ORDER BY st.id
"""


def feed_stories_to_score(hours: int, max_attempts: int, *, unscored: bool, pillars: Sequence[str] = (),
                          conn: psycopg.Connection | None = None) -> list[RankStory]:
    """Streamed row by row (no fetchall), vectors straight to float32.
    `unscored`: only stories with no score yet, or with a pillar among
    `pillars` (those with a profile) that has no native score: a story that
    gained a pillar since it was scored."""
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute(FEED_TO_SCORE_SQL, {"hours": hours, "max_attempts": max_attempts, "unscored": unscored,
                                        "pillars": list(pillars)}, binary=True)
        return [_rank_story(r) for r in cur]


@dataclass(frozen=True)
class ProfileRow:
    id: int
    pillar: str
    mean: tuple[float, ...]
    mean_n: int
    love: tuple[float, ...] | None
    ni: tuple[float, ...] | None
    topic_weights: dict[str, float]
    source_weights: dict[str, float]
    entity_weights: dict[str, float]
    love_count: int
    ni_count: int
    open_count: int
    neutral_count: int
    seed_weight_love: float
    seed_weight_ni: float
    built_at: datetime


def feed_insert_profile(p: Any, *, conn: psycopg.Connection | None = None) -> int:
    """`p` is a feed.rank.Profile. Returns the new interest_profiles id."""
    with _brief_conn(conn) as c:
        return int(c.execute(
            """
            INSERT INTO interest_profiles
                (pillar, mean_vec, love_vec, ni_vec, topic_weights, source_weights, entity_weights,
                 reaction_count, love_count, ni_count, open_count, neutral_count,
                 seed_weight_love, seed_weight_ni, mean_n)
            VALUES (%s, %s::extensions.halfvec, %s::extensions.halfvec, %s::extensions.halfvec,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (p.pillar, _vec_text(p.mean), _vec_text(p.love) if p.love is not None else None,
             _vec_text(p.ni) if p.ni is not None else None, Jsonb(dict(p.topic_weights)),
             Jsonb(dict(p.source_weights)), Jsonb(dict(p.entity_weights)), p.reaction_count,
             p.love_count, p.ni_count, p.open_count, p.neutral_count, p.seed_weight_love,
             p.seed_weight_ni, p.mean_n),
        ).fetchone()[0])


def feed_latest_profiles(*, conn: psycopg.Connection | None = None) -> dict[str, ProfileRow]:
    with _brief_conn(conn) as c:
        _binary_vectors(c)
        rows = c.execute("""
            SELECT DISTINCT ON (pillar)
                   id, pillar, mean_vec, mean_n, love_vec, ni_vec, topic_weights,
                   source_weights, entity_weights, love_count, ni_count, open_count, neutral_count,
                   seed_weight_love, seed_weight_ni, built_at
              FROM interest_profiles
             ORDER BY pillar, built_at DESC, id DESC
        """, binary=True).fetchall()
    return {r[1]: ProfileRow(int(r[0]), r[1], _vec_parse(r[2]) or (), int(r[3]), _vec_parse(r[4]),
                             _vec_parse(r[5]), dict(r[6]), dict(r[7]), dict(r[8]), r[9], r[10], r[11],
                             r[12], float(r[13]), float(r[14]), r[15])
            for r in rows}


def feed_profile_built_today(*, conn: psycopg.Connection | None = None) -> bool:
    """Any profile built since the owner's local midnight (database clock)."""
    with _brief_conn(conn) as c:
        return bool(c.execute(
            f"SELECT EXISTS (SELECT 1 FROM interest_profiles WHERE built_at >= {_LOCAL_DAY_START})"
        ).fetchone()[0])


def feed_insert_scores(scores: Sequence[Any], profile_ids: Mapping[str, int], *,
                       conn: psycopg.Connection | None = None) -> int:
    """`scores` are feed.rank.Score; each row names the profile of its pillar.
    One transaction: a rescore lands whole or not at all."""
    if not scores:
        return 0
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO story_scores (story_id, pillar, score, components, follow_tier, floor,
                                      cross_pillar, profile_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(s.story_id, s.pillar, s.score, Jsonb(dict(s.components)), s.follow_tier, s.floor,
              s.cross_pillar, profile_ids.get(s.pillar)) for s in scores],
        )
    return len(scores)


@dataclass(frozen=True)
class ForYouRow:
    id: int
    pos: int
    rank: float
    floored: bool
    cross_pillar: bool
    title: str
    source_name: str
    summary: str | None
    why_it_matters: str | None


def feed_for_you(pillar: str, limit: int, offset: int = 0, *,
                 conn: psycopg.Connection | None = None) -> list[ForYouRow]:
    """db/039 feed_for_you(): the For you order, as the owner sees it."""
    with _brief_conn(conn) as c:
        rows = c.execute(
            """
            SELECT id, pos, rank, coalesce(floored, false), cross_pillar, title, source_name,
                   summary, why_it_matters
              FROM feed_for_you(%s, %s, %s)
            """, (pillar, limit, offset)).fetchall()
    return [ForYouRow(int(r[0]), int(r[1]), float(r[2]), r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows]


@dataclass(frozen=True)
class WhyJob:
    story_id: int
    title: str
    summary: str | None
    source_name: str


#: Home's and Tech's top `top` stories in For you that were never asked "why
#: it matters" (Tech since db/045: the why-it-matters engine is shared). In
#: rank order across the two (owner, 2026-10-14): position 1 of each, then
#: position 2, Home before Tech at a tie, so page one is asked first. It was
#: Home's whole list, then Tech's. Repo stories are asked by their own job
#: (db/049; FEED_REPO_WHY_QUEUE_SQL, feed/why.py repo_why_job).
FEED_WHY_QUEUE_SQL: Final = """
    SELECT x.id, x.title, x.summary, x.source_name
      FROM (SELECT f.id, f.title, f.summary, f.source_name, 0 AS feed, f.pos
              FROM feed_for_you('home', %(top)s, 0) f
            UNION ALL
            SELECT f.id, f.title, f.summary, f.source_name, 1 AS feed, f.pos
              FROM feed_for_you('tech', %(top)s, 0) f) x
      JOIN stories st ON st.id = x.id
     WHERE st.why_generated_at IS NULL AND st.category IS DISTINCT FROM 'repos'
     ORDER BY x.pos, x.feed
"""


def feed_why_queue(top: int, *, conn: psycopg.Connection | None = None) -> list[WhyJob]:
    with _brief_conn(conn) as c:
        rows = c.execute(FEED_WHY_QUEUE_SQL, {"top": top}).fetchall()
    return [WhyJob(int(r[0]), r[1], r[2], r[3]) for r in rows]


def feed_why_jobs(story_ids: Sequence[int], *, conn: psycopg.Connection | None = None) -> list[WhyJob]:
    """Named real stories as why-jobs (scripts/feed_profile.py --why --again)."""
    with _brief_conn(conn) as c:
        rows = c.execute("""
            SELECT v.id, v.title, v.summary, v.source_name FROM v_feed_stories v
             WHERE v.id = ANY(%s) ORDER BY v.id
        """, (list(story_ids),)).fetchall()
    return [WhyJob(int(r[0]), r[1], r[2], r[3]) for r in rows]


def feed_reset_why(story_ids: Sequence[int], *, conn: psycopg.Connection | None = None) -> int:
    """Forget that named stories were asked, so they can be asked again (by
    hand only: the owner's re-check after a context change). Returns rows."""
    with _brief_conn(conn) as c, c.transaction():
        return c.execute("""
            UPDATE stories SET why_it_matters = NULL, why_generated_at = NULL, why_ai_usage_id = NULL
             WHERE id = ANY(%s) AND NOT is_test
        """, (list(story_ids),)).rowcount


def ai_calls_today(purpose: str, *, conn: psycopg.Connection | None = None) -> int:
    """ai_usage rows for `purpose` since the owner's local midnight."""
    with _brief_conn(conn) as c:
        return int(c.execute(
            f"SELECT count(*) FROM ai_usage WHERE purpose = %s AND created_at >= {_LOCAL_DAY_START}",
            (purpose,)).fetchone()[0])


def ctx_ventures(*, conn: psycopg.Connection | None = None) -> dict[str, Any]:
    """db/029 ctx_ventures(): the same venture context Wags and the brief read."""
    with _brief_conn(conn) as c:
        return c.execute("SELECT ctx_ventures()").fetchone()[0] or {}


def feed_save_why(answers: Mapping[int, str | None], ai_usage_id: int | None, *,
                  conn: psycopg.Connection | None = None) -> int:
    """Record that each story was asked (why_generated_at), with its line or
    NULL. A story already asked is left as it is. Returns rows written."""
    if not answers:
        return 0
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        written = 0
        for story_id, line in answers.items():
            cur.execute(
                """
                UPDATE stories SET why_it_matters = %s, why_generated_at = now(), why_ai_usage_id = %s
                 WHERE id = %s AND why_generated_at IS NULL
                """, (line, ai_usage_id, story_id))
            written += cur.rowcount
    return written


# ---------------------------------------------------------------------------
# GitHub repos in Tech Hub (db/049; feed/repos.py). `repos` is an app table
# the worker updates; repo_star_snapshots and repo_surfacings are append-only
# and their days are the database's (owner_today()). The 8-a-day cap and the
# resurface rule are repo_surfacings' trigger: repo_surface() asks the rule
# first and, if the trigger still refuses, rolls the whole pick back.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepoIn:
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    topics: tuple[str, ...]
    license: str | None
    archived: bool
    fork: bool
    gh_created_at: datetime | None
    pushed_at: datetime | None
    stars: int


def owner_today(*, conn: psycopg.Connection | None = None) -> date:
    with _brief_conn(conn) as c:
        return c.execute("SELECT public.owner_today()").fetchone()[0]


#: This job's own success today, in the owner's timezone (a JobSkipped
#: counts: it means the run already happened).
JOB_OK_TODAY_SQL: Final = f"""
    SELECT coalesce((SELECT last_ok_at >= {_LOCAL_DAY_START} FROM job_health WHERE job = %s), false)
"""


def job_ok_today(job: str, *, conn: psycopg.Connection | None = None) -> bool:
    with _brief_conn(conn) as c:
        return bool(c.execute(JOB_OK_TODAY_SQL, (job,)).fetchone()[0])


def repos_upsert(rows: Sequence[RepoIn], *, conn: psycopg.Connection | None = None) -> dict[str, int]:
    """Insert or refresh each repo (real rows only) by lower(full_name).
    Returns {lower(full_name): id}."""
    out: dict[str, int] = {}
    if not rows:
        return out
    with _brief_conn(conn) as c, c.transaction():
        for r in rows:
            rid = c.execute(
                """
                INSERT INTO repos (full_name, html_url, description, language, topics, license, archived, fork,
                                   gh_created_at, pushed_at, stars, last_checked_at)
                VALUES (%(full_name)s, %(html_url)s, left(%(description)s, 1000), left(%(language)s, 60),
                        %(topics)s, left(%(license)s, 100), %(archived)s, %(fork)s, %(created)s, %(pushed)s,
                        %(stars)s, now())
                ON CONFLICT (lower(full_name)) DO UPDATE SET
                    html_url = EXCLUDED.html_url, description = coalesce(EXCLUDED.description, repos.description),
                    language = coalesce(EXCLUDED.language, repos.language), topics = EXCLUDED.topics,
                    license = EXCLUDED.license, archived = EXCLUDED.archived, fork = EXCLUDED.fork,
                    gh_created_at = coalesce(EXCLUDED.gh_created_at, repos.gh_created_at),
                    pushed_at = coalesce(EXCLUDED.pushed_at, repos.pushed_at), stars = EXCLUDED.stars,
                    last_checked_at = now()
                 WHERE NOT repos.is_test
                RETURNING id
                """,
                {"full_name": r.full_name, "html_url": r.html_url, "description": r.description,
                 "language": r.language, "topics": list(r.topics)[:30], "license": r.license,
                 "archived": r.archived, "fork": r.fork, "created": r.gh_created_at, "pushed": r.pushed_at,
                 "stars": r.stars},
            ).fetchone()
            if rid is None:
                raise LedgerError(f"repo {r.full_name!r} collides with a test repo")
            out[r.full_name.lower()] = int(rid[0])
    return out


def repo_snapshot(pairs: Sequence[tuple[int, int]], *, conn: psycopg.Connection | None = None) -> int:
    """Today's star count per repo (repo id, stars); the day is the
    database's. A repo already snapshotted today is left as it is. Returns
    rows written."""
    written = 0
    if not pairs:
        return written
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        for rid, stars in pairs:
            cur.execute("INSERT INTO repo_star_snapshots (repo_id, stars) VALUES (%s, %s) "
                        "ON CONFLICT (repo_id, snapped_on) DO NOTHING", (rid, max(int(stars), 0)))
            written += cur.rowcount
    return written


#: Real repos to keep snapshotting though they were not candidates today:
#: first seen in the last `days`, or surfaced in the last 60 days (their
#: stars_7d decides whether they may surface again). Oldest checked first.
REPOS_TO_TRACK_SQL: Final = """
    SELECT r.id, r.full_name FROM repos r
     WHERE NOT r.is_test AND NOT (r.id = ANY (%(exclude)s))
       AND (r.first_seen_at > now() - make_interval(days => %(days)s)
            OR EXISTS (SELECT 1 FROM repo_surfacings s
                        WHERE s.repo_id = r.id AND s.surfaced_on > owner_today() - 60))
       AND NOT EXISTS (SELECT 1 FROM repo_star_snapshots x WHERE x.repo_id = r.id AND x.snapped_on = owner_today())
     ORDER BY r.last_checked_at, r.id
     LIMIT %(limit)s
"""


def repos_to_track(days: int, limit: int, *, exclude: Sequence[int] = (),
                   conn: psycopg.Connection | None = None) -> list[tuple[int, str]]:
    with _brief_conn(conn) as c:
        return [(int(r[0]), r[1]) for r in c.execute(
            REPOS_TO_TRACK_SQL, {"days": days, "limit": limit, "exclude": list(exclude)}).fetchall()]


#: stars_7d MEASURED: today's snapshot minus the one exactly 7 days ago. A
#: repo without both has no row (its stars_7d is an estimate, feed/repos.py).
REPO_STARS_7D_SQL: Final = """
    SELECT t.repo_id, t.stars - w.stars
      FROM repo_star_snapshots t
      JOIN repo_star_snapshots w ON w.repo_id = t.repo_id AND w.snapped_on = t.snapped_on - 7
     WHERE t.repo_id = ANY (%(ids)s) AND t.snapped_on = owner_today()
"""


def repo_stars_7d_measured(repo_ids: Sequence[int], *, conn: psycopg.Connection | None = None) -> dict[int, int]:
    if not repo_ids:
        return {}
    with _brief_conn(conn) as c:
        return {int(r[0]): int(r[1]) for r in c.execute(REPO_STARS_7D_SQL, {"ids": list(repo_ids)}).fetchall()}


def repos_surfaced_today(*, conn: psycopg.Connection | None = None) -> int:
    with _brief_conn(conn) as c:
        return int(c.execute("SELECT count(*) FROM repo_surfacings "
                             "WHERE surfaced_on = owner_today() AND NOT is_test").fetchone()[0])


def repo_may_surface(repo_id: int, stars_7d: int | None, *, conn: psycopg.Connection | None = None) -> bool:
    """db/049 repo_may_surface for today: the resurface rule, asked first."""
    with _brief_conn(conn) as c:
        return bool(c.execute("SELECT repo_may_surface(%s, %s, owner_today())", (repo_id, stars_7d)).fetchone()[0])


@dataclass(frozen=True)
class RepoSurfaceIn:
    full_name: str
    html_url: str
    teaser: str | None
    #: snake_case GitHub topics: the story's topics, which train the profile.
    topics: tuple[str, ...]
    vec: Sequence[float]
    day: date
    stars: int
    stars_7d: int | None
    stars_7d_source: str | None


def repo_surface(source: FeedSource, repo_id: int, s: RepoSurfaceIn, *,
                 conn: psycopg.Connection | None = None) -> int | None:
    """One repo becomes a tech story in category repos: the story, its one
    item (published the day it surfaced, precision 'date'), the surfacing
    row and the repo's latest values, in ONE transaction. Returns the story
    id, or None when the database refused (the cap of 8, the resurface
    rule) and nothing was written. A repo URL another item already holds
    (a Hacker News link to it) gets a #surfaced-<day> fragment."""
    vec = _vec_text(s.vec)
    published = datetime(s.day.year, s.day.month, s.day.day, tzinfo=timezone.utc)
    try:
        with _brief_conn(conn) as c, c.transaction():
            story_id = c.execute(
                """
                INSERT INTO stories (pillars, title, centroid, category, topics, is_test)
                VALUES (ARRAY['tech'], %s, %s::extensions.halfvec, 'repos', %s, %s)
                RETURNING id
                """, (s.full_name, vec, list(s.topics), source.is_test)).fetchone()[0]
            taken = c.execute("SELECT EXISTS (SELECT 1 FROM content_items WHERE url = %s)", (s.html_url,)).fetchone()[0]
            url = f"{s.html_url}#surfaced-{s.day.isoformat()}" if taken else s.html_url
            item_id = c.execute(
                """
                INSERT INTO content_items (source_id, external_id, url, title, teaser, published_at, fetched_at,
                                           story_id, embedding, title_embedding, is_test, published_precision)
                VALUES (%s, %s, %s, %s, left(%s, 500), LEAST(%s, now()), now(), %s, %s::extensions.halfvec,
                        %s::extensions.halfvec, %s, 'date')
                RETURNING id
                """, (source.id, f"{s.full_name.lower()}@{s.day.isoformat()}", url, s.full_name, s.teaser,
                      published, story_id, vec, vec, source.is_test)).fetchone()[0]
            c.execute("UPDATE stories SET lead_item_id = %s WHERE id = %s", (item_id, story_id))
            c.execute(
                """
                INSERT INTO repo_surfacings (repo_id, story_id, stars, stars_7d, stars_7d_source, is_test)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (repo_id, story_id, s.stars, s.stars_7d, s.stars_7d_source if s.stars_7d is not None else None,
                      source.is_test))
            c.execute("UPDATE repos SET story_id = %s, stars_7d = %s, stars_7d_source = %s WHERE id = %s",
                      (story_id, s.stars_7d, s.stars_7d_source if s.stars_7d is not None else None, repo_id))
    except psycopg.errors.RaiseException as exc:
        if "repo_surfacings:" in str(exc):
            return None
        raise
    return int(story_id)


#: Repo stories waiting for their why-it-matters line (feed/why.py
#: repo_why_job): real, kept, never asked, and summarized (or out of
#: summary attempts, or an hour old, so a stuck summary never holds it).
FEED_REPO_WHY_QUEUE_SQL: Final = """
    SELECT st.id, st.title, st.summary, s.name, r.description, r.topics
      FROM stories st
      JOIN repo_surfacings rs ON rs.story_id = st.id AND NOT rs.is_test
      JOIN repos r ON r.id = rs.repo_id
      JOIN content_items i ON i.id = st.lead_item_id
      JOIN sources s ON s.id = i.source_id
     WHERE st.category = 'repos' AND NOT st.is_test AND st.filtered_reason IS NULL
       AND st.why_generated_at IS NULL
       AND (st.summary IS NOT NULL OR st.summary_attempts >= %(max_attempts)s
            OR st.first_seen_at < now() - interval '1 hour')
     ORDER BY st.first_seen_at, st.id
     LIMIT %(limit)s
"""


@dataclass(frozen=True)
class RepoWhyJob:
    story_id: int
    full_name: str
    summary: str | None
    source_name: str
    description: str | None
    topics: tuple[str, ...]


def feed_repo_why_queue(limit: int, max_attempts: int, *,
                        conn: psycopg.Connection | None = None) -> list[RepoWhyJob]:
    with _brief_conn(conn) as c:
        rows = c.execute(FEED_REPO_WHY_QUEUE_SQL, {"limit": limit, "max_attempts": max_attempts}).fetchall()
    return [RepoWhyJob(int(r[0]), r[1], r[2], r[3], r[4], tuple(r[5] or ())) for r in rows]


def feed_reset_repo_why(story_ids: Sequence[int], *, conn: psycopg.Connection | None = None) -> int:
    """By hand only (feed/why.py repo_ask_again): forget that these real repo
    stories were asked, on the story and its repo, so they are asked again.
    Returns stories reset."""
    with _brief_conn(conn) as c, c.transaction():
        n = c.execute("""
            UPDATE stories SET why_it_matters = NULL, why_generated_at = NULL, why_ai_usage_id = NULL
             WHERE id = ANY(%s) AND NOT is_test AND category = 'repos'
        """, (list(story_ids),)).rowcount
        c.execute("UPDATE repos SET why_it_matters = NULL WHERE story_id = ANY(%s) AND NOT is_test",
                  (list(story_ids),))
    return n


def feed_save_repo_why(answers: Mapping[int, str], ai_usage_id: int | None, *,
                       conn: psycopg.Connection | None = None) -> int:
    """A repo story's line (never NULL: "General interest." when there is no
    concrete use), on the story and on its repo. Returns stories written."""
    if not answers:
        return 0
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        written = 0
        for story_id, line in answers.items():
            cur.execute(
                """
                UPDATE stories SET why_it_matters = %s, why_generated_at = now(), why_ai_usage_id = %s
                 WHERE id = %s AND why_generated_at IS NULL
                """, (line, ai_usage_id, story_id))
            if cur.rowcount:
                written += 1
                cur.execute("UPDATE repos SET why_it_matters = %s WHERE story_id = %s", (line, story_id))
    return written


# ---------------------------------------------------------------------------
# The Screening Room (db/051; screening/). `titles` is an app table both the
# web (owner session) and the worker write; `title_trailers` is the worker's;
# watch_status is append-only and the web's (the worker only reads it).
# ---------------------------------------------------------------------------

def titles_upsert(t: Any, *, conn: psycopg.Connection | None = None) -> int:
    """One REAL title (screening/tmdb.py TitleIn) by tmdb_id + kind: inserted,
    or refreshed in place (every TMDB field, providers_refreshed_at,
    refreshed_at). Returns its id."""
    tonight = sorted(set(getattr(t, "tonight", ()) or ()))
    keywords = list(getattr(t, "keywords", ()) or ())[:30]
    vote_average = getattr(t, "vote_average", None)
    vote_count = getattr(t, "vote_count", None)
    popularity = getattr(t, "popularity", None)
    language = getattr(t, "original_language", None)
    with _brief_conn(conn) as c, c.transaction():
        row = c.execute(
            """
            INSERT INTO titles (tmdb_id, kind, name, release_date, poster_path, overview, genres, providers,
                                providers_refreshed_at, trailer_key, next_episode_date, status, refreshed_at,
                                keywords, vote_average, vote_count, popularity, original_language,
                                last_episode_date, tonight)
            VALUES (%(tmdb_id)s, %(kind)s, %(name)s, %(release)s, %(poster)s, %(overview)s, %(genres)s, %(providers)s,
                    now(), %(trailer)s, %(next)s, %(status)s, now(), %(keywords)s, %(vote_average)s, %(vote_count)s,
                    %(popularity)s, %(language)s, %(last)s, %(tonight)s)
            ON CONFLICT (tmdb_id, kind) DO UPDATE SET
                name = EXCLUDED.name, release_date = EXCLUDED.release_date, poster_path = EXCLUDED.poster_path,
                overview = EXCLUDED.overview, genres = EXCLUDED.genres, providers = EXCLUDED.providers,
                providers_refreshed_at = now(), trailer_key = coalesce(EXCLUDED.trailer_key, titles.trailer_key),
                next_episode_date = EXCLUDED.next_episode_date, status = EXCLUDED.status, refreshed_at = now(),
                keywords = EXCLUDED.keywords, vote_average = EXCLUDED.vote_average,
                vote_count = EXCLUDED.vote_count, popularity = EXCLUDED.popularity,
                original_language = EXCLUDED.original_language, last_episode_date = EXCLUDED.last_episode_date,
                tonight = EXCLUDED.tonight
             WHERE NOT titles.is_test
            RETURNING id
            """,
            {"tmdb_id": t.tmdb_id, "kind": t.kind, "name": t.name, "release": t.release_date,
             "poster": t.poster_path if t.poster_path and re.fullmatch(r"/[A-Za-z0-9_.-]+", t.poster_path) else None,
             "overview": t.overview, "genres": list(t.genres), "providers": Jsonb(t.providers),
             "trailer": t.trailer_key if t.trailer_key and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", t.trailer_key) else None,
             "next": t.next_episode_date if t.kind == "tv" else None, "status": t.status,
             "keywords": keywords,
             "vote_average": min(10.0, max(0.0, float(vote_average))) if vote_average is not None else None,
             "vote_count": max(0, int(vote_count)) if vote_count is not None else None,
             "popularity": max(0.0, float(popularity)) if popularity is not None else None,
             "language": language[:8] if language else None,
             "last": getattr(t, "last_episode_date", None) if t.kind == "tv" else None, "tonight": tonight},
        ).fetchone()
    if row is None:
        raise LedgerError(f"title {t.kind}/{t.tmdb_id} collides with a test title")
    return int(row[0])


@dataclass(frozen=True)
class TrailerItem:
    item_id: int
    title: str
    published_at: datetime


#: Real, kept screening stories whose lead item has no title_trailers row yet
#: (newest first): the trailers the match job still has to look up.
SCREENING_UNMATCHED_SQL: Final = """
    SELECT i.id, i.title, i.published_at
      FROM stories st
      JOIN content_items i ON i.id = st.lead_item_id
     WHERE 'screening' = ANY (st.pillars) AND NOT st.is_test AND st.filtered_reason IS NULL
       AND NOT EXISTS (SELECT 1 FROM title_trailers tt WHERE tt.item_id = i.id)
     ORDER BY i.fetched_at DESC, i.id DESC
     LIMIT %(limit)s
"""


def screening_unmatched(limit: int, *, conn: psycopg.Connection | None = None) -> list[TrailerItem]:
    with _brief_conn(conn) as c:
        return [TrailerItem(int(r[0]), r[1], r[2])
                for r in c.execute(SCREENING_UNMATCHED_SQL, {"limit": limit}).fetchall()]


def title_trailer_record(item_id: int, title_id: int | None, query: str, *,
                         conn: psycopg.Connection | None = None) -> bool:
    """A trailer's match (or `unmatched`), once. Returns whether it was new."""
    with _brief_conn(conn) as c:
        return c.execute(
            "INSERT INTO title_trailers (item_id, title_id, status, query) VALUES (%s, %s, %s, left(%s, 300)) "
            "ON CONFLICT (item_id) DO NOTHING",
            (item_id, title_id, "matched" if title_id is not None else "unmatched", query)).rowcount == 1


@dataclass(frozen=True)
class ShelfTitle:
    id: int
    tmdb_id: int
    kind: str
    status: str
    #: titles.providers before this refresh, or None when TMDB was never read
    #: for it: what an arrival (db/053) is measured against. Not titles.tonight,
    #: which is empty on every title shelved before db/053 until its refresh.
    providers: Mapping[str, Any] | None = None


def screening_shelf_titles(*, conn: psycopg.Connection | None = None) -> list[ShelfTitle]:
    """Every REAL title on a shelf (its latest move), for the daily refresh."""
    with _brief_conn(conn) as c:
        return [ShelfTitle(int(r[0]), int(r[1]), r[2], r[3], r[4] if r[5] is not None else None) for r in c.execute(
            """
            SELECT t.id, t.tmdb_id, t.kind, c.status, t.providers, t.providers_refreshed_at
              FROM v_watch_current c JOIN titles t ON t.id = c.title_id
             WHERE NOT t.is_test ORDER BY c.status, t.id
            """).fetchall()]


def screening_known_youtube_keys(keys: Sequence[str], *, conn: psycopg.Connection | None = None) -> set[str]:
    """Which of these YouTube keys a real item already has (either trailer
    source): the TMDB path skips them before embedding anything."""
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return set()
    with _brief_conn(conn) as c:
        return {r[0] for r in c.execute(
            "SELECT youtube_key FROM content_items WHERE youtube_key = ANY (%s) AND NOT is_test", (keys,)).fetchall()}


# ---------------------------------------------------------------------------
# Screening recommendations (db/053; screening/recs.py, screening/rec_jobs.py).
# The worker writes the titles' inputs and embeddings, the pool, the scores,
# the runs, the seen-it pass, arrivals and release pushes; the owner writes
# title_reactions (the web). Every read here is of REAL rows only.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TitleRecord:
    """One title in the owner's record (v_title_state), with its TMDB key."""
    title_id: int
    tmdb_id: int
    kind: str
    name: str
    reaction: str | None
    reacted_at: datetime | None
    seen: bool
    seen_pass: str | None
    seed: bool
    seed_group: str | None
    shelf: str | None
    finished: bool
    trailer_reaction: str | None
    embedded: bool


#: Every real title with a record. tests_live runs this text.
SCREENING_RECORD_SQL: Final = """
    SELECT s.title_id, t.tmdb_id, t.kind, t.name, s.reaction, s.reacted_at, s.seen, s.seen_pass, s.seed,
           s.seed_group, s.shelf, s.finished, s.trailer_reaction, t.embedding IS NOT NULL
      FROM v_title_state s JOIN titles t ON t.id = s.title_id
     WHERE NOT s.is_test
     ORDER BY s.title_id
"""


def screening_records(*, conn: psycopg.Connection | None = None) -> list[TitleRecord]:
    with _brief_conn(conn) as c:
        return [TitleRecord(int(r[0]), int(r[1]), r[2], r[3], r[4], r[5], bool(r[6]), r[7], bool(r[8]), r[9],
                            r[10], bool(r[11]), r[12], bool(r[13]))
                for r in c.execute(SCREENING_RECORD_SQL).fetchall()]


def screening_note_phrases(*, conn: psycopg.Connection | None = None) -> list[str]:
    """The owner's taste notes (db/053: seed_interests.feed.screening.love)."""
    return feed_seed_phrases(conn=conn).get("screening", {}).get("love", [])


@dataclass(frozen=True)
class TitleRef:
    id: int
    tmdb_id: int
    kind: str
    embedded: bool
    is_test: bool


def screening_titles_by_key(keys: Sequence[tuple[int, str]], *,
                            conn: psycopg.Connection | None = None) -> dict[tuple[int, str], TitleRef]:
    """Titles already in the database for these (tmdb_id, kind) keys."""
    if not keys:
        return {}
    with _brief_conn(conn) as c:
        rows = c.execute(
            """
            SELECT t.id, t.tmdb_id, t.kind, t.embedding IS NOT NULL, t.is_test
              FROM titles t JOIN unnest(%s::int[], %s::text[]) AS k(tmdb_id, kind)
                ON t.tmdb_id = k.tmdb_id AND t.kind = k.kind
            """, ([k[0] for k in keys], [k[1] for k in keys])).fetchall()
    return {(int(r[1]), r[2]): TitleRef(int(r[0]), int(r[1]), r[2], bool(r[3]), bool(r[4])) for r in rows}


def titles_set_embedding(title_id: int, vector: Sequence[float], *, conn: psycopg.Connection | None = None) -> None:
    """A real title's embedding, once: a re-embed would quietly move every
    score computed against the old vector."""
    with _brief_conn(conn) as c:
        c.execute(
            "UPDATE titles SET embedding = %s::extensions.halfvec, embedded_at = now() "
            "WHERE id = %s AND embedding IS NULL AND NOT is_test",
            (_vec_text(vector), title_id))


def screening_title_vectors(ids: Sequence[int], *,
                            conn: psycopg.Connection | None = None) -> "dict[int, array[float]]":
    """id -> embedding (float32), read one row at a time."""
    out: dict[int, array[float]] = {}
    if not ids:
        return out
    with _brief_conn(conn) as c, c.cursor() as cur:
        _binary_vectors(c, cur)
        cur.execute("SELECT id, embedding FROM titles WHERE id = ANY (%s) AND embedding IS NOT NULL",
                    (list(ids),), binary=True)
        for tid, vec in cur:
            out[int(tid)] = _vec_array(vec)
    return out


def screening_candidates_upsert(rows: Sequence[tuple[int, Sequence[str], Sequence[int]]], *,
                                conn: psycopg.Connection | None = None) -> int:
    """(title_id, lanes, via_title_ids) per candidate of a full run: last
    seen now, with this run's lanes and via (the pool is what the latest runs
    found). One transaction."""
    if not rows:
        return 0
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO screening_candidates (title_id, lanes, via_title_ids)
            VALUES (%s, %s, %s)
            ON CONFLICT (title_id) DO UPDATE SET lanes = EXCLUDED.lanes, via_title_ids = EXCLUDED.via_title_ids,
                                                 last_seen_at = now()
            """, [(tid, sorted(set(lanes)), sorted(set(via))[:60]) for tid, lanes, via in rows])
    return len(rows)


@dataclass(frozen=True)
class PoolTitle:
    id: int
    kind: str
    name: str
    vote_average: float | None
    vote_count: int | None
    tonight: tuple[str, ...]
    release_date: date | None
    last_episode_date: date | None
    trending: bool


#: The pool a re-rank scores: real, embedded candidates a full run saw in the
#: last 3 days (screening_for_you's window). tests_live runs this text.
SCREENING_POOL_SQL: Final = """
    SELECT t.id, t.kind, t.name, t.vote_average, t.vote_count, t.tonight, t.release_date, t.last_episode_date,
           'trending' = ANY (c.lanes)
      FROM screening_candidates c JOIN titles t ON t.id = c.title_id
     WHERE NOT t.is_test AND t.embedding IS NOT NULL AND c.last_seen_at > now() - interval '3 days'
     ORDER BY t.id
"""


def screening_pool(*, conn: psycopg.Connection | None = None) -> list[PoolTitle]:
    with _brief_conn(conn) as c:
        return [PoolTitle(int(r[0]), r[1], r[2], float(r[3]) if r[3] is not None else None,
                          int(r[4]) if r[4] is not None else None, tuple(r[5] or ()), r[6], r[7], bool(r[8]))
                for r in c.execute(SCREENING_POOL_SQL).fetchall()]


#: Embedded real titles outside the pool that still want a score: shelved,
#: or matched to a trailer (Trailers For you ranks by it).
SCREENING_EXTRA_SQL: Final = """
    SELECT t.id, t.kind, t.name, t.vote_average, t.vote_count, t.tonight, t.release_date, t.last_episode_date, false
      FROM titles t
     WHERE NOT t.is_test AND t.embedding IS NOT NULL
       AND (EXISTS (SELECT 1 FROM title_trailers tt WHERE tt.title_id = t.id AND tt.status = 'matched')
            OR EXISTS (SELECT 1 FROM watch_status w WHERE w.title_id = t.id))
     ORDER BY t.id
"""


def screening_extra_titles(*, conn: psycopg.Connection | None = None) -> list[PoolTitle]:
    with _brief_conn(conn) as c:
        return [PoolTitle(int(r[0]), r[1], r[2], float(r[3]) if r[3] is not None else None,
                          int(r[4]) if r[4] is not None else None, tuple(r[5] or ()), r[6], r[7], bool(r[8]))
                for r in c.execute(SCREENING_EXTRA_SQL).fetchall()]


def screening_unembedded(limit: int, *, conn: psycopg.Connection | None = None) -> list[TitleRef]:
    """Real titles the recommendations need a vector for and lack: seeds,
    titles with a reaction, shelved titles, titles matched to a trailer.
    Oldest first."""
    with _brief_conn(conn) as c:
        return [TitleRef(int(r[0]), int(r[1]), r[2], False, False) for r in c.execute(
            """
            SELECT t.id, t.tmdb_id, t.kind FROM titles t
             WHERE NOT t.is_test AND t.embedding IS NULL
               AND (EXISTS (SELECT 1 FROM screening_seeds s WHERE s.title_id = t.id)
                    OR EXISTS (SELECT 1 FROM title_reactions r WHERE r.title_id = t.id)
                    OR EXISTS (SELECT 1 FROM watch_status w WHERE w.title_id = t.id)
                    OR EXISTS (SELECT 1 FROM title_trailers tt WHERE tt.title_id = t.id AND tt.status = 'matched'))
             ORDER BY t.id
             LIMIT %s
            """, (limit,)).fetchall()]


def screening_insert_run(p: Any, *, reason: str, pool_n: int, eligible_n: int, scored_n: int, tmdb_calls: int,
                         top: Sequence[Mapping[str, Any]], is_test: bool = False,
                         conn: psycopg.Connection | None = None) -> int:
    """`p` is a screening.recs.Profile. Append-only (db/053)."""
    with _brief_conn(conn) as c:
        return int(c.execute(
            """
            INSERT INTO screening_rec_runs (reason, mean_vec, love_vec, ni_vec, love_count, ni_count, neutral_count,
                                            seed_count, notes_weight, pool_n, eligible_n, scored_n, tmdb_calls,
                                            top, is_test)
            VALUES (%s, %s::extensions.halfvec, %s::extensions.halfvec, %s::extensions.halfvec, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (reason, _vec_text(p.mean) if p.mean else None, _vec_text(p.love) if p.love is not None else None,
             _vec_text(p.ni) if p.ni is not None else None, p.love_count, p.ni_count, p.neutral_count,
             p.seed_count, p.notes_weight, pool_n, eligible_n, scored_n, tmdb_calls, Jsonb(list(top)), is_test),
        ).fetchone()[0])


def title_scores_upsert(scores: Sequence[Any], run_id: int, *, conn: psycopg.Connection | None = None) -> int:
    """`scores` are screening.recs.Scored: one row per title, replaced in
    place. One transaction: a run's scores land whole or not at all."""
    if not scores:
        return 0
    with _brief_conn(conn) as c, c.transaction(), c.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO title_scores (title_id, score, because_title_id, components, run_id, scored_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (title_id) DO UPDATE SET score = EXCLUDED.score, because_title_id = EXCLUDED.because_title_id,
                                                 components = EXCLUDED.components, run_id = EXCLUDED.run_id,
                                                 scored_at = now()
            """,
            [(s.title_id, max(-50.0, min(50.0, float(s.score))),
              s.because if s.because != s.title_id else None, Jsonb(dict(s.components)), run_id) for s in scores])
    return len(scores)


def screening_seen_pass_count(*, conn: psycopg.Connection | None = None) -> int:
    with _brief_conn(conn) as c:
        return int(c.execute("SELECT count(*) FROM screening_seen_pass WHERE NOT is_test").fetchone()[0])


def screening_seen_pass_write(rows: Sequence[tuple[int, str]], *, conn: psycopg.Connection | None = None) -> int:
    """The seen-it pass, once: (title_id, group) in order. Written only while
    no real row exists (one transaction, locked), so it is never topped up
    or rewritten."""
    if not rows:
        return 0
    with _brief_conn(conn) as c, c.transaction():
        c.execute("SELECT pg_advisory_xact_lock(hashtext('screening_seen_pass'))")
        if c.execute("SELECT EXISTS (SELECT 1 FROM screening_seen_pass WHERE NOT is_test)").fetchone()[0]:
            return 0
        with c.cursor() as cur:
            cur.executemany("INSERT INTO screening_seen_pass (title_id, grp, pos) VALUES (%s, %s, %s) "
                            "ON CONFLICT (title_id) DO NOTHING",
                            [(tid, grp, i) for i, (tid, grp) in enumerate(rows)])
    return len(rows)


def screening_top(limit: int, *, conn: psycopg.Connection | None = None) -> list[dict[str, Any]]:
    """screening_for_you as the web reads it (real rows), for a run's record."""
    with _brief_conn(conn) as c:
        return [{"title_id": int(r[0]), "name": r[1], "kind": r[2], "score": float(r[3]), "because": r[4]}
                for r in c.execute(
                    "SELECT title_id, name, kind, score, because_name FROM screening_for_you(%s, false) ORDER BY pos",
                    (limit,)).fetchall()]


def title_arrivals_add(title_id: int, services: Sequence[str], *, conn: psycopg.Connection | None = None) -> int:
    """Log a shelf title newly streaming on these services (once a day each;
    db/053 sets the day)."""
    if not services:
        return 0
    n = 0
    with _brief_conn(conn) as c, c.transaction():
        for svc in services:
            n += c.execute("INSERT INTO title_provider_arrivals (title_id, service, detected_on) "
                           "VALUES (%s, %s, CURRENT_DATE) ON CONFLICT DO NOTHING", (title_id, svc)).rowcount
    return n


@dataclass(frozen=True)
class ReleaseEvent:
    title_id: int
    tmdb_id: int
    kind: str
    name: str
    #: release, episode or arrival.
    what: str
    event_key: str
    service: str | None
    alerted: bool


def screening_events(day: date | None = None, *, conn: psycopg.Connection | None = None) -> list[ReleaseEvent]:
    """db/053 screening_events_on: the day's (today's, by default) events for
    the owner's Want and Watching titles."""
    with _brief_conn(conn) as c:
        return [ReleaseEvent(int(r[0]), int(r[1]), r[2], r[3], r[4], r[5], r[6], bool(r[7])) for r in c.execute(
            "SELECT title_id, tmdb_id, kind, name, what, event_key, service, alerted "
            "FROM screening_events_on(%s::date)", (day,)).fetchall()]


_RELEASE_PUSH_LOCK: Final = "SELECT pg_advisory_xact_lock(hashtext('release_pushes'))"


def release_push(events: Sequence[ReleaseEvent], send: Callable[[Sequence[ReleaseEvent]], int | None], *,
                 test: bool = False, conn: psycopg.Connection | None = None) -> int | None:
    """One release push, recorded with every event it announced, in one
    transaction. A REAL push re-checks under the lock: no real push today
    yet, and only events never alerted (db/053 refuses a second push a day
    and an event twice anyway). `send(events)` pushes what is left and
    returns the notification id; it runs only once the database has said
    yes. Returns the release_pushes id, or None when nothing is left to say.
    A TEST push (`test`) is exempt from both rules."""
    with _brief_conn(conn) as c, c.transaction():
        c.execute(_RELEASE_PUSH_LOCK)
        todo = list(events)
        if not test:
            if c.execute("SELECT EXISTS (SELECT 1 FROM release_pushes WHERE local_day = owner_today() "
                         "AND NOT is_test)").fetchone()[0]:
                return None
            done = {(int(r[0]), r[1]) for r in c.execute(
                "SELECT title_id, event_key FROM release_alerts WHERE NOT is_test AND title_id = ANY (%s)",
                ([e.title_id for e in todo],)).fetchall()}
            todo = [e for e in todo if (e.title_id, e.event_key) not in done]
            if not todo:
                return None
        notification_id = send(todo)
        push_id = int(c.execute(
            "INSERT INTO release_pushes (notification_id, local_day, is_test) VALUES (%s, CURRENT_DATE, %s) "
            "RETURNING id", (notification_id, test)).fetchone()[0])
        with c.cursor() as cur:
            cur.executemany("INSERT INTO release_alerts (push_id, title_id, event_key, service) VALUES (%s, %s, %s, %s)",
                            [(push_id, e.title_id, e.event_key, e.service) for e in todo])
    return push_id


def screening_test_push_title(title_id: int | None = None, *,
                              conn: psycopg.Connection | None = None) -> ReleaseEvent | None:
    """What a TEST release push announces: `title_id` if it is a real title,
    else the newest Want or Watching title (one with a tonight service
    first), shaped as today's release or episode."""
    with _brief_conn(conn) as c:
        r = c.execute(
            """
            SELECT t.id, t.tmdb_id, t.kind, t.name, t.tonight[1]
              FROM titles t LEFT JOIN v_watch_current c ON c.title_id = t.id
             WHERE NOT t.is_test
               AND (CASE WHEN %(id)s::bigint IS NULL THEN c.status IN ('want', 'watching')
                         ELSE t.id = %(id)s::bigint END)
             ORDER BY (cardinality(t.tonight) = 0), c.moved_at DESC NULLS LAST, t.id DESC
             LIMIT 1
            """, {"id": title_id}).fetchone()
    if r is None:
        return None
    what = "episode" if r[2] == "tv" else "release"
    return ReleaseEvent(int(r[0]), int(r[1]), r[2], r[3], what, f"{what}:{date.today().isoformat()}", r[4], False)


def queue_enqueue(kind: str, payload: Mapping[str, Any], run_after: datetime, *,
                  conn: psycopg.Connection | None = None) -> int:
    """Queue one job for the worker's poller (job_queue, db/020)."""
    if run_after.tzinfo is None:
        raise LedgerError("run_after must be timezone-aware.")
    with _brief_conn(conn) as c:
        return int(c.execute("INSERT INTO job_queue (kind, payload, run_after) VALUES (%s, %s, %s) RETURNING id",
                             (kind, Jsonb(dict(payload)), run_after)).fetchone()[0])


# ---------------------------------------------------------------------------
# Kalshi archive (db/011, db/014): store-only. Nothing here reads archived
# prices back; every insert is ON CONFLICT DO NOTHING so a rerun is harmless,
# and the database refuses in-play candles and prints.
# ---------------------------------------------------------------------------

def archive_market(
    *, ticker: str, event_ticker: str, series_ticker: str, sport: str, game_id: str | None,
    kickoff: datetime, kickoff_source: str, floor_strike: Numeric | None, title: str | None,
) -> None:
    if kickoff.tzinfo is None:
        raise LedgerError("kickoff must be timezone-aware.")
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO kalshi_markets
                (ticker, event_ticker, series_ticker, sport, game_id, kickoff,
                 kickoff_source, floor_strike, title)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker) DO NOTHING
            """,
            (ticker, event_ticker, series_ticker, sport, game_id, kickoff,
             kickoff_source, _num(floor_strike), title),
        )


def archive_settlement(*, ticker: str, result: str, value: Numeric, settled_at: datetime) -> None:
    if settled_at.tzinfo is None:
        raise LedgerError("settled_at must be timezone-aware.")
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO kalshi_settlements (market_id, result, settlement_value, settlement_ts)
            SELECT id, %s, %s, %s FROM kalshi_markets WHERE ticker = %s
            ON CONFLICT (market_id) DO NOTHING
            """,
            (result, _num(value), settled_at, ticker),
        )


def archive_candles(*, ticker: str, candles: Sequence[Any], source: str) -> None:
    """1-minute candles: closes, volume and open interest only (the fields
    F1/F2 read). Open/high/low stay NULL to keep the archive small."""
    rows = [(c.end, _num(c.yes_bid_close), _num(c.yes_ask_close), _num(c.trade_close),
             _num(c.volume), _num(c.open_interest), source, ticker) for c in candles]
    if not rows:
        return
    with _pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO kalshi_candles
                (market_id, period_minutes, end_period_ts, yes_bid_close, yes_ask_close,
                 trade_close, volume, open_interest, source)
            SELECT id, 1, %s, %s, %s, %s, %s, %s, %s FROM kalshi_markets WHERE ticker = %s
            ON CONFLICT (market_id, period_minutes, end_period_ts) DO NOTHING
            """,
            rows,
        )


def archive_trades(*, ticker: str, trades: Sequence[Any], source: str) -> None:
    rows = [(t.trade_id, t.at, _num(t.yes_price), _num(t.count), t.taker_side, source, ticker)
            for t in trades if t.trade_id]
    if not rows:
        return
    with _pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO kalshi_trades
                (market_id, trade_id, created_time, yes_price, count, taker_side, source)
            SELECT id, %s, %s, %s, %s, %s, %s FROM kalshi_markets WHERE ticker = %s
            ON CONFLICT (trade_id) DO NOTHING
            """,
            rows,
        )


def record_resolution_attempt(
    commitment_id: int,
    result: AttemptResult = "deferred",
    reason: str | None = None,
    purpose: AttemptPurpose = "resolve",
) -> None:
    """Note that a resolution attempt did not produce an answer.

    Counted, not summed into a column, because "how long did we wait and why"
    is worth being able to ask later — and because `commitments` is immutable,
    which is the point of it.

    Both `deferred` and `error` count against the cap. An adapter that raises
    every sweep is no more resolvable than one that keeps saying "not yet".

    An ``'answered'`` attempt is never written alone: `add_resolution` and
    `add_closing_snapshot` write it with the answer, in one transaction.
    """
    if result == "answered":
        raise LedgerError(
            "an answered attempt is written with its resolution or snapshot, "
            "in the same transaction — never on its own"
        )
    with _pool().connection() as conn, conn.cursor() as cur:
        _insert_attempt(cur, commitment_id, result, reason, purpose)


def _insert_attempt(
    cur: Any,
    commitment_id: int,
    result: AttemptResult,
    reason: str | None,
    purpose: AttemptPurpose,
) -> None:
    cur.execute(
        """
        INSERT INTO resolution_attempts
            (commitment_id, result, reason, purpose)
        VALUES (%s, %s, %s, %s)
        """,
        (commitment_id, result, reason[:2000] if reason else None, purpose),
    )


# ---------------------------------------------------------------------------
# Reads — the resolution sweep's one query.
# ---------------------------------------------------------------------------

_DUE_SQL: Final = """
    SELECT c.id, c.agent_id, a.slug, a.is_test, c.kind, c.thesis, c.confidence,
           c.payload, c.committed_at, c.resolves_after,
           -- Counted here rather than in the worker: the bounded-defer
           -- thresholds must not depend on a worker's clock or its memory of
           -- previous sweeps. Both come off the database.
           -- purpose = 'resolve' is LOAD-BEARING, not tidiness. Resolution and
           -- capture share this table but NOT their budgets: each has its own
           -- DeferPolicy. Counting both here spent the resolution budget on
           -- capture waits, so a commitment whose market legitimately sat open
           -- past its expected close burned 12 resolution attempts while
           -- capture was still correctly waiting, and was then voided. A void
           -- is permanent (resolutions.commitment_id is UNIQUE), so the row
           -- died of bookkeeping while every component worked. The capture
           -- query below has always filtered; this one had not.
           (SELECT count(*) FROM resolution_attempts ra
             WHERE ra.commitment_id = c.id
               AND ra.purpose = 'resolve') AS attempts,
           now() - c.resolves_after AS overdue_by,
           c.closes_at,
           COALESCE(
               json_agg(
                   json_build_object(
                       'subject',   l.subject,
                       'market',    l.market,
                       'line',      l.line,
                       'direction', l.direction,
                       'size',      l.size
                   ) ORDER BY l.leg_index
               ) FILTER (WHERE l.id IS NOT NULL),
               '[]'::json
           ) AS legs
      FROM commitments c
      JOIN agents a ON a.id = c.agent_id
      LEFT JOIN legs l ON l.commitment_id = c.id
      LEFT JOIN resolutions r ON r.commitment_id = c.id
     WHERE (%(due_only)s = false OR c.resolves_after <= now())
       AND r.id IS NULL
       AND (%(agent_id)s::smallint IS NULL OR c.agent_id = %(agent_id)s)
       AND (%(include_test)s OR a.is_test = false)
     -- Grouping by both primary keys lets Postgres functionally determine
     -- every other selected column of `agents` and `commitments`. Listing the
     -- columns individually instead is how `a.is_test` got missed once.
     GROUP BY c.id, a.id
     -- STARVATION GUARD. The sweep reads one page (sweep_limit). Ordered by
     -- deadline alone, a backlog larger than the page kept handing back the
     -- same oldest rows, and the rest were never asked at all; then, past
     -- max_overdue, core gave up on them unseen. Now: rows never attempted
     -- first, then the one whose last attempt is oldest. Every due row is
     -- tried within ceil(n / limit) sweeps (tests_live/test_sweep_order.py).
     ORDER BY (SELECT max(ra.attempted_at) FROM resolution_attempts ra
                WHERE ra.commitment_id = c.id AND ra.purpose = 'resolve') ASC NULLS FIRST,
              c.resolves_after ASC, c.id ASC
     LIMIT %(limit)s
"""


def due_for_resolution(
    agent_id: int | None = None,
    limit: int = 100,
    *,
    include_test: bool = True,
) -> list[PendingCommitment]:
    """Commitments past `resolves_after` with no resolution row yet.

    Never-attempted rows first, then the oldest last attempt, then the oldest
    deadline: a backlog bigger than one page drains fairly instead of
    re-serving the same rows while the rest starve. Legs come
    back in `leg_index` order — the same order they were passed to `commit()`,
    which is what lets `resolve()` line them up positionally.

    This is an **operational** read, not a scoring read. The resolution sweep
    must see harness agents or the fake agent never resolves, so `include_test`
    defaults to True. Anything that computes a track record wants
    `include_test=False` — and more likely wants its own query built with
    `TRACK_RECORD_FILTER`, because a scorer has no business looking at the
    unresolved set in the first place. Every row also carries `is_test`.
    """
    return _unresolved(agent_id, limit, include_test, due_only=True)


def open_commitments(
    agent_id: int | None = None,
    limit: int = 200,
    *,
    include_test: bool = True,
) -> list[PendingCommitment]:
    """Every unresolved commitment, whether or not it is due yet.

    This is what an agent needs to avoid committing twice to the same thing.
    `due_for_resolution` deliberately cannot answer it: a position opened ten
    minutes ago with a six-hour horizon is not due, so it is invisible there —
    and an agent that can't see it will re-enter the same trade on every tick
    until it is.

    Exposed through `BaseAgent.open_commitments()` rather than as a fifth hook,
    so the four-hook contract is unchanged and no adapter writes SQL.
    """
    return _unresolved(agent_id, limit, include_test, due_only=False)


_CAPTURE_SQL: Final = """
    SELECT c.id, c.agent_id, a.slug, a.is_test, c.kind, c.thesis, c.confidence,
           c.payload, c.committed_at, c.resolves_after,
           (SELECT count(*) FROM resolution_attempts ra
             WHERE ra.commitment_id = c.id AND ra.purpose = 'capture')
             AS attempts,
           now() - c.closes_at AS overdue_by,
           c.closes_at,
           COALESCE(
               json_agg(
                   json_build_object(
                       'subject',   l.subject,
                       'market',    l.market,
                       'line',      l.line,
                       'direction', l.direction,
                       'size',      l.size
                   ) ORDER BY l.leg_index
               ) FILTER (WHERE l.id IS NOT NULL),
               '[]'::json
           ) AS legs
      FROM commitments c
      JOIN agents a ON a.id = c.agent_id
      LEFT JOIN legs l ON l.commitment_id = c.id
      LEFT JOIN closing_snapshots s ON s.commitment_id = c.id
     WHERE c.closes_at IS NOT NULL
       AND c.closes_at <= now()
       AND s.id IS NULL
       AND (%(agent_id)s::smallint IS NULL OR c.agent_id = %(agent_id)s)
       AND (%(include_test)s OR a.is_test = false)
     GROUP BY c.id, a.id
     -- STARVATION GUARD, as in _DUE_SQL: never-attempted rows first, then the
     -- oldest last attempt, then the oldest close. A close happens once, so a
     -- row the page never reached was the worst case of all.
     ORDER BY (SELECT max(ra.attempted_at) FROM resolution_attempts ra
                WHERE ra.commitment_id = c.id AND ra.purpose = 'capture') ASC NULLS FIRST,
              c.closes_at ASC, c.id ASC
     LIMIT %(limit)s
"""


def due_for_capture(
    agent_id: int | None = None,
    limit: int = 100,
    *,
    include_test: bool = True,
) -> list[PendingCommitment]:
    """Commitments past their close with no snapshot yet.

    Never-attempted rows first, then the oldest last attempt, then the oldest
    close. A missed snapshot is permanent data loss (the close happens once),
    so no row may wait behind a page of rows that keep deferring: every due
    row gets an attempt within ceil(n / limit) sweeps.

    A `missed` tombstone counts as a snapshot and removes the row from this
    set, which is what stops a permanently uncapturable commitment being
    retried forever.
    """
    with _pool().connection() as conn:
        rows = conn.execute(
            _CAPTURE_SQL,
            {"agent_id": agent_id, "limit": limit, "include_test": include_test},
        ).fetchall()

    return [
        PendingCommitment(
            id=row[0], agent_id=row[1], agent_slug=row[2], is_test=row[3],
            kind=row[4], thesis=row[5], confidence=row[6], payload=row[7],
            committed_at=row[8], resolves_after=row[9],
            attempts=row[10], overdue_by=row[11], closes_at=row[12],
            legs=_legs_from_json(row[13]),
        )
        for row in rows
    ]


def _legs_from_json(rows: Sequence[dict[str, Any]]) -> tuple[Leg, ...]:
    return tuple(
        Leg(
            subject=leg["subject"],
            market=leg["market"],
            line=_num(leg["line"]),
            direction=leg["direction"],
            size=_num(leg["size"]),
        )
        for leg in rows
    )


def _unresolved(
    agent_id: int | None,
    limit: int,
    include_test: bool,
    *,
    due_only: bool,
) -> list[PendingCommitment]:
    with _pool().connection() as conn:
        rows = conn.execute(
            _DUE_SQL,
            {
                "agent_id": agent_id,
                "limit": limit,
                "include_test": include_test,
                "due_only": due_only,
            },
        ).fetchall()

    return [
        PendingCommitment(
            id=row[0],
            agent_id=row[1],
            agent_slug=row[2],
            is_test=row[3],
            kind=row[4],
            thesis=row[5],
            confidence=row[6],
            payload=row[7],
            committed_at=row[8],
            resolves_after=row[9],
            attempts=row[10],
            overdue_by=row[11],
            closes_at=row[12],
            legs=_legs_from_json(row[13]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# The Arsenal (db/054; Chat 5 Phase 1; workers/arsenal/). The worker writes
# the pool, the seeds' embeddings, verification, the pool-run log and the
# drops (through arsenal_pick_drop); the owner writes reactions (the web).
# Narrow reads only: no SELECT *, and no vector is ever read back (the
# near-duplicate check runs inside Postgres, arsenal_near_dup).
# ---------------------------------------------------------------------------

ARSENAL_VERIFY_KIND: Final = "arsenal_verify"


@dataclass(frozen=True)
class ArsenalOpenBatch:
    run_id: int
    batch_id: str
    model: str
    submitted_at: datetime


@dataclass(frozen=True)
class ArsenalPoolStatus:
    """Real rows only. 'ready' is verified or corrected, still in the pool."""
    refs_ready: int
    refs_pending: int
    words_ready: int
    words_pending: int
    #: Categories with at least one ready reference.
    ready_categories: tuple[str, ...]
    last_submitted_at: datetime | None
    open_batch: ArsenalOpenBatch | None


#: tests_live runs this exact text. An item with an ACTIVE hold (db/056
#: v_arsenal_active_holds: a hold with no release) is never ready: the
#: picker skips it, so counting it would hide a short pool.
ARSENAL_POOL_STATUS_SQL: Final = """
    SELECT
        (SELECT count(*) FROM arsenal_refs r
          WHERE NOT r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected')
            AND NOT EXISTS (SELECT 1 FROM v_arsenal_active_holds h WHERE h.ref_id = r.id)),
        (SELECT count(*) FROM arsenal_refs WHERE NOT is_test AND status = 'pool' AND verification = 'pending'),
        (SELECT count(*) FROM arsenal_words w
          WHERE NOT w.is_test AND w.status = 'pool' AND w.verification IN ('verified', 'corrected')
            AND NOT EXISTS (SELECT 1 FROM v_arsenal_active_holds h WHERE h.word_id = w.id)),
        (SELECT count(*) FROM arsenal_words WHERE NOT is_test AND status = 'pool' AND verification = 'pending'),
        (SELECT coalesce(array_agg(DISTINCT r.category ORDER BY r.category), '{}') FROM arsenal_refs r
          WHERE NOT r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected')
            AND NOT EXISTS (SELECT 1 FROM v_arsenal_active_holds h WHERE h.ref_id = r.id)),
        (SELECT max(created_at) FROM arsenal_pool_runs WHERE NOT is_test AND kind = 'batch' AND status = 'submitted')
"""

#: The newest real batch submitted and not yet collected or failed.
ARSENAL_OPEN_BATCH_SQL: Final = """
    SELECT s.id, s.batch_id, s.model, s.created_at
      FROM arsenal_pool_runs s
     WHERE s.kind = 'batch' AND s.status = 'submitted' AND NOT s.is_test
       AND NOT EXISTS (SELECT 1 FROM arsenal_pool_runs x
                        WHERE x.run_id = s.id AND x.status IN ('collected', 'failed'))
     ORDER BY s.id DESC
     LIMIT 1
"""


def arsenal_open_batch(*, conn: psycopg.Connection | None = None) -> ArsenalOpenBatch | None:
    with _brief_conn(conn) as c:
        row = c.execute(ARSENAL_OPEN_BATCH_SQL).fetchone()
    return None if row is None else ArsenalOpenBatch(int(row[0]), str(row[1]), str(row[2] or ""), row[3])


def arsenal_pool_status(*, conn: psycopg.Connection | None = None) -> ArsenalPoolStatus:
    with _brief_conn(conn) as c:
        r = c.execute(ARSENAL_POOL_STATUS_SQL).fetchone()
        open_batch = arsenal_open_batch(conn=c)
    return ArsenalPoolStatus(int(r[0]), int(r[1]), int(r[2]), int(r[3]), tuple(r[4] or ()), r[5], open_batch)


def arsenal_recent_keys(limit: int = 60, *, conn: psycopg.Connection | None = None) -> list[str]:
    """The newest real canonical keys (not seeds): the prompt's never-repeat list."""
    with _brief_conn(conn) as c:
        return [r[0] for r in c.execute(
            "SELECT canonical_key FROM arsenal_refs WHERE NOT is_test AND origin_kind <> 'seed' "
            "ORDER BY id DESC LIMIT %s", (limit,)).fetchall()]


def arsenal_recent_lemmas(limit: int = 60, *, conn: psycopg.Connection | None = None) -> list[str]:
    with _brief_conn(conn) as c:
        return [r[0] for r in c.execute(
            "SELECT lemma FROM arsenal_words WHERE NOT is_test ORDER BY id DESC LIMIT %s", (limit,)).fetchall()]


@dataclass(frozen=True)
class ArsenalPoolRef:
    """An undropped verified or corrected real reference: what the item 5
    outcome check reads (scripts.arsenal_outcome_check)."""
    ref: "ArsenalRefToVerify"
    held: bool


def arsenal_undropped_checked_refs(*, conn: psycopg.Connection | None = None) -> list[ArsenalPoolRef]:
    with _brief_conn(conn) as c:
        ids = [(int(r[0]), bool(r[1])) for r in c.execute(
            "SELECT r.id, EXISTS (SELECT 1 FROM v_arsenal_active_holds h WHERE h.ref_id = r.id) FROM arsenal_refs r "
            "WHERE NOT r.is_test AND r.status = 'pool' AND r.verification IN ('verified', 'corrected') "
            "ORDER BY r.id").fetchall()]
        out = []
        for ref_id, held in ids:
            ref = arsenal_ref_to_verify(ref_id, conn=c)
            if ref is not None:
                out.append(ArsenalPoolRef(ref, held))
    return out


def arsenal_hold(*, ref_id: int | None = None, word_id: int | None = None, reason: str, detail: str | None,
                 conn: psycopg.Connection | None = None) -> int:
    """One arsenal_holds row (db/055, append-only): out of every drop."""
    if (ref_id is None) == (word_id is None):
        raise LedgerError("arsenal_hold: exactly one of ref_id and word_id")
    with _brief_conn(conn) as c:
        return int(c.execute(
            "INSERT INTO arsenal_holds (ref_id, word_id, reason, detail) VALUES (%s, %s, %s, %s) RETURNING id",
            (ref_id, word_id, reason, detail[:500] if detail else None)).fetchone()[0])


@dataclass(frozen=True)
class ArsenalHold:
    id: int
    ref_id: int | None
    word_id: int | None
    reason: str
    released: bool


def arsenal_holds_by_id(hold_ids: Sequence[int], *, conn: psycopg.Connection | None = None) -> list[ArsenalHold]:
    with _brief_conn(conn) as c:
        return [ArsenalHold(int(r[0]), r[1], r[2], r[3], bool(r[4])) for r in c.execute(
            "SELECT h.id, h.ref_id, h.word_id, h.reason, "
            "EXISTS (SELECT 1 FROM arsenal_hold_releases x WHERE x.hold_id = h.id) "
            "FROM arsenal_holds h WHERE h.id = ANY(%s) ORDER BY h.id", (list(hold_ids),)).fetchall()]


def arsenal_release_hold(hold_id: int, reason: str, *, conn: psycopg.Connection | None = None) -> int:
    """db/056 arsenal_release_hold: one append-only release (a hold is
    released at most once). Returns the release's id."""
    with _brief_conn(conn) as c:
        return int(c.execute("SELECT arsenal_release_hold(%s, %s)", (hold_id, reason)).fetchone()[0])


def arsenal_seed_lemmas(*, conn: psycopg.Connection | None = None) -> list[str]:
    """The owner's known words (db/055 status 'seed'): never a Power Word."""
    with _brief_conn(conn) as c:
        return [r[0] for r in c.execute(
            "SELECT lemma FROM arsenal_words WHERE status = 'seed' AND NOT is_test ORDER BY lemma").fetchall()]


def arsenal_seeds(*, conn: psycopg.Connection | None = None) -> list[tuple[str, str, str]]:
    """(canonical_key, title, source_work) of every seed: taste, never repeat."""
    with _brief_conn(conn) as c:
        return [(r[0], r[1], r[2]) for r in c.execute(
            "SELECT canonical_key, title, source_work FROM arsenal_refs "
            "WHERE status = 'seed' AND NOT is_test ORDER BY id").fetchall()]


def arsenal_weights(*, conn: psycopg.Connection | None = None) -> dict[tuple[str, str], float]:
    """(dim, key) -> weight, from db/054 arsenal_weights()."""
    with _brief_conn(conn) as c:
        return {(r[0], r[1]): float(r[2]) for r in c.execute(
            "SELECT dim, key, weight FROM arsenal_weights()").fetchall()}


_ARSENAL_RUN_COUNTS: Final = ("refs_requested", "words_requested", "inserted_refs", "inserted_words", "skipped_dup",
                              "skipped_invalid", "verified", "corrected", "unverified", "rejected")


def arsenal_log_run(kind: str, status: str, *, run_id: int | None = None, batch_id: str | None = None,
                    model: str | None = None, cost_usd: Decimal | None = None, note: str | None = None,
                    conn: psycopg.Connection | None = None, **counts: int) -> int:
    """One arsenal_pool_runs row (append-only). Returns its id."""
    unknown = set(counts) - set(_ARSENAL_RUN_COUNTS)
    if unknown:
        raise LedgerError(f"arsenal_log_run: unknown count(s) {sorted(unknown)}")
    cols = ["kind", "status", "run_id", "batch_id", "model", "cost_usd", "note", *counts]
    vals = [kind, status, run_id, batch_id, model, cost_usd if cost_usd is not None else Decimal(0),
            note[:500] if note else None, *counts.values()]
    with _brief_conn(conn) as c:
        return int(c.execute(
            f"INSERT INTO arsenal_pool_runs ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            vals).fetchone()[0])


@dataclass(frozen=True)
class ArsenalCardIn:
    """A validated card from the model (workers/arsenal/cards.py)."""
    canonical_key: str
    category: str
    title: str
    origin: str
    meaning: str
    deploy_line: str
    room: str
    source_work: str
    quote: str | None
    quote_by: str | None
    source_kind: str
    source_page: str | None


@dataclass(frozen=True)
class ArsenalWordIn:
    lemma: str
    meaning: str
    room: str
    sentence: str


def arsenal_near_dup(vector: Sequence[float], threshold: float, *, conn: psycopg.Connection | None = None) -> bool:
    """db/054 arsenal_near_dup: the vector goes in, a boolean comes out."""
    with _brief_conn(conn) as c:
        # ::real: the function takes real; a Python float binds as double.
        return bool(c.execute("SELECT arsenal_near_dup(%s::extensions.halfvec, %s::real)",
                              (_vec_text(vector), threshold)).fetchone()[0])


def arsenal_insert_ref(card: ArsenalCardIn, *, pool_run_id: int | None, vector: Sequence[float] | None,
                       conn: psycopg.Connection | None = None) -> int | None:
    """A new real pool reference, pending. None when the key already exists
    (UNIQUE canonical_key: never a repeat)."""
    with _brief_conn(conn) as c:
        row = c.execute(
            """
            INSERT INTO arsenal_refs (canonical_key, category, title, origin, meaning, deploy_line, room, source_work,
                                      quote, quote_by, source_kind, source_page, status, origin_kind, pool_run_id,
                                      embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pool', 'pool', %s, %s::extensions.halfvec)
            ON CONFLICT (canonical_key) DO NOTHING
            RETURNING id
            """,
            (card.canonical_key, card.category, card.title, card.origin, card.meaning, card.deploy_line, card.room,
             card.source_work, card.quote, card.quote_by, card.source_kind, card.source_page, pool_run_id,
             _vec_text(vector) if vector is not None else None)).fetchone()
    return None if row is None else int(row[0])


def arsenal_insert_word(word: ArsenalWordIn, *, pool_run_id: int | None,
                        conn: psycopg.Connection | None = None) -> int | None:
    """A new real Power Word, pending. None when the lemma already exists."""
    with _brief_conn(conn) as c:
        row = c.execute(
            "INSERT INTO arsenal_words (lemma, meaning, room, sentence, pool_run_id) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (lemma) DO NOTHING RETURNING id",
            (word.lemma, word.meaning, word.room, word.sentence, pool_run_id)).fetchone()
    return None if row is None else int(row[0])


def arsenal_unembedded(*, conn: psycopg.Connection | None = None) -> list[tuple[int, str, str]]:
    """(id, title, source_work) of real references with no embedding yet
    (the seeds, at first): embedded once so the near-duplicate check sees them."""
    with _brief_conn(conn) as c:
        return [(int(r[0]), r[1], r[2]) for r in c.execute(
            "SELECT id, title, source_work FROM arsenal_refs WHERE NOT is_test AND embedding IS NULL "
            "AND status <> 'rejected' ORDER BY id LIMIT 200").fetchall()]


def arsenal_set_embedding(ref_id: int, vector: Sequence[float], *, conn: psycopg.Connection | None = None) -> None:
    with _brief_conn(conn) as c:
        c.execute("UPDATE arsenal_refs SET embedding = %s::extensions.halfvec WHERE id = %s AND embedding IS NULL",
                  (_vec_text(vector), ref_id))


@dataclass(frozen=True)
class ArsenalRefToVerify:
    id: int
    category: str
    title: str
    origin: str
    source_work: str
    quote: str | None
    quote_by: str | None
    source_kind: str
    source_page: str | None
    verification: str
    pool_run_id: int | None
    #: What the outcome check reads (owner, 2026-10-16).
    meaning: str = ""
    deploy_line: str = ""


def arsenal_ref_to_verify(ref_id: int, *, conn: psycopg.Connection | None = None) -> ArsenalRefToVerify | None:
    with _brief_conn(conn) as c:
        r = c.execute(
            "SELECT id, category, title, origin, source_work, quote, quote_by, source_kind, source_page, "
            "verification, pool_run_id, meaning, deploy_line FROM arsenal_refs WHERE id = %s", (ref_id,)).fetchone()
    return None if r is None else ArsenalRefToVerify(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                                                     None if r[10] is None else int(r[10]), r[11], r[12])


@dataclass(frozen=True)
class ArsenalWordToVerify:
    id: int
    lemma: str
    verification: str
    pool_run_id: int | None


def arsenal_word_to_verify(word_id: int, *, conn: psycopg.Connection | None = None) -> ArsenalWordToVerify | None:
    with _brief_conn(conn) as c:
        r = c.execute("SELECT id, lemma, verification, pool_run_id FROM arsenal_words WHERE id = %s",
                      (word_id,)).fetchone()
    return None if r is None else ArsenalWordToVerify(int(r[0]), r[1], r[2], None if r[3] is None else int(r[3]))


def arsenal_settle_ref(ref_id: int, *, verification: str, note: str | None, source_url: str | None,
                       origin: str | None = None, source_work: str | None = None, drop_quote: bool = False,
                       meaning: str | None = None, deploy_line: str | None = None,
                       conn: psycopg.Connection | None = None) -> bool:
    """Settle a pending reference once (db/054's guard refuses a second time):
    the verdict, its note, the fetched page's URL, and any corrected wording
    (an outcome-conflict reframe rewrites meaning and deploy_line). False
    when it was not pending (already settled by another run)."""
    with _brief_conn(conn) as c:
        row = c.execute(
            """
            UPDATE arsenal_refs
               SET verification = %s, verification_note = %s, source_url = coalesce(%s, source_url),
                   origin = coalesce(%s, origin), source_work = coalesce(%s, source_work),
                   meaning = coalesce(%s, meaning), deploy_line = coalesce(%s, deploy_line),
                   quote = CASE WHEN %s THEN NULL ELSE quote END,
                   quote_by = CASE WHEN %s THEN NULL ELSE quote_by END
             WHERE id = %s AND verification = 'pending'
            RETURNING id
            """,
            (verification, note[:500] if note else None, source_url, origin, source_work, meaning, deploy_line,
             drop_quote, drop_quote, ref_id)).fetchone()
    return row is not None


def arsenal_settle_word(word_id: int, *, verification: str, note: str | None, pronunciation: str | None,
                        etymology: str | None, etymology_url: str | None,
                        conn: psycopg.Connection | None = None) -> bool:
    with _brief_conn(conn) as c:
        row = c.execute(
            """
            UPDATE arsenal_words
               SET verification = %s, verification_note = %s, pronunciation = coalesce(%s, pronunciation),
                   etymology = coalesce(%s, etymology), etymology_url = coalesce(%s, etymology_url)
             WHERE id = %s AND verification = 'pending'
            RETURNING id
            """,
            (verification, note[:500] if note else None, pronunciation, etymology, etymology_url, word_id)).fetchone()
    return row is not None


def arsenal_close_run_if_settled(run_id: int, *, conn: psycopg.Connection | None = None) -> bool:
    """Once a run's last pending item settles, write its 'verified' row with
    the counts (once). True when this call wrote it."""
    with _brief_conn(conn) as c, c.transaction():
        # One closer at a time per run (two verify jobs can finish together).
        c.execute("SELECT pg_advisory_xact_lock(hashtext('arsenal_run'), %s::int)", (run_id % 2_000_000_000,))
        row = c.execute(
            """
            WITH items AS (
                SELECT verification, status FROM arsenal_refs WHERE pool_run_id = %(run)s
                UNION ALL
                SELECT verification, status FROM arsenal_words WHERE pool_run_id = %(run)s
            )
            SELECT (SELECT kind FROM arsenal_pool_runs WHERE id = %(run)s),
                   count(*) FILTER (WHERE verification = 'pending'),
                   count(*) FILTER (WHERE verification = 'verified'),
                   count(*) FILTER (WHERE verification = 'corrected'),
                   count(*) FILTER (WHERE verification = 'unverified'),
                   count(*) FILTER (WHERE status = 'rejected'),
                   EXISTS (SELECT 1 FROM arsenal_pool_runs WHERE run_id = %(run)s AND status = 'verified'),
                   count(*)
              FROM items
            """, {"run": run_id}).fetchone()
        kind, pending, verified, corrected, unverified, rejected, closed, total = row
        if kind is None or closed or pending or not total:
            return False
        arsenal_log_run(kind, "verified", run_id=run_id, verified=int(verified), corrected=int(corrected),
                        unverified=int(unverified), rejected=int(rejected), conn=c)
        return True


#: Pending real items older than `hours` with no verify job queued or
#: running: the pool job queues them again.
ARSENAL_STALE_PENDING_SQL: Final = """
    SELECT 'ref', r.id FROM arsenal_refs r
     WHERE NOT r.is_test AND r.verification = 'pending' AND r.status IN ('pool', 'seed')
       AND r.created_at < now() - make_interval(hours => %(hours)s)
       AND NOT EXISTS (SELECT 1 FROM job_queue q WHERE q.kind = 'arsenal_verify' AND q.status IN ('queued', 'running')
                         AND q.payload ->> 'ref_id' = r.id::text)
    UNION ALL
    SELECT 'word', w.id FROM arsenal_words w
     WHERE NOT w.is_test AND w.verification = 'pending' AND w.status = 'pool'
       AND w.created_at < now() - make_interval(hours => %(hours)s)
       AND NOT EXISTS (SELECT 1 FROM job_queue q WHERE q.kind = 'arsenal_verify' AND q.status IN ('queued', 'running')
                         AND q.payload ->> 'word_id' = w.id::text)
     ORDER BY 1, 2
     LIMIT 200
"""


def arsenal_stale_pending(hours: int = 24, *, conn: psycopg.Connection | None = None) -> list[tuple[str, int]]:
    with _brief_conn(conn) as c:
        return [(r[0], int(r[1])) for r in c.execute(ARSENAL_STALE_PENDING_SQL, {"hours": hours}).fetchall()]


def arsenal_queue_verify(target: str, item_id: int, *, conn: psycopg.Connection | None = None) -> int:
    """One arsenal_verify job ({'ref_id': n} or {'word_id': n}), due now."""
    if target not in ("ref", "word"):
        raise LedgerError(f"arsenal_queue_verify: target is 'ref' or 'word', got {target!r}")
    return queue_enqueue(ARSENAL_VERIFY_KIND, {f"{target}_id": int(item_id)}, datetime.now(timezone.utc), conn=conn)


def arsenal_drop_on(day: date, *, conn: psycopg.Connection | None = None) -> int | None:
    """The real drop for `day`, if there is one."""
    with _brief_conn(conn) as c:
        row = c.execute("SELECT id FROM arsenal_drops WHERE drop_date = %s AND NOT is_test", (day,)).fetchone()
    return None if row is None else int(row[0])


def arsenal_pick_drop(day: date, *, topped_up: bool = False, conn: psycopg.Connection | None = None) -> int:
    """db/054 arsenal_pick_drop for a real drop: the drop's id (the existing
    one when `day` already has a drop). Raises when the pool is short."""
    with _brief_conn(conn) as c:
        return int(c.execute("SELECT arsenal_pick_drop(%s, false, %s)", (day, topped_up)).fetchone()[0])


def arsenal_owner_today(*, conn: psycopg.Connection | None = None) -> date:
    with _brief_conn(conn) as c:
        return c.execute("SELECT owner_today()").fetchone()[0]


@dataclass(frozen=True)
class ArsenalCheck:
    repeated_refs: int
    repeated_words: int
    seeds_dropped: int
    refs_by_verification: dict[str, int]
    words_by_verification: dict[str, int]
    today: date
    today_drop: tuple[int, tuple[int, ...], int] | None


def arsenal_check(*, conn: psycopg.Connection | None = None) -> ArsenalCheck:
    """scripts/arsenal_check.py: the invariants as counts (real rows only)."""
    with _brief_conn(conn) as c:
        rep_refs, rep_words, seeds = c.execute(
            """
            SELECT (SELECT count(*) FROM (SELECT x FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x
                                           WHERE NOT d.is_test GROUP BY x HAVING count(*) > 1) t),
                   (SELECT count(*) FROM (SELECT word_id FROM arsenal_drops WHERE NOT is_test
                                           GROUP BY word_id HAVING count(*) > 1) t),
                   (SELECT count(*) FROM arsenal_drops d CROSS JOIN LATERAL unnest(d.ref_ids) x
                      JOIN arsenal_refs r ON r.id = x WHERE r.origin_kind = 'seed')
            """).fetchone()
        refs = {r[0]: int(r[1]) for r in c.execute(
            "SELECT verification, count(*) FROM arsenal_refs WHERE NOT is_test GROUP BY 1 ORDER BY 1").fetchall()}
        words = {r[0]: int(r[1]) for r in c.execute(
            "SELECT verification, count(*) FROM arsenal_words WHERE NOT is_test GROUP BY 1 ORDER BY 1").fetchall()}
        today = c.execute("SELECT owner_today()").fetchone()[0]
        d = c.execute("SELECT id, ref_ids, word_id FROM arsenal_drops WHERE drop_date = %s AND NOT is_test",
                      (today,)).fetchone()
    drop = None if d is None else (int(d[0]), tuple(int(x) for x in d[1]), int(d[2]))
    return ArsenalCheck(int(rep_refs), int(rep_words), int(seeds), refs, words, today, drop)


@contextmanager
def arsenal_collect_lock(run_id: int) -> Iterator[bool]:
    """A session advisory lock on one run, across processes (the worker's
    collect job and scripts/arsenal_pool --now): only the holder reads the
    batch, so its usage is recorded once. Yields False when another holds it."""
    key = run_id % 2_000_000_000
    with _pool().connection() as c:
        got = bool(c.execute("SELECT pg_try_advisory_lock(hashtext('arsenal_collect'), %s::int)",
                             (key,)).fetchone()[0])
        c.commit()  # never idle in a transaction while the batch is read
        try:
            yield got
        finally:
            if got:
                c.execute("SELECT pg_advisory_unlock(hashtext('arsenal_collect'), %s::int)", (key,))
                c.commit()


def arsenal_ai_cost_since(since: datetime, *, conn: psycopg.Connection | None = None) -> dict[str, Decimal]:
    """purpose -> cost of the Arsenal's ai_usage rows since `since` (scripts)."""
    with _brief_conn(conn) as c:
        return {r[0]: Decimal(r[1]) for r in c.execute(
            "SELECT purpose, sum(cost_usd) FROM ai_usage WHERE purpose LIKE 'arsenal%%' AND created_at >= %s "
            "GROUP BY 1 ORDER BY 1", (since,)).fetchall()}


@dataclass(frozen=True)
class ArsenalDropCard:
    slot: int
    title: str
    category: str
    room: str
    verification: str


@dataclass(frozen=True)
class ArsenalDropDetails:
    drop_id: int
    day: date
    topped_up: bool
    cards: tuple[ArsenalDropCard, ...]
    word: str
    word_verification: str


def arsenal_drop_details(drop_id: int, *, conn: psycopg.Connection | None = None) -> ArsenalDropDetails | None:
    with _brief_conn(conn) as c:
        d = c.execute(
            "SELECT d.id, d.drop_date, d.topped_up, w.lemma, w.verification FROM arsenal_drops d "
            "JOIN arsenal_words w ON w.id = d.word_id WHERE d.id = %s", (drop_id,)).fetchone()
        if d is None:
            return None
        rows = c.execute(
            "SELECT s.ord, r.title, r.category, r.room, r.verification FROM arsenal_drops d "
            "CROSS JOIN LATERAL unnest(d.ref_ids) WITH ORDINALITY AS s(ref_id, ord) "
            "JOIN arsenal_refs r ON r.id = s.ref_id WHERE d.id = %s ORDER BY s.ord", (drop_id,)).fetchall()
    return ArsenalDropDetails(int(d[0]), d[1], bool(d[2]),
                              tuple(ArsenalDropCard(int(r[0]), r[1], r[2], r[3], r[4]) for r in rows), d[3], d[4])


#: The nearest real reference to a candidate, by the same rule as db/054's
#: arsenal_near_dup (seeds, dropped, and pool rows not retired as unverified
#: or rejected). The vector goes in; a key and a cosine come back, never a
#: vector. tests_live runs this exact text against arsenal_near_dup.
ARSENAL_NEAREST_SQL: Final = """
    SELECT r.canonical_key, 1 - extensions.cosine_distance(r.embedding, %(v)s::extensions.halfvec) AS cosine
      FROM arsenal_refs r
     WHERE NOT r.is_test
       AND r.embedding IS NOT NULL
       AND r.status <> 'rejected'
       AND NOT (r.status = 'pool' AND r.verification = 'unverified')
     ORDER BY 2 DESC, r.id
     LIMIT 1
"""


def arsenal_nearest(vector: Sequence[float], *, conn: psycopg.Connection | None = None) -> tuple[str, float] | None:
    """(canonical_key, cosine) of the nearest real reference; None when none is embedded."""
    with _brief_conn(conn) as c:
        row = c.execute(ARSENAL_NEAREST_SQL, {"v": _vec_text(vector)}).fetchone()
    return None if row is None else (row[0], float(row[1]))


def arsenal_run_rows(run_id: int, *, conn: psycopg.Connection | None = None) -> list[dict[str, Any]]:
    """Every arsenal_pool_runs row of one run (its 'submitted' row first)."""
    cols = ("id", "status", "batch_id", "model", "refs_requested", "words_requested", "inserted_refs",
            "inserted_words", "skipped_dup", "skipped_invalid", "verified", "corrected", "unverified", "rejected",
            "cost_usd", "note")
    with _brief_conn(conn) as c:
        rows = c.execute(f"SELECT {', '.join(cols)} FROM arsenal_pool_runs WHERE id = %s OR run_id = %s ORDER BY id",
                         (run_id, run_id)).fetchall()
    return [dict(zip(cols, r)) for r in rows]
