"""BaseAgent — the shared loop. Adapters inherit; they do not reimplement.

    observe() -> form_thesis() -> build_commitment() -> [wait] -> resolve()

Scheduling, persistence, scoring, event emission, retries and error handling
belong here and are written exactly once. An adapter that starts growing its
own version of any of that is a signal the abstraction is wrong.

An adapter implements four methods and nothing else. It never touches SQL,
never calls `emit_event`, never opens a run, and never decides when it runs.
It answers four questions:

    observe()            what is true right now?
    form_thesis(obs)     is there an opportunity in that?
    build_commitment(t)  what exactly am I committing to?
    resolve(pending)     what actually happened?

The design test for this class was Kalshi. An event contract has an entry
price like a paper position AND a binary settlement like a prop, so if any
shape were going to need a fifth hook or a branch on `kind`, it would be that
one. It doesn't: a contract is a position whose exit price only ever lands on
0 or 1, and `Leg(line=0.34, direction='yes')` carries it. If a future domain
needs core to special-case it, the abstraction is wrong — say so rather than
adding the branch.
"""

from __future__ import annotations

import logging
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Final, Literal, Sequence

from core import ledger
from core.ledger import (
    Factor,
    Kind,
    Leg,
    LegOutcome,
    Numeric,
    Outcome,
    PendingCommitment,
)

__all__ = [
    "BaseAgent",
    "Proposal",
    "Verdict",
    "RunOutcome",
    "SweepOutcome",
    "DeferPolicy",
    "AgentError",
    "return_on_risk",
    "declared_risk",
    "directional_return",
    "MIN_STOP_FRACTION",
    "ClosePrice",
    "CloseUnavailable",
    "CaptureOutcome",
    "closing_line_value",
    "MAX_STOP_FRACTION",
]

log = logging.getLogger("valemont.agent")


class AgentError(RuntimeError):
    """Raised when an adapter is wired up wrong. Surfaces at import or boot."""


# ---------------------------------------------------------------------------
# What the hooks hand back
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Proposal:
    """What `build_commitment()` returns: a commitment, not yet written.

    Deliberately the same shape for all four domains. A one-leg paper position
    and a six-leg slip differ only in the length of `legs`.

    `resolves_after` must be timezone-aware and in the future — the database
    rejects anything else, loudly, which is the intent.

    `closes_at` is when this market's opinion becomes final (CLAUDE.md §10).
    Leave it None for a domain with no meaningful close — crypto trades 24/7
    and never has one. Set, it must fall between `committed_at` and
    `resolves_after`, and it commits the adapter to implementing
    `capture_close()` with `captures_close = True`.

    `factors` are the named signed adjustments that produced this thesis
    (§10.1). They land in the same transaction as the commitment, because an
    attribution written afterwards is the revision §2 forbids.
    """

    kind: Kind
    thesis: str
    payload: dict[str, Any]
    resolves_after: datetime
    legs: Sequence[Leg]
    confidence: Numeric | None = None
    closes_at: datetime | None = None
    factors: Sequence[Factor] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    """What `resolve()` returns: what actually happened, as the adapter reads it.

    `outcome` and `pnl` are the adapter's call. Core persists them without
    inspection — see the pnl contract in CLAUDE.md §9, which `pnl` is bound by.
    Use `return_on_risk()` to compute it and put the domain-native figures in
    `detail`.

    `leg_outcomes` are matched by `leg_index`, 0-based, in the order the legs
    were passed to `build_commitment()`.
    """

    outcome: Outcome
    leg_outcomes: Sequence[LegOutcome]
    pnl: Numeric | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of one `run_once()`. Returned for logging, not for control flow."""

    run_id: int
    status: ledger.RunStatus
    #: Every commitment this run wrote, in the order proposed. A single-commitment
    #: adapter writes one; a slate adapter writes many.
    commitment_ids: tuple[int, ...] = ()
    #: Proposals in the slate that could not be written. The rest still were.
    failed: int = 0
    error: str | None = None

    @property
    def committed(self) -> bool:
        return bool(self.commitment_ids)

    @property
    def commitment_id(self) -> int | None:
        """The sole commitment id, when exactly one was written.

        A convenience for adapters that commit one thing per tick — which is
        most of them. Deliberately None for a slate rather than the first id:
        a caller that wants "the" commitment of a forty-row slate is asking a
        question with no answer, and should read `commitment_ids`.
        """
        return self.commitment_ids[0] if len(self.commitment_ids) == 1 else None


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """Result of one `resolve_due()`."""

    run_id: int | None
    due: int = 0
    resolved: int = 0
    deferred: int = 0
    voided: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class ClosePrice:
    """What `capture_close()` returns: the market's final opinion.

    `price` is the price **of the side the commitment holds**, in probability
    units. A NO position reports `1 - yes_price`, normalized by the adapter, so
    the CLV arithmetic never needs to know which side it was.

    `entry_price` may be supplied when the leg's recorded `line` is not already
    in those terms. Left None, the leg's line is used.
    """

    price: Decimal
    entry_price: Decimal | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class CloseUnavailable(RuntimeError):
    """The close for this commitment can never be captured.

    Distinct from returning None (not closed yet) and from raising a feed error
    (try again). This says the moment has passed irrecoverably — the venue does
    not serve historical closes, the market was delisted, the ticker is gone —
    and burning twenty-four retries on it would only delay recording the loss.
    Raising this writes the `missed` tombstone immediately.
    """


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """Result of one `capture_due()`."""

    run_id: int | None
    due: int = 0
    captured: int = 0
    deferred: int = 0
    missed: int = 0
    failed: int = 0


# ---------------------------------------------------------------------------
# Bounded defer
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DeferPolicy:
    """How long to keep asking before giving up on a commitment.

    `resolve()` returning None is the honest answer to "not knowable yet", and
    it has to stay available. But an unresolvable commitment — a postponed
    game that never gets rescheduled, a delisted ticker, a voided market —
    would otherwise sit in the due set forever, and every sweep would get a
    little slower. That leak only shows up weeks in, on the 24/7 process,
    which is the worst place to find one.

    Either threshold trips the void. They are per-agent because patience is
    domain-specific and a shared constant would be wrong for everyone: a
    postponed NFL game is legitimately unresolvable for a week, while a crypto
    feed that hasn't answered in an hour is broken, not slow.

    Attributes:
        max_attempts: Give up after this many sweeps produced no answer.
            Counts errors as well as deferrals — an adapter that raises every
            time is no more resolvable than one that keeps saying "not yet".
        max_overdue: Give up this long past `resolves_after`, however few
            attempts that took. Measured on the database clock.
    """

    max_attempts: int = 12
    max_overdue: timedelta = timedelta(hours=24)

    def expired(self, pending: PendingCommitment) -> str | None:
        """Return the reason to give up, or None to keep waiting."""
        if pending.attempts >= self.max_attempts:
            return (
                f"no resolution after {pending.attempts} attempts "
                f"(cap {self.max_attempts})"
            )
        if pending.overdue_by >= self.max_overdue:
            return (
                f"still unresolved {pending.overdue_by} past resolves_after "
                f"(cap {self.max_overdue})"
            )
        return None


# ---------------------------------------------------------------------------
# The one piece of scoring arithmetic core is allowed to own
# ---------------------------------------------------------------------------

#: Tightest stop a *chosen* invalidation may declare, as a fraction of entry.
#: The denominator is picked by the agent, so it is a thing the agent could
#: shrink to inflate its own multiple — a 0.01% stop would turn an ordinary
#: move into +500R. This is the backstop; the real floor is the adapter's
#: volatility term (see MIN_STOP_ATR_MULT in the adapter), which this cannot
#: express because core does not know what an ATR is.
MIN_STOP_FRACTION: Final = Decimal("0.005")      # 0.5% of entry

#: Widest a chosen invalidation may be. The weaker guard: an absurdly wide stop
#: deflates every result toward zero and conveniently means -1.0 never appears.
#: Declining to be wrong is not the same as being right.
MAX_STOP_FRACTION: Final = Decimal("0.25")       # 25% of entry


def declared_risk(
    entry: Numeric,
    stop: Numeric,
    size: Numeric,
) -> Decimal:
    """`capital_at_risk` per CLAUDE.md §9: |entry - stop| × size.

    One definition, all four domains. A long's stop sits below entry, a
    short's above; a prop's stake and an event contract's price are stops at
    exactly zero, supplied by the instrument rather than chosen.

        long   entry 60000, stop 58200, size 1   ->  1800
        short  entry 60000, stop 61800, size 1   ->  1800
        prop   stake 1,     stop 0,     size 1   ->     1
        YES    price 0.34,  stop 0,     size 100 ->    34

    A `stop` of exactly zero is treated as **structural** — the instrument
    itself goes to zero and there is no free parameter to exploit — so the
    sanity band is not applied. Every other stop is a claim the agent chose
    and is bounded by §9.0.

    Raises:
        AgentError: if the stop equals entry (zero risk is unscoreable), or a
            chosen stop falls outside [MIN_STOP_FRACTION, MAX_STOP_FRACTION]
            of entry. Standing down is the correct response to a stop that
            will not fit — not widening it until it does.
    """
    entry_d = Decimal(str(entry))
    stop_d = Decimal(str(stop))
    distance = abs(entry_d - stop_d)

    if distance == 0:
        raise AgentError(
            "entry equals the invalidation level — risk is zero and the "
            "result is unscoreable. Report pnl=None."
        )
    if entry_d <= 0:
        raise AgentError(f"entry must be positive, got {entry_d}")

    if stop_d != 0:                       # a chosen stop, so bound it
        fraction = distance / entry_d
        if fraction < MIN_STOP_FRACTION:
            raise AgentError(
                f"invalidation is {fraction:.5f} of entry, tighter than the "
                f"{MIN_STOP_FRACTION} floor (§9.0). A stop inside ordinary "
                f"noise inflates the multiple without taking real risk."
            )
        if fraction > MAX_STOP_FRACTION:
            raise AgentError(
                f"invalidation is {fraction:.5f} of entry, wider than the "
                f"{MAX_STOP_FRACTION} ceiling (§9.0). A stop that wide "
                f"deflates every result and means -1.0 never appears."
            )

    return distance * Decimal(str(size))


def _as_slate(
    result: "Proposal | Sequence[Proposal] | None",
) -> tuple["Proposal", ...]:
    """Normalize `build_commitment`'s three return shapes into one.

    `Proposal` is a slots dataclass and not a Sequence, so the isinstance check
    is unambiguous. None and an empty sequence both become () — the caller
    treats them identically, because standing down is standing down.
    """
    if result is None:
        return ()
    if isinstance(result, Proposal):
        return (result,)
    return tuple(result)


def _decimal_or_none(value: Numeric | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def closing_line_value(
    entry_price: Numeric,
    close_price: Numeric,
) -> tuple[Decimal, Decimal]:
    """CLV for a probability-priced market. Returns (clv, clv_pct).

        clv     = close_price - entry_price
        clv_pct = clv / entry_price

    **Both prices are the price of the side we hold.** That single convention
    is what removes the YES/NO branch entirely:

        YES bought at 0.34, YES closes at 0.40  ->  +0.06   market came to us
        NO  bought at 0.66, NO  closes at 0.60  ->  -0.06   market left us

    A NO position is stored with `entry_price = 1 - yes_price`, so the adapter
    normalizes once at commit time and nothing downstream has to know which
    side it was. **Positive always means the market moved toward our view.**

    Why this is worth more than hit rate: it produces a measurement on every
    commitment, won or lost. Hit rate needs hundreds of resolutions before it
    says anything; CLV says something on the first one, which is the difference
    between signal in weeks and signal in a season.

    Why `clv_pct` too: six points of edge on a 0.10 contract is a different
    achievement from six points on a 0.80 one, and the absolute number cannot
    tell them apart.

    Raises:
        AgentError: on a non-positive or out-of-range probability. A price
            outside (0, 1] is not a probability, and silently scoring it would
            put a meaningless number in the primary metric.
    """
    entry = Decimal(str(entry_price))
    close = Decimal(str(close_price))

    for label, value in (("entry", entry), ("close", close)):
        if not value.is_finite():
            raise AgentError(f"{label} price is {value}")
        if value < 0 or value > 1:
            raise AgentError(
                f"{label} price {value} is outside [0, 1] — CLV is defined "
                f"for probability-priced markets, and a price this is not."
            )

    # The bounds are asymmetric on purpose. A CLOSE of exactly 0 is a real and
    # meaningful close: the market wrote our side off completely, which is the
    # most negative CLV there is. An ENTRY of 0 is not — it is the divisor, and
    # a position acquired for nothing has no line to have beaten.
    if entry == 0:
        raise AgentError(
            "entry price is 0 — clv_pct is undefined, and a position taken at "
            "zero cost has no line to beat."
        )

    clv = close - entry
    return clv, clv / entry


def return_on_risk(
    capital_at_risk: Numeric,
    proceeds: Numeric,
) -> Decimal:
    """Normalized pnl per the CLAUDE.md §9 contract.

        (proceeds - capital_at_risk) / capital_at_risk

    Arithmetic, not domain knowledge — which is why it can live in core
    without violating "core never computes an outcome". The adapter still
    decides what its risk and proceeds *are*; that part stays in the adapter.

        prop slip, 1u stake, 3x hit   return_on_risk(1, 3)     ->  2
        prop slip, 1u stake, miss     return_on_risk(1, 0)     -> -1
        YES @ 0.34, settles 1         return_on_risk(0.34, 1)  ->  1.941...

    Directional positions should use `directional_return`, which applies the
    same definition without the caller having to assemble `proceeds` by hand.

    Raises:
        AgentError: if `capital_at_risk` is zero. A commitment that risked
            nothing has no return on risk; the adapter should pass pnl=None.
    """
    risk = Decimal(str(capital_at_risk))
    if risk == 0:
        raise AgentError(
            "capital_at_risk is zero — return on risk is undefined. If the "
            "commitment genuinely risked nothing, report pnl=None."
        )
    return (Decimal(str(proceeds)) - risk) / risk


def directional_return(
    entry: Numeric,
    stop: Numeric,
    exit_price: Numeric,
    direction: Literal["long", "short"],
) -> Decimal:
    """R-multiple for a long or a short. §9's table, in one call.

        long   (exit - entry) / (entry - stop)
        short  (entry - exit) / (stop - entry)

    Long and short are the same expression with a sign, which is the whole
    point of the §9 rewrite — the same favorable move scores the same in both
    directions instead of differing by an order of magnitude.

        long  60000, stop 58200, exit 61800  ->  +1.00
        short 60000, stop 61800, exit 58200  ->  +1.00
        long  60000, stop 58200, exit 58200  ->  -1.00   (stopped out)

    `size` cancels out of the ratio, so it is not a parameter here. It still
    belongs on the leg, because a size-weighted metric will want it later.

    The caller is responsible for having already truncated `exit_price` at the
    stop when the stop was hit during the holding period (§9.1). This function
    will happily return a number below -1.0 if handed an exit beyond the stop,
    because silently clamping would hide exactly the modeling gap §9.1 asks to
    be made visible.
    """
    entry_d = Decimal(str(entry))
    stop_d = Decimal(str(stop))
    exit_d = Decimal(str(exit_price))

    # Validates the stop band and raises if the declaration is unusable.
    risk_per_unit = declared_risk(entry_d, stop_d, 1)

    if direction == "long":
        if stop_d >= entry_d:
            raise AgentError(
                f"a long's invalidation must sit below entry "
                f"(entry={entry_d}, stop={stop_d})"
            )
        move = exit_d - entry_d
    else:
        if stop_d <= entry_d:
            raise AgentError(
                f"a short's invalidation must sit above entry "
                f"(entry={entry_d}, stop={stop_d})"
            )
        move = entry_d - exit_d

    return move / risk_per_unit


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent[Obs, Th](ABC):
    """The observe/thesis/commit/resolve loop, written once.

    Subclasses set `slug` to match a row in the `agents` table and implement
    the four hooks. Everything else here is final in spirit: overriding
    `run_once` or `resolve_due` means core isn't doing its job.

    `Obs` and `Th` are whatever the adapter finds convenient — core never
    inspects them, it only passes them between hooks.
    """

    #: Must match `agents.slug`. Checked against the database at first use.
    slug: ClassVar[str] = ""

    #: How many due commitments one sweep will attempt. Keeps a backlog from
    #: monopolizing the scheduler thread.
    sweep_limit: ClassVar[int] = 50

    #: When to stop asking. Override per adapter — the default is a compromise
    #: that suits nothing exactly. See `DeferPolicy`.
    default_defer_policy: ClassVar[DeferPolicy] = DeferPolicy()

    #: Most commitments one tick may write. A cap rather than a guideline: over
    #: it, the whole slate is refused and NOTHING is written, because half a
    #: slate looks exactly like a complete one and the rows cannot be deleted.
    #:
    #: 25 is deliberately lower than a real prop slate needs. An adapter that
    #: genuinely commits a board must raise it explicitly, the same way
    #: `max_overdue` must be set deliberately (CLAUDE.md §9.3) — a cap nobody
    #: chose is a cap that will be wrong for somebody.
    max_slate_size: ClassVar[int] = 25

    #: Does this domain have a market close worth snapshotting? False by
    #: default, so the four-hook contract holds untouched for every adapter
    #: that does not opt in. Setting it True obliges `capture_close()` and
    #: means commitments should carry `closes_at`.
    captures_close: ClassVar[bool] = False

    #: Patience for the capture sweep. Deliberately more generous than
    #: `default_defer_policy`: a missed resolution can be retried until it
    #: voids, but a missed close is permanent data loss — the close happens
    #: once, and there is no second chance to look.
    default_capture_policy: ClassVar[DeferPolicy] = DeferPolicy(
        max_attempts=48, max_overdue=timedelta(hours=48)
    )

    def __init__(
        self,
        defer_policy: DeferPolicy | None = None,
        capture_policy: DeferPolicy | None = None,
        max_slate_size: int | None = None,
    ) -> None:
        if not self.slug:
            raise AgentError(
                f"{type(self).__name__} has no slug. Set a class attribute "
                f"matching a row in the agents table."
            )
        # Instance override wins over the class default, so the orchestrator's
        # registry can tune patience without subclassing the adapter.
        self.defer_policy: DeferPolicy = defer_policy or self.default_defer_policy
        self.capture_policy: DeferPolicy = (
            capture_policy or self.default_capture_policy
        )
        self.slate_cap: int = (
            max_slate_size if max_slate_size is not None else self.max_slate_size
        )
        self.log = logging.getLogger(f"valemont.{self.slug}")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} slug={self.slug!r}>"

    @property
    def agent_id(self) -> int:
        """This agent's row id. Cached in the ledger; raises if unregistered."""
        return ledger.agent_id(self.slug)

    def open_commitments(self, limit: int = 200) -> list[PendingCommitment]:
        """This agent's unresolved commitments, due or not.

        Available to `form_thesis()` so an adapter can avoid committing twice
        to the same thing. Without it, an agent re-enters an identical position
        on every tick until the first one resolves — it cannot see what it is
        already holding, because a position opened ten minutes ago with a
        six-hour horizon is not yet due.

        A helper rather than a fifth hook: the four-hook contract is what makes
        adapters interchangeable, and this is a read an adapter *may* want, not
        a stage every adapter must implement. It still writes no SQL.
        """
        return ledger.open_commitments(agent_id=self.agent_id, limit=limit)

    def open_subjects(self, limit: int = 200) -> set[str]:
        """The `leg.subject` values this agent currently has open.

        The common case of `open_commitments()` — "am I already in BTC-USD?" —
        without every adapter writing the same comprehension.

        **Read it once, at the top of a run.** It is a database round trip and
        the answer does not change mid-run; forty reads for a forty-proposal
        slate would be wasteful and no more correct.

        **It does not include the slate you are about to propose.** This is the
        sharp edge, and it is deliberate rather than fixable here: whether two
        legs on the same subject are the same position is domain knowledge. A
        player's passing yards and his passing touchdowns share a subject and
        are different markets; two entries on one crypto pair share a subject
        and are the same position. Core refuses only *exact* duplicates —
        identical subject, market, direction and line (see `build_commitment`).
        Anything narrower than that is the adapter's judgement to make.
        """
        return {
            leg.subject
            for commitment in self.open_commitments(limit)
            for leg in commitment.legs
        }

    # -- the four hooks -----------------------------------------------------

    @abstractmethod
    def observe(self) -> Obs:
        """Fetch domain data. What is true right now?

        Return whatever shape suits the domain. Core does not look inside it.
        Raising is fine — the run is marked errored and the traceback logged.
        """

    @abstractmethod
    def form_thesis(self, observation: Obs) -> Th | None:
        """Reason about the observation.

        Return `None` when there is nothing worth committing to. That is a
        normal outcome, not a failure: the run ends `ok` with an `idle` event.
        An agent that commits on every wake-up is not being selective.
        """

    @abstractmethod
    def build_commitment(
        self, thesis: Th
    ) -> Proposal | Sequence[Proposal] | None:
        """Turn a thesis into one commitment, or a slate of them.

        This is the last moment before the record becomes permanent. Prices go
        in as they stand right now — never a price chosen later, because there
        is no later.

        Three return shapes:

            Proposal              one commitment. The common case.
            Sequence[Proposal]    a slate — forty independent player props on a
                                  Sunday are forty independent commitments, and
                                  one-per-tick would either lose thirty-nine or
                                  need forty ticks.
            None or ()            stand down after all. Both are the same
                                  outcome: an `idle` run, not a failure.

        A slate is committed **one proposal at a time, each independently**: a
        bad proposal at index 3 does not strand the other thirty-nine. Failures
        are counted, logged, and the run ends `error`, but every writable
        commitment is written. Same discipline as the resolution sweep.

        Two things core checks before writing anything:

        *   **`max_slate_size`.** A runaway adapter proposing five hundred
            commitments would write five hundred permanent rows. Over the cap,
            the WHOLE slate is refused and nothing is written — half a slate is
            worse than none, because it looks like a complete one.
        *   **Exact duplicates.** Two legs identical in subject, market,
            direction AND line inside one slate is the adapter proposing the
            same bet twice. Refused whole.

            Core does NOT dedupe by subject alone. Two props on one player —
            his passing yards and his passing touchdowns — are legitimately
            different markets, and a ladder on the same market at two different
            lines is legitimate too. Deciding what counts as the same position
            is domain knowledge, and core does not have it.
        """

    @abstractmethod
    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        """Determine what actually happened, and score it.

        `pending` carries the thesis, payload and legs exactly as committed, so
        no database access is needed here.

        Return `None` when the outcome is not knowable yet — a postponed game,
        a feed that hasn't updated, a market that hasn't settled. No resolution
        row is written and the commitment stays in the due set for the next
        sweep. That is the honest answer, and it is better than guessing.

        Deferring is **bounded**. After `defer_policy.max_attempts` or
        `max_overdue`, core abandons the commitment as void with pnl NULL and
        stops calling this method for it. Deferring forever is not an option
        the adapter has, so don't rely on it as a way to skip hard cases.

        `Verdict.pnl` is bound by the pnl contract — CLAUDE.md §9. Note in
        particular that a paper short with no declared invalidation level has
        undefined risk: report `pnl=None`, never a clamped number.
        """

    # -- the optional fifth hook -------------------------------------------

    def capture_close(self, pending: PendingCommitment) -> ClosePrice | None:
        """Price the market at its close. **Opt-in; not one of the four.**

        Only called for agents with `captures_close = True`. A domain with no
        meaningful close — crypto trades 24/7, there is no moment its opinion
        is final — leaves the flag False and never implements this. That is the
        clean opt-out: no stub, no `raise NotImplementedError` in production, no
        `closes_at` on its commitments, and no capture job scheduled for it.

        Return the price **of the side the commitment holds**, in probability
        units, so the CLV arithmetic never branches on YES vs NO.

        Three ways to not answer, and the difference matters:

            return None            not closed yet. Retry. This is how a
                                   postponed game is handled — `closes_at` is
                                   the earliest moment worth looking, not a
                                   promise the market closed then.
            raise FeedError etc.   transient. Retry, counted against the cap.
            raise CloseUnavailable permanent. Write the `missed` tombstone now
                                   rather than burning the whole retry budget
                                   on something that will never arrive.

        Never guess a close. A fabricated closing price corrupts the primary
        metric in a way that is undetectable afterwards — unlike a missing one,
        which is visible as a `missed` row.
        """
        raise NotImplementedError(
            f"{type(self).__name__} sets captures_close=True but does not "
            f"implement capture_close()."
        )

    # -- the cycle ----------------------------------------------------------

    def run_once(self) -> RunOutcome:
        """One wake-up: observe, think, and either commit or stand down.

        Always closes its run. Exceptions from a hook are caught, logged with
        a full traceback, written to `runs.error` and emitted as an `error`
        event — then swallowed, because the scheduler must survive a bad tick.
        Swallowed is not silent: three separate places record it.
        """
        agent_id = self.agent_id
        run_id = ledger.start_run(agent_id)
        self._event("woke", run_id=run_id)

        try:
            self._event("observing", run_id=run_id)
            observation = self.observe()

            thesis = self.form_thesis(observation)
            if thesis is None:
                self._event("idle", "no thesis this tick", run_id=run_id)
                ledger.end_run(run_id, "ok")
                return RunOutcome(run_id=run_id, status="ok")

            self._event("thesis", str(thesis)[:500], run_id=run_id)

            slate = _as_slate(self.build_commitment(thesis))
            if not slate:
                # None and () are the same outcome. An adapter that looked at a
                # forty-leg board and liked none of it stood down, exactly like
                # one that had a single idea and dropped it.
                self._event("idle", "thesis formed, no commitment", run_id=run_id)
                ledger.end_run(run_id, "ok")
                return RunOutcome(run_id=run_id, status="ok")

            # Both refusals happen BEFORE the first write, so a rejected slate
            # leaves no trace in `commitments` at all.
            self._refuse_bad_slate(slate)

            written: list[int] = []
            failed = 0
            for index, proposal in enumerate(slate):
                try:
                    written.append(self._write(proposal, agent_id, run_id))
                except Exception as exc:
                    failed += 1
                    # One bad proposal must not strand the rest of the slate.
                    self._fail(
                        exc, f"commitment {index + 1} of {len(slate)}",
                        run_id=run_id,
                        detail={"slate_index": index, "kind": proposal.kind},
                    )

            self._announce(slate, written, failed, run_id=run_id)

            status: ledger.RunStatus = "error" if failed else "ok"
            ledger.end_run(
                run_id, status,
                error=(
                    f"{failed} of {len(slate)} proposals failed to commit"
                    if failed else None
                ),
            )
            return RunOutcome(
                run_id=run_id,
                status=status,
                commitment_ids=tuple(written),
                failed=failed,
            )

        except Exception as exc:
            return RunOutcome(
                run_id=run_id,
                status="error",
                error=self._fail(exc, "run", run_id=run_id, end_run=True),
            )

    # -- slate plumbing -----------------------------------------------------

    def _refuse_bad_slate(self, slate: Sequence[Proposal]) -> None:
        """Reject a whole slate before anything is written, or return silently.

        Raises rather than writing a partial slate. Commitments cannot be
        deleted, so half a slate is permanently indistinguishable from a
        complete one — which makes it worse than no slate at all.
        """
        if len(slate) > self.slate_cap:
            raise AgentError(
                f"{self.slug} proposed {len(slate)} commitments in one tick, "
                f"over its max_slate_size of {self.slate_cap}. Refusing "
                f"the WHOLE slate: these rows cannot be deleted, and a "
                f"half-written slate is indistinguishable from a complete one. "
                f"Raise max_slate_size deliberately if the board is really "
                f"this big."
            )

        seen: dict[tuple[str, str, str | None, str | None], int] = {}
        for index, proposal in enumerate(slate):
            for leg in proposal.legs:
                # The line compares NUMERICALLY, not as text. `str(Decimal(...))`
                # preserves trailing zeros, so 25.5 and 25.50 produced different
                # keys and two permanent rows were written for one position.
                # Decimal equality ignores scale and Python guarantees equal
                # numbers hash equal, so the bare Decimal is a correct dict key.
                identity = (
                    leg.subject,
                    leg.market,
                    leg.direction,
                    None if leg.line is None else Decimal(str(leg.line)),
                )
                if identity in seen:
                    raise AgentError(
                        f"{self.slug} proposed the same leg twice in one slate: "
                        f"{leg.subject!r}/{leg.market!r}/{leg.direction!r} at "
                        f"line {leg.line} appears in proposals "
                        f"{seen[identity] + 1} and {index + 1}. Refusing the "
                        f"whole slate — two identical bets is a bug, not a "
                        f"position. (Same subject on a DIFFERENT market or "
                        f"line is fine and is not what this checks.)"
                    )
                seen[identity] = index

    def _write(self, proposal: Proposal, agent_id: int, run_id: int) -> int:
        """Persist one proposal. Raises on failure; the caller isolates it."""
        committed = ledger.commit(
            agent_id=agent_id,
            run_id=run_id,
            kind=proposal.kind,
            thesis=proposal.thesis,
            payload=proposal.payload,
            resolves_after=proposal.resolves_after,
            legs=proposal.legs,
            confidence=proposal.confidence,
            closes_at=proposal.closes_at,
            factors=proposal.factors,
        )
        self.log.info(
            "committed #%s (%s, %d leg(s)) resolving after %s",
            committed.id, proposal.kind, len(proposal.legs),
            committed.resolves_after.isoformat(),
        )
        return committed.id

    def _announce(
        self,
        slate: Sequence[Proposal],
        written: Sequence[int],
        failed: int,
        *,
        run_id: int,
    ) -> None:
        """Emit one event for the run, whatever the slate's size.

        A forty-commitment slate emits ONE row, not forty. The event stream is
        the activity feed; it is not a second index of `commitments`. Per-row
        traceability already exists and is exact: every commitment carries this
        `run_id`, so `SELECT * FROM commitments WHERE run_id = N` recovers the
        slate precisely.

        A slate uses a DIFFERENT event kind on purpose. Reusing `committed`
        would let a consumer that counts `committed` rows silently undercount a
        forty-row slate as one. With `slate_committed`, a consumer that does not
        know the kind sees nothing instead of a wrong number — the same choice
        as the `missed` close tombstone (§10). Absence is detectable; a quietly
        wrong count is not.
        """
        if not written and failed:
            # Nothing landed. The per-failure error events already said why.
            return

        if len(slate) == 1 and not failed:
            # Unchanged from before slates existed, byte for byte, so a
            # single-commitment adapter's event stream does not move.
            proposal = slate[0]
            self._event(
                "committed",
                proposal.thesis[:500],
                run_id=run_id,
                detail={
                    "commitment_id": written[0],
                    "kind": proposal.kind,
                    "legs": len(proposal.legs),
                    "resolves_after": proposal.resolves_after.isoformat(),
                    "closes_at": (
                        None if proposal.closes_at is None
                        else proposal.closes_at.isoformat()
                    ),
                    "factors": {f.name: str(f.value) for f in proposal.factors},
                },
            )
            return

        kinds: dict[str, int] = {}
        for proposal in slate:
            kinds[proposal.kind] = kinds.get(proposal.kind, 0) + 1
        self._event(
            "slate_committed",
            f"{len(written)} of {len(slate)} committed",
            run_id=run_id,
            detail={
                "commitment_ids": list(written),
                "committed": len(written),
                "proposed": len(slate),
                "failed": failed,
                "kinds": kinds,
            },
        )
        self.log.info(
            "slate: %d of %d committed (%s)%s",
            len(written), len(slate),
            ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())),
            f", {failed} FAILED" if failed else "",
        )

    def resolve_due(self, limit: int | None = None) -> SweepOutcome:
        """Resolve every commitment of this agent that reality has caught up to.

        One run covers the whole sweep, but each commitment is attempted
        independently: a single adapter blowing up on one row must not strand
        the rest. A commitment whose `resolve()` returns `None` is left in the
        due set for next time.

        Opens no run at all when nothing is due, which keeps `runs` from
        filling with empty rows on a fast sweep interval.
        """
        agent_id = self.agent_id
        pending = ledger.due_for_resolution(
            agent_id=agent_id, limit=limit or self.sweep_limit
        )
        if not pending:
            return SweepOutcome(run_id=None)

        run_id = ledger.start_run(agent_id, notes="resolution sweep")
        self._event(
            "resolving", f"{len(pending)} due", run_id=run_id,
            detail={"due": len(pending)},
        )

        resolved = deferred = voided = failed = 0
        for commitment in pending:
            # Patience is checked BEFORE asking again. A commitment that has
            # already exhausted its budget should not get one more call into an
            # adapter that has never answered for it.
            give_up = self.defer_policy.expired(commitment)
            if give_up is not None:
                if self._abandon(commitment, give_up, run_id=run_id):
                    voided += 1
                else:
                    failed += 1
                continue

            try:
                verdict = self.resolve(commitment)
            except Exception as exc:
                failed += 1
                self._note_attempt(commitment, "error", f"{type(exc).__name__}: {exc}")
                self._fail(
                    exc, f"resolve of commitment {commitment.id}",
                    run_id=run_id, detail={"commitment_id": commitment.id},
                )
                continue

            if verdict is None:
                deferred += 1
                self._note_attempt(commitment, "deferred", "resolve() returned None")
                self.log.info(
                    "commitment #%s not yet knowable, deferring "
                    "(attempt %d/%d, %s past due)",
                    commitment.id, commitment.attempts + 1,
                    self.defer_policy.max_attempts, commitment.overdue_by,
                )
                continue

            try:
                ledger.add_resolution(
                    commitment_id=commitment.id,
                    outcome=verdict.outcome,
                    leg_outcomes=verdict.leg_outcomes,
                    pnl=verdict.pnl,
                    detail=verdict.detail,
                )
            except Exception as exc:
                failed += 1
                self._fail(
                    exc, f"writing resolution for {commitment.id}",
                    run_id=run_id, detail={"commitment_id": commitment.id},
                )
                continue

            resolved += 1
            self._event(
                "resolved",
                f"commitment {commitment.id}: {verdict.outcome}",
                run_id=run_id,
                detail={
                    "commitment_id": commitment.id,
                    "outcome": verdict.outcome,
                    "pnl": None if verdict.pnl is None else str(verdict.pnl),
                },
            )
            self.log.info(
                "resolved #%s %s (pnl=%s)",
                commitment.id, verdict.outcome, verdict.pnl,
            )

        status: ledger.RunStatus = "error" if failed else "ok"
        ledger.end_run(
            run_id,
            status,
            error=f"{failed} of {len(pending)} failed to resolve" if failed else None,
        )
        if voided:
            # Loud on purpose. Voids are not a normal steady-state outcome; a
            # rising rate means an adapter's resolve() has stopped working and
            # the track record is quietly filling with unscored rows.
            self.log.warning(
                "%s ABANDONED %d commitment(s) this sweep — if this is not a "
                "one-off, resolve() is broken, not merely slow",
                self.slug, voided,
            )
        return SweepOutcome(
            run_id=run_id,
            due=len(pending),
            resolved=resolved,
            deferred=deferred,
            voided=voided,
            failed=failed,
        )

    def capture_due(self, limit: int | None = None) -> CaptureOutcome:
        """Snapshot the close for every commitment whose market has closed.

        Mirrors `resolve_due()` deliberately — same run, same per-commitment
        isolation, same bounded patience — but the failure semantics differ in
        one way that matters. A missed resolution can be retried until it
        voids; **a missed close is permanent data loss.** The close happens
        once. So this fails toward retry at every opportunity: a failure to
        record an *attempt* still leaves the commitment in the due set, and
        only an exhausted budget or an explicit `CloseUnavailable` writes the
        `missed` tombstone that stops us asking.

        Returns an empty outcome without opening a run for agents that do not
        opt in, so a `captures_close=False` adapter costs nothing.
        """
        if not self.captures_close:
            return CaptureOutcome(run_id=None)

        agent_id = self.agent_id
        pending = ledger.due_for_capture(
            agent_id=agent_id, limit=limit or self.sweep_limit
        )
        if not pending:
            return CaptureOutcome(run_id=None)

        run_id = ledger.start_run(agent_id, notes="close capture")
        self._event(
            "capturing", f"{len(pending)} due", run_id=run_id,
            detail={"due": len(pending)},
        )

        captured = deferred = missed = failed = 0
        for commitment in pending:
            give_up = self.capture_policy.expired(commitment)
            if give_up is not None:
                if self._miss(commitment, give_up, run_id=run_id):
                    missed += 1
                else:
                    failed += 1
                continue

            try:
                close = self.capture_close(commitment)
            except CloseUnavailable as exc:
                # Permanent by the adapter's own assessment. Record it now
                # rather than spending the budget discovering the same thing.
                if self._miss(commitment, f"unavailable: {exc}", run_id=run_id):
                    missed += 1
                else:
                    failed += 1
                continue
            except Exception as exc:
                failed += 1
                self._note_attempt(
                    commitment, "error", f"{type(exc).__name__}: {exc}",
                    purpose="capture",
                )
                self._fail(
                    exc, f"capture of commitment {commitment.id}",
                    run_id=run_id, detail={"commitment_id": commitment.id},
                )
                continue

            if close is None:
                deferred += 1
                self._note_attempt(
                    commitment, "deferred", "market not closed yet",
                    purpose="capture",
                )
                self.log.info(
                    "commitment #%s market still open, deferring "
                    "(attempt %d/%d, %s past declared close)",
                    commitment.id, commitment.attempts + 1,
                    self.capture_policy.max_attempts, commitment.overdue_by,
                )
                continue

            entry = close.entry_price
            if entry is None and commitment.legs:
                entry = _decimal_or_none(commitment.legs[0].line)
            if entry is None:
                failed += 1
                self._fail(
                    AgentError(
                        f"commitment {commitment.id} has no entry price to "
                        f"compare against — CLV is undefined"
                    ),
                    f"capture of commitment {commitment.id}",
                    run_id=run_id, detail={"commitment_id": commitment.id},
                )
                continue

            try:
                clv, clv_pct = closing_line_value(entry, close.price)
                snapshot = ledger.add_closing_snapshot(
                    commitment_id=commitment.id,
                    entry_price=entry,
                    close_price=close.price,
                    clv=clv,
                    clv_pct=clv_pct,
                    detail={**close.detail, "captured_by": self.slug},
                )
            except Exception as exc:
                failed += 1
                self._note_attempt(
                    commitment, "error", f"{type(exc).__name__}: {exc}",
                    purpose="capture",
                )
                self._fail(
                    exc, f"writing the close snapshot for {commitment.id}",
                    run_id=run_id, detail={"commitment_id": commitment.id},
                )
                continue

            captured += 1
            self._event(
                "captured",
                f"commitment {commitment.id}: clv {clv:+}",
                run_id=run_id,
                detail={
                    "commitment_id": commitment.id,
                    "snapshot_id": snapshot.id,
                    "entry_price": str(entry),
                    "close_price": str(close.price),
                    "clv": str(clv),
                    "clv_pct": str(clv_pct),
                },
            )
            self.log.info(
                "captured #%s close=%s entry=%s clv=%+s",
                commitment.id, close.price, entry, clv,
            )

        status: ledger.RunStatus = "error" if failed else "ok"
        ledger.end_run(
            run_id, status,
            error=f"{failed} of {len(pending)} captures failed" if failed else None,
        )
        if missed:
            # Louder than a void. A void means we could not learn the outcome;
            # a missed close means we permanently lost a measurement that was
            # available for a moment and never will be again.
            self.log.warning(
                "%s PERMANENTLY MISSED the close on %d commitment(s) — that "
                "measurement cannot be recovered; check capture scheduling "
                "before this becomes a pattern",
                self.slug, missed,
            )
        return CaptureOutcome(
            run_id=run_id, due=len(pending), captured=captured,
            deferred=deferred, missed=missed, failed=failed,
        )

    def _miss(
        self,
        commitment: PendingCommitment,
        reason: str,
        *,
        run_id: int,
    ) -> bool:
        """Write the tombstone that records a permanently lost close."""
        self.log.warning(
            "giving up on the close for commitment #%s: %s", commitment.id, reason
        )
        try:
            ledger.add_closing_snapshot(
                commitment_id=commitment.id,
                status="missed",
                reason=reason,
                detail={
                    "attempts": commitment.attempts,
                    "overdue_by_seconds": commitment.overdue_by.total_seconds(),
                    "policy": {
                        "max_attempts": self.capture_policy.max_attempts,
                        "max_overdue_seconds":
                            self.capture_policy.max_overdue.total_seconds(),
                    },
                },
            )
        except Exception as exc:
            self._fail(
                exc, f"recording the missed close for {commitment.id}",
                run_id=run_id, detail={"commitment_id": commitment.id},
            )
            return False

        self._event(
            "close_missed",
            f"commitment {commitment.id}: {reason}",
            run_id=run_id,
            detail={"commitment_id": commitment.id, "reason": reason},
        )
        return True

    # -- bounded defer ------------------------------------------------------

    def _note_attempt(
        self,
        commitment: PendingCommitment,
        result: ledger.AttemptResult,
        reason: str,
        purpose: ledger.AttemptPurpose = "resolve",
    ) -> None:
        """Count one unproductive attempt. Never breaks the sweep.

        If this write fails the commitment simply gets asked again next sweep,
        which is the safe direction to fail in — we keep trying rather than
        abandoning something we could have resolved.
        """
        try:
            ledger.record_resolution_attempt(
                commitment.id, result, reason, purpose=purpose
            )
        except Exception:
            self.log.error(
                "could not record resolution attempt for #%s — it will be "
                "retried, not abandoned", commitment.id, exc_info=True,
            )

    def _abandon(
        self,
        commitment: PendingCommitment,
        reason: str,
        *,
        run_id: int,
    ) -> bool:
        """Close an unresolvable commitment: void, pnl NULL, reason recorded.

        Core writes this one, not the adapter — it is not a domain judgement
        about what happened, it is a statement that we stopped asking. `pnl` is
        NULL rather than 0 because nothing was scored; per CLAUDE.md §9 a NULL
        must never be counted as a break-even. Every leg is voided too, so the
        dashboard doesn't show half-resolved rows.

        Returns True if the void was written.
        """
        self.log.warning(
            "abandoning commitment #%s: %s — thesis was: %s",
            commitment.id, reason, commitment.thesis[:200],
        )
        try:
            ledger.add_resolution(
                commitment_id=commitment.id,
                outcome="void",
                leg_outcomes=[
                    LegOutcome(leg_index=i, outcome="void", actual=None)
                    for i in range(len(commitment.legs))
                ],
                pnl=None,
                detail={
                    "abandoned": True,
                    "reason": reason,
                    "attempts": commitment.attempts,
                    "overdue_by_seconds": commitment.overdue_by.total_seconds(),
                    "policy": {
                        "max_attempts": self.defer_policy.max_attempts,
                        "max_overdue_seconds":
                            self.defer_policy.max_overdue.total_seconds(),
                    },
                },
            )
        except Exception as exc:
            # Could not even give up cleanly. Leave it in the due set — the
            # next sweep will try again — but make the noise now.
            self._fail(
                exc, f"abandoning commitment {commitment.id}",
                run_id=run_id, detail={"commitment_id": commitment.id},
            )
            return False

        self._event(
            "voided",
            f"commitment {commitment.id} abandoned: {reason}",
            run_id=run_id,
            detail={
                "commitment_id": commitment.id,
                "reason": reason,
                "attempts": commitment.attempts,
            },
        )
        return True

    # -- plumbing -----------------------------------------------------------

    def _event(
        self,
        kind: ledger.EventKind,
        message: str | None = None,
        *,
        run_id: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event, and never let the emission itself break a run.

        The stream is how we see what happened, but losing one row is not worth
        aborting a real commitment over. A failure here is logged at ERROR and
        the cycle continues.
        """
        try:
            ledger.emit_event(
                kind, message, agent_id=self.agent_id,
                run_id=run_id, detail=detail,
            )
        except Exception:
            self.log.error(
                "could not emit %r event (run %s) — continuing", kind, run_id,
                exc_info=True,
            )

    def _fail(
        self,
        exc: BaseException,
        what: str,
        *,
        run_id: int | None = None,
        end_run: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> str:
        """Record a failure in all three places, then let the caller continue.

        A silent exception in a worker that runs at 3am is the single most
        likely way this project quietly dies, so: stderr traceback, an `error`
        event, and `runs.error`. Each of those three writes is independently
        guarded — if the database is the thing that's broken, the log still
        gets it.

        `end_run` is False for a failure inside a sweep: one bad commitment
        does not close the run, because the remaining ones still get their
        turn. The sweep closes its own run once, at the end.
        """
        summary = f"{type(exc).__name__}: {exc}"
        self.log.error("%s failed — %s", what, summary, exc_info=exc)

        self._event(
            "error", f"{what} failed: {summary}"[:1000],
            run_id=run_id,
            detail={
                **(detail or {}),
                "exception": type(exc).__name__,
                "traceback": "".join(
                    traceback.format_exception(exc)
                )[-4000:],
            },
        )

        if end_run and run_id is not None:
            try:
                ledger.end_run(run_id, "error", error=summary[:2000])
            except Exception:
                self.log.error(
                    "could not mark run %s errored", run_id, exc_info=True
                )

        return summary
