"""The commitment gate, written once for every Kalshi agent.

    entry_cost = ask + taker_fee(ask)          (the side being bought)
    edge       = p_model(side) − entry_cost
    commit iff edge ≥ margin AND spread ≤ max_spread AND size ≤ displayed size

Edge is measured **after** crossing the spread and paying the fee. Disagreeing
with the mid is not enough; the disagreement has to pay for its own costs
(CLAUDE.md §8; preregistration_nfl.md §2.1–§2.2). One implementation, so no
agent can quietly compute edge against the mid or before fees.

`p_model` is always a probability for YES; the NO side is `1 − p_model`, bought
at `no_ask`. Prices are side-held (§10): a NO position's entry price is the NO
ask, so CLV and pnl never branch on side downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from venues.kalshi.client import Quote
from venues.kalshi.fees import FeeRegime

__all__ = ["Candidate", "Gate", "evaluate"]

Side = Literal["yes", "no"]


@dataclass(frozen=True, slots=True)
class Gate:
    """One agent's pre-registered thresholds."""

    margin: Decimal
    max_spread: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class Candidate:
    """One side of one market, priced and gated. `passes` is the verdict."""

    ticker: str
    side: Side
    p_model: Decimal
    entry_price: Decimal
    bid: Decimal
    fee: Decimal
    entry_cost: Decimal
    edge: Decimal
    spread: Decimal
    size: Decimal
    displayed_size: Decimal | None
    passes: bool
    reason: str

    def payload(self) -> dict[str, str | bool | None]:
        return {
            "side": self.side,
            "p_model": str(self.p_model),
            "entry_price": str(self.entry_price),
            "bid": str(self.bid),
            "fee_per_contract": str(self.fee),
            "entry_cost": str(self.entry_cost),
            "edge": str(self.edge),
            "spread": str(self.spread),
            "displayed_size": None if self.displayed_size is None else str(self.displayed_size),
        }


def evaluate(
    quote: Quote,
    p_yes: Decimal,
    regime: FeeRegime,
    gate: Gate,
    *,
    check_depth: bool = True,
) -> list[Candidate]:
    """Price YES and NO on one market. Returns both, each with its verdict.

    `check_depth=False` is for a backtest replay, where candles carry no book
    sizes; the assumption is disclosed there, not hidden here.
    """
    if not (Decimal(0) <= p_yes <= Decimal(1)):
        raise ValueError(f"p_yes must be a probability, not {p_yes}")
    out: list[Candidate] = []
    sides: tuple[tuple[Side, Decimal, Decimal | None, Decimal | None, Decimal | None], ...] = (
        ("yes", p_yes, quote.yes_ask, quote.yes_bid, quote.yes_ask_size),
        ("no", 1 - p_yes, quote.no_ask, quote.no_bid, quote.no_ask_size),
    )
    for side, p_side, ask, bid, displayed in sides:
        if ask is None or bid is None or not (Decimal(0) < ask < Decimal(1)):
            continue
        fee = regime.taker_fee(ask)
        cost = ask + fee
        edge = p_side - cost
        spread = ask - bid
        size = gate.size if displayed is None else min(gate.size, displayed)
        if edge < gate.margin:
            passes, reason = False, f"edge {edge} < margin {gate.margin}"
        elif spread > gate.max_spread:
            passes, reason = False, f"spread {spread} > {gate.max_spread}"
        elif check_depth and (displayed is None or displayed <= 0):
            # Size is min(nominal, displayed) (preregistration §2.3), so the
            # only depth failure is no displayed depth at all.
            passes, reason = False, f"no displayed size ({displayed})"
        else:
            passes, reason = True, "passes"
        out.append(Candidate(
            ticker=quote.ticker, side=side, p_model=p_side, entry_price=ask,
            bid=bid, fee=fee, entry_cost=cost, edge=edge, spread=spread,
            size=size, displayed_size=displayed, passes=passes, reason=reason,
        ))
    return out
