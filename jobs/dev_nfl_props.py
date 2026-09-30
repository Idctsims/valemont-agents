"""`nfl_props` development phase — 2025 yardage props only, walk-forward, dry run.

    python -m jobs.dev_nfl_props

Scope (owner, 2026-09-30): passing, rushing and receiving yards
(`KXNFLPASSYDS`, `KXNFLRSHYDS`, `KXNFLRECYDS`), 2025 weeks where the series
exist (week 6 onward), per-player negative binomial refit weekly on earlier
weeks only. Commit instant t = kickoff − 75 min (preregistration §2.6).

Reports:
  - MAE of the model mean per stat, against the ~27-yard anchor and against
    the market's own implied median at t (the rung where P(over) crosses 0.5)
  - calibration of P(over) against settlement, and against the Kalshi mid at t
  - edge at t under taker (ask + fee) and maker (resting at the bid; props are
    `quadratic`, so maker-free), its distribution against the 4¢ margin, and
    what the gated candidates would have returned (development data:
    descriptive, not a claim)

No 2026 data: historical tier and the 2024–2025 nflverse files only. Nothing
is written to the ledger. Output: docs/dev/nfl_props-dev-<date>.json.

Disclosed simplifications of this phase: the environment term of §3.3 is 1.0
(implied team points not fetched); eligibility cannot see game-day inactives
(nflverse has no inactive list), so some evaluated players were inactive and
settled at Kalshi's fair price — counted, and excluded from calibration.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import random
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sports.nfl import injuries
from sports.nfl.injuries import OUT_STATUSES, STATS_URL, fetch_text, report_public_by
from sports.nfl.props_model import (
    LeagueContext, PlayerGame, PlayerHistory, estimate_mean, fit_size, match_player,
    nb_sf, normalize_name,
)
from sports.nfl.schedule import Game, NflSchedule, kalshi_codes
from venues.kalshi.client import KalshiClient, Quote
from venues.kalshi.fees import TAKER_COEFFICIENT

log = logging.getLogger("valemont.dev_nfl_props")

SERIES = {"pass": "KXNFLPASSYDS", "rush": "KXNFLRSHYDS", "rec": "KXNFLRECYDS"}
COLUMNS = {"pass": ("passing_yards", "attempts"), "rush": ("rushing_yards", "carries"),
           "rec": ("receiving_yards", "targets")}
COMMIT_LEAD = timedelta(minutes=75)
STALE_AFTER = timedelta(minutes=60)
MARGIN = 0.04
MAX_SPREAD = 0.08
MAE_ANCHOR = 27.0
MIN_RECENT_ACTIVE = 2          # of the team's previous 4 games
SEED = 20260930


@dataclass
class RungEval:
    game_id: str
    week: int
    stat: str
    player_id: str
    position: str
    floor: float
    p_model: float
    bid: float
    ask: float
    settle: float | None
    mean: float
    size: float


def load_player_games(schedule: NflSchedule, seasons: list[int]) -> dict[str, list[PlayerGame]]:
    kick = {g.game_id: g.kickoff for g in schedule.games}
    out: dict[str, list[PlayerGame]] = {s: [] for s in SERIES}
    for season in seasons:
        for r in csv.DictReader(io.StringIO(fetch_text(STATS_URL.format(season=season)))):
            if r.get("season_type") not in ("REG", "POST") or r["game_id"] not in kick:
                continue
            for stat, (ycol, ocol) in COLUMNS.items():
                yards = int(float(r.get(ycol) or 0))
                opp = int(float(r.get(ocol) or 0))
                if opp <= 0 and yards == 0:
                    continue
                out[stat].append(PlayerGame(
                    player_id=r["player_id"], name=r["player_display_name"],
                    position=r.get("position") or "", team=r["team"],
                    opponent=r["opponent_team"], game_id=r["game_id"],
                    kickoff=kick[r["game_id"]], stat=stat, yards=max(0, yards),
                    opportunities=opp,
                ))
    return out


_TITLE_NAME = re.compile(r"^(.*?)(?::| records? )")


def player_name_from_title(title: str) -> str:
    """'Nico Collins: 50+ receiving yards' and the older 'TreVeyon Henderson
    records 30+ receiving yards' both → the player's name."""
    m = _TITLE_NAME.match(title)
    return (m.group(1) if m else title).strip()


def team_code_of(suffix: str, game: Game) -> str | None:
    """`LACJHERBERT10` → the team whose Kalshi code prefixes it (longest wins:
    LAC before LA)."""
    options = [c for team in (game.home, game.away) for c in kalshi_codes(team)]
    hits = [c for c in options if suffix.startswith(c)]
    if not hits:
        return None
    best = max(hits, key=len)
    return game.home if best in kalshi_codes(game.home) else game.away


def recently_active(pid: str, team: str, t: datetime, schedule: NflSchedule,
                    history: PlayerHistory) -> bool:
    prior = sorted((g for g in schedule.games if g.kickoff < t and team in (g.home, g.away)),
                   key=lambda g: g.kickoff)[-4:]
    ids = {g.game_id for g in prior}
    played = {g.game_id for g in history.player_before(pid, t) if g.game_id in ids and g.opportunities > 0}
    return len(played) >= MIN_RECENT_ACTIVE


def taker_fee(p: float) -> float:
    return float(TAKER_COEFFICIENT) * p * (1 - p)


def market_median(rungs: list[tuple[float, float]]) -> float | None:
    """Where P(over) crosses 0.5 on a ladder of (floor, mid), by interpolation."""
    pts = sorted(rungs)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return x0 + (p0 - 0.5) * (x1 - x0) / (p0 - p1)
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    schedule = NflSchedule.fetch([2024, 2025])
    games_2025 = {g.game_id: g for g in schedule.games if g.season == 2025}
    reports, _ = injuries.load([2025])
    player_games = load_player_games(schedule, [2024, 2025])
    histories = {s: PlayerHistory(v) for s, v in player_games.items()}
    client = KalshiClient(min_interval=0.25)

    evals: list[RungEval] = []
    mae_rows: list[dict] = []
    skipped: dict[str, int] = {}
    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for stat, series in SERIES.items():
        history = histories[stat]
        roster: dict[str, dict[str, str]] = {}
        positions: dict[str, str] = {}
        for g in player_games[stat]:
            roster.setdefault(g.team, {})[normalize_name(g.name)] = g.player_id
            positions[g.player_id] = g.position
        by_key: dict[tuple[str, str, str], list[Quote]] = {}     # (game, player, team)
        for q in client.historical_markets(series_ticker=series):
            game = schedule.for_event(q.event_ticker)
            if game is None or game.game_id not in games_2025 or q.floor_strike is None:
                skip("market without a 2025 game or rung")
                continue
            parts = q.ticker.split("-")
            # Older tickers glue the rung onto the player (…-JACBTHOMAS770), so
            # the player part is always the third, whatever follows it.
            team = team_code_of(parts[2], game) if len(parts) >= 3 else None
            name = player_name_from_title(q.title)
            pid = match_player(name, roster.get(team, {})) if team else None
            if pid is None:
                skip("player not matched to nflverse")
                continue
            by_key.setdefault((game.game_id, pid, team), []).append(q)
        log.info("%s: %d player-games with markets", stat, len(by_key))

        contexts: dict[datetime, LeagueContext] = {}
        size_pairs: dict[tuple[str, int], list[tuple[int, float]]] = {}   # (position, week) → (y, μ)
        actual_of = {(g.game_id, g.player_id): g.yards for g in player_games[stat]}

        for (game_id, pid, team), quotes in sorted(by_key.items(), key=lambda kv: games_2025[kv[0][0]].kickoff):
            game = games_2025[game_id]
            t = game.kickoff - COMMIT_LEAD
            # The team comes from the market ticker, not the player's history:
            # a player traded mid-season has rows for two teams.
            opponent = game.away if team == game.home else game.home
            pos = positions.get(pid, "")
            if not report_public_by(game, t):
                skip("report publication unverifiable (A1c): ineligible")
                continue
            status = reports.status(game.season, game.week, team, pid)
            if status is None or status in OUT_STATUSES:
                skip("no report filed or listed Out/Doubtful")
                continue
            if not recently_active(pid, team, t, schedule, history):
                skip("fewer than 2 of last 4 team games active")
                continue
            ctx = contexts.get(t) or contexts.setdefault(t, LeagueContext.build(history.before(t), t))
            mean = estimate_mean(player_games=history.player_before(pid, t), context=ctx,
                                 position=pos, opponent=opponent)
            if mean is None:
                skip("no position baseline")
                continue
            prior = [pair for (p, w), pairs in size_pairs.items() if p == pos and w < game.week
                     for pair in pairs]
            size = fit_size(prior)
            actual = actual_of.get((game_id, pid))

            ladder = []
            for q in quotes:
                candles = client.candles(series, q.ticker, t - STALE_AFTER, t, 1, settled_at=q.settlement_ts)
                quoted = [c for c in candles if c.end <= t and c.yes_bid_close is not None
                          and c.yes_ask_close is not None]
                if not quoted:
                    continue
                last = max(quoted, key=lambda c: c.end)
                if t - last.end > STALE_AFTER:
                    continue
                bid, ask = float(last.yes_bid_close), float(last.yes_ask_close)
                floor = float(q.floor_strike)
                ladder.append((floor, (bid + ask) / 2))
                evals.append(RungEval(
                    game_id=game_id, week=game.week, stat=stat, player_id=pid, position=pos,
                    floor=floor, p_model=nb_sf(floor, mean, size), bid=bid, ask=ask,
                    settle=None if q.settlement_value is None else float(q.settlement_value),
                    mean=mean, size=size,
                ))
            if actual is not None:
                mae_rows.append({"stat": stat, "mean": mean, "actual": actual,
                                 "market_median": market_median(ladder)})
                size_pairs.setdefault((pos, game.week), []).append((actual, mean))

    report = summarize(evals, mae_rows, skipped)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(f"docs/dev/nfl_props-dev-{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


def summarize(evals: list[RungEval], mae_rows: list[dict], skipped: dict[str, int]) -> dict:
    out: dict = {"skipped": skipped, "n_rungs": len(evals),
                 "n_player_games": len({(e.game_id, e.player_id, e.stat) for e in evals})}

    mae: dict = {}
    for stat in SERIES:
        rows = [r for r in mae_rows if r["stat"] == stat]
        both = [r for r in rows if r["market_median"] is not None]
        if rows:
            mae[stat] = {
                "n": len(rows),
                "mae_model": statistics.mean(abs(r["mean"] - r["actual"]) for r in rows),
                "anchor": MAE_ANCHOR,
                "n_with_market_median": len(both),
                "mae_model_same_games": statistics.mean(abs(r["mean"] - r["actual"]) for r in both) if both else None,
                "mae_market_median": statistics.mean(abs(r["market_median"] - r["actual"]) for r in both) if both else None,
            }
    out["mae"] = mae

    binary = [e for e in evals if e.settle in (0.0, 1.0)]
    out["fair_value_rungs_excluded_from_calibration"] = sum(
        1 for e in evals if e.settle is not None and e.settle not in (0.0, 1.0))
    mid = lambda e: (e.bid + e.ask) / 2
    cal: dict = {"n": len(binary)}
    if binary:
        cal["brier_model"] = statistics.mean((e.p_model - e.settle) ** 2 for e in binary)
        cal["brier_market_mid"] = statistics.mean((mid(e) - e.settle) ** 2 for e in binary)
        cal["mean_abs_model_minus_mid"] = statistics.mean(abs(e.p_model - mid(e)) for e in binary)
        for label, key in (("model", lambda e: e.p_model), ("market", mid)):
            ordered = sorted(binary, key=key)
            cal[f"reliability_{label}"] = [
                {"mean_p": round(statistics.mean(key(e) for e in chunk), 4),
                 "hit_rate": round(statistics.mean(e.settle for e in chunk), 4), "n": len(chunk)}
                for k in range(10)
                if (chunk := ordered[k * len(ordered) // 10:(k + 1) * len(ordered) // 10])
            ]
    out["calibration"] = cal

    def edges(e: RungEval) -> dict[str, tuple[str, float, float]]:
        """side → (side, edge, cost) for taker and maker."""
        no_ask, no_bid = 1 - e.bid, 1 - e.ask
        taker = max((("yes", e.p_model - (e.ask + taker_fee(e.ask)), e.ask + taker_fee(e.ask)),
                     ("no", (1 - e.p_model) - (no_ask + taker_fee(no_ask)), no_ask + taker_fee(no_ask))),
                    key=lambda x: x[1])
        maker = max((("yes", e.p_model - e.bid, e.bid), ("no", (1 - e.p_model) - no_bid, no_bid)),
                    key=lambda x: x[1])
        return {"taker": taker, "maker": maker}

    edge_report: dict = {}
    for mode in ("taker", "maker"):
        values = [edges(e)[mode][1] for e in evals if 0 < e.bid < e.ask < 1]
        best: dict[tuple[str, str, str], tuple[float, RungEval, str, float]] = {}
        for e in evals:
            if not (0 < e.bid < e.ask < 1) or e.ask - e.bid > MAX_SPREAD:
                continue
            side, edge, cost = edges(e)[mode]
            if edge < MARGIN or cost <= 0:
                continue
            key = (e.game_id, e.player_id, e.stat)
            if key not in best or edge > best[key][0]:
                best[key] = (edge, e, side, cost)
        realized = []
        for edge, e, side, cost in best.values():
            if e.settle is None:
                continue
            value = e.settle if side == "yes" else 1 - e.settle
            realized.append((e.game_id, (value - cost) / cost))
        by_game: dict[str, list[float]] = {}
        for g, r in realized:
            by_game.setdefault(g, []).append(r)
        edge_report[mode] = {
            "n_rungs": len(values),
            "edge_max": max(values) if values else None,
            "edge_p99": sorted(values)[int(0.99 * len(values))] if values else None,
            "edge_p90": sorted(values)[int(0.90 * len(values))] if values else None,
            "share_rungs_edge_ge_margin": sum(v >= MARGIN for v in values) / len(values) if values else None,
            "gated_candidates_one_per_player_stat": len(best),
            "gated_mean_R": statistics.mean(r for _, r in realized) if realized else None,
            "gated_mean_R_game_bootstrap_95ci": game_bootstrap(by_game),
            "note": ("maker assumes a fill at the bid; adverse selection is NOT modelled here"
                     if mode == "maker" else "taker at the ask plus the quadratic fee"),
        }
    out["edge"] = edge_report
    out["margin"] = MARGIN
    out["max_spread"] = MAX_SPREAD
    return out


def game_bootstrap(by_game: dict[str, list[float]], draws: int = 5000) -> list[float] | None:
    """95% CI of the mean R, resampling whole games (clustered by game, §2.5)."""
    games = list(by_game.values())
    if len(games) < 2:
        return None
    rng = random.Random(SEED)
    means = []
    for _ in range(draws):
        sample = [r for _ in games for r in rng.choice(games)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return [means[int(0.025 * draws)], means[int(0.975 * draws) - 1]]


if __name__ == "__main__":
    sys.exit(main())
