"""M1's analysis (scripts/research/m1_analysis.py): the definitions fixed before any fill was analyzed."""

import unittest
from collections import Counter

from scripts.research import m1_analysis as m1


def trade(tid, ticker, side, yes, n="10", t="2026-05-01T00:10:30Z"):
    return {"trade_id": tid, "ticker": ticker, "taker_outcome_side": side, "yes_price_dollars": f"{yes:.4f}",
            "no_price_dollars": f"{1 - yes:.4f}", "count_fp": n, "created_time": t, "is_block_trade": False}


class Edge(unittest.TestCase):
    def test_maker_edge_is_what_the_taker_paid_minus_what_its_side_settles_to(self):
        self.assertAlmostEqual(m1.maker_edge_c(trade("a", "C", "yes", 0.30), 0.0), 30.0)
        self.assertAlmostEqual(m1.maker_edge_c(trade("a", "C", "yes", 0.30), 1.0), -70.0)
        self.assertAlmostEqual(m1.maker_edge_c(trade("a", "C", "no", 0.30), 0.0), -30.0)  # NO paid 0.70, settles 1
        self.assertAlmostEqual(m1.maker_edge_c(trade("a", "C", "no", 0.30), 0.25), -5.0)  # fractional settlement


class Mid(unittest.TestCase):
    C = [{"end": 1000, "bid_close": "0.40", "ask_close": "0.44"},
         {"end": 1060, "bid_close": "0.41", "ask_close": "0.45"},
         {"end": 1120, "bid_close": "0.00", "ask_close": "0.70"}]

    def test_the_last_closed_minute_and_two_sided_only(self):
        self.assertAlmostEqual(m1.side_mid(self.C, 1100, "yes"), 0.43)
        self.assertAlmostEqual(m1.side_mid(self.C, 1100, "no"), 0.57)
        self.assertIsNone(m1.side_mid(self.C, 1130, "yes"))  # the latest closed minute is one-sided
        self.assertIsNone(m1.side_mid(self.C[:1], 1000 + m1.LOOKBACK_S + 1, "yes"))  # too old


class Bands(unittest.TestCase):
    def test_leg_and_price_bands(self):
        self.assertEqual([m1.leg_band(n) for n in (2, 3, 5, 6, 10, 11, 20, 21)],
                         ["2", "3-5", "3-5", "6-10", "6-10", "11-20", "11-20", "21+"])
        self.assertEqual([m1.price_band(p) for p in (0.05, 0.10, 0.3, 0.5, 0.99)],
                         ["<10c", "10-25c", "25-50c", "50-75c", "75c+"])
        self.assertEqual(m1.sport_of([{"event": "KXNBAPTS-26MAY01X"}, {"event": "KXNBAGAME-26MAY01X"}]), "NBA")
        self.assertEqual(m1.sport_of([{"event": "KXNBAPTS-26MAY01X"}, {"event": "KXNFLGAME-26MAY01X"}]), "mixed")


class Gate(unittest.TestCase):
    def combo(self, t, legs=2):
        return {"ticker": t, "series": "KXMVENBASINGLEGAME", "kind": "same-game", "close_time": "2026-05-01T05:00:00Z",
                "legs": [{"event": "KXNBAPTS-26APR30AAABBB", "market": f"KXNBAPTS-26APR30AAABBB-M{i}", "side": "yes"}
                         for i in range(legs)]}

    def test_cells_are_withheld_unless_e1_reproduces(self):
        sample = {f"C{i}": self.combo(f"C{i}") for i in range(4)}
        settle = {k: 0.0 for k in sample}
        far = [trade(f"t{i}", f"C{i}", "yes", 0.50) for i in range(4)]  # maker edge +50c: far from +3.3
        out = m1.analyze(sample, far, {}, settle, Counter())
        self.assertFalse(out["reproduction"]["reproduced"])
        self.assertIn("NOT COMPUTED", out["cells"])

    def test_when_reproduced_a_thin_cell_borrows_and_says_so(self):
        sample = {f"C{i}": self.combo(f"C{i}") for i in range(4)}
        # +3.0c maker edge: YES bought at 0.03 on combos that settle 0
        settle = {k: 0.0 for k in sample}
        trades = [trade(f"t{i}", f"C{i}", "yes", 0.03) for i in range(4)]
        candles = {f"KXNBAPTS-26APR30AAABBB-M{j}": [{"end": m1.ts_of("2026-05-01T00:10:00Z"), "bid_close": "0.10",
                                                     "ask_close": "0.20"}] for j in range(2)}
        out = m1.analyze(sample, trades, candles, settle, Counter())
        self.assertTrue(out["reproduction"]["reproduced"])
        cell = out["cells"]["same-game 2"]
        self.assertAlmostEqual(cell["mean_c"], (0.03 - 0.15 * 0.15) * 100, places=3)
        self.assertFalse(cell["number"])  # 4 combos < 300
        self.assertIsNone(cell["borrowed_from"])  # nothing shorter to borrow from
        self.assertEqual(out["cells"]["same-game 3-5"]["borrowed_from"], None)


if __name__ == "__main__":
    unittest.main()
