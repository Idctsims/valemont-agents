"""CLAUDE.md §9 — return on declared risk, and §9.0's gaming band."""

from __future__ import annotations

import unittest
from decimal import Decimal as D

from core.agent import (
    MAX_STOP_FRACTION, MIN_STOP_FRACTION, AgentError, declared_risk,
    directional_return, return_on_risk,
)


class DeclaredRiskAcrossDomains(unittest.TestCase):
    """capital_at_risk = |entry - stop| × size, one definition for all four."""

    def test_paper_long_stop_below(self) -> None:
        self.assertEqual(declared_risk(60000, 58200, 1), D(1800))

    def test_paper_short_stop_above(self) -> None:
        self.assertEqual(declared_risk(60000, 61800, 1), D(1800))

    def test_long_and_short_are_symmetric(self) -> None:
        self.assertEqual(
            declared_risk(60000, 58200, 1), declared_risk(60000, 61800, 1)
        )

    def test_size_scales_risk(self) -> None:
        self.assertEqual(declared_risk(60000, 58200, D("0.5")), D(900))

    def test_prop_slip_stake_is_the_stop(self) -> None:
        self.assertEqual(declared_risk(1, 0, 1), D(1))

    def test_event_contract_yes(self) -> None:
        self.assertEqual(declared_risk(D("0.34"), 0, 100), D(34))

    def test_event_contract_no_uses_one_minus_price(self) -> None:
        self.assertEqual(declared_risk(1 - D("0.34"), 0, 100), D(66))

    def test_equal_entry_and_stop_is_unscoreable(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(100, 100, 1)

    def test_non_positive_entry_rejected(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(-5, -10, 1)


class WorkedExamples(unittest.TestCase):
    """The §9 worked block: the same 1R move scores the same in every domain."""

    def test_all_four_favorable_score_plus_one(self) -> None:
        self.assertEqual(directional_return(60000, 58200, 61800, "long"), D(1))
        self.assertEqual(directional_return(60000, 61800, 58200, "short"), D(1))
        self.assertEqual(return_on_risk(declared_risk(1, 0, 1), D("2.0")), D(1))
        self.assertEqual(
            return_on_risk(declared_risk(D("0.50"), 0, 1), D("1.00")), D(1)
        )

    def test_all_four_fully_wrong_score_minus_one(self) -> None:
        self.assertEqual(directional_return(60000, 58200, 58200, "long"), D(-1))
        self.assertEqual(directional_return(60000, 61800, 61800, "short"), D(-1))
        self.assertEqual(return_on_risk(declared_risk(1, 0, 1), 0), D(-1))
        self.assertEqual(return_on_risk(declared_risk(D("0.50"), 0, 1), 0), D(-1))

    def test_exit_at_entry_is_a_push(self) -> None:
        self.assertEqual(directional_return(60000, 58200, 60000, "long"), D(0))
        self.assertEqual(directional_return(60000, 61800, 60000, "short"), D(0))

    def test_prop_three_x_hit(self) -> None:
        self.assertEqual(return_on_risk(1, 3), D(2))

    def test_yes_contract_settles_one(self) -> None:
        self.assertEqual(return_on_risk(D("0.34"), 1), D("0.66") / D("0.34"))

    def test_no_contract_wins_when_yes_settles_zero(self) -> None:
        # (price - settlement) / (1 - price), price being the YES price.
        yes_price, settlement = D("0.34"), D(0)
        expected = (yes_price - settlement) / (1 - yes_price)
        self.assertEqual(return_on_risk(1 - yes_price, 1), expected)

    def test_no_contract_loses_when_yes_settles_one(self) -> None:
        self.assertEqual(return_on_risk(1 - D("0.34"), 0), D(-1))

    def test_zero_capital_at_risk_raises(self) -> None:
        with self.assertRaises(AgentError):
            return_on_risk(0, 1)

    def test_long_stop_on_wrong_side_rejected(self) -> None:
        with self.assertRaises(AgentError):
            directional_return(60000, 61800, 62000, "long")

    def test_short_stop_on_wrong_side_rejected(self) -> None:
        with self.assertRaises(AgentError):
            directional_return(60000, 58200, 58000, "short")


class GamingBand(unittest.TestCase):
    """§9.0: a chosen stop must sit in [0.5%, 25%] of entry. Structural 0 is exempt."""

    ENTRY = D(60000)

    def test_constants_are_the_documented_band(self) -> None:
        self.assertEqual(MIN_STOP_FRACTION, D("0.005"))
        self.assertEqual(MAX_STOP_FRACTION, D("0.25"))

    def test_floor_is_inclusive_long(self) -> None:
        self.assertEqual(declared_risk(self.ENTRY, 59700, 1), D(300))

    def test_inside_floor_rejected_long(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(self.ENTRY, 59701, 1)

    def test_floor_is_inclusive_short(self) -> None:
        self.assertEqual(declared_risk(self.ENTRY, 60300, 1), D(300))

    def test_inside_floor_rejected_short(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(self.ENTRY, 60299, 1)

    def test_ceiling_is_inclusive_long(self) -> None:
        self.assertEqual(declared_risk(self.ENTRY, 45000, 1), D(15000))

    def test_beyond_ceiling_rejected_long(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(self.ENTRY, 44999, 1)

    def test_ceiling_is_inclusive_short(self) -> None:
        self.assertEqual(declared_risk(self.ENTRY, 75000, 1), D(15000))

    def test_beyond_ceiling_rejected_short(self) -> None:
        with self.assertRaises(AgentError):
            declared_risk(self.ENTRY, 75001, 1)

    def test_tiny_stop_cannot_manufacture_a_huge_multiple(self) -> None:
        # A 0.01% stop would turn a 1% move into +100R.
        with self.assertRaises(AgentError):
            directional_return(self.ENTRY, 59994, 60600, "long")

    def test_structural_zero_stop_is_exempt_from_the_ceiling(self) -> None:
        # 100% of entry, far past 25% — allowed because nothing was chosen.
        self.assertEqual(declared_risk(D("0.34"), 0, 1), D("0.34"))
        self.assertEqual(declared_risk(1, 0, 1), D(1))

    def test_directional_return_enforces_the_band_too(self) -> None:
        with self.assertRaises(AgentError):
            directional_return(self.ENTRY, 40000, 61000, "long")


if __name__ == "__main__":
    unittest.main()
