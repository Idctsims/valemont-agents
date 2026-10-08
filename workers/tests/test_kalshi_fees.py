"""Kalshi fees: the schedule formula, per-series regimes, and regimes over time."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal as D

from tests.kalshi_fakes import regime
from venues.kalshi.edge import Gate, evaluate
from venues.kalshi.fees import (
    FeeRegime, FeeSchedule, UnsupportedFeeType, fetch_schedule,
)
from tests.kalshi_fakes import quote

UTC = timezone.utc


class TakerFee(unittest.TestCase):
    def test_matches_kalshis_own_worked_example(self) -> None:
        """docs fee_rounding: model fee $0.00363825 at $0.055, rounded up at 6 dp."""
        self.assertEqual(regime().taker_fee(D("0.055")), D("0.003639"))

    def test_peaks_at_fifty_cents(self) -> None:
        self.assertEqual(regime().taker_fee(D("0.50")), D("0.0175"))

    def test_multiplier_scales_it(self) -> None:
        self.assertEqual(regime(mult="2").taker_fee(D("0.50")), D("0.035"))

    def test_rejects_an_impossible_price(self) -> None:
        for bad in (D(0), D(1), D("1.2")):
            with self.subTest(price=bad), self.assertRaises(ValueError):
                regime().taker_fee(bad)


class MakerFeeComesFromFeeType(unittest.TestCase):
    """No per-series maker coefficient: the fetched fee_type decides."""

    def test_quadratic_series_charge_makers_nothing(self) -> None:
        self.assertEqual(regime("quadratic").maker_fee(D("0.50")), D(0))

    def test_maker_fee_series_charge_a_quarter_of_taker(self) -> None:
        self.assertEqual(regime("quadratic_with_maker_fees").maker_fee(D("0.50")), D("0.004375"))

    def test_combo_series_charge_half(self) -> None:
        self.assertEqual(regime("quadratic_with_combo_maker_fees").maker_fee(D("0.50")), D("0.00875"))

    def test_an_unmodelled_fee_type_is_refused_not_guessed(self) -> None:
        with self.assertRaises(UnsupportedFeeType):
            regime("flat")


class RegimeAsOf(unittest.TestCase):
    CHANGE = datetime(2026, 1, 1, 8, tzinfo=UTC)

    def schedule(self) -> FeeSchedule:
        return FeeSchedule("KXNFLGAME", (regime(effective_from=self.CHANGE),))

    def test_after_the_change_the_changed_regime_applies(self) -> None:
        r = self.schedule().regime_at(datetime(2026, 10, 1, tzinfo=UTC))
        self.assertEqual(r.fee_type, "quadratic_with_maker_fees")
        self.assertFalse(r.assumed)

    def test_before_any_published_regime_it_is_flagged_assumed(self) -> None:
        """KXNFLGAME's pre-2026 regime is not published; a 2025 trade says so."""
        r = self.schedule().regime_at(datetime(2025, 11, 1, tzinfo=UTC))
        self.assertTrue(r.assumed)
        self.assertTrue(r.as_payload()["assumed"])

    def test_a_scheduled_future_change_does_not_apply_early(self) -> None:
        future = datetime(2027, 1, 1, tzinfo=UTC)
        sched = FeeSchedule("X", (regime("quadratic", effective_from=self.CHANGE),
                                  regime("quadratic_with_maker_fees", effective_from=future)))
        self.assertEqual(sched.regime_at(datetime(2026, 10, 1, tzinfo=UTC)).fee_type, "quadratic")

    def test_naive_moment_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.schedule().regime_at(datetime(2026, 10, 1))


class FetchSchedule(unittest.TestCase):
    class Client:
        def series(self, s: str) -> dict:
            return {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}

        def series_fee_changes(self, s: str) -> list[dict]:
            return [{"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1,
                     "scheduled_ts": "2026-01-01T08:00:00Z", "series_ticker": s}]

    def test_history_becomes_dated_regimes_stamped_with_the_fetch(self) -> None:
        sched = fetch_schedule(self.Client(), "KXNFLGAME")  # type: ignore[arg-type]
        self.assertEqual(len(sched.regimes), 1)
        self.assertEqual(sched.regimes[0].effective_from, datetime(2026, 1, 1, 8, tzinfo=UTC))
        self.assertIsNotNone(sched.regimes[0].fetched_at.tzinfo)

    def test_no_history_uses_the_current_regime(self) -> None:
        class Bare(self.Client):
            def series_fee_changes(self, s: str) -> list[dict]:
                return []
        sched = fetch_schedule(Bare(), "KXNFLREC")  # type: ignore[arg-type]
        self.assertIsNone(sched.regimes[0].effective_from)


class EdgeIsAfterSpreadAndFee(unittest.TestCase):
    GATE = Gate(margin=D("0.02"), max_spread=D("0.03"), size=D(100))

    def test_agreeing_with_the_mid_never_passes(self) -> None:
        """p_model = mid: buying either side costs more than it is worth."""
        q = quote("KXNFLGAME-26OCT04ARINYG-NYG", bid="0.49", ask="0.51")
        cands = evaluate(q, D("0.50"), regime(), self.GATE)
        self.assertEqual(len(cands), 2)
        self.assertTrue(all(c.edge < 0 and not c.passes for c in cands))

    def test_edge_subtracts_the_fee(self) -> None:
        q = quote("M-X", bid="0.49", ask="0.50")
        yes = next(c for c in evaluate(q, D("0.60"), regime(), self.GATE) if c.side == "yes")
        self.assertEqual(yes.entry_cost, D("0.5175"))
        self.assertEqual(yes.edge, D("0.0825"))
        self.assertTrue(yes.passes)

    def test_no_side_is_priced_at_the_no_ask(self) -> None:
        q = quote("M-X", bid="0.49", ask="0.50")        # no_ask = 0.51
        no = next(c for c in evaluate(q, D("0.30"), regime(), self.GATE) if c.side == "no")
        self.assertEqual(no.entry_price, D("0.51"))
        self.assertEqual(no.p_model, D("0.70"))

    def test_wide_spread_blocks_even_a_big_edge(self) -> None:
        q = quote("M-X", bid="0.40", ask="0.50")
        yes = next(c for c in evaluate(q, D("0.80"), regime(), self.GATE) if c.side == "yes")
        self.assertFalse(yes.passes)
        self.assertIn("spread", yes.reason)

    def test_size_is_capped_at_displayed_depth(self) -> None:
        q = quote("M-X", bid="0.49", ask="0.50", size="40")
        yes = next(c for c in evaluate(q, D("0.60"), regime(), self.GATE) if c.side == "yes")
        self.assertEqual(yes.size, D(40))
        self.assertTrue(yes.passes)

    def test_no_displayed_depth_blocks(self) -> None:
        q = quote("M-X", bid="0.49", ask="0.50", size="0")
        yes = next(c for c in evaluate(q, D("0.60"), regime(), self.GATE) if c.side == "yes")
        self.assertFalse(yes.passes)


if __name__ == "__main__":
    unittest.main()
