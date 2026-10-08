"""`KalshiContractAgent`: `resolve()` and `capture_close()`, written once.

Every Kalshi agent, in every sport, settles and closes the same way, so those
two hooks live here and sport adapters implement only `observe()`,
`form_thesis()` and `build_commitment()`, plus `kickoff_now()` (where the
sport's schedule says the game starts now).

**Resolution** reads Kalshi's settlement and never voids (the exchange never
does; docs/kalshi_nfl.md §6):

    side_value      = settlement (YES) or 1 − settlement (NO)
    capital_at_risk = entry_cost × contracts        entry_cost = price + taker fee
    proceeds        = side_value × contracts
    pnl             = return_on_risk(capital_at_risk, proceeds)

A side value of 1 is a hit, 0 a miss, anything between (a tie at $0.50,
Kalshi's "last fair price" for a long postponement or an inactive player) is
`partial` with the leg `settled` (db/008).

**The maker shadow** is computed alongside, in `detail["maker"]`, and is never
the record (preregistration_nfl.md §2.4). It is not a commitment and cannot
void.

**The close** is the side-held mid of the last 1-minute candle ending at or
before the *actual* kickoff. `closes_at` is the kickoff as it was known at
commit time; a later kickoff means postponement, handled by returning None
until the game starts, or `CloseUnavailable` once Kalshi has fair-priced a
game that never started within 48 hours.
"""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, ClassVar, Final

from core.agent import (
    BaseAgent,
    ClosePrice,
    CloseUnavailable,
    Verdict,
    return_on_risk,
)
from core.ledger import Leg, LegOutcome, PendingCommitment
from venues.kalshi.client import Candle, KalshiClient, KalshiError, ts
from venues.kalshi.edge import Candidate
from venues.kalshi.fees import FeeRegime

__all__ = [
    "KalshiContractAgent",
    "POSTPONEMENT_WINDOW",
    "contract_commitment",
    "maker_shadow",
]

#: Kalshi's rule in every NFL contract read (FOOTBALLGAMEWIN, FOOTBALLSPREAD,
#: FOOTBALLENTITYSTAT): a game that starts within 48 h of its original time
#: stays open; one that does not is settled at the last fair price.
POSTPONEMENT_WINDOW: Final = timedelta(hours=48)

#: How long after kickoff to wait before reading the close, so the final
#: pre-kickoff minute has certainly closed. Far larger than any worker clock
#: skew measured (~2 s), which is why a local clock is acceptable here: the
#: close itself is read from exchange timestamps, never from this clock.
CAPTURE_SETTLE: Final = timedelta(minutes=5)

#: How far before kickoff to look for the last quote.
CLOSE_LOOKBACK: Final = timedelta(hours=3)

#: A resting order is treated as filled only if this much volume printed
#: through its price: we do not know our queue position.
MAKER_VOLUME_MULTIPLE: Final = Decimal(2)


def contract_commitment(
    *,
    candidate: Candidate,
    series: str,
    regime: FeeRegime,
    p_market: Decimal,
    margin: Decimal,
    kickoff: datetime,
    kickoff_source: str,
    kickoff_fetched_at: datetime,
    game_id: str,
    model_version: int | None,
    extra: dict[str, Any] | None = None,
) -> tuple[Leg, dict[str, Any]]:
    """The one leg and the payload every Kalshi commitment carries.

    The leg's `line` is the entry price **of the side held** (§10), so a NO is
    stored as the NO ask and CLV never branches on side. `invalidation` is the
    structural 0 (§9.0): a contract that settles worthless is its own stop.
    """
    leg = Leg(
        subject=candidate.ticker,
        market=series,
        line=candidate.entry_price,
        direction=candidate.side,
        size=candidate.size,
    )
    payload: dict[str, Any] = {
        "invalidation": "0",
        "stop_rule": "structural_zero",
        "series": series,
        "ticker": candidate.ticker,
        **candidate.payload(),
        "p_market": str(p_market),
        "margin": str(margin),
        "fee_regime": regime.as_payload(),
        "kickoff_asof": kickoff.isoformat(),
        "kickoff_source": kickoff_source,
        "kickoff_fetched_at": kickoff_fetched_at.isoformat(),
        "game_id": game_id,
        "model_version": model_version,
    }
    if extra:
        payload.update(extra)
    return leg, payload


def maker_shadow(
    *,
    side: str,
    limit: Decimal,
    contracts: Decimal,
    candles: list[Candle],
    regime: FeeRegime,
    side_value: Decimal,
) -> dict[str, Any]:
    """Would a resting bid at `limit` have filled, and what would it have made?

    Filled only if trades printed **strictly through** the limit and at least
    `MAKER_VOLUME_MULTIPLE × contracts` traded in those periods. A touch is not
    a fill. For a NO bid at `limit`, a NO trade below it is a YES trade above
    `1 − limit`. Adverse selection is the point of reporting this separately:
    a resting bid fills when the market moves against it.
    """
    if side == "yes":
        through = [c for c in candles if c.trade_low is not None and c.trade_low < limit]
    else:
        through = [c for c in candles if c.trade_high is not None and c.trade_high > 1 - limit]
    volume = sum((c.volume for c in through), Decimal(0))
    filled = bool(through) and volume >= MAKER_VOLUME_MULTIPLE * contracts
    out: dict[str, Any] = {
        "fill": "assumed_maker_trade_through",
        "limit": str(limit),
        "filled": filled,
        "volume_through": str(volume),
    }
    if filled and Decimal(0) < limit < Decimal(1):
        fee = regime.maker_fee(limit)
        capital = (limit + fee) * contracts
        out.update({
            "maker_fee_per_contract": str(fee),
            "capital_at_risk": str(capital),
            "pnl": str(return_on_risk(capital, side_value * contracts)),
        })
    return out


def _regime_from_payload(raw: dict[str, Any]) -> FeeRegime:
    return FeeRegime(
        series=raw["series"],
        fee_type=raw["fee_type"],
        multiplier=Decimal(raw["fee_multiplier"]),
        effective_from=ts(raw.get("effective_from")),
        fetched_at=ts(raw["fetched_at"]) or datetime.now(timezone.utc),
        assumed=bool(raw.get("assumed")),
    )


class KalshiContractAgent[Obs, Th](BaseAgent[Obs, Th]):
    """Base for every Kalshi event-contract agent."""

    captures_close: ClassVar[bool] = True

    def __init__(
        self,
        *,
        client: KalshiClient | None = None,
        clock: Callable[[], datetime] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.client = client or KalshiClient()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @abstractmethod
    def kickoff_now(self, pending: PendingCommitment) -> datetime | None:
        """Where the sport's schedule says this game starts, as of now.

        Differs from `pending.closes_at` (kickoff as known at commit) only when
        the game moved. None when the schedule no longer lists it.
        """

    # -- resolve ------------------------------------------------------------

    def resolve(self, pending: PendingCommitment) -> Verdict | None:
        leg = pending.legs[0]
        quote = self.client.market(leg.subject)
        if not quote.finalized:
            return None
        if quote.settlement_value is None:
            raise KalshiError(f"{leg.subject} is finalized with no settlement value")

        yes_value = quote.settlement_value
        side_value = yes_value if leg.direction == "yes" else 1 - yes_value
        contracts = Decimal(str(leg.size))
        entry_cost = Decimal(pending.payload["entry_cost"])
        capital = entry_cost * contracts
        proceeds = side_value * contracts
        pnl = return_on_risk(capital, proceeds)

        if side_value == 1:
            outcome, leg_result = "hit", "hit"
        elif side_value == 0:
            outcome, leg_result = "miss", "miss"
        else:
            outcome, leg_result = "partial", "settled"

        kickoff = self.kickoff_now(pending) or pending.closes_at
        maker: dict[str, Any]
        if kickoff is None:
            maker = {"filled": False, "reason": "no kickoff to bound the resting order"}
        else:
            candles = self.client.candles(
                pending.payload["series"], leg.subject,
                pending.committed_at, kickoff, 1,
                settled_at=quote.settlement_ts,
            )
            maker = maker_shadow(
                side=leg.direction or "yes",
                limit=Decimal(pending.payload["bid"]),
                contracts=contracts,
                candles=[c for c in candles if c.end <= kickoff],
                regime=_regime_from_payload(pending.payload["fee_regime"]),
                side_value=side_value,
            )

        return Verdict(
            outcome=outcome,
            leg_outcomes=[LegOutcome(0, leg_result, side_value)],
            pnl=pnl,
            detail={
                "unit": "usd",
                "settlement": "binary" if side_value in (0, 1) else "fair_value",
                "settlement_value_yes": str(yes_value),
                "side_value": str(side_value),
                "settlement_ts": None if quote.settlement_ts is None else quote.settlement_ts.isoformat(),
                "result": quote.result,
                "entry_cost": str(entry_cost),
                "capital_at_risk": str(capital),
                "proceeds": str(proceeds),
                "gross": str(proceeds - capital),
                "maker": maker,
            },
        )

    # -- capture ------------------------------------------------------------

    def capture_close(self, pending: PendingCommitment) -> ClosePrice | None:
        leg = pending.legs[0]
        asof = pending.closes_at
        kickoff = self.kickoff_now(pending)
        if kickoff is None or asof is None:
            return None

        if kickoff - asof > POSTPONEMENT_WINDOW:
            if self.client.market(leg.subject).finalized:
                raise CloseUnavailable(
                    f"postponed beyond {POSTPONEMENT_WINDOW}; settled at fair "
                    f"price (scheduled {asof.isoformat()}, now {kickoff.isoformat()})"
                )
            return None

        if self.clock() < kickoff + CAPTURE_SETTLE:
            return None     # not kicked off yet (or only just): keep asking

        quote = self.client.market(leg.subject)
        candles = self.client.candles(
            pending.payload["series"], leg.subject,
            kickoff - CLOSE_LOOKBACK, kickoff, 1,
            settled_at=quote.settlement_ts if quote.finalized else None,
        )
        quoted = [c for c in candles if c.end <= kickoff and c.mid is not None]
        if not quoted:
            raise KalshiError(
                f"{leg.subject}: no quote in the {CLOSE_LOOKBACK} before kickoff "
                f"{kickoff.isoformat()}"
            )
        last = quoted[-1]
        assert last.mid is not None
        side_mid = last.mid if leg.direction == "yes" else 1 - last.mid
        return ClosePrice(
            price=side_mid,
            detail={
                "rule": "last_1m_mid_at_or_before_kickoff",
                "kickoff_asof": asof.isoformat(),
                "kickoff_actual": kickoff.isoformat(),
                "candle_end": last.end.isoformat(),
                "yes_bid": str(last.yes_bid_close),
                "yes_ask": str(last.yes_ask_close),
            },
        )
