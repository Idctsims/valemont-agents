"""`nfl_spread` development fit — 2025 only, walk-forward, dry run.

    python -m jobs.dev_nfl_spread

Pre-registration §3.2: fit the home-margin ladder `Normal(μ_t, σ_t)` at the
commit instant t = kickoff − 24 h and at the close, and regress μ_close − μ_t
on the same four factors as `nfl_ml` (ridge, no intercept, λ by walk-forward
validation, A2). Reports λ, coefficients, and the largest adjustment the
coefficients can produce against what a commitment needs.

Development only: reads the historical tier (2025), writes nothing to the
ledger, and touches no 2026 data. Output: docs/dev/nfl_spread-dev-fit-<date>.json.
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sports.nfl import injuries
from sports.nfl.ml_dataset import build_rows
from sports.nfl.ml_model import (
    FACTORS, STALE_AFTER, choose_lambda, fit_ridge, mid_at, walk_forward_predictions,
)
from sports.nfl.schedule import NflSchedule, kalshi_codes
from sports.nfl.spread_model import fit_ladder, rung_from_ticker
from venues.kalshi.client import KalshiClient

ML_SERIES = "KXNFLGAME"
SPREAD_SERIES = "KXNFLSPREAD"
COMMIT_LEAD = timedelta(hours=24)
MARGIN = 0.03                       # preregistration §2.2, nfl_spread
#: Feature extremes used for the "largest achievable adjustment" bound.
EXTREMES = {"line_movement": 0.30, "line_movement_late": 0.15, "rest": 7.0, "injury": 1.0}

log = logging.getLogger("valemont.dev_nfl_spread")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    schedule = NflSchedule.fetch([2024, 2025])
    games = [g for g in schedule.games if g.season == 2025]
    client = KalshiClient(min_interval=0.25)
    reports, passing = injuries.load([2024, 2025])

    ml_rows, _ = build_rows(client=client, series=ML_SERIES,
                            markets=client.historical_markets(series_ticker=ML_SERIES),
                            schedule=schedule, games=games, reports=reports, passing=passing)
    features = {r.game_id: r for r in ml_rows}

    spread_markets = client.historical_markets(series_ticker=SPREAD_SERIES)
    by_game: dict[str, list] = {}
    for q in spread_markets:
        g = schedule.for_event(q.event_ticker)
        if g is not None and g.season == 2025:
            by_game.setdefault(g.game_id, []).append(q)
    log.info("%d spread markets across %d 2025 games", len(spread_markets), len(by_game))

    rows, skipped, sigmas, spreads_at_t = [], {}, [], []
    for g in games:
        feat = features.get(g.game_id)
        quotes = by_game.get(g.game_id, [])
        if feat is None or not quotes:
            skipped[g.game_id] = "no ml features" if feat is None else "no spread markets"
            continue
        t = g.kickoff - COMMIT_LEAD
        home, away = set(kalshi_codes(g.home)), set(kalshi_codes(g.away))
        at_t, at_close = [], []
        for q in quotes:
            if q.floor_strike is None:
                continue
            candles = client.candles(SPREAD_SERIES, q.ticker, t - STALE_AFTER, g.kickoff, 1,
                                     settled_at=q.settlement_ts)
            m_t, m_c = mid_at(candles, t, STALE_AFTER), mid_at(candles, g.kickoff, None)
            if m_t is not None:
                r = rung_from_ticker(q.ticker, q.floor_strike, m_t, home, away)
                if r is not None:
                    at_t.append(r)
                    near = [c for c in candles if c.end <= t and c.spread is not None]
                    if near and 0.35 <= r.p_over <= 0.65:
                        spreads_at_t.append(float(max(near, key=lambda c: c.end).spread))
            if m_c is not None:
                r = rung_from_ticker(q.ticker, q.floor_strike, m_c, home, away)
                if r is not None:
                    at_close.append(r)
        fit_t, fit_c = fit_ladder(at_t), fit_ladder(at_close)
        if fit_t is None or fit_c is None:
            skipped[g.game_id] = "ladder unfittable at t or close"
            continue
        sigmas.append(fit_t.sigma)
        rows.append((g.season * 100 + g.week, feat.features.values, fit_c.mu - fit_t.mu))

    lam, scores = choose_lambda(rows)
    model = fit_ridge([(x, y) for _, x, y in rows], lam)
    preds = walk_forward_predictions(rows, lam)
    mse_model = sum((rows[i][2] - p) ** 2 for i, p in preds) / len(preds)
    mse_zero = sum(rows[i][2] ** 2 for i, _ in preds) / len(preds)

    sigma = statistics.median(sigmas)
    max_dmu = sum(abs(float(model.betas[f])) * EXTREMES[f] for f in FACTORS)
    slope_at_median = 1.0 / (sigma * (2 * 3.141592653589793) ** 0.5)
    max_dp = max_dmu * slope_at_median
    half_spread = statistics.median(spreads_at_t) / 2 if spreads_at_t else 0.005
    needed = 0.07 * 0.25 + half_spread + MARGIN          # at a 50¢ rung, where Δp peaks
    out = {
        "lambda": lam, "lambda_scores": scores,
        "betas_points": {k: str(v) for k, v in model.betas.items()},
        "n_rows": len(rows), "n_skipped": len(skipped), "skipped_reasons": sorted(set(skipped.values())),
        "walk_forward": {"n_out_of_sample": len(preds), "mse_model": mse_model, "mse_zero": mse_zero,
                         "improvement_pct": 100 * (1 - mse_model / mse_zero)},
        "target_sd_points": statistics.pstdev([y for _, _, y in rows]),
        "sigma_median_points": sigma,
        "max_adjustment_points": max_dmu,
        "max_adjustment_prob_at_median_rung": max_dp,
        "median_spread_near_50c": 2 * half_spread,
        "needed_prob_at_50c": needed,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(f"docs/dev/nfl_spread-dev-fit-{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
