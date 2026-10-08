"""The shared resolve() and capture_close(): never void, fee inside the risk,
maker as a shadow, the close at the actual kickoff (preregistration §2.4, §2.7)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from typing import Any, ClassVar

from core.agent import CloseUnavailable, return_on_risk
from core.ledger import Leg, PendingCommitment
from tests.kalshi_fakes import FakeKalshi, T0, candle, quote, regime
from tests.support import LedgerTestCase, pending
from venues.kalshi.client import KalshiError
from venues.kalshi.contract_agent import KalshiContractAgent, maker_shadow

KICK = T0 + timedelta(hours=2)
TICKER = "KXNFLGAME-26OCT03BALDAL-DAL"


class Agent(KalshiContractAgent[None, None]):
    slug: ClassVar[str] = "_test"

    def __init__(self, fake: FakeKalshi, kickoff: datetime | None = KICK,
                 now: datetime = KICK + timedelta(minutes=10)) -> None:
        super().__init__(client=fake, clock=lambda: now)  # type: ignore[arg-type]
        self._kickoff = kickoff

    def kickoff_now(self, pending: PendingCommitment) -> datetime | None:
        return self._kickoff

    def observe(self) -> None: ...
    def form_thesis(self, observation: None) -> None: ...
    def build_commitment(self, thesis: None) -> None: ...


def contract(side: str = "yes", entry: str = "0.50", cost: str = "0.5175",
             bid: str = "0.49") -> PendingCommitment:
    return pending(
        701, legs=(Leg(TICKER, "KXNFLGAME", D(entry), side, D(100)),),  # type: ignore[arg-type]
        payload={"series": "KXNFLGAME", "entry_cost": cost, "bid": bid,
                 "fee_regime": regime().as_payload(), "game_id": "g"},
        committed_at=T0 - timedelta(hours=20), closes_at=KICK,
    )


class Resolve(LedgerTestCase):
    def settle(self, value: str | None, side: str = "yes", status: str = "finalized"):
        fake = FakeKalshi(quotes={TICKER: quote(TICKER, status=status, settlement=value)})
        return Agent(fake).resolve(contract(side=side))

    def test_unsettled_defers(self) -> None:
        self.assertIsNone(self.settle(None, status="closed"))

    def test_yes_hit_scores_with_the_fee_inside_the_risk(self) -> None:
        v = self.settle("1.0000")
        self.assertEqual(v.outcome, "hit")
        self.assertEqual(v.pnl, return_on_risk(D("51.75"), D("100")))
        self.assertEqual(v.detail["capital_at_risk"], "51.7500")

    def test_a_full_loss_is_minus_one(self) -> None:
        v = self.settle("0.0000")
        self.assertEqual((v.outcome, v.pnl), ("miss", D(-1)))

    def test_no_side_reads_the_complement(self) -> None:
        v = self.settle("0.0000", side="no")
        self.assertEqual(v.outcome, "hit")
        self.assertEqual(v.leg_outcomes[0].actual, D(1))

    def test_a_tie_is_settled_not_void(self) -> None:
        v = self.settle("0.5000")
        self.assertEqual(v.outcome, "partial")
        self.assertEqual(v.leg_outcomes[0].outcome, "settled")
        self.assertEqual(v.detail["settlement"], "fair_value")
        self.assertIsNotNone(v.pnl)

    def test_a_fair_price_settlement_scores_at_that_price(self) -> None:
        """The inactive-player / 48h-postponement case: Kalshi pays 0.19 on YES."""
        v = self.settle("0.1900", side="no")
        self.assertEqual(v.leg_outcomes[0].actual, D("0.8100"))
        self.assertEqual(v.pnl, return_on_risk(D("51.75"), D("81.0000")))

    def test_finalized_without_a_value_is_an_error_not_a_guess(self) -> None:
        with self.assertRaises(KalshiError):
            self.settle(None)

    def test_maker_shadow_rides_in_detail_never_in_pnl(self) -> None:
        v = self.settle("1.0000")
        self.assertIn("maker", v.detail)
        self.assertEqual(v.detail["maker"]["fill"], "assumed_maker_trade_through")


class MakerShadow(unittest.TestCase):
    def fill(self, side: str, candles: list, limit: str = "0.49") -> dict[str, Any]:
        return maker_shadow(side=side, limit=D(limit), contracts=D(100), candles=candles,
                            regime=regime(), side_value=D(1))

    def test_trade_through_with_enough_volume_fills(self) -> None:
        out = self.fill("yes", [candle(T0, "0.47", "0.48", low="0.47", volume="500")])
        self.assertTrue(out["filled"])
        self.assertEqual(out["maker_fee_per_contract"], "0.004374")  # 0.00437325 rounded up

    def test_a_touch_is_not_a_fill(self) -> None:
        out = self.fill("yes", [candle(T0, "0.49", "0.50", low="0.49", volume="99999")])
        self.assertFalse(out["filled"])

    def test_through_but_thin_is_not_a_fill(self) -> None:
        out = self.fill("yes", [candle(T0, "0.47", "0.48", low="0.47", volume="150")])
        self.assertFalse(out["filled"])

    def test_no_bid_fills_on_a_yes_trade_above_one_minus_limit(self) -> None:
        # NO bid at 0.49 ⇔ YES trade above 0.51
        out = self.fill("no", [candle(T0, "0.51", "0.53", high="0.53", volume="500")])
        self.assertTrue(out["filled"])

    def test_quadratic_series_makers_pay_nothing(self) -> None:
        out = maker_shadow(side="yes", limit=D("0.49"), contracts=D(100),
                           candles=[candle(T0, "0.47", "0.48", low="0.47", volume="500")],
                           regime=regime("quadratic"), side_value=D(1))
        self.assertEqual(out["maker_fee_per_contract"], "0.000000")


class Capture(LedgerTestCase):
    def fake(self, *, status: str = "active", candles: list | None = None) -> FakeKalshi:
        return FakeKalshi(
            quotes={TICKER: quote(TICKER, status=status, settlement="0.5" if status == "finalized" else None)},
            candle_map={(TICKER, 1): candles if candles is not None else [
                candle(KICK - timedelta(minutes=2), "0.60", "0.62"),
                candle(KICK, "0.62", "0.64"),
                candle(KICK + timedelta(minutes=1), "0.10", "0.12"),   # in-play: must be ignored
            ]},
        )

    def test_close_is_the_last_mid_at_or_before_kickoff(self) -> None:
        close = Agent(self.fake()).capture_close(contract())
        self.assertEqual(close.price, D("0.63"))
        self.assertEqual(close.detail["rule"], "last_1m_mid_at_or_before_kickoff")

    def test_no_side_close_is_the_complement(self) -> None:
        close = Agent(self.fake()).capture_close(contract(side="no"))
        self.assertEqual(close.price, D("0.37"))

    def test_before_kickoff_settles_it_keeps_asking(self) -> None:
        agent = Agent(self.fake(), now=KICK + timedelta(minutes=2))
        self.assertIsNone(agent.capture_close(contract()))

    def test_a_short_postponement_waits_for_the_real_kickoff(self) -> None:
        later = KICK + timedelta(hours=24)
        agent = Agent(self.fake(), kickoff=later, now=KICK + timedelta(hours=1))
        self.assertIsNone(agent.capture_close(contract()))

    def test_postponed_beyond_48h_and_fair_priced_is_unavailable(self) -> None:
        agent = Agent(self.fake(status="finalized"), kickoff=KICK + timedelta(hours=72))
        with self.assertRaises(CloseUnavailable):
            agent.capture_close(contract())

    def test_postponed_beyond_48h_but_unsettled_keeps_asking(self) -> None:
        agent = Agent(self.fake(), kickoff=KICK + timedelta(hours=72))
        self.assertIsNone(agent.capture_close(contract()))

    def test_no_pre_kickoff_quote_is_an_error_to_retry(self) -> None:
        with self.assertRaises(KalshiError):
            Agent(self.fake(candles=[])).capture_close(contract())

    def test_game_dropped_from_schedule_keeps_asking(self) -> None:
        self.assertIsNone(Agent(self.fake(), kickoff=None).capture_close(contract()))


if __name__ == "__main__":
    unittest.main()
