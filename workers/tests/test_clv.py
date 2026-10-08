"""CLAUDE.md §10 — closing line value, priced on the side we hold."""

from __future__ import annotations

import unittest
from decimal import Decimal as D

from core.agent import AgentError, closing_line_value


class ClosingLineValue(unittest.TestCase):

    def test_yes_market_came_to_us(self) -> None:
        clv, pct = closing_line_value(D("0.34"), D("0.40"))
        self.assertEqual(clv, D("0.06"))
        self.assertEqual(pct, D("0.06") / D("0.34"))

    def test_no_market_left_us(self) -> None:
        clv, pct = closing_line_value(D("0.66"), D("0.60"))
        self.assertEqual(clv, D("-0.06"))
        self.assertEqual(pct, D("-0.06") / D("0.66"))

    def test_no_market_came_to_us_is_positive(self) -> None:
        clv, _ = closing_line_value(D("0.66"), D("0.70"))
        self.assertEqual(clv, D("0.04"))

    def test_sides_mirror_each_other(self) -> None:
        yes_entry, yes_close = D("0.34"), D("0.40")
        yes_clv, _ = closing_line_value(yes_entry, yes_close)
        no_clv, _ = closing_line_value(1 - yes_entry, 1 - yes_close)
        self.assertEqual(no_clv, -yes_clv)

    def test_pct_distinguishes_cheap_from_expensive_contracts(self) -> None:
        _, cheap = closing_line_value(D("0.10"), D("0.16"))
        _, dear = closing_line_value(D("0.80"), D("0.86"))
        self.assertEqual(cheap, D("0.6"))
        self.assertEqual(dear, D("0.075"))

    def test_zero_close_is_a_real_close(self) -> None:
        # The market wrote our side off: the most negative CLV there is.
        clv, pct = closing_line_value(D("0.40"), D(0))
        self.assertEqual(clv, D("-0.40"))
        self.assertEqual(pct, D(-1))

    def test_close_of_one_is_allowed(self) -> None:
        clv, _ = closing_line_value(D("0.40"), D(1))
        self.assertEqual(clv, D("0.60"))

    def test_zero_entry_is_undefined(self) -> None:
        with self.assertRaises(AgentError):
            closing_line_value(D(0), D("0.40"))

    def test_close_above_one_rejected(self) -> None:
        with self.assertRaises(AgentError):
            closing_line_value(D("0.40"), D("1.01"))

    def test_negative_close_rejected(self) -> None:
        with self.assertRaises(AgentError):
            closing_line_value(D("0.40"), D("-0.01"))

    def test_non_finite_rejected(self) -> None:
        with self.assertRaises(AgentError):
            closing_line_value(D("0.40"), D("NaN"))


if __name__ == "__main__":
    unittest.main()
