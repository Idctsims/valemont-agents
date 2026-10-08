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

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Sequence

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

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

#: Why an attempt did not produce an answer.
AttemptResult = Literal["deferred", "error"]

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
            kwargs={"application_name": "valemont-agents"},
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
) -> int:
    """Record what happened. One transaction: leg outcomes, then the seal.

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
) -> ClosingSnapshot:
    """Record the market's final opinion. One per commitment, ever.

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

    with _pool().connection() as conn:
        row = conn.execute(
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
    """Create the job's row, or update its interval. Never resets its state."""
    with _pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO job_health (job, expected_interval_s) VALUES (%s, %s)
            ON CONFLICT (job) DO UPDATE SET expected_interval_s = EXCLUDED.expected_interval_s
            """,
            (job, expected_interval_s),
        )


def job_started(job: str) -> None:
    with _pool().connection() as conn:
        conn.execute(
            "UPDATE job_health SET last_started_at = now(), updated_at = now() WHERE job = %s",
            (job,),
        )


def job_succeeded(job: str) -> None:
    with _pool().connection() as conn:
        conn.execute(
            """
            UPDATE job_health
               SET last_ok_at = now(), consecutive_failures = 0, last_error = NULL,
                   updated_at = now()
             WHERE job = %s
            """,
            (job,),
        )


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
              FROM job_health ORDER BY job
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
    """
    with _pool().connection() as conn:
        conn.execute(
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
     ORDER BY c.resolves_after ASC
     LIMIT %(limit)s
"""


def due_for_resolution(
    agent_id: int | None = None,
    limit: int = 100,
    *,
    include_test: bool = True,
) -> list[PendingCommitment]:
    """Commitments past `resolves_after` with no resolution row yet.

    Oldest first, so a backlog drains in the order reality arrived. Legs come
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
     ORDER BY c.closes_at ASC
     LIMIT %(limit)s
"""


def due_for_capture(
    agent_id: int | None = None,
    limit: int = 100,
    *,
    include_test: bool = True,
) -> list[PendingCommitment]:
    """Commitments past their close with no snapshot yet.

    Oldest close first. A missed snapshot is permanent data loss — the close
    happens once — so this drains in the order the closes actually happened,
    giving the oldest and most at-risk the first attempt.

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
