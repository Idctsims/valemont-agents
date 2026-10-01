"""The CFB totals holdout runner's frozen pieces, on synthetic rows. Never runs
the holdout and never reaches the network."""

from __future__ import annotations

import random
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jobs import holdout_cfb_totals as h


def fee(p: float) -> float:
    return 0.07 * p * (1 - p)


def row(event: str = "KXNCAAFTOTAL-26SEP12AB", *, bid: float = 0.40, ask: float = 0.43,
        base: float = 0.60, settle: float = 1.0, volume: float = 70.0) -> dict:
    return {"event": event, "ticker": event + "-50", "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "base": base, "settle": settle, "volume_24h": volume,
            "taker_fee_yes": fee(ask), "taker_fee_no": fee(1 - bid),
            "maker_fee_yes": 0.25 * fee(bid), "maker_fee_no": 0.25 * fee(1 - ask)}


class Frozen(unittest.TestCase):
    def test_constants_match_the_preregistration(self) -> None:
        self.assertEqual(
            (h.SERIES, h.HOLDOUT_START, h.HOLDOUT_END, h.POOL_END, h.W_BLEND, h.WINDOW, h.MARGIN,
             h.MAX_SPREAD, h.PARTICIPATION, h.MIN_TRADES, h.MIN_GAMES, h.SEED, h.DRAWS),
            ("KXNCAAFTOTAL", date(2026, 7, 1), date(2026, 9, 27), date(2026, 6, 30), 0.625, 3.0,
             0.03, 0.06, 0.10, 20, 10, 20261002, 2000))

    def test_forecast(self) -> None:
        self.assertAlmostEqual(h.forecast(0.4, 0.8), 0.375 * 0.4 + 0.625 * 0.8)


class Scope(unittest.TestCase):
    def test_holdout_dates_and_series(self) -> None:
        self.assertTrue(h.in_holdout("KXNCAAFTOTAL-26JUL01ABCD"))
        self.assertTrue(h.in_holdout("KXNCAAFTOTAL-26SEP27ABCD"))
        self.assertFalse(h.in_holdout("KXNCAAFTOTAL-26JUN30ABCD"))
        self.assertFalse(h.in_holdout("KXNCAAFTOTAL-26SEP28ABCD"))
        self.assertFalse(h.in_holdout("KXNCAAFSPREAD-26SEP12ABCD"))
        with self.assertRaises(h.HoldoutScope):
            h.assert_holdout("KXNCAAFTOTAL-26OCT03ABCD")

    def test_refuses_when_output_exists(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(h.refuse_reasons(Path(d)), [])
            (Path(d) / f"{h.OUTPUT_STEM}-x.json").write_text("{}")
            self.assertTrue(h.refuse_reasons(Path(d)))


class Gate(unittest.TestCase):
    def test_edge_is_net_of_half_spread_and_fee(self) -> None:
        r = row(base=0.60)
        [tr] = h.select_taker([r])
        p = h.forecast(r["mid"], 0.60)
        half = (r["ask"] - r["bid"]) / 2
        self.assertEqual(tr["side"], "yes")
        self.assertAlmostEqual(tr["edge"], (p - r["mid"]) - half - fee(r["ask"]))
        self.assertAlmostEqual(tr["cost"], r["ask"] + fee(r["ask"]))

    def test_the_no_side_mirrors(self) -> None:
        [tr] = h.select_taker([row(base=0.05)])
        self.assertEqual(tr["side"], "no")
        self.assertAlmostEqual(tr["entry"], 0.60)

    def test_margin_and_spread_gates(self) -> None:
        self.assertEqual(h.select_taker([row(base=0.45)]), [])            # edge under 3¢
        self.assertEqual(h.select_taker([row(bid=0.30, ask=0.37, base=0.9)]), [])  # 7¢ spread

    def test_one_per_game_largest_edge(self) -> None:
        ev = "KXNCAAFTOTAL-26SEP12AB"
        small, big = row(ev, base=0.62), row(ev, base=0.80)
        [tr] = h.select_taker([small, big])
        self.assertIs(tr["row"], big)
        self.assertEqual(len(h.select_taker([big, row("KXNCAAFTOTAL-26SEP12CD", base=0.8)])), 2)

    def test_market_only_never_fires(self) -> None:
        rng = random.Random(3)
        rows = []
        for i in range(500):
            bid = rng.uniform(0.01, 0.95)
            rows.append(row(f"KXNCAAFTOTAL-26SEP12G{i}", bid=bid, ask=min(0.99, bid + rng.uniform(0.01, 0.06)),
                            base=rng.random()))
        self.assertEqual(h.select_taker(rows, w=0.0), [])

    def test_maker_cost_includes_the_maker_fee(self) -> None:
        [tr] = h.select_maker([row(base=0.6)])
        self.assertAlmostEqual(tr["cost"], 0.40 + 0.25 * fee(0.40))


class Scoring(unittest.TestCase):
    def test_r_both_sides_and_fair_value(self) -> None:
        self.assertAlmostEqual(h.r_of(1.0, "yes", 0.5), 1.0)
        self.assertAlmostEqual(h.r_of(1.0, "no", 0.5), -1.0)
        self.assertAlmostEqual(h.r_of(0.5, "yes", 0.4), 0.25)
        self.assertIsNone(h.r_of(None, "yes", 0.4))

    def test_verdict_needs_sample_and_a_positive_bound(self) -> None:
        self.assertTrue(h.verdict(20, 10, [0.01, 0.5]))
        self.assertFalse(h.verdict(19, 10, [0.01, 0.5]))
        self.assertFalse(h.verdict(20, 9, [0.01, 0.5]))
        self.assertFalse(h.verdict(20, 10, [0.0, 0.5]))
        self.assertFalse(h.verdict(50, 30, None))

    def test_capacity_is_ten_percent_of_rung_volume_at_cost(self) -> None:
        [tr] = h.select_taker([row(base=0.6, volume=70)])
        contracts, dollars = h.capacity(tr)
        self.assertAlmostEqual(contracts, 7.0)
        self.assertAlmostEqual(dollars, 7.0 * tr["cost"])


if __name__ == "__main__":
    unittest.main()
