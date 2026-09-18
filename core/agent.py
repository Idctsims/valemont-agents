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
from typing import Any, ClassVar, Sequence

from core import ledger
from core.ledger import (
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
    """

    kind: Kind
    thesis: str
    payload: dict[str, Any]
    resolves_after: datetime
    legs: Sequence[Leg]
    confidence: Numeric | None = None


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
    commitment_id: int | None = None
    error: str | None = None

    @property
    def committed(self) -> bool:
        return self.commitment_id is not None


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """Result of one `resolve_due()`."""

    run_id: int | None
    due: int = 0
    resolved: int = 0
    deferred: int = 0
    voided: int = 0
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

def return_on_risk(
    capital_at_risk: Numeric,
    proceeds: Numeric,
) -> Decimal:
    """Normalized pnl per the CLAUDE.md §9 contract.

        (proceeds - capital_at_risk) / capital_at_risk

    This is arithmetic, not domain knowledge — which is why it can live in
    core without violating "core never computes an outcome". The adapter still
    decides what its capital at risk and proceeds *are*; that part is the
    domain knowledge, and it stays in the adapter.

        long BTC 60000 -> 63000   return_on_risk(60000, 63000)  ->  0.05
        prop slip, 1u stake, miss return_on_risk(1, 0)          -> -1
        YES @ 0.34, settles 1     return_on_risk(0.34, 1)       ->  1.941...

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

    def __init__(self, defer_policy: DeferPolicy | None = None) -> None:
        if not self.slug:
            raise AgentError(
                f"{type(self).__name__} has no slug. Set a class attribute "
                f"matching a row in the agents table."
            )
        # Instance override wins over the class default, so the orchestrator's
        # registry can tune patience without subclassing the adapter.
        self.defer_policy: DeferPolicy = defer_policy or self.default_defer_policy
        self.log = logging.getLogger(f"valemont.{self.slug}")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} slug={self.slug!r}>"

    @property
    def agent_id(self) -> int:
        """This agent's row id. Cached in the ledger; raises if unregistered."""
        return ledger.agent_id(self.slug)

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
    def build_commitment(self, thesis: Th) -> Proposal | None:
        """Turn a thesis into an exact commitment.

        This is the last moment before the record becomes permanent. Prices go
        in as they stand right now — never a price chosen later, because there
        is no later. Return `None` to stand down after all.
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

            proposal = self.build_commitment(thesis)
            if proposal is None:
                self._event("idle", "thesis formed, no commitment", run_id=run_id)
                ledger.end_run(run_id, "ok")
                return RunOutcome(run_id=run_id, status="ok")

            committed = ledger.commit(
                agent_id=agent_id,
                run_id=run_id,
                kind=proposal.kind,
                thesis=proposal.thesis,
                payload=proposal.payload,
                resolves_after=proposal.resolves_after,
                legs=proposal.legs,
                confidence=proposal.confidence,
            )

            self._event(
                "committed",
                proposal.thesis[:500],
                run_id=run_id,
                detail={
                    "commitment_id": committed.id,
                    "kind": proposal.kind,
                    "legs": len(proposal.legs),
                    "resolves_after": committed.resolves_after.isoformat(),
                },
            )
            ledger.end_run(run_id, "ok")
            self.log.info(
                "committed #%s (%s, %d leg(s)) resolving after %s",
                committed.id, proposal.kind, len(proposal.legs),
                committed.resolves_after.isoformat(),
            )
            return RunOutcome(
                run_id=run_id, status="ok", commitment_id=committed.id
            )

        except Exception as exc:
            return RunOutcome(
                run_id=run_id,
                status="error",
                error=self._fail(exc, "run", run_id=run_id, end_run=True),
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

    # -- bounded defer ------------------------------------------------------

    def _note_attempt(
        self,
        commitment: PendingCommitment,
        result: ledger.AttemptResult,
        reason: str,
    ) -> None:
        """Count one unproductive attempt. Never breaks the sweep.

        If this write fails the commitment simply gets asked again next sweep,
        which is the safe direction to fail in — we keep trying rather than
        abandoning something we could have resolved.
        """
        try:
            ledger.record_resolution_attempt(commitment.id, result, reason)
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
