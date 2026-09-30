"""The pre-registered `nfl_ml` holdout: checks H1–H5, run once.

    python -m jobs.holdout_nfl_ml --execute

docs/preregistration_nfl.md §5. The holdout is 2026 weeks 1–3 minus three
week-3 games whose data was displayed during API verification (45 games). It
is a **bug and calibration filter**: with ~45 games it cannot establish an
edge, and passing licenses only running forward on paper.

**Run once.** The runner refuses when any output already exists, when the
pre-registered λ has not been recorded, when an excluded game would be scored,
or without `--execute`. Results go to `docs/backtests/`, never to the ledger:
a commitment recorded after its outcome is known is exactly what CLAUDE.md §2
forbids.

**Same code as live.** Decisions come from the agent's own `form_thesis()` and
`build_commitment()`; scoring from its own `resolve()` and `capture_close()`.
Only the observation is rebuilt, from exchange-timestamped candles at the
commit instant t. Replay cannot see book depth (candles carry no sizes), so it
runs with `check_depth = False`; the pre-registration discloses that.

**Walk-forward.** Holdout week w is decided by a fit on every game with kickoff
before week w's first kickoff: all of 2025, plus earlier holdout weeks.

Checks:
    H1  leakage and determinism tests pass, and replaying twice decides identically
    H2  with every β = 0, zero commitments
    H3  Brier(p_model) ≤ Brier(mid at t) + 0.005, fair-value settlements excluded
    H4  props-only (not applicable to nfl_ml)
    H5  commits on ≤ 50% of eligible games, and mean net R ≤ +1.0
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import sys
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Final, Sequence

from adapters.nfl_ml import (
    COMMIT_LEAD, SERIES, GameObservation, NflMoneylineAgent, Observation,
)
from core.agent import Proposal
from core.ledger import PendingCommitment
from sports.nfl import injuries
from sports.nfl.ml_dataset import build_rows
from sports.nfl.ml_model import (
    ANCHOR_LEAD, FACTORS, LATE_WINDOW, STALE_AFTER, FactorModel, features_asof, fit_ridge,
)
from sports.nfl.schedule import Game, NflSchedule
from venues.kalshi.client import Candle, KalshiClient, Quote
from venues.kalshi.fees import FeeRegime, fetch_schedule

__all__ = ["main", "HOLDOUT_SEASON", "HOLDOUT_WEEKS", "EXCLUDED_GAMES", "PREREG_LAMBDA",
           "quote_at", "check_h2", "check_h3", "check_h5", "GameResult"]

log = logging.getLogger("valemont.holdout_nfl_ml")

HOLDOUT_SEASON: Final = 2026
HOLDOUT_WEEKS: Final = (1, 2, 3)
#: preregistration §1.1: displayed during API verification on 2026-09-30.
EXCLUDED_GAMES: Final = frozenset({"2026_03_BAL_DAL", "2026_03_LA_DEN", "2026_03_PHI_CHI"})
EXPECTED_GAMES: Final = 45

#: The λ fixed by amendment A3 (docs/preregistration_nfl.md), from the
#: walk-forward 2025 development run. The runner refuses if this is None.
PREREG_LAMBDA: Final[float | None] = 10.0

H3_TOLERANCE: Final = 0.005
H5_MAX_COMMIT_SHARE: Final = 0.50
H5_MAX_MEAN_R: Final = 1.0
RANDOM_SEED: Final = 20260930
BOOTSTRAP_DRAWS: Final = 10_000

OUTPUT_DIR: Final = Path("docs/backtests")
OUTPUT_STEM: Final = "nfl_ml-holdout"

#: The unit tests H1 requires to pass before anything is scored.
H1_TESTS: Final = (
    "tests.test_nfl_ml.Leakage",
    "tests.test_nfl_ml.LambdaIsChosenWalkForward",
    "tests.test_nfl_injury_timing",
    "tests.test_nfl_schedule.Kickoff",
)


# ---------------------------------------------------------------------------
# Rebuilding the observation at t
# ---------------------------------------------------------------------------

def quote_at(meta: Quote, minute: Sequence[Candle], t: datetime) -> Quote | None:
    """The market as it stood at t, from the latest 1-minute candle ending at or
    before t — or None when that candle is older than 30 minutes (stale, §1.2).

    NO prices are the complements of the same candle's YES prices. Sizes are
    unknown to replay and left None. Settlement fields are blanked: nothing at
    t knew them.
    """
    quoted = [c for c in minute if c.end <= t
              and c.yes_bid_close is not None and c.yes_ask_close is not None]
    if not quoted:
        return None
    last = max(quoted, key=lambda c: c.end)
    if t - last.end > STALE_AFTER:
        return None
    return Quote(
        ticker=meta.ticker, event_ticker=meta.event_ticker, status="active",
        yes_bid=last.yes_bid_close, yes_ask=last.yes_ask_close,
        no_bid=1 - last.yes_ask_close, no_ask=1 - last.yes_bid_close,  # type: ignore[operator]
        yes_ask_size=None, no_ask_size=None, result="", settlement_value=None,
        settlement_ts=None, expected_expiration=meta.expected_expiration,
        close_time=None, fetched_at=t,
    )


class ReplayAgent(NflMoneylineAgent):
    """The live agent with a replay clock, the replay schedule, and no depth
    check. It never writes: only pure hooks are called on it."""

    check_depth = False

    def __init__(self, *, client: KalshiClient, schedule: NflSchedule,
                 clock: Callable[[], datetime]) -> None:
        super().__init__(client=client, clock=clock,
                         load_schedule=lambda seasons: schedule)
        self._schedule = schedule

    def schedule(self, now: datetime) -> NflSchedule:
        return self._schedule


@dataclass
class GameResult:
    game_id: str
    week: int
    eligible: bool
    reason: str = ""
    mid_t: float | None = None
    p_model_home: float | None = None
    home_outcome: float | None = None
    committed: bool = False
    ticker: str = ""
    side: str = ""
    entry_price: float | None = None
    entry_cost: float | None = None
    pnl: float | None = None
    clv: float | None = None
    settlement: str = ""
    maker_filled: bool | None = None
    maker_pnl: float | None = None
    random_side_pnl: float | None = None
    factors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Checks — pure, so they can be tested without running the holdout
# ---------------------------------------------------------------------------

def check_h2(zero_model_commits: int) -> tuple[bool, str]:
    return zero_model_commits == 0, f"{zero_model_commits} commitments with every β = 0"


def check_h3(results: Sequence[GameResult]) -> tuple[bool, str, float, float]:
    scored = [r for r in results if r.eligible and r.mid_t is not None
              and r.p_model_home is not None and r.home_outcome in (0.0, 1.0)]
    if not scored:
        return False, "no scorable games", float("nan"), float("nan")
    b_model = sum((r.p_model_home - r.home_outcome) ** 2 for r in scored) / len(scored)  # type: ignore[operator]
    b_mid = sum((r.mid_t - r.home_outcome) ** 2 for r in scored) / len(scored)  # type: ignore[operator]
    ok = b_model <= b_mid + H3_TOLERANCE
    return ok, f"Brier model {b_model:.5f} vs mid {b_mid:.5f} (n={len(scored)})", b_model, b_mid


def check_h5(results: Sequence[GameResult]) -> tuple[bool, str]:
    eligible = [r for r in results if r.eligible]
    committed = [r for r in eligible if r.committed and r.pnl is not None]
    share = len(committed) / len(eligible) if eligible else 0.0
    mean_r = sum(r.pnl for r in committed) / len(committed) if committed else 0.0  # type: ignore[misc]
    ok = share <= H5_MAX_COMMIT_SHARE and mean_r <= H5_MAX_MEAN_R
    return ok, f"committed {len(committed)}/{len(eligible)} ({share:.0%}); mean net R {mean_r:+.3f}"


def bootstrap_ci(values: Sequence[float], seed: int = RANDOM_SEED) -> tuple[float, float] | None:
    """95% CI of the mean, resampling games (one value per game: clustered by
    game, §2.5)."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values)
                   for _ in range(BOOTSTRAP_DRAWS))
    return means[int(0.025 * BOOTSTRAP_DRAWS)], means[int(0.975 * BOOTSTRAP_DRAWS) - 1]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def refuse_reasons(games: Sequence[Game], lam: float | None, out_dir: Path) -> list[str]:
    reasons = []
    if lam is None:
        reasons.append("PREREG_LAMBDA is not recorded (amendment pending)")
    existing = sorted(out_dir.glob(f"{OUTPUT_STEM}*")) if out_dir.exists() else []
    if existing:
        reasons.append(f"holdout output already exists ({existing[0]}); it runs once")
    leaked = EXCLUDED_GAMES & {g.game_id for g in games}
    if leaked:
        reasons.append(f"excluded games in scope: {sorted(leaked)}")
    if len(games) != EXPECTED_GAMES:
        reasons.append(f"expected {EXPECTED_GAMES} holdout games, found {len(games)}")
    return reasons


def run_h1_tests() -> tuple[bool, str]:
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(H1_TESTS))
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    return result.wasSuccessful(), f"{result.testsRun} tests, {len(result.failures) + len(result.errors)} failed"


def fit_for_week(week_start: datetime, rows) -> FactorModel:
    assert PREREG_LAMBDA is not None
    train = [(r.features.values, r.y) for r in rows if r.kickoff < week_start]
    return fit_ridge(train, PREREG_LAMBDA)


def decide(agent: ReplayAgent, game: Game, home: Quote, away: Quote, hourly, minute,
           qb_home, qb_away, model: FactorModel, regime: FeeRegime, t: datetime,
           schedule: NflSchedule) -> list[Proposal]:
    obs = Observation(
        at=t, games=(GameObservation(game=game, home=home, away=away, hourly=tuple(hourly),
                                     minute=tuple(minute), qb_out_home=qb_home, qb_out_away=qb_away),),
        model_version=0, model=model, regime=regime, schedule_fetched_at=schedule.fetched_at,
    )
    thesis = agent.form_thesis(obs)
    return [] if thesis is None else agent.build_commitment(thesis)


def score(agent: ReplayAgent, proposal: Proposal, t: datetime) -> tuple[Any, Any]:
    pending = PendingCommitment(
        id=0, agent_id=0, agent_slug="nfl_ml", is_test=False, kind=proposal.kind,
        thesis=proposal.thesis, confidence=None, payload=proposal.payload,
        committed_at=t, resolves_after=proposal.resolves_after, legs=tuple(proposal.legs),
        closes_at=proposal.closes_at,
    )
    return agent.resolve(pending), agent.capture_close(pending)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true",
                    help="required: this runs the one-shot pre-registered holdout")
    args = ap.parse_args(argv)
    if not args.execute:
        ap.error("refusing without --execute: the holdout runs once")

    schedule = NflSchedule.fetch([2024, 2025, HOLDOUT_SEASON])
    games = sorted(
        (g for g in schedule.games
         if g.season == HOLDOUT_SEASON and g.week in HOLDOUT_WEEKS and g.game_id not in EXCLUDED_GAMES),
        key=lambda g: g.kickoff,
    )
    reasons = refuse_reasons(games, PREREG_LAMBDA, OUTPUT_DIR)
    if reasons:
        for r in reasons:
            log.error("refusing: %s", r)
        return 2

    h1_ok, h1_detail = run_h1_tests()
    if not h1_ok:
        log.error("H1 failed before scoring: %s", h1_detail)
        return 1

    client = KalshiClient()
    markets = client.historical_markets(series_ticker=SERIES) + client.markets(series_ticker=SERIES)
    fees = fetch_schedule(client, SERIES)
    reports, passing = injuries.load([2025, HOLDOUT_SEASON])
    dev_games = [g for g in schedule.games if g.season == 2025
                 or (g.season == HOLDOUT_SEASON and g.week < max(HOLDOUT_WEEKS)
                     and g.game_id not in EXCLUDED_GAMES)]
    rows, _ = build_rows(client=client, series=SERIES, markets=markets, schedule=schedule,
                         games=dev_games, reports=reports, passing=passing)

    by_event: dict[str, list[Quote]] = {}
    for q in markets:
        by_event.setdefault(q.event_ticker, []).append(q)
    event_for = {}
    for event, quotes in by_event.items():
        g = schedule.for_event(event)
        if g is not None:
            event_for[g.game_id] = quotes

    rng = random.Random(RANDOM_SEED)
    zero_model = FactorModel(betas={f: Decimal(0) for f in FACTORS}, lam=0.0)
    results: list[GameResult] = []
    zero_commits = 0
    determinism_ok = True
    for week in HOLDOUT_WEEKS:
        week_games = [g for g in games if g.week == week]
        model = fit_for_week(min(g.kickoff for g in week_games), rows)
        for game in week_games:
            t = game.kickoff - COMMIT_LEAD
            res = GameResult(game_id=game.game_id, week=week, eligible=False)
            results.append(res)
            quotes = event_for.get(game.game_id, [])
            suffix = {q.ticker.rsplit("-", 1)[-1]: q for q in quotes}
            hc, ac = game.kalshi_code(game.home, suffix), game.kalshi_code(game.away, suffix)
            if hc is None or ac is None:
                res.reason = "no Kalshi markets"
                continue
            home_meta, away_meta = suffix[hc], suffix[ac]
            settled = home_meta.settlement_ts
            hourly = client.candles(SERIES, home_meta.ticker, game.kickoff - ANCHOR_LEAD - timedelta(hours=2),
                                    t, 60, settled_at=settled)
            minute = (client.candles(SERIES, home_meta.ticker, t - LATE_WINDOW - STALE_AFTER,
                                     t - LATE_WINDOW, 1, settled_at=settled)
                      + client.candles(SERIES, home_meta.ticker, t - STALE_AFTER, t, 1, settled_at=settled))
            away_minute = client.candles(SERIES, away_meta.ticker, t - STALE_AFTER, t, 1,
                                         settled_at=away_meta.settlement_ts)
            home_q, away_q = quote_at(home_meta, minute, t), quote_at(away_meta, away_minute, t)
            if home_q is None or away_q is None:
                res.reason = "stale price at t"
                continue
            qb_h = injuries.qb_out(game, game.home, t, reports, passing, schedule)
            qb_a = injuries.qb_out(game, game.away, t, reports, passing, schedule)
            regime = fees.regime_at(t)
            features = features_asof(t=t, kickoff=game.kickoff, hourly=hourly, minute=minute,
                                     home_rest=game.home_rest, away_rest=game.away_rest,
                                     qb_out_home=qb_h, qb_out_away=qb_a)
            if features.mid_t is None:
                res.reason = "stale price at t"
                continue
            res.eligible = True
            res.mid_t = float((home_q.yes_bid + home_q.yes_ask) / 2)  # type: ignore[operator]
            res.p_model_home = min(0.999, max(0.001, res.mid_t + float(sum(
                model.contributions(features).values(), Decimal(0)))))
            res.home_outcome = None if home_meta.settlement_value is None else float(home_meta.settlement_value)

            agent = ReplayAgent(client=client, schedule=schedule,
                                clock=lambda: game.kickoff + timedelta(days=30))
            args_ = (agent, game, home_q, away_q, hourly, minute, qb_h, qb_a)
            proposals = decide(*args_, model, regime, t, schedule)
            if [p.payload for p in decide(*args_, model, regime, t, schedule)] != [p.payload for p in proposals]:
                determinism_ok = False
            zero_commits += len(decide(*args_, zero_model, regime, t, schedule))
            if not proposals:
                continue
            [proposal] = proposals
            verdict, close = score(agent, proposal, t)
            leg = proposal.legs[0]
            res.committed = True
            res.ticker, res.side = leg.subject, leg.direction or ""
            res.entry_price = float(leg.line)
            res.entry_cost = float(proposal.payload["entry_cost"])
            res.factors = {f.name: str(f.value) for f in proposal.factors}
            if verdict is not None:
                res.pnl = float(verdict.pnl) if verdict.pnl is not None else None
                res.settlement = verdict.detail.get("settlement", "")
                maker = verdict.detail.get("maker", {})
                res.maker_filled = maker.get("filled")
                res.maker_pnl = float(maker["pnl"]) if maker.get("pnl") else None
            if close is not None:
                res.clv = float(close.price) - res.entry_price
            # Random-side baseline at the same count: YES on a coin-flipped team.
            pick = home_q if rng.random() < 0.5 else away_q
            fee = float(regime.taker_fee(pick.yes_ask))  # type: ignore[arg-type]
            cost = float(pick.yes_ask) + fee  # type: ignore[arg-type]
            won = res.home_outcome if pick is home_q else (None if res.home_outcome is None else 1 - res.home_outcome)
            res.random_side_pnl = None if won is None else (won - cost) / cost

    h2 = check_h2(zero_commits)
    h3_ok, h3_detail, _, _ = check_h3(results)
    h5 = check_h5(results)
    h1 = (h1_ok and determinism_ok, f"{h1_detail}; replay deterministic: {determinism_ok}")
    passed = h1[0] and h2[0] and h3_ok and h5[0]
    write_report(results, {"H1": h1, "H2": h2, "H3": (h3_ok, h3_detail),
                           "H4": (True, "not applicable to nfl_ml (props only)"), "H5": h5}, passed)
    log.info("holdout %s", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


def write_report(results: list[GameResult], checks: dict[str, tuple[bool, str]], passed: bool) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.csv"
    md_path = OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.md"
    fields = list(GameResult.__dataclass_fields__)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: getattr(r, k) for k in fields})

    committed = [r for r in results if r.committed and r.pnl is not None]
    pnls = [r.pnl for r in committed]  # type: ignore[misc]
    clvs = [r.clv for r in committed if r.clv is not None]
    rnd = [r.random_side_pnl for r in committed if r.random_side_pnl is not None]
    fills = [r for r in committed if r.maker_filled]
    def fmt(vals: list[float]) -> str:
        if not vals:
            return "n/a"
        ci = bootstrap_ci(vals)
        mean = sum(vals) / len(vals)
        return f"{mean:+.4f}" + ("" if ci is None else f" (95% CI {ci[0]:+.4f} … {ci[1]:+.4f}, game-resampled)")
    lines = [
        f"# nfl_ml holdout — {'PASS' if passed else 'FAIL'}",
        "",
        f"Run {stamp} under docs/preregistration_nfl.md (λ = {PREREG_LAMBDA}). "
        "A bug and calibration filter: passing licenses running forward on paper, nothing else.",
        "",
        "| Check | Result | Detail |", "|---|---|---|",
        *[f"| {k} | {'pass' if ok else 'FAIL'} | {d} |" for k, (ok, d) in checks.items()],
        "",
        f"- Games: {len(results)}; eligible {sum(r.eligible for r in results)}; committed {len(committed)}",
        f"- Mean net R (taker, the record): {fmt(pnls)}",
        f"- Mean CLV (close mid − entry ask): {fmt(clvs)}",
        f"- Win rate (never the headline): {sum(p > 0 for p in pnls)}/{len(pnls)}" if pnls else "- Win rate: n/a",
        f"- Fair-value settlements: {sum(r.settlement == 'fair_value' for r in committed)}",
        f"- Maker shadow: filled {len(fills)}/{len(committed)}; mean maker R {fmt([r.maker_pnl for r in fills if r.maker_pnl is not None])}",
        "- Baseline, market-only: 0 commitments (H2)",
        f"- Baseline, random side at the same count: mean R {fmt(rnd)}",
        "",
        f"Per-game rows: `{csv_path.name}`.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("wrote %s and %s", md_path, csv_path)


if __name__ == "__main__":
    sys.exit(main())
