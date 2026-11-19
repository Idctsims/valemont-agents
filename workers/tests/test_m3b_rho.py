"""M3b's pure parts: the row pick, the tetrachoric estimate, the walk-forward lines and the pair counts."""

import random
import unittest

from consensus.correlation import Phi_inv
from scripts.research import m3b_rho as m3b


class Pick(unittest.TestCase):
    def test_only_box_score_rows_and_the_quota(self):
        rows = {"game|cover|win": 0.9, "team|pts|cover": 0.5, "team|pts|over": 0.4}
        rows |= {f"self|pts|k{i}": i / 100 for i in range(20)}
        rows |= {f"self|{a}|{b}": 0.1 for a, b in (("pts", "reb"), ("ast", "reb"), ("pa", "pr"), ("ar", "td"),
                                                    ("dd", "reb"), ("blk", "stl"), ("sb", "tpm"), ("pts", "tpm"),
                                                    ("ast", "td"))}
        rows |= {f"mate|pts|{b}": 0.01 for b in ("reb", "ast", "tpm", "stl", "blk")}
        rows |= {f"opp|pts|{b}": -0.01 for b in ("reb", "ast", "tpm", "stl", "blk")}
        rows |= {f"team|{a}|win": 0.2 for a in ("pts", "reb", "ast", "tpm", "stl")}
        picked = m3b.pick_rows(rows)
        self.assertEqual(len(picked), 20)
        self.assertEqual(len(set(picked)), 20)
        self.assertTrue(all(m3b.reproducible(k) for k in picked))
        self.assertNotIn("game|cover|win", picked)
        self.assertFalse(any(k.startswith("self|pts|k") for k in picked))  # unknown kinds are not reproducible
        self.assertEqual(m3b.pick_rows(rows), picked)  # deterministic


class Tetrachoric(unittest.TestCase):
    def test_recovers_a_known_rho(self):
        rng = random.Random(1)
        for true in (-0.4, 0.0, 0.6):
            t = [0, 0, 0, 0]
            for _ in range(60000):
                z1 = rng.gauss(0, 1)
                z2 = true * z1 + (1 - true * true) ** 0.5 * rng.gauss(0, 1)
                t[2 * (z1 > 0.3) + (z2 > -0.2)] += 1
            self.assertAlmostEqual(m3b.tetrachoric(t), true, delta=0.03)

    def test_degenerate_tables(self):
        self.assertIsNone(m3b.tetrachoric([0, 0, 0, 0]))
        self.assertIsNone(m3b.tetrachoric([5, 5, 0, 0]))


def row(pid, team, game, date, pts, mins="30:00", wl="W", season="2024-25"):
    return {"PLAYER_ID": pid, "TEAM_ID": team, "GAME_ID": game, "GAME_DATE": date, "MIN": mins, "WL": wl,
            "PTS": pts, "REB": 5, "AST": 3, "STL": 1, "BLK": 0, "FG3M": 2, "SEASON_KEY": season}


class WalkForward(unittest.TestCase):
    def test_needs_ten_earlier_games_and_uses_only_the_past(self):
        rows = [row(1, 10, f"g{i:02d}", f"2024-11-{i + 1:02d}", 20) for i in range(10)]
        rows.append(row(1, 10, "g10", "2024-11-20", 25))
        rows.append(row(1, 10, "g11", "2024-11-21", 15))
        games = m3b.outcomes(rows)
        self.assertNotIn("g09", games)  # only 9 earlier games
        self.assertEqual(games["g10"][0]["over"]["pts"], 1)  # line 20.5
        self.assertEqual(games["g11"][0]["over"]["pts"], 0)  # median of 10x20 and 25 is 20 -> 20.5

    def test_low_minutes_never_qualify(self):
        rows = [row(1, 10, f"g{i:02d}", f"2024-11-{i + 1:02d}", 20, mins="10:00") for i in range(12)]
        self.assertEqual(m3b.outcomes(rows), {})


class Pairs(unittest.TestCase):
    def test_mate_opp_self_team_counts(self):
        g = [{"player": 1, "team": 1, "win": 1, "over": {"pts": 1, "reb": 0}},
             {"player": 2, "team": 1, "win": 1, "over": {"pts": 1, "reb": 1}},
             {"player": 3, "team": 2, "win": 0, "over": {"pts": 0, "reb": 1}}]
        self.assertEqual(sum(m3b.table_for("mate|pts|pts", g)), 1)  # one unordered teammate pair
        self.assertEqual(sum(m3b.table_for("mate|pts|reb", g)), 2)  # both orders
        self.assertEqual(sum(m3b.table_for("opp|pts|pts", g)), 2)   # 1-3, 2-3
        self.assertEqual(sum(m3b.table_for("opp|pts|reb", g)), 4)   # every cross ordered pair
        self.assertEqual(m3b.table_for("self|pts|reb", g), [0, 1, 1, 1])
        self.assertEqual(m3b.table_for("team|pts|win", g), [1, 0, 0, 2])


if __name__ == "__main__":
    unittest.main()
