"""`nfl_ml` against its pre-registration: leakage, NULLs, the fit, the gate,
and a full run_once through the stub ledger. No network, no database."""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from typing import ClassVar

from adapters.nfl_ml import GATE, MODEL, NflMoneylineAgent, SERIES
from core import ledger
from core.ledger import Leg
from sports.nfl import injuries
from sports.nfl.ml_model import (
    FACTORS, FactorModel, choose_lambda, features_asof, fit_ridge,
)
from tests.kalshi_fakes import (
    T0, FakeKalshi, candle, fee_schedule, flat_candles, game, quote, schedule,
)
from tests.support import TEST_AGENTS, LedgerTestCase, pending
from venues.kalshi.client import Candle

KICK = T0 + timedelta(hours=23)                 # inside the kickoff − 24 h window
HOME = "KXNFLGAME-26OCT04BALDAL-DAL"
AWAY = "KXNFLGAME-26OCT04BALDAL-BAL"


class TestAgent(NflMoneylineAgent):
    slug: ClassVar[str] = "_test_nfl_ml"


def market_candles(t: datetime, *, then_bid: str = "0.40", now_bid: str = "0.49"
                   ) -> tuple[list[Candle], list[Candle]]:
    """Hourly from the 6-day anchor to t, 1-minute around t − 24 h and t."""
    hourly = flat_candles(KICK - timedelta(days=6, hours=2), t - timedelta(hours=25),
                          timedelta(hours=1), then_bid, f"{D(then_bid) + D('0.01')}")
    hourly += flat_candles(t - timedelta(hours=24), t, timedelta(hours=1),
                           now_bid, f"{D(now_bid) + D('0.01')}")
    minute = flat_candles(t - timedelta(hours=24, minutes=30), t - timedelta(hours=24),
                          timedelta(minutes=1), "0.45", "0.46")
    minute += flat_candles(t - timedelta(minutes=30), t, timedelta(minutes=1),
                           now_bid, f"{D(now_bid) + D('0.01')}")
    return hourly, minute


def features(t: datetime = T0, **kw):
    hourly, minute = market_candles(t)
    args = dict(t=t, kickoff=KICK, hourly=hourly, minute=minute,
                home_rest=10, away_rest=6, qb_out_home=0, qb_out_away=1)
    args.update(kw)
    return features_asof(**args)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class Features(unittest.TestCase):
    def test_all_four_factors(self) -> None:
        f = features()
        self.assertEqual(f.mid_t, D("0.495"))
        self.assertEqual(f.values["line_movement"], D("0.090"))       # 0.495 − 0.405
        self.assertEqual(f.values["line_movement_late"], D("0.040"))  # 0.495 − 0.455
        self.assertEqual(f.values["rest"], D(4))
        self.assertEqual(f.values["injury"], D(1))                    # away QB out

    def test_rest_is_clipped(self) -> None:
        self.assertEqual(features(home_rest=14, away_rest=4).values["rest"], D(7))

    def test_thin_anchor_window_makes_line_movement_null(self) -> None:
        hourly, minute = market_candles(T0)
        thin = [Candle(c.end, c.yes_bid_close, c.yes_ask_close, c.trade_low, c.trade_high,
                       c.trade_close, D(0), None) for c in hourly]
        f = features_asof(t=T0, kickoff=KICK, hourly=thin, minute=minute,
                          home_rest=7, away_rest=7, qb_out_home=0, qb_out_away=0)
        self.assertIsNone(f.values["line_movement"])

    def test_stale_price_at_t_nulls_the_price_dependent_factors(self) -> None:
        hourly, minute = market_candles(T0)
        f = features_asof(t=T0, kickoff=KICK, hourly=hourly,
                          minute=[c for c in minute if c.end < T0 - timedelta(minutes=31)],
                          home_rest=7, away_rest=7, qb_out_home=0, qb_out_away=0)
        self.assertIsNone(f.mid_t)
        self.assertIsNone(f.values["line_movement"])

    def test_unknown_qb_status_is_null_not_zero(self) -> None:
        self.assertIsNone(features(qb_out_home=None).values["injury"])


class Leakage(unittest.TestCase):
    """§4.1: randomize everything after t; nothing may move."""

    def test_candles_after_t_cannot_change_any_feature(self) -> None:
        hourly, minute = market_candles(T0)
        rng = random.Random(7)
        def noise(start: datetime, step: timedelta, n: int) -> list[Candle]:
            out = []
            for i in range(n):
                b = D(rng.randint(1, 97)) / 100
                out.append(candle(start + step * (i + 1), str(b), str(b + D("0.01")),
                                  volume=str(rng.randint(0, 10**6))))
            return out
        base = features_asof(t=T0, kickoff=KICK, hourly=hourly, minute=minute,
                             home_rest=7, away_rest=6, qb_out_home=0, qb_out_away=0)
        for trial in range(20):
            poisoned = features_asof(
                t=T0, kickoff=KICK,
                hourly=hourly + noise(T0, timedelta(hours=1), 30),
                minute=minute + noise(T0, timedelta(minutes=1), 300),   # includes the close
                home_rest=7, away_rest=6, qb_out_home=0, qb_out_away=0,
            )
            self.assertEqual(poisoned, base, f"trial {trial}: a post-t candle moved a feature")


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

class Fit(unittest.TestCase):
    def synthetic(self, n: int = 400, null_every: int = 0) -> list[tuple[dict, float]]:
        rng = random.Random(3)
        true = {"line_movement": 0.5, "line_movement_late": -0.2, "rest": 0.002, "injury": 0.03}
        rows = []
        for i in range(n):
            x = {"line_movement": D(str(round(rng.gauss(0, 0.05), 4))),
                 "line_movement_late": D(str(round(rng.gauss(0, 0.02), 4))),
                 "rest": D(rng.randint(-7, 7)), "injury": D(rng.choice([-1, 0, 0, 0, 1]))}
            y = sum(true[k] * float(v) for k, v in x.items()) + rng.gauss(0, 0.002)
            if null_every and i % null_every == 0:
                x["injury"] = None
            rows.append((x, y))
        return rows

    def test_recovers_known_coefficients(self) -> None:
        m = fit_ridge(self.synthetic(), lam=1e-6)
        self.assertAlmostEqual(float(m.betas["line_movement"]), 0.5, places=1)
        self.assertAlmostEqual(float(m.betas["injury"]), 0.03, places=2)

    def test_nulls_are_per_factor_complete_cases(self) -> None:
        m = fit_ridge(self.synthetic(null_every=3), lam=1e-6)
        self.assertAlmostEqual(float(m.betas["injury"]), 0.03, places=2)

    def test_lambda_shrinks_toward_zero(self) -> None:
        loose, tight = fit_ridge(self.synthetic(), 1e-6), fit_ridge(self.synthetic(), 1e4)
        self.assertLess(abs(tight.betas["line_movement"]), abs(loose.betas["line_movement"]))

    def test_leave_one_week_out_returns_a_grid_value_and_every_score(self) -> None:
        rows = [(i % 8, x, y) for i, (x, y) in enumerate(self.synthetic(160))]
        lam, scores = choose_lambda(rows)
        self.assertIn(lam, scores)
        self.assertEqual(len(scores), 5)

    def test_params_round_trip(self) -> None:
        m = fit_ridge(self.synthetic(), 1.0)
        self.assertEqual(FactorModel.from_params(m.to_params()).betas, m.betas)
        self.assertFalse(m.to_params()["intercept"])


# ---------------------------------------------------------------------------
# The agent, end to end
# ---------------------------------------------------------------------------

def model_version(betas: dict[str, str], through: datetime = T0 - timedelta(days=2)) -> ledger.ModelVersion:
    return ledger.ModelVersion(
        id=41, agent_id=TEST_AGENTS["_test_nfl_ml"], model=MODEL, fitted_at=through,
        data_through=through, params={"betas": betas, "lambda": 1.0}, diagnostics={},
    )


class EndToEnd(LedgerTestCase):
    def build(self, *, home_bid: str = "0.49", betas: dict[str, str] | None = None,
              qb_out_away: bool = True) -> TestAgent:
        hourly, minute = market_candles(T0, now_bid=home_bid)
        exp = KICK + timedelta(hours=6)
        hb = D(home_bid)
        fake = FakeKalshi(
            quotes={
                HOME: quote(HOME, bid=home_bid, ask=str(hb + D("0.01")), expected_expiration=exp),
                AWAY: quote(AWAY, bid=str(1 - hb - D("0.01")), ask=str(1 - hb), expected_expiration=exp),
            },
            candle_map={(HOME, 60): hourly, (HOME, 1): minute},
        )
        g = game(KICK)
        reports = injuries.InjuryReports([
            injuries.InjuryRow(2026, 5, "BAL", "qb_bal", "QB", "Out" if qb_out_away else ""),
            injuries.InjuryRow(2026, 5, "DAL", "qb_dal", "QB", ""),
        ])
        prior = game(KICK - timedelta(days=7), game_id="2026_04_BAL_DAL")
        passing = [injuries.PassingRow("qb_bal", "BAL", prior.game_id, 30),
                   injuries.PassingRow("qb_dal", "DAL", prior.game_id, 30)]
        self.ledger.model_versions.append(model_version(betas or {f: "0" for f in FACTORS}))
        return TestAgent(
            client=fake, clock=lambda: T0,
            load_schedule=lambda seasons: schedule(g, prior),
            load_injuries=lambda seasons: (reports, passing),
            load_fees=lambda client, series: fee_schedule(),
            load_model=ledger.latest_model_version,
        )

    def commits(self) -> list:
        return self.ledger.named("commit")

    def test_market_only_model_commits_nothing(self) -> None:
        """Holdout check H2: with every β = 0, edge = mid − ask − fee < 0 always."""
        outcome = self.build().run_once()
        self.assertFalse(outcome.committed)
        self.assertEqual(self.commits(), [])

    def test_no_usable_model_stands_down(self) -> None:
        agent = self.build()
        self.ledger.model_versions.clear()
        agent.run_once()
        self.assertEqual(self.commits(), [])

    def test_a_model_fit_that_saw_t_is_never_loaded(self) -> None:
        agent = self.build(betas={"injury": "0.2"})
        self.ledger.model_versions[:] = [model_version({"injury": "0.2"}, through=T0)]
        agent.run_once()
        self.assertEqual(self.commits(), [])

    def test_a_real_edge_commits_one_contract_as_preregistered(self) -> None:
        agent = self.build(betas={"injury": "0.10"})     # away QB out → home +10pp
        agent.run_once()
        [call] = self.commits()
        kw = call.kwargs
        [leg] = kw["legs"]
        self.assertEqual(kw["kind"], "event_contract")
        self.assertEqual(kw["closes_at"], KICK)                      # kickoff as-of
        self.assertEqual(kw["resolves_after"], KICK + timedelta(hours=6))  # API expected_expiration
        self.assertEqual(leg.subject, HOME)
        self.assertEqual(leg.direction, "yes")
        self.assertEqual(leg.line, D("0.50"))                         # the ask, side-held
        self.assertEqual(leg.size, D(100))
        p = kw["payload"]
        self.assertEqual(p["entry_cost"], "0.517500")                 # 0.50 + 0.0175
        self.assertEqual(p["invalidation"], "0")
        self.assertEqual(p["kickoff_source"], "nflverse")
        self.assertEqual(p["model_version"], 41)
        self.assertEqual(p["fee_regime"]["fee_type"], "quadratic_with_maker_fees")
        self.assertEqual({f.name for f in kw["factors"]}, {"injury"})
        self.assertEqual(kw["factors"][0].value, D("0.100000"))

    def test_an_away_edge_holds_the_away_team_and_flips_factor_signs(self) -> None:
        agent = self.build(betas={"injury": "-0.10"})
        agent.run_once()
        [call] = self.commits()
        leg = call.kwargs["legs"][0]
        holds_away = (leg.subject == AWAY and leg.direction == "yes") or \
                     (leg.subject == HOME and leg.direction == "no")
        self.assertTrue(holds_away, f"{leg.subject} {leg.direction}")
        self.assertEqual(call.kwargs["factors"][0].value, D("0.100000"))

    def test_edge_below_the_margin_does_not_commit(self) -> None:
        agent = self.build(betas={"injury": "0.03"})    # +3pp < 1¢ spread-half + 1.75¢ fee + 2¢
        agent.run_once()
        self.assertEqual(self.commits(), [])

    def test_a_game_already_held_is_not_committed_again(self) -> None:
        agent = self.build(betas={"injury": "0.10"})
        self.ledger.open.append(pending(
            1, agent_id=TEST_AGENTS["_test_nfl_ml"], slug="_test_nfl_ml",
            legs=(Leg(AWAY, SERIES, D("0.5"), "yes", D(100)),)))
        agent.run_once()
        self.assertEqual(self.commits(), [])

    def test_margin_is_the_preregistered_two_cents(self) -> None:
        self.assertEqual((GATE.margin, GATE.max_spread, GATE.size),
                         (D("0.02"), D("0.03"), D(100)))


if __name__ == "__main__":
    unittest.main()
