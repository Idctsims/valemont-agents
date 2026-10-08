"""Throwaway harness agent. Proves the core loop against the real schema.

This exists to answer one question before any real adapter is written: does
observe -> commit -> wait -> resolve -> score actually work end to end, against
real Postgres, with the append-only triggers armed? It invents commitments so
that answer doesn't depend on a market being open or an API being up.

It is **deterministic**, deliberately. A random harness proves the pipeline
runs; a scripted one lets you predict every row before you run it and notice
when a row is missing. The script covers all four commitment shapes and all
four resolution paths:

    #1  paper_position  long BTC + stop   resolves first sweep      hit,  +1.00
    #2  prop_slip       two legs          defers once, then hits     hit,  +2.00
    #3  event_contract  YES @ 0.34        resolves first sweep      miss, -1.00
    #4  paper_position  short + stop      NEVER resolves            void,  NULL
    #5  event_contract  YES @ 0.40 + CLOSE  captures, then resolves hit,  +1.50

#4 is the point of the exercise. It never becomes knowable, so the bounded
defer in `core.agent` has to notice and close it out — the failure mode that
would otherwise leak a growing due-set for weeks before anyone saw it.

#3 is the Kalshi shape, committed and scored through the same path as the
others with no special-casing, which is the design claim this whole phase
rests on.

#5 is the only one with a `closes_at`, and it is why this harness opts in to
`captures_close`. The capture path — commit -> SNAPSHOT AT CLOSE -> resolve —
is machinery that no real adapter exercises yet, and untested machinery that
only runs unattended is exactly the failure step 4 of the build order exists to
catch. #5 runs it against the real schema, the real timing trigger and the real
CLV arithmetic, with factors attached so the attribution join has something to
land on.

Every spec declares an invalidation level, because CLAUDE.md §9 requires one
of every commitment in every domain. #1 and #4 declare a chosen stop; #2 and
#3 have structural ones at zero (a prop's stake IS its stop, and a contract
settling worthless is a stop at zero), which is why those two needed no change
when §9 was rewritten — they were always R-multiples.

SAFETY: this agent refuses to run unless its row has `is_test = true`. Test
rows cannot be deleted, so pointing it at a real slug would permanently
contaminate the track record. The check happens before the first write.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar, Final, Mapping, Sequence

from core import ledger
from core.agent import (
    BaseAgent, ClosePrice, DeferPolicy, Proposal, Verdict, return_on_risk,
)
from core.ledger import Kind, Leg, LegOutcome, Numeric, Outcome, PendingCommitment
from core.orchestrator import Schedule, every

__all__ = [
    "FakeAgent", "SCRIPT", "FAKE_DEFER_POLICY",
    "CanaryConfigError", "canary_interval_s", "canary_schedule",
]


#: Tight on purpose so a forced abandonment is watchable in under a minute.
#: A real adapter's patience is measured in hours or days (CLAUDE.md §9.3).
FAKE_DEFER_POLICY: Final = DeferPolicy(
    max_attempts=3,
    max_overdue=timedelta(minutes=30),
)

#: Capture patience, tight like the defer policy so a missed close is
#: watchable in the same minute rather than in two days.
FAKE_CAPTURE_POLICY: Final = DeferPolicy(
    max_attempts=4,
    max_overdue=timedelta(minutes=10),
)

#: How far out scripted commitments resolve. Must clear the `resolves_in_future`
#: CHECK and the `resolutions_timing` trigger, so nothing can resolve early.
HORIZON: Final = timedelta(seconds=45)

#: How long after the declared close the scripted market "actually" closes.
#: Non-zero on purpose: the first capture attempt must see an open market and
#: defer, so the postponement path is exercised rather than assumed.
CAPTURE_DELAY: Final = timedelta(seconds=5)


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
    #: How long after commit this market's opinion becomes final. None for a
    #: spec with no close, which is most of them.
    closes_in: timedelta | None = None
    #: Price of the side we hold when the market closes. Scripted.
    close_price: Decimal | None = None
    #: Named signed adjustments, so the §10.1 attribution query has rows.
    factors: Sequence[ledger.Factor] = ()


SCRIPT: Final[tuple[Spec, ...]] = (
    # -- #1 paper position, the crypto shape -------------------------------
    Spec(
        label="paper long",
        kind="paper_position",
        thesis="BTC holds support at 60k; scripted to close at +1R.",
        confidence=Decimal("0.60"),
        script="resolve_now",
        legs=[Leg("BTC-USD", "spot_long", Decimal("60000"), "long", Decimal("0.5"))],
        payload={"venue": "fake", "entry": "60000", "invalidation": "57000",
                 "stop_rule": "scripted", "unit": "usd"},
        # §9: risk = |60000-57000| * 0.5 = 1500. Exit 63000 -> gross 1500.
        # proceeds = gross + risk = 3000 -> +1.00, exactly one R.
        result=("hit", [Decimal("63000")], Decimal("1500"), Decimal("3000")),
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
        # Structural invalidation: a missed slip returns nothing, so the stake
        # IS the stop. No change was needed here when §9 was rewritten.
        payload={"stake": "1", "invalidation": "0", "multiplier": "3.0",
                 "unit": "stake"},
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
        # Structural invalidation again: a YES contract settling NO is worth
        # zero, so the stop is zero and risk is the premium paid.
        payload={"venue": "fake", "price": "0.34", "invalidation": "0",
                 "contracts": "100", "unit": "usd"},
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
        # A chosen invalidation, 6% above entry — inside §9.0's [0.5%, 25%]
        # band. Never scored here; it voids. risk would be |100-106|*10 = 60.
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
    # -- #5 event contract WITH a close — the CLV path ----------------------
    Spec(
        label="event contract (clv)",
        kind="event_contract",
        thesis=(
            "YES at 0.40 on a second fake market. Scripted to close at 0.55 "
            "and settle YES — a commitment that beats the close AND wins."
        ),
        confidence=Decimal("0.40"),
        script="resolve_now",
        legs=[Leg("FAKE-CLOSE-26", "event_contract", Decimal("0.40"), "yes", Decimal("100"))],
        payload={"venue": "fake", "price": "0.40", "invalidation": "0",
                 "contracts": "100", "unit": "usd"},
        # The close lands well before resolution, which is the whole point of
        # the three-moment loop: commit -> CLOSE -> resolve.
        closes_in=timedelta(seconds=15),
        close_price=Decimal("0.55"),
        # risk 0.40*100 = 40, settles 1 -> proceeds 100 -> +1.50
        result=("hit", [Decimal("1")], Decimal("40"), Decimal("100")),
        # Two factors so the §10.1 attribution query has something to group by.
        # Signed, snake_case, in the same probability units as the price.
        factors=(
            ledger.Factor("injury", Decimal("-0.04")),
            ledger.Factor("short_week", Decimal("0.09")),
        ),
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

    #: Opted in so the capture path gets exercised end to end. Only spec #5
    #: carries a closes_at, and `due_for_capture` filters on that being NOT
    #: NULL — so the other four are never asked for a close they do not have.
    captures_close: ClassVar[bool] = True
    default_capture_policy: ClassVar[DeferPolicy] = FAKE_CAPTURE_POLICY

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
        now = datetime.now(timezone.utc)
        return Proposal(
            kind=thesis.kind,
            quote_fetched_at=now,
            thesis=thesis.thesis,
            confidence=thesis.confidence,
            legs=thesis.legs,
            resolves_after=now + HORIZON,
            closes_at=None if thesis.closes_in is None else now + thesis.closes_in,
            factors=thesis.factors,
            payload={
                **thesis.payload,
                "script": thesis.script,
                "label": thesis.label,
                "close_price": (
                    None if thesis.close_price is None else str(thesis.close_price)
                ),
            },
        )

    def capture_close(self, pending: PendingCommitment) -> ClosePrice | None:
        """Price the scripted close, once enough time has passed.

        Reads the scripted close off the committed row, same as `resolve()`
        does, so the behaviour survives a restart. Returns None for the first
        `CAPTURE_DELAY` past the declared close, which exercises the
        "market still open, keep asking" path that a postponed game takes.
        """
        price = pending.payload.get("close_price")
        if price is None:
            self.log.warning(
                "commitment #%s has a closes_at but no scripted close price",
                pending.id,
            )
            return None

        if pending.overdue_by < CAPTURE_DELAY:
            self.log.info(
                "commitment #%s market still open (%s past declared close)",
                pending.id, pending.overdue_by,
            )
            return None

        return ClosePrice(price=Decimal(price), detail={"scripted": True})

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


#: The Railway canary's cadence (`main.py`, CANARY=true). Every tick writes a
#: permanent `runs` row and ~3 `events` rows even when idle, so 5 s meant
#: ~12 MB a day of rows nothing can delete; 60 s is ~1 MB. Run, sweep and
#: capture all use it: `_fake` opts in to close capture, and an opted-in agent
#: registered without a capture trigger is refused by the orchestrator.
CANARY_INTERVAL_ENV: Final = "CANARY_INTERVAL_S"
CANARY_INTERVAL_DEFAULT_S: Final = 60
CANARY_INTERVAL_MIN_S: Final = 5


class CanaryConfigError(ValueError):
    """CANARY_INTERVAL_S is set to something the canary must not run with."""


def canary_interval_s(environ: Mapping[str, str] = os.environ) -> int:
    """The canary interval in whole seconds: unset or blank is the default.

    Anything else must be a plain integer of at least CANARY_INTERVAL_MIN_S.
    `int()` alone would accept "1_0" and "+5"; a config value that means
    something other than what it looks like is refused instead.
    """
    raw = environ.get(CANARY_INTERVAL_ENV, "").strip()
    if not raw:
        return CANARY_INTERVAL_DEFAULT_S
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) < CANARY_INTERVAL_MIN_S:
        raise CanaryConfigError(
            f"{CANARY_INTERVAL_ENV}={raw!r} is not a whole number of seconds "
            f">= {CANARY_INTERVAL_MIN_S}. Unset it for the default "
            f"({CANARY_INTERVAL_DEFAULT_S})."
        )
    return int(raw)


def canary_schedule(interval_s: int) -> Schedule:
    """The canary's one cadence, for run, sweep and capture alike."""
    return Schedule(
        run=every(seconds=interval_s),
        sweep=every(seconds=interval_s),
        capture=every(seconds=interval_s),
    )


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
