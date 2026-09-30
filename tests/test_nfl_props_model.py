"""`nfl_props` dev-phase model: NB math, as-of fences, matching helpers."""

from __future__ import annotations

import math
import unittest
from datetime import timedelta

from jobs.dev_nfl_props import market_median, team_code_of
from sports.nfl.props_model import (
    LeagueContext, PlayerGame, PlayerHistory, estimate_mean, fit_size, nb_logpmf, nb_sf,
    normalize_name,
)
from tests.kalshi_fakes import T0, game


def pg(pid: str, days_ago: float, yards: int, *, pos: str = "WR", team: str = "HOU",
       opp: str = "KC", gid: str | None = None) -> PlayerGame:
    return PlayerGame(pid, pid, pos, team, opp, gid or f"g{pid}{days_ago}",
                      T0 - timedelta(days=days_ago), "rec", yards, 5)


class NegativeBinomial(unittest.TestCase):
    def test_pmf_sums_to_one(self) -> None:
        self.assertAlmostEqual(sum(math.exp(nb_logpmf(k, 40.0, 2.0)) for k in range(2000)), 1.0, places=6)

    def test_large_size_approaches_poisson(self) -> None:
        lam = 3.0
        poisson = 1 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(3))
        self.assertAlmostEqual(nb_sf(2.5, lam, 1e6), poisson, places=4)

    def test_tail_matches_the_rung_semantics(self) -> None:
        """'50+ yards' has floor_strike 49.5: P(X > 49.5) = P(X ≥ 50)."""
        self.assertAlmostEqual(nb_sf(49.5, 50.0, 2.0), 1 - sum(math.exp(nb_logpmf(k, 50.0, 2.0)) for k in range(50)))

    def test_tail_is_monotone(self) -> None:
        self.assertGreater(nb_sf(24.5, 50.0, 2.0), nb_sf(74.5, 50.0, 2.0))

    def test_fit_size_prefers_the_generating_dispersion(self) -> None:
        # Very overdispersed data: many zeros and some big games around mean 40.
        data = [(0, 40.0)] * 30 + [(120, 40.0)] * 10 + [(40, 40.0)] * 5
        self.assertLessEqual(fit_size(data), 1.0)


class AsOfFences(unittest.TestCase):
    def test_league_context_refuses_a_game_at_or_after_t(self) -> None:
        with self.assertRaises(ValueError):
            LeagueContext.build([pg("a", 0, 50)], T0)          # kickoff == t

    def test_mean_refuses_a_future_player_game(self) -> None:
        ctx = LeagueContext.build([pg("a", 7, 50)], T0)
        with self.assertRaises(ValueError):
            estimate_mean(player_games=[pg("a", -1, 90)], context=ctx, position="WR", opponent="KC")

    def test_history_admits_only_the_past(self) -> None:
        h = PlayerHistory([pg("a", 7, 50), pg("a", -7, 200)])
        self.assertEqual([g.yards for g in h.player_before("a", T0)], [50])
        self.assertEqual(len(h.before(T0)), 1)

    def test_future_games_cannot_move_the_mean(self) -> None:
        past = [pg("a", d, 40 + d) for d in (7, 14, 21)] + [pg("b", 7, 60, opp="KC")]
        future = [pg("a", -d, 999) for d in (7, 14)] + [pg("b", -7, 999, opp="KC")]
        h1, h2 = PlayerHistory(past), PlayerHistory(past + future)
        m1 = estimate_mean(player_games=h1.player_before("a", T0),
                           context=LeagueContext.build(h1.before(T0), T0), position="WR", opponent="KC")
        m2 = estimate_mean(player_games=h2.player_before("a", T0),
                           context=LeagueContext.build(h2.before(T0), T0), position="WR", opponent="KC")
        self.assertEqual(m1, m2)

    def test_shrinkage_pulls_a_one_game_player_toward_the_position(self) -> None:
        league = [pg(f"p{i}", 7, 40, gid=f"g{i}") for i in range(10)] + [pg("hot", 7, 200, gid="gh")]
        h = PlayerHistory(league)
        m = estimate_mean(player_games=h.player_before("hot", T0),
                          context=LeagueContext.build(h.before(T0), T0), position="WR", opponent="ZZZ")
        self.assertLess(m, 200)
        self.assertGreater(m, 40)


class Matching(unittest.TestCase):
    def test_names_normalize_across_punctuation_and_suffixes(self) -> None:
        self.assertEqual(normalize_name("Amon-Ra St. Brown Jr."), normalize_name("Amon-Ra St. Brown"))
        self.assertEqual(normalize_name("Marvin Harrison Jr."), "marvinharrison")

    def test_longest_team_code_wins(self) -> None:
        g = game(T0, away="LA", home="LAC")
        self.assertEqual(team_code_of("LACJHERBERT10", g), "LAC")
        self.assertEqual(team_code_of("LARPNACUA17", g), "LA")

    def test_market_median_interpolates_the_half_crossing(self) -> None:
        self.assertAlmostEqual(market_median([(24.5, 0.8), (49.5, 0.6), (74.5, 0.4)]), 62.0)
        self.assertIsNone(market_median([(24.5, 0.9), (49.5, 0.7)]))


if __name__ == "__main__":
    unittest.main()
