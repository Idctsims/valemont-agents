"""M4's pure parts: the four methods per row, the consensus span, and the frozen claim rule."""

import unittest
from decimal import Decimal

from scripts.research import m4_devig as m4


def leg(**kw):
    base = dict(market_ticker="KXT-1", league="nba", market_kind="game_line", tier="books", n_books=3,
                p_books=Decimal("0.60"), p_kalshi=Decimal("0.55"), p_consensus=Decimal("0.585"),
                yes_ask=Decimal("0.54"), no_ask=Decimal("0.47"), fee_yes=Decimal("0.02"), fee_no=Decimal("0.02"),
                edge_yes=Decimal("0.025"), edge_no=Decimal("-0.075"), edge_claimed=True,
                stale_kalshi=False, stale_outside=False, starts_at=None,
                p_books_lo=Decimal("0.59"), p_books_hi=Decimal("0.60"))
    return base | kw


class Four(unittest.TestCase):
    def test_even_prices_agree_everywhere(self):
        ps = m4.four(-110, -110)
        for p in ps.values():
            self.assertAlmostEqual(p, 0.5, places=12)

    def test_lopsided_prices_spread_and_bad_prices_refuse(self):
        ps = m4.four(-900, 600)
        self.assertGreater(max(ps.values()) - min(ps.values()), 0.005)
        self.assertIsNone(m4.four(50, -110))

    def test_band(self):
        self.assertEqual(m4.band_of(0.55), "0.5-0.6")
        self.assertEqual(m4.band_of(0.95), "0.9-1.0")
        self.assertEqual(m4.band_of(1.0), "0.9-1.0")


class Consensus(unittest.TestCase):
    def test_needs_two_books_and_takes_medians(self):
        rows, _ = m4.per_row([
            dict(sports_event_id=1, market="ml", subject=None, line=None, source="o", book=b, league="nba",
                 price_a=a, price_b=bb) for b, a, bb in (("x", -200, 170), ("y", -210, 175))]
            + [dict(sports_event_id=2, market="ml", subject=None, line=None, source="o", book="x", league="nba",
                    price_a=-110, price_b=-110)])
        cons = m4.consensus_lines(rows)
        self.assertEqual(len(cons), 1)
        self.assertEqual(cons[0]["n_books"], 2)
        self.assertGreaterEqual(cons[0]["span_c"], 0)


class ClaimRule(unittest.TestCase):
    def test_passes_when_least_favourable_keeps_the_margin(self):
        # YES power edge 2.5c; least favourable drops 0.7 x 1c = 0.7c -> 1.8c < 2c: positive, not the margin.
        c = m4.claim_test([leg()])["counts"]
        self.assertEqual(c["sides positive under all four"], 1)
        self.assertEqual(c.get("sides pass the rule (all four > 0, least >= margin)", 0), 0)
        c = m4.claim_test([leg(edge_yes=Decimal("0.03"))])["counts"]
        self.assertEqual(c["sides pass the rule (all four > 0, least >= margin)"], 1)

    def test_no_side_uses_the_high_median(self):
        c = m4.claim_test([leg(edge_yes=Decimal("-0.05"), edge_no=Decimal("0.03"), p_books_hi=Decimal("0.62"))])["counts"]
        # NO drop = 0.7 x 2c = 1.4c -> 1.6c: positive under all four, below the margin.
        self.assertEqual(c["sides positive under all four"], 1)
        self.assertEqual(c.get("sides pass the rule (all four > 0, least >= margin)", 0), 0)

    def test_no_span_never_passes_and_the_blend_is_checked(self):
        out = m4.claim_test([leg(p_books_lo=None, p_books_hi=None, edge_yes=Decimal("0.10"))])
        self.assertEqual(out["counts"].get("sides pass the rule (all four > 0, least >= margin)", 0), 0)
        self.assertEqual(out["counts"].get("sides with a span", 0), 0)
        self.assertAlmostEqual(out["blend_check_max_abs"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
