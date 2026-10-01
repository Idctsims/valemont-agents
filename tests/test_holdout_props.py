"""The props holdout runner's frozen pieces and guards, on synthetic data.
Never runs the holdout."""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from jobs import holdout_nfl_props as h
from tests.kalshi_fakes import T0, game


def row(**kw) -> dict:
    base = dict(game_id="g1", player_id="p", stat="rec", bid=0.40, ask=0.44, mid=0.42,
                settle=1.0, p_v1=0.42, p_blend=0.42, p_p2=0.42, base=0.42, bucket="[0.4, 0.6)",
                ticker="T", t=T0.isoformat(), kickoff=(T0 + timedelta(minutes=75)).isoformat())
    base.update(kw)
    return base


class Frozen(unittest.TestCase):
    def test_frozen_constants_match_the_preregistration(self) -> None:
        self.assertEqual((h.W_BLEND, h.K_FLAT, h.MARGIN, h.MAX_SPREAD), (0.30, 0.145, 0.04, 0.08))

    def test_blend_and_p2_formulas(self) -> None:
        self.assertAlmostEqual(h.p_blend(0.6, 0.4), 0.46)
        self.assertAlmostEqual(h.p_p2(0.9, 0.5), 0.855 * 0.9 + 0.145 * 0.5)

    def test_buckets(self) -> None:
        self.assertEqual(h.bucket_of(0.05), "[0.0, 0.2)")
        self.assertEqual(h.bucket_of(0.95), "[0.8, 1.0]")


class BaseRateIsFencedBySettlement(unittest.TestCase):
    def test_only_settled_before_t_counts(self) -> None:
        settled = [("rec", 50.0, 1.0, T0 - timedelta(hours=1))] * 20
        not_yet = [("rec", 50.0, 0.0, T0 + timedelta(hours=1))] * 100   # kicked off, not settled
        self.assertEqual(h.base_rate(settled + not_yet, "rec", 49.5, T0), 1.0)

    def test_window_stat_and_minimum(self) -> None:
        pool = [("rec", 60.0, 1.0, T0 - timedelta(days=1))] * 19
        self.assertEqual(h.base_rate(pool, "rec", 54.5, T0), 0.5)      # 19 < 20
        pool += [("rush", 55.0, 1.0, T0 - timedelta(days=1))] * 50
        self.assertEqual(h.base_rate(pool, "rec", 54.5, T0), 0.5)      # other stat ignored


class Selection(unittest.TestCase):
    def test_taker_needs_edge_after_ask_and_fee(self) -> None:
        r = row(p_p2=0.50)                      # 0.50 − (0.44 + 0.0172) = 0.043 ≥ 0.04
        [(sel, side, price, cost)] = h.select([r], "p_p2", "taker")
        self.assertEqual((side, price), ("yes", 0.44))
        self.assertEqual(h.select([row(p_p2=0.49)], "p_p2", "taker"), [])

    def test_maker_measures_edge_against_the_bid(self) -> None:
        [(sel, side, price, cost)] = h.select([row(p_p2=0.45)], "p_p2", "maker")
        self.assertEqual((side, price), ("yes", 0.40))

    def test_one_per_player_stat_and_spread_cap(self) -> None:
        rs = [row(ticker="a", p_p2=0.60), row(ticker="b", p_p2=0.70)]
        self.assertEqual(len(h.select(rs, "p_p2", "taker")), 1)
        self.assertEqual(h.select([row(bid=0.30, ask=0.44, p_p2=0.9)], "p_p2", "taker"), [])


class ContinueRule(unittest.TestCase):
    def test_model_must_beat_the_base_rate_not_just_zero(self) -> None:
        import random
        rng = random.Random(3)
        rows = []
        for g in range(60):
            for _ in range(20):
                truth = rng.uniform(0.1, 0.9)
                y = 1.0 if rng.random() < truth else 0.0
                mid = min(0.99, max(0.01, truth + rng.gauss(0, 0.15)))
                rows.append(row(game_id=f"g{g}", settle=y, mid=mid, p_v1=truth, base=0.5))
        out = h.weight_comparison(rows)
        self.assertGreater(out["w_v1"], out["w_base"])
        self.assertTrue(out["continue"])

    def test_no_better_than_the_base_rate_does_not_continue(self) -> None:
        rows = [row(game_id=f"g{i}", settle=float(i % 2), mid=0.5, p_v1=0.5, base=0.5) for i in range(40)]
        self.assertFalse(h.weight_comparison(rows)["continue"])


class Guards(unittest.TestCase):
    def test_refusals(self) -> None:
        games = [game(T0 + timedelta(hours=i), game_id=f"2026_01_G{i}") for i in range(45)]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "nfl_props-holdout-x.json").write_text("{}")
            reasons = h.refuse_reasons(games, Path(d))
            self.assertTrue(any("runs once" in r for r in reasons))
        with tempfile.TemporaryDirectory() as d:
            bad = games[:44] + [game(T0, game_id="2026_03_PHI_CHI")]
            self.assertTrue(any("excluded" in r for r in h.refuse_reasons(bad, Path(d))))
            self.assertTrue(any("expected 45" in r for r in h.refuse_reasons(games[:44], Path(d))))

    def test_main_refuses_without_execute(self) -> None:
        with self.assertRaises(SystemExit):
            h.main([])


if __name__ == "__main__":
    unittest.main()
