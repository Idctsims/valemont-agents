"""Props development variants: fences, usage prior, vacated usage, calibration,
environment, and the trade-through maker fill rule."""

from __future__ import annotations

import math
import random
import unittest
from datetime import timedelta

from jobs.dev_props_blend import w_star
from jobs.dev_props_maker import fill
from sports.nfl.props_model import LeagueContext, PlayerGame, PlayerHistory, estimate_mean
from sports.nfl.props_variants import (
    environment_factor, fit_calibrator, opportunity_shares, usage_mean, vacated_factor,
)
from tests.kalshi_fakes import T0


def pg(pid, days, yards, opps, *, pos="WR", team="HOU", opp="KC", gid=None) -> PlayerGame:
    return PlayerGame(pid, pid, pos, team, opp, gid or f"{pid}-{days}", T0 - timedelta(days=days),
                      "rec", yards, opps)


class UsagePrior(unittest.TestCase):
    def league(self):
        # Many low-usage WRs (2 targets, 15 yds) and one starter (10 targets, 80 yds).
        games = [pg(f"low{i}", d, 15, 2, gid=f"g{i}{d}") for i in range(12) for d in (7, 14, 21)]
        games += [pg("star", d, 80, 10, gid=f"s{d}") for d in (7, 14, 21)]
        return PlayerHistory(games)

    def test_starter_is_not_dragged_toward_low_usage_players(self) -> None:
        h = self.league()
        ctx = LeagueContext.build(h.before(T0), T0)
        v0 = estimate_mean(player_games=h.player_before("star", T0), context=ctx, position="WR", opponent="ZZZ")
        v1 = usage_mean(player_games=h.player_before("star", T0), league_before=h.before(T0),
                        context=ctx, position="WR", opponent="ZZZ")
        self.assertLess(v0, v1, "V1 should remove the volume shrinkage behind the low bias")
        self.assertGreater(v1, 60)

    def test_fence_refuses_future_games(self) -> None:
        h = self.league()
        ctx = LeagueContext.build(h.before(T0), T0)
        with self.assertRaises(ValueError):
            usage_mean(player_games=[pg("star", -1, 999, 30)], league_before=h.before(T0),
                       context=ctx, position="WR", opponent="KC")


class VacatedUsage(unittest.TestCase):
    def test_out_starter_share_is_redistributed_proportionally(self) -> None:
        shares = {"wr1": 0.40, "wr2": 0.30, "te": 0.30}
        f2 = vacated_factor("wr2", shares, {"wr1"})
        fte = vacated_factor("te", shares, {"wr1"})
        self.assertAlmostEqual(f2, 1 + 0.40 * 0.30 / 0.60)
        self.assertAlmostEqual(f2, fte)

    def test_a_minor_absence_vacates_nothing(self) -> None:
        self.assertEqual(vacated_factor("wr1", {"wr1": 0.9, "wr5": 0.10}, {"wr5"}), 1.0)

    def test_shares_come_from_the_past_only(self) -> None:
        games = [pg("a", 7, 50, 6, gid="g1"), pg("b", 7, 30, 4, gid="g1")]
        s = opportunity_shares("HOU", "rec", games, T0)
        self.assertAlmostEqual(s["a"], 0.6)
        with self.assertRaises(ValueError):
            opportunity_shares("HOU", "rec", games + [pg("a", -1, 1, 99)], T0)


class Calibration(unittest.TestCase):
    def test_recovers_a_known_distortion(self) -> None:
        rng = random.Random(1)
        pairs = []
        for _ in range(5000):
            p_true = rng.uniform(0.05, 0.95)
            z = math.log(p_true / (1 - p_true))
            p_reported = 1 / (1 + math.exp(-(z - 0.5) / 1.5))     # biased low, too flat
            pairs.append((p_reported, 1.0 if rng.random() < p_true else 0.0))
        cal = fit_calibrator(pairs)
        self.assertAlmostEqual(cal.a, 0.5 / 1.5 * 1.5, delta=0.15)
        self.assertAlmostEqual(cal.b, 1.5, delta=0.15)

    def test_too_little_data_is_identity(self) -> None:
        cal = fit_calibrator([(0.3, 1.0)] * 10)
        self.assertAlmostEqual(cal(0.3), 0.3)


class Environment(unittest.TestCase):
    def test_ratio_and_missing(self) -> None:
        self.assertAlmostEqual(environment_factor(27.5, 22.0), 1.25)
        self.assertEqual(environment_factor(None, 22.0), 1.0)


class MakerFill(unittest.TestCase):
    def test_yes_bid_needs_volume_strictly_below_the_limit(self) -> None:
        self.assertTrue(fill("yes", 0.40, [(0.39, 150), (0.38, 60)]))
        self.assertFalse(fill("yes", 0.40, [(0.40, 999)]))           # a touch is not a fill
        self.assertFalse(fill("yes", 0.40, [(0.39, 199)]))           # under 2 × size

    def test_no_bid_fills_on_yes_prints_above_one_minus_limit(self) -> None:
        self.assertTrue(fill("no", 0.55, [(0.46, 250)]))             # NO 0.54 < 0.55
        self.assertFalse(fill("no", 0.55, [(0.45, 250)]))            # NO 0.55: a touch


class BlendWeight(unittest.TestCase):
    def test_closed_form_minimizer(self) -> None:
        # Brier(w) = see + 2w·sed + w²·sdd  →  w* = −sed / sdd, clipped.
        self.assertAlmostEqual(w_star(1.0, -0.3, 1.0), 0.3)
        self.assertEqual(w_star(1.0, 0.5, 1.0), 0.0)
        self.assertEqual(w_star(1.0, -5.0, 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
