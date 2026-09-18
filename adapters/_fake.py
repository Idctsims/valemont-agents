"""Throwaway harness agent. Proves the core loop against the real schema.

This exists to answer one question before any real adapter is written: does
observe -> commit -> wait -> resolve -> score actually work end to end, against
real Postgres, with the append-only triggers armed? It invents commitments so
that answer doesn't depend on a market being open or an API being up.

It is **deterministic**, deliberately. A random harness proves the pipeline
runs; a scripted one lets you predict every row before you run it and notice
when a row is missing. The script covers all four commitment shapes and all
four resolution paths:

    #1  paper_position  long BTC          resolves first sweep      hit,  +0.05
    #2  prop_slip       two legs          defers once, then hits     hit,  +2.00
    #3  event_contract  YES @ 0.34        resolves first sweep      miss, -1.00
    #4  paper_position  short + stop      NEVER resolves            void,  NULL

#4 is the point of the exercise. It never becomes knowable, so the bounded
defer in `core.agent` has to notice and close it out — the failure mode that
would otherwise leak a growing due-set for weeks before anyone saw it.

#3 is the Kalshi shape, committed and scored through the same path as the
others with no special-casing, which is the design claim this whole phase
rests on.

#4 also carries a declared invalidation level in its payload, because
CLAUDE.md §9 requires one for a paper short. It is never scored here — it
voids — but the shape is the one a real short must use.

SAFETY: this agent refuses to run unless its row has `is_test = true`. Test
rows cannot be deleted, so pointing it at a real slug would permanently
contaminate the track record. The check happens before the first write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar, Final, Sequence

from core import ledger
from core.agent import BaseAgent, DeferPolicy, Proposal, Verdict, return_on_risk
from core.ledger import Kind, Leg, LegOutcome, Numeric, Outcome, PendingCommitment

__all__ = ["FakeAgent", "SCRIPT", "FAKE_DEFER_POLICY"]


#: Tight on purpose so a forced abandonment is watchable in under a minute.
#: A real adapter's patience is measured in hours or days (CLAUDE.md §9.2).
FAKE_DEFER_POLICY: Final = DeferPolicy(
    max_attempts=3,
    max_overdue=timedelta(minutes=30),
)

#: How far out scripted commitments resolve. Must clear the `resolves_in_future`
#: CHECK and the `resolutions_timing` trigger, so nothing can resolve early.
HORIZON: Final = timedelta(seconds=45)


@dataclass(frozen=True, slots=True)
class Spec:
    """One scripted commitment and the fate it is written to meet.

    `script` is carried in the commitment payload and read back by `resolve()`,
    so behaviour is a property of the committed row rather than of in-memory
    state. That matters: it survives a restart, which means the abandonment
    path can be watched across a process bounce the way it would really happen.

        resolve_now   answer on the first sweep after resolves_after
        defer_once    return None once, then answer
        never         never answer — core must eventually abandon it
    """

    label: str
    kind: Kind
    thesis: str
    confidence: Decimal
    script: str
    legs: Sequence[Leg]
    payload: dict[str, Any]
    #: (outcome, per-leg actuals, capital_at_risk, proceeds) — the scripted
    #: result. None for specs that never resolve.
    result: tuple[Outcome, Sequence[Numeric | None], Numeric, Numeric] | None


SCRIPT: Final[tuple[Spec, ...]] = (
    # -- #1 paper position, the crypto shape -------------------------------
    Spec(
        label="paper long",
        kind="paper_position",
        thesis="BTC holds support at 60k; scripted to close +5%.",
        confidence=Decimal("0.60"),
        script="resolve_now",
        legs=[Leg("BTC-USD", "spot_long", Decimal("60000"), "long", Decimal("0.5"))],
        payload={"venue": "fake", "entry": "60000", "unit": "usd"},
        # risk 60000*0.5 = 30000, proceeds 63000*0.5 = 31500 -> +0.05
        result=("hit", [Decimal("63000")], Decimal("30000"), Decimal("31500")),
    ),
    # -- #2 prop slip, two legs, defers once -------------------------------
    Spec(
        label="prop slip",
        kind="prop_slip",
        thesis="Two fake overs, 3x payout. Scripted to defer once, then hit.",
        confidence=Decimal("0.45"),
        script="defer_once",
        legs=[
            Leg("Fake Player A", "points_over", Decimal("25.5"), "over", Decimal("1")),
            Leg("Fake Player B", "points_over", Decimal("30.5"), "over", Decimal("1")),
        ],
        payload={"stake": "1", "multiplier": "3.0", "unit": "stake"},
        # risk 1 stake, proceeds 1*3.0 -> +2.00
        result=("hit", [Decimal("26"), Decimal("31")], Decimal("1"), Decimal("3")),
    ),
    # -- #3 event contract, the Kalshi shape -------------------------------
    Spec(
        label="event contract",
        kind="event_contract",
        thesis="YES at 0.34 on a fake market. Scripted to settle NO.",
        confidence=Decimal("0.34"),
        script="resolve_now",
        legs=[Leg("FAKE-EVENT-26", "event_contract", Decimal("0.34"), "yes", Decimal("100"))],
        payload={"venue": "fake", "price": "0.34", "contracts": "100", "unit": "usd"},
        # risk 0.34*100 = 34, settles 0 -> proceeds 0 -> -1.00 exactly
        result=("miss", [Decimal("0")], Decimal("34"), Decimal("0")),
    ),
    # -- #4 paper short that never resolves --------------------------------
    Spec(
        label="paper short (abandoned)",
        kind="paper_position",
        thesis="Short a fake ticker. Scripted to NEVER resolve, forcing a void.",
        confidence=Decimal("0.55"),
        script="never",
        legs=[Leg("FAKE-EQ", "spot_short", Decimal("100"), "short", Decimal("10"))],
        # A paper short MUST declare where it is wrong (CLAUDE.md §9) — risk is
        # |entry - stop| * size, not notional. Never scored here; it voids.
        payload={
            "venue": "fake",
            "entry": "100",
            "invalidation": "106",
            "capital_at_risk": "60",
            "fill": "assumed_at_stop",
            "unit": "usd",
        },
        result=None,
    ),
)


class FakeAgent(BaseAgent[int, Spec]):
    """Commits the script, one spec per wake-up, then goes idle forever.

    The script position is per-process, so running the harness a second time
    appends a second full cycle of four rather than resuming. That is fine for
    a harness and keeps it from needing state of its own — but it does mean
    row counts below are per run, not totals.
    """

    slug: ClassVar[str] = "_fake"
    default_defer_policy: ClassVar[DeferPolicy] = FAKE_DEFER_POLICY

    def __init__(self, defer_policy: DeferPolicy | None = None) -> None:
        super().__init__(defer_policy)
        self._committed = 0

    # -- the four hooks -----------------------------------------------------

    def observe(self) -> int:
        """How far through the script we are. The only 'market data' here."""
        return self._committed

    def form_thesis(self, observation: int) -> Spec | None:
        """Hand back the next spec, or None once the script is done.

        Returning None is the ordinary 'nothing worth committing to' path, and
        exercising it matters: it is what a real agent does most of the time.
        """
        if observation >= len(SCRIPT):
            return None
        return SCRIPT[observation]

    def build_commitment(self, thesis: Spec) -> Proposal | None:
        """Freeze the spec into a commitment.

        `resolves_after` is computed from the local clock, but `committed_at`
        is not sent at all — the database stamps it. If those two disagree the
        `resolves_in_future` CHECK rejects the row, which is the correct
        outcome and not something to paper over.
        """
        self._committed += 1
        return Proposal(
            kind=thesis.kind,
            thesis=thesis.thesis,
            confidence=thesis.confidence,
            legs=thesis.legs,
            resolves_after=datetime.now(timezone.utc) + HORIZON,
            payload={**thesis.payload, "script": thesis.script, "label": thesis.label},
        )

    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        """Read the scripted fate off the committed row and act it out.

        Deliberately reads `pending.payload` rather than consulting `SCRIPT` by
        index: this is how a real adapter behaves, using only what was frozen
        at commit time plus whatever reality now says.
        """
        script = pending.payload.get("script")

        if script == "never":
            # The data source that never comes back. Core's bounded defer has
            # to be the thing that ends this, not the adapter.
            return None

        if script == "defer_once" and pending.attempts < 1:
            return None

        spec = self._spec_for(pending)
        if spec is None or spec.result is None:
            # Unrecognized row — decline rather than guess. It will defer and
            # then be abandoned, which is the honest handling of a commitment
            # this adapter no longer understands.
            self.log.warning(
                "commitment #%s has no scripted result (%r) — deferring",
                pending.id, pending.payload.get("label"),
            )
            return None

        outcome, actuals, capital_at_risk, proceeds = spec.result
        pnl = return_on_risk(capital_at_risk, proceeds)

        return Verdict(
            outcome=outcome,
            leg_outcomes=[
                LegOutcome(
                    leg_index=i,
                    outcome="hit" if outcome == "hit" else "miss",
                    actual=actual,
                )
                for i, actual in enumerate(actuals)
            ],
            pnl=pnl,
            detail={
                "unit": pending.payload.get("unit", "unknown"),
                "capital_at_risk": str(capital_at_risk),
                "proceeds": str(proceeds),
                "scripted": True,
                "attempts_before_answer": pending.attempts,
            },
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _spec_for(pending: PendingCommitment) -> Spec | None:
        label = pending.payload.get("label")
        return next((s for s in SCRIPT if s.label == label), None)


def build() -> FakeAgent:
    """Construct the harness, refusing to run against a real agent.

    The guard is not ceremony. `commitments` and `events` are append-only by
    trigger, so a harness pointed at `crypto` could not be cleaned up
    afterwards — the record would carry invented rows permanently. Checked
    before the first write, because after is too late.
    """
    if not ledger.agent_is_test(FakeAgent.slug):
        raise RuntimeError(
            f"Agent {FakeAgent.slug!r} does not have is_test = true. Refusing "
            f"to run: its rows cannot be deleted, so they would permanently "
            f"contaminate the track record. Fix db/002 before continuing."
        )
    return FakeAgent()
