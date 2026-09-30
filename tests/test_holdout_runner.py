"""The holdout runner's guards and checks, on synthetic data only.

These tests never run the holdout: no schedule is fetched, no Kalshi call is
made, and `main()` is only exercised far enough to prove it refuses.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from jobs import holdout_nfl_ml as h
from sports.nfl.ml_model import FACTORS, FactorModel
from tests.kalshi_fakes import (
    T0, FakeKalshi, candle, flat_candles, game, quote, regime, schedule,
)
from tests.support import LedgerTestCase


def result(**kw) -> h.GameResult:
    base = dict(game_id="g", week=1, eligible=True)
    base.update(kw)
    return h.GameResult(**base)


class Refusals(unittest.TestCase):
    def games(self, n: int = 45):
        return [game(T0 + timedelta(hours=i), game_id=f"2026_01_G{i}") for i in range(n)]

    def test_refuses_while_lambda_is_unrecorded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("PREREG_LAMBDA is not recorded (amendment pending)",
                          h.refuse_reasons(self.games(), None, Path(d)))

    def test_refuses_a_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "nfl_ml-holdout-20261001T000000Z.md").write_text("done")
            reasons = h.refuse_reasons(self.games(), 1.0, Path(d))
            self.assertTrue(any("runs once" in r for r in reasons))

    def test_refuses_if_an_excluded_game_is_in_scope(self) -> None:
        games = self.games(44) + [game(T0, game_id="2026_03_BAL_DAL")]
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(any("excluded" in r for r in h.refuse_reasons(games, 1.0, Path(d))))

    def test_refuses_the_wrong_game_count(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(any("expected 45" in r for r in h.refuse_reasons(self.games(44), 1.0, Path(d))))

    def test_clean_state_has_no_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(h.refuse_reasons(self.games(), 1.0, Path(d)), [])

    def test_main_refuses_without_execute(self) -> None:
        with self.assertRaises(SystemExit):
            h.main([])

    def test_lambda_is_the_amended_value(self) -> None:
        """Amendment A3: λ = 10.0 from the 2025 walk-forward development run."""
        self.assertEqual(h.PREREG_LAMBDA, 10.0)

    def test_exclusions_match_the_preregistration(self) -> None:
        self.assertEqual(h.EXCLUDED_GAMES,
                         {"2026_03_BAL_DAL", "2026_03_LA_DEN", "2026_03_PHI_CHI"})
        self.assertEqual((h.HOLDOUT_SEASON, h.HOLDOUT_WEEKS, h.EXPECTED_GAMES), (2026, (1, 2, 3), 45))


class QuoteAtT(unittest.TestCase):
    META = quote("KXNFLGAME-26SEP13BALDAL-DAL", status="finalized", settlement="1",
                 expected_expiration=T0 + timedelta(hours=30))

    def test_rebuilds_the_book_from_the_last_candle_at_or_before_t(self) -> None:
        q = h.quote_at(self.META, [candle(T0 - timedelta(minutes=5), "0.55", "0.57"),
                                   candle(T0 + timedelta(minutes=1), "0.10", "0.12")], T0)
        self.assertEqual((q.yes_bid, q.yes_ask, q.no_bid, q.no_ask),
                         (D("0.55"), D("0.57"), D("0.43"), D("0.45")))

    def test_settlement_is_blanked_nothing_at_t_knew_it(self) -> None:
        q = h.quote_at(self.META, [candle(T0, "0.55", "0.57")], T0)
        self.assertIsNone(q.settlement_value)
        self.assertEqual(q.status, "active")
        self.assertIsNone(q.yes_ask_size)

    def test_stale_candle_gives_no_quote(self) -> None:
        self.assertIsNone(h.quote_at(self.META, [candle(T0 - timedelta(minutes=31), "0.5", "0.51")], T0))


class Checks(unittest.TestCase):
    def test_h2_passes_only_on_zero(self) -> None:
        self.assertTrue(h.check_h2(0)[0])
        self.assertFalse(h.check_h2(1)[0])

    def test_h3_compares_brier_to_the_mid_and_skips_fair_values(self) -> None:
        rs = [result(mid_t=0.6, p_model_home=0.6, home_outcome=1.0),
              result(mid_t=0.4, p_model_home=0.4, home_outcome=0.0),
              result(mid_t=0.5, p_model_home=0.9, home_outcome=0.5)]   # tie: excluded
        ok, detail, b_model, b_mid = h.check_h3(rs)
        self.assertTrue(ok)
        self.assertAlmostEqual(b_model, b_mid)
        self.assertIn("n=2", detail)

    def test_h3_fails_a_model_worse_than_the_market(self) -> None:
        rs = [result(mid_t=0.6, p_model_home=0.2, home_outcome=1.0)]
        self.assertFalse(h.check_h3(rs)[0])

    def test_h5_commit_share_cap(self) -> None:
        rs = [result(committed=True, pnl=0.1), result(committed=True, pnl=-0.2), result()]
        self.assertFalse(h.check_h5(rs)[0])       # 2/3 > 50%
        self.assertTrue(h.check_h5(rs + [result(), result()])[0])

    def test_h5_absurd_mean_r_is_a_failure(self) -> None:
        rs = [result(committed=True, pnl=1.5)] + [result() for _ in range(9)]
        self.assertFalse(h.check_h5(rs)[0])

    def test_bootstrap_is_seeded_and_brackets_the_mean(self) -> None:
        vals = [0.1, -0.2, 0.3, -1.0, 0.9, 0.0]
        lo, hi = h.bootstrap_ci(vals)
        self.assertLess(lo, sum(vals) / len(vals))
        self.assertGreater(hi, sum(vals) / len(vals))
        self.assertEqual(h.bootstrap_ci(vals), (lo, hi))


class ReplayUsesTheLiveHooksAndWritesNothing(LedgerTestCase):
    """decide() and score() on synthetic markets: real agent code, zero writes."""

    def test_decide_then_score(self) -> None:
        kick = T0 + timedelta(hours=24)
        t = kick - timedelta(hours=24)
        home_t, away_t = "KXNFLGAME-26OCT04BALDAL-DAL", "KXNFLGAME-26OCT04BALDAL-BAL"
        exp = kick + timedelta(hours=6)
        fake = FakeKalshi(
            quotes={home_t: quote(home_t, status="finalized", settlement="1", expected_expiration=exp),
                    away_t: quote(away_t, status="finalized", settlement="0", expected_expiration=exp)},
            candle_map={
                (home_t, 1): flat_candles(t - timedelta(hours=25), kick, timedelta(minutes=1), "0.49", "0.50"),
                (home_t, 60): flat_candles(kick - timedelta(days=7), t, timedelta(hours=1), "0.49", "0.50"),
            },
        )
        g = game(kick)
        sched = schedule(g)
        agent = h.ReplayAgent(client=fake, schedule=sched, clock=lambda: kick + timedelta(days=30))
        minute = fake.candle_map[(home_t, 1)]
        home_q = h.quote_at(fake.quotes[home_t], minute, t)
        away_q = h.quote_at(fake.quotes[away_t], [c for c in minute], t)
        model = FactorModel(betas={f: D("0") for f in FACTORS} | {"injury": D("0.2")}, lam=10.0)
        proposals = h.decide(agent, g, home_q, away_q, fake.candle_map[(home_t, 60)],
                             [c for c in minute if c.end <= t], 0, 1, model, regime(), t, sched)
        self.assertEqual(len(proposals), 1)
        verdict, close = h.score(agent, proposals[0], t)
        self.assertEqual(verdict.outcome, "hit")
        self.assertIsNotNone(close)
        self.assertEqual(self.ledger.writes(), [], "replay wrote to the ledger")

    def test_the_zero_model_decides_nothing(self) -> None:
        kick = T0 + timedelta(hours=24)
        t = kick - timedelta(hours=24)
        home_t, away_t = "KXNFLGAME-26OCT04BALDAL-DAL", "KXNFLGAME-26OCT04BALDAL-BAL"
        fake = FakeKalshi(quotes={home_t: quote(home_t, expected_expiration=kick + timedelta(hours=6)),
                                  away_t: quote(away_t, expected_expiration=kick + timedelta(hours=6))})
        minute = flat_candles(t - timedelta(hours=25), t, timedelta(minutes=1), "0.49", "0.50")
        hourly = flat_candles(kick - timedelta(days=7), t, timedelta(hours=1), "0.49", "0.50")
        g = game(kick)
        agent = h.ReplayAgent(client=fake, schedule=schedule(g), clock=lambda: t)
        zero = FactorModel(betas={f: D("0") for f in FACTORS}, lam=0.0)
        self.assertEqual(h.decide(agent, g, h.quote_at(fake.quotes[home_t], minute, t),
                                  h.quote_at(fake.quotes[away_t], minute, t), hourly, minute,
                                  0, 0, zero, regime(), t, schedule(g)), [])


if __name__ == "__main__":
    unittest.main()
