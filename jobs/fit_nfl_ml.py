"""Weekly walk-forward fit for `nfl_ml`. Writes one `model_versions` row.

    python -m jobs.fit_nfl_ml --through 2026-01-20T00:00:00Z --choose-lambda --dry-run
    python -m jobs.fit_nfl_ml --through 2026-10-06T12:00:00Z --lambda 1.0

Fits on every settled game with kickoff before `--through`, and records
`data_through` = the latest kickoff actually used, so the agent's read-time
fence (`latest_model_version(before=t)`) can never load a fit that saw t.

**Holdout guard.** 2026 games are refused unless `--allow-holdout` is passed.
The pre-registered holdout (2026 weeks 1–3) is evaluated once, before any fit
has seen it (preregistration_nfl.md §1.1, §5); forward fits may include those
weeks only after that evaluation. The flag exists so crossing that line is a
deliberate, visible act.

**λ.** `--choose-lambda` runs leave-one-week-out over `LAMBDA_GRID` and prints
the scores — a development-data step whose result is then recorded in the
pre-registration by amendment. Every later fit passes `--lambda` explicitly.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from core import ledger
from sports.nfl import injuries
from sports.nfl.ml_dataset import as_fit_rows, build_rows
from sports.nfl.ml_model import choose_lambda, fit_ridge
from sports.nfl.schedule import NflSchedule
from venues.kalshi.client import KalshiClient

SERIES = "KXNFLGAME"
MODEL = "nfl_ml_factor_ridge"
FIRST_GUARDED_SEASON = 2026

log = logging.getLogger("valemont.fit_nfl_ml")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--through", required=True, help="ISO-8601 UTC; fit on kickoffs before this")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--lambda", dest="lam", type=float)
    group.add_argument("--choose-lambda", action="store_true")
    ap.add_argument("--allow-holdout", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="fit and print; write nothing")
    args = ap.parse_args(argv)

    through = datetime.fromisoformat(args.through.replace("Z", "+00:00"))
    if through.tzinfo is None:
        ap.error("--through must carry a timezone")

    seasons = sorted({2024, 2025, through.year if through.month >= 3 else through.year - 1})
    schedule = NflSchedule.fetch(seasons)
    games = [g for g in schedule.games if g.kickoff < through and g.season >= 2025]
    guarded = [g for g in games if g.season >= FIRST_GUARDED_SEASON]
    if guarded and not args.allow_holdout:
        log.error("%d game(s) from %d+ fall before --through; refusing without "
                  "--allow-holdout (the holdout is evaluated before any fit sees it)",
                  len(guarded), FIRST_GUARDED_SEASON)
        return 2

    client = KalshiClient()
    markets = client.historical_markets(series_ticker=SERIES) + client.markets(series_ticker=SERIES)
    reports, passing = injuries.load(seasons)
    rows, skipped = build_rows(client=client, series=SERIES, markets=markets, schedule=schedule,
                               games=games, reports=reports, passing=passing)
    if len(rows) < 30:
        log.error("only %d usable rows (skipped: %d) — refusing to fit", len(rows), len(skipped))
        return 2

    scores = None
    if args.choose_lambda:
        lam, scores = choose_lambda([(r.week_key, r.features.values, r.y) for r in rows])
        log.info("leave-one-week-out scores: %s → λ=%s", scores, lam)
    else:
        lam = args.lam
    model = fit_ridge(as_fit_rows(rows), lam)
    data_through = max(r.kickoff for r in rows)
    diagnostics = {
        "n_rows": len(rows), "n_skipped": len(skipped), "skipped": skipped,
        "lambda_scores": scores, "through_arg": through.isoformat(),
        "coverage": {f: sum(r.features.values[f] is not None for r in rows)
                     for f in model.betas},
    }
    print(json.dumps({"params": model.to_params(), "data_through": data_through.isoformat(),
                      "diagnostics": diagnostics}, indent=2, default=str))
    if args.dry_run:
        return 0

    version = ledger.record_model_version(
        agent_id=ledger.agent_id("nfl_ml"), model=MODEL, data_through=data_through,
        params=model.to_params(), diagnostics=diagnostics,
    )
    log.info("recorded model_versions #%s (data through %s)", version, data_through)
    return 0


if __name__ == "__main__":
    sys.exit(main())
