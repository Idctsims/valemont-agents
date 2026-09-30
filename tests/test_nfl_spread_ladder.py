"""The spread ladder: rungs restated on the home margin, and the normal fit."""

from __future__ import annotations

import unittest
from decimal import Decimal as D
from statistics import NormalDist

from sports.nfl.spread_model import Rung, fit_ladder, rung_from_ticker


class Rungs(unittest.TestCase):
    def test_home_rung_is_p_margin_over_x(self) -> None:
        r = rung_from_ticker("KXNFLSPREAD-26SEP28PHICHI-CHI3", D("3.5"), D("0.40"), {"CHI"}, {"PHI"})
        self.assertEqual(r, Rung(tau=3.5, p_over=0.40))

    def test_away_rung_flips_sign_and_complement(self) -> None:
        r = rung_from_ticker("KXNFLSPREAD-26SEP28PHICHI-PHI7", D("7.5"), D("0.25"), {"CHI"}, {"PHI"})
        self.assertEqual(r, Rung(tau=-7.5, p_over=0.75))

    def test_unknown_team_is_ignored(self) -> None:
        self.assertIsNone(rung_from_ticker("X-Y-KC3", D("3.5"), D("0.4"), {"CHI"}, {"PHI"}))


class Fit(unittest.TestCase):
    def ladder(self, mu: float, sigma: float) -> list[Rung]:
        nd = NormalDist(mu, sigma)
        return [Rung(tau, 1 - nd.cdf(tau)) for tau in (-10.5, -6.5, -3.5, -0.5, 2.5, 6.5, 9.5)]

    def test_recovers_mu_and_sigma(self) -> None:
        fit = fit_ladder(self.ladder(3.0, 13.5))
        self.assertAlmostEqual(fit.mu, 3.0, places=6)
        self.assertAlmostEqual(fit.sigma, 13.5, places=6)

    def test_needs_three_usable_rungs(self) -> None:
        self.assertIsNone(fit_ladder([Rung(0.5, 0.5), Rung(3.5, 0.4), Rung(40.5, 0.01)]))

    def test_a_ladder_sloping_the_wrong_way_is_refused(self) -> None:
        self.assertIsNone(fit_ladder([Rung(-3, 0.3), Rung(0, 0.5), Rung(3, 0.7)]))

    def test_slope_peaks_at_the_mean(self) -> None:
        fit = fit_ladder(self.ladder(0.0, 13.5))
        self.assertGreater(fit.slope_at(0.0), fit.slope_at(10.0))


if __name__ == "__main__":
    unittest.main()
