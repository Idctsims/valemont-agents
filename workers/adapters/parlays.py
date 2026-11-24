"""The Parlay Generator: `parlay_safer`, `parlay_mid`, `parlay_lotto` (paper).

docs/preregistration_parlays.md version 2 (rule version P2; stamped by db/085
and db/086), built per docs/parlay_generator_plan.md §4.4. Paper only: nothing
places an order, every commitment is `mode = 'paper'` (CLAUDE.md §1).

    observe          this slot's shared re-quote (consensus/parlay_slot.py,
                     run at :33); no Kalshi call here
    form_thesis      rules 1-4 on the fresh quotes at this moment (a leg over
                     20 minutes old is dropped), then the tier's search per
                     sport (consensus/parlay_ticket.py), plus Lotto's one
                     cross-sport ticket. Every scope's result, ticket or empty
                     slot with its binding rule, is a `gate_evaluated` event
    build_commitment the stamp guard first (consensus/parlay_prereg.py: every
                     v2 section stamped before now, the shipped copy not
                     drifted, or nothing commits); a ticket with a leg whose
                     game has started by the DATABASE clock, or a leg over 20
                     minutes old, is dropped (recorded); a slate of the rest,
                     at most one per sport plus one cross-sport for Lotto
    resolve          the product of the legs' settlement values: hit (1),
                     miss (0) or partial (between). Nothing voids on Kalshi
    capture_close    the CLV proxy (§6), by the ticket's own rule version:
                     P2.1 (cohort 2): each leg's last 1-minute candle at or
                     before its kickoff, within 60 minutes, two-sided, with its
                     age; the kickoff re-read from the board when it moved
                     later (a postponement; not due while that start is ahead).
                     P2 (cohort 1, never re-scored): the last two-sided candle
                     within 5 minutes, as stamped then. Either way the side
                     mids are multiplied, against naive_p; a leg without one
                     is a `missed` close naming the leg

resolves_after (P2.1 §5) is the later of the latest leg's expected expiration
and closes_at + 1 minute.

The risk (§5, §9): capital_at_risk = (scored_price + taker fee per contract)
x 100 contracts; the invalidation is structural 0; proceeds = settlement value
x 100. A miss scores -1.0 exactly.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, ClassVar, Mapping, Sequence

from consensus import correlation as cr
from consensus import parlay_legs as pl
from consensus import parlay_prereg
from consensus import parlay_slot
from consensus import parlay_ticket as pt
from core import ledger
from core.agent import BaseAgent, ClosePrice, CloseUnavailable, DeferPolicy, Proposal, Verdict, return_on_risk
from core.ledger import Leg, LegOutcome, PendingCommitment
from venues.kalshi.client import KalshiClient, KalshiError

__all__ = ["ParlayAgent", "ParlaySafer", "ParlayMid", "ParlayLotto", "PARLAY_DEFER_POLICY",
           "PARLAY_CAPTURE_POLICY", "KICKOFF_SOURCE"]

#: §5: past 10 days after resolves_after, core's give-up applies (never the default, §9.3).
PARLAY_DEFER_POLICY = DeferPolicy(max_attempts=500, max_overdue=timedelta(days=10))
#: The close is read from candle history, so a late first look still recovers it.
PARLAY_CAPTURE_POLICY = DeferPolicy(max_attempts=450, max_overdue=timedelta(hours=72))
#: How long after the latest kickoff before the close proxy is read.
CAPTURE_SETTLE = timedelta(minutes=5)
#: P2's §6 (cohort 1): a leg's close is its last two-sided candle within this before its kickoff.
CLOSE_WINDOW = timedelta(minutes=5)
#: P2.1's §6 (cohort 2): the last candle at or before kickoff, within this; it must be two-sided.
CLOSE_LOOKBACK = timedelta(minutes=60)
#: P2.1's §5: resolves_after is never earlier than closes_at plus this.
RESOLVES_AFTER_CLOSE = timedelta(minutes=1)
#: The rule versions whose §6 is P2.1's (a P2 ticket keeps P2's close, never re-scored).
P21_CLOSE = frozenset({"P2.1"})
KICKOFF_SOURCE = "kalshi_board_events.starts_at (the linked schedule game), as of the slot's candidate read"


@dataclass(frozen=True)
class Observation:
    at: datetime
    key: tuple[Any, str]
    data: parlay_slot.SlotQuotes | None


@dataclass(frozen=True)
class Thesis:
    at: datetime
    data: parlay_slot.SlotQuotes
    tickets: tuple[pt.Ticket, ...]

    def __str__(self) -> str:
        return "; ".join(f"{t.scope} {t.sport or 'cross-sport'}: {len(t.legs)} legs at "
                         f"{t.priced.scored_price:.4f}" for t in self.tickets)


def _side_value(settlement: Decimal, side: str) -> Decimal:
    return settlement if side == "yes" else Decimal(1) - settlement


class ParlayAgent(BaseAgent[Observation, Thesis]):
    """One tier's agent. Subclasses set `slug` and `tier`."""

    tier: ClassVar[str] = ""
    captures_close: ClassVar[bool] = True
    default_defer_policy: ClassVar[DeferPolicy] = PARLAY_DEFER_POLICY
    default_capture_policy: ClassVar[DeferPolicy] = PARLAY_CAPTURE_POLICY

    def __init__(self, *, client: KalshiClient | None = None,
                 clock: Callable[[], datetime] | None = None,
                 slots: Callable[[Any], parlay_slot.SlotQuotes | None] = parlay_slot.get,
                 guard: Callable[[], Any] = parlay_prereg.require_stamped,
                 db_now: Callable[[], datetime] | None = None,
                 rho: cr.RhoTable | None = None,
                 starts: Callable[[Sequence[str]], Mapping[str, datetime]] | None = None,
                 draws: int = cr.MC_DRAWS, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.tier not in pt.TIERS:
            raise ValueError(f"{type(self).__name__}: unknown tier {self.tier!r}")
        self.client = client or KalshiClient()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.slots = slots
        self.guard = guard
        self.db_now = db_now or (lambda: ledger.measure_clock().db_time)
        self.rho = rho
        #: P2.1 §6: each leg's start as the board knows it now (to follow a postponement).
        self.starts = starts or (lambda events: ledger.board_event_starts(events))
        self.draws = draws

    # -- observe -----------------------------------------------------------------
    def observe(self) -> Observation:
        at = self.clock()
        key = parlay_slot.slot_key(at)
        return Observation(at=at, key=key, data=self.slots(key))

    # -- form_thesis ---------------------------------------------------------------
    def _record(self, r: pt.Result, slot: str) -> None:
        detail: dict[str, Any] = {"slot": slot, "tier": r.tier, "scope": r.scope, "sport": r.sport,
                                  "rule_version": pt.RULE_VERSION, "candidates": r.candidates, "tried": r.tried}
        if r.ticket is None:
            detail["binding"] = r.binding
            self.record_gate_evaluation(detail, f"empty: {r.binding}")
        else:
            p = r.ticket.priced
            detail.update({"legs": len(r.ticket.legs), "scored_price": str(p.scored_price),
                           "fair_p": round(p.fair_p, 8), "rel_cost_over_fair": round(p.rel_cost, 6)})
            self.record_gate_evaluation(detail, f"ticket: {len(r.ticket.legs)} legs at {p.scored_price:.4f}")

    def form_thesis(self, observation: Observation) -> Thesis | None:
        slot = f"{observation.key[0]} {observation.key[1]}"
        data = observation.data
        if data is None:
            # No re-quote for this slot (it misfired past its grace, or failed):
            # every leg fails rule 2. The slot is empty, binding rule `fresh`.
            self.record_gate_evaluation({"slot": slot, "tier": self.tier, "binding": "fresh",
                                         "why": "no re-quote for this slot"}, "empty: fresh")
            return None
        legs = pl.legs_of(data.markets, data.quotes)
        f = pl.funnel(legs, observation.at, cut=data.cut)
        cands, _ = pt.candidates(f.passed)
        rho = self.rho or cr.load_table()
        results: list[pt.Result] = []
        table = f.table()
        # Every sport on the board gets a result (§2: an empty slot is recorded with
        # its binding rule, never skipped silently), including one whose legs all
        # failed rules 1-4: bound by the first of them that left it none.
        for sport in sorted(set(table) | {c.m.sport for c in cands}):
            mine = [c for c in cands if c.m.sport == sport]
            if not mine:
                counts = table.get(sport, {})
                rule = next((r for r in pl.RULES if counts.get(r, 0) == 0), "no consensus p")
                results.append(pt.Result(self.tier, "sport", sport, 0, None, f"rules 1-4: {rule}", {}))
                continue
            results.append(pt.search(self.tier, mine, rho, sport=sport, draws=self.draws))
        if self.tier == "lotto":
            results.append(pt.search("lotto", cands, rho, sport=None, cross_sport=True, draws=self.draws))
        for r in results:
            self._record(r, slot)
        tickets = tuple(r.ticket for r in results if r.ticket is not None)
        return Thesis(at=observation.at, data=data, tickets=tickets) if tickets else None

    # -- build_commitment ----------------------------------------------------------
    def build_commitment(self, thesis: Thesis) -> Sequence[Proposal]:
        self.guard()  # PreregistrationMissing is never caught: nothing commits unstamped
        db_now = self.db_now()
        worker_now = self.clock()
        out = []
        for ticket in thesis.tickets:
            why = self._refusal(ticket, db_now, worker_now)
            if why is not None:
                self.record_gate_evaluation({"tier": ticket.tier, "scope": ticket.scope, "sport": ticket.sport,
                                             "refused": why}, f"refused at commit: {why}")
                continue
            out.append(self._proposal(ticket, thesis.data, worker_now))
        return out

    @staticmethod
    def _refusal(ticket: pt.Ticket, db_now: datetime, worker_now: datetime) -> str | None:
        started = [c.m.market_ticker for c in ticket.legs if c.m.starts_at <= db_now]
        if started:
            return f"game started by the database clock: {', '.join(started)}"
        stale = [c.m.market_ticker for c in ticket.legs
                 if c.leg.quote is None or worker_now - c.leg.quote.at > pl.FRESH]
        if stale:
            return f"older than 20 minutes at commit: {', '.join(stale)}"
        no_exp = [c.m.market_ticker for c in ticket.legs if c.leg.quote is None or c.leg.quote.expires is None]
        if no_exp:
            return f"no expected expiration from Kalshi: {', '.join(no_exp)}"
        return None

    def _proposal(self, ticket: pt.Ticket, data: parlay_slot.SlotQuotes, worker_now: datetime) -> Proposal:
        p = ticket.priced
        entry_cost = p.scored_price + p.taker_fee
        capital = entry_cost * pt.NOMINAL_CONTRACTS
        kickoffs = {c.m.market_ticker: c.m.starts_at for c in ticket.legs}
        expires = {c.m.market_ticker: c.leg.quote.expires for c in ticket.legs}  # type: ignore[union-attr]
        oldest = min(c.leg.quote.at for c in ticket.legs)  # type: ignore[union-attr]
        closes_at = max(kickoffs.values())
        # P2.1 §5: never before the close. A postponed leg's expected expiration can stay on
        # the original day (Sun Nov 22 16:45 Safer Tennis was refused after selection).
        resolves_after = max(max(e for e in expires.values() if e is not None), closes_at + RESOLVES_AFTER_CLOSE)
        payload = ticket.payload() | {
            "invalidation": "0", "stop_rule": "structural_zero",
            "entry_cost": str(entry_cost), "capital_at_risk": str(capital),
            "kickoffs": {t: k.isoformat() for t, k in kickoffs.items()}, "kickoff_source": KICKOFF_SOURCE,
            "expected_expirations": {t: e.isoformat() for t, e in expires.items() if e is not None},
            "oldest_leg_age_s_at_commit": round((worker_now - oldest).total_seconds(), 1),
            "requote": data.summary(), "agent": self.slug,
        }
        legs = [Leg(subject=c.m.market_ticker, market=c.m.series_ticker, line=c.ask, direction=c.leg.side,  # type: ignore[arg-type]
                    size=pt.NOMINAL_CONTRACTS) for c in ticket.legs]
        where = "cross-sport" if ticket.scope == "cross_sport" else ticket.sport
        thesis = (f"{self.tier} {where} ({pt.RULE_VERSION}): {len(legs)} legs at {p.scored_price:.4f} "
                  f"(pays x{1 / float(p.scored_price):.1f}), fair p {p.fair_p:.4f}, relative cost over fair "
                  f"{p.rel_cost:+.4f}")
        return Proposal(kind="event_contract", thesis=thesis, payload=payload, resolves_after=resolves_after,
                        legs=legs, closes_at=closes_at, quote_fetched_at=oldest)

    # -- resolve ---------------------------------------------------------------------
    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        values: list[Decimal] = []
        detail_legs = []
        for leg in pending.legs:
            q = self.client.market(leg.subject)
            if not q.finalized:
                return None  # every leg settles before the ticket is scored
            if q.settlement_value is None:
                raise KalshiError(f"{leg.subject} is finalized with no settlement value")
            v = _side_value(q.settlement_value, leg.direction or "yes")
            values.append(v)
            detail_legs.append({"ticker": leg.subject, "side": leg.direction, "settlement_value_yes": str(q.settlement_value),
                                "side_value": str(v), "result": q.result})
        value = math.prod(values, start=Decimal(1))
        capital = Decimal(pending.payload["capital_at_risk"])
        proceeds = value * pt.NOMINAL_CONTRACTS
        outcome = "hit" if value == 1 else "miss" if value == 0 else "partial"
        leg_outcomes = [LegOutcome(i, "hit" if v == 1 else "miss" if v == 0 else "settled", v)
                        for i, v in enumerate(values)]
        return Verdict(outcome=outcome, leg_outcomes=leg_outcomes, pnl=return_on_risk(capital, proceeds), detail={
            "unit": "usd", "contracts": pt.NOMINAL_CONTRACTS, "ticket_value": str(value),
            "capital_at_risk": str(capital), "proceeds": str(proceeds), "gross": str(proceeds - capital),
            "legs": detail_legs})

    # -- the CLV proxy -------------------------------------------------------------------
    def capture_close(self, pending: PendingCommitment) -> ClosePrice | None:
        if pending.closes_at is None or self.clock() < pending.closes_at + CAPTURE_SETTLE:
            return None
        if pending.payload.get("rule_version") in P21_CLOSE:
            return self._capture_p21(pending)
        return self._capture_p2(pending)

    def _capture_p21(self, pending: PendingCommitment) -> ClosePrice | None:
        """P2.1 §6: each leg's last 1-minute candle at or before its kickoff, within
        60 minutes, two-sided, with its age; the kickoff re-read from the board
        when it has moved later (a postponement)."""
        at_commit = {t: datetime.fromisoformat(k) for t, k in pending.payload["kickoffs"].items()}
        event_of = {x["market_ticker"]: x["event_ticker"] for x in pending.payload["legs"]}
        now_starts = self.starts(sorted({event_of[t] for t in at_commit if t in event_of}))
        kickoffs: dict[str, datetime] = {}
        for t, k in at_commit.items():
            s = now_starts.get(event_of.get(t, ""))
            kickoffs[t] = s if s is not None and s > k else k
        if self.clock() < max(kickoffs.values()) + CAPTURE_SETTLE:
            return None  # a postponed leg's start is still ahead: the close is not yet due
        mids: list[Decimal] = []
        legs_detail = []
        for leg in pending.legs:
            kickoff = kickoffs[leg.subject]
            candles = self.client.candles(leg.market, leg.subject, kickoff - CLOSE_LOOKBACK, kickoff, 1)
            before = [c for c in candles if c.end <= kickoff]
            if not before:
                raise CloseUnavailable(f"no candle in the {CLOSE_LOOKBACK} before kickoff: {leg.subject}")
            last = before[-1]
            if not (last.yes_bid_close is not None and last.yes_ask_close is not None
                    and 0 < last.yes_bid_close <= last.yes_ask_close < 1):
                raise CloseUnavailable(f"the last candle before kickoff ({last.end.isoformat()}, bid "
                                       f"{last.yes_bid_close}, ask {last.yes_ask_close}) is not two-sided: {leg.subject}")
            mid = (last.yes_bid_close + last.yes_ask_close) / 2
            side_mid = mid if leg.direction == "yes" else 1 - mid
            mids.append(side_mid)
            legs_detail.append({
                "ticker": leg.subject, "kickoff": kickoff.isoformat(),
                "kickoff_at_commit": at_commit[leg.subject].isoformat(),
                "postponed": kickoff != at_commit[leg.subject], "candle_end": last.end.isoformat(),
                "age_min": round((kickoff - last.end).total_seconds() / 60, 1),
                "yes_bid": str(last.yes_bid_close), "yes_ask": str(last.yes_ask_close), "side_mid": str(side_mid)})
        ages = [x["age_min"] for x in legs_detail]
        close = math.prod(mids, start=Decimal(1))
        return ClosePrice(price=close, entry_price=Decimal(pending.payload["naive_p"]), detail={
            "label": "CLV (proxy)", "rule_version": "P2.1",
            "rule": "product of each leg's last 1-minute candle at or before its kickoff, within 60 minutes, "
                    "two-sided, side mid; the kickoff re-read when it moved later",
            "age_min_median": statistics.median(ages), "age_min_max": max(ages), "legs": legs_detail})

    def _capture_p2(self, pending: PendingCommitment) -> ClosePrice | None:
        """P2's §6, unchanged, for cohort 1: a P2 ticket is never re-scored under P2.1."""
        kickoffs = {t: datetime.fromisoformat(k) for t, k in pending.payload["kickoffs"].items()}
        mids: list[Decimal] = []
        legs_detail = []
        for leg in pending.legs:
            kickoff = kickoffs[leg.subject]
            candles = self.client.candles(leg.market, leg.subject, kickoff - CLOSE_WINDOW, kickoff, 1)
            two_sided = [c for c in candles if c.end <= kickoff and c.yes_bid_close is not None
                         and c.yes_ask_close is not None and 0 < c.yes_bid_close <= c.yes_ask_close < 1]
            if not two_sided:
                raise CloseUnavailable(f"no two-sided quote in the {CLOSE_WINDOW} before kickoff: {leg.subject}")
            last = two_sided[-1]
            mid = (last.yes_bid_close + last.yes_ask_close) / 2  # type: ignore[operator]
            side_mid = mid if leg.direction == "yes" else 1 - mid
            mids.append(side_mid)
            legs_detail.append({"ticker": leg.subject, "kickoff": kickoff.isoformat(), "candle_end": last.end.isoformat(),
                                "age_min": round((kickoff - last.end).total_seconds() / 60, 1),
                                "yes_bid": str(last.yes_bid_close), "yes_ask": str(last.yes_ask_close),
                                "side_mid": str(side_mid)})
        ages = [x["age_min"] for x in legs_detail]
        close = math.prod(mids, start=Decimal(1))
        # The ages are measurement only (owner, Nov 24: never a CLV figure without its ages); the rule is P2's.
        return ClosePrice(price=close, entry_price=Decimal(pending.payload["naive_p"]),
                          detail={"label": "CLV (proxy)", "rule_version": pending.payload.get("rule_version", "P2"),
                                  "rule": "product of each leg's last two-sided 1-minute "
                                  "side mid at or before its kickoff, within 5 minutes",
                                  "age_min_median": statistics.median(ages), "age_min_max": max(ages),
                                  "legs": legs_detail})


class ParlaySafer(ParlayAgent):
    slug: ClassVar[str] = "parlay_safer"
    tier: ClassVar[str] = "safer"


class ParlayMid(ParlayAgent):
    slug: ClassVar[str] = "parlay_mid"
    tier: ClassVar[str] = "mid"


class ParlayLotto(ParlayAgent):
    slug: ClassVar[str] = "parlay_lotto"
    tier: ClassVar[str] = "lotto"
