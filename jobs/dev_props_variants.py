"""Run one props development variant (docs/dev/props_variants.md) on the cache.

    python -m jobs.dev_props_variants --variant V1

Every run, pass or fail, appends a row to the run log in props_variants.md.
Writes docs/dev/props-<variant>-<stamp>.json and per-rung predictions to
.cache/props_pred_<variant>.json for the blend and maker diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from jobs.dev_nfl_props import load_player_games, market_median, taker_fee
from sports.nfl import injuries
from sports.nfl.injuries import OUT_STATUSES, report_public_by
from sports.nfl.props_model import (
    LeagueContext, PlayerHistory, estimate_mean, fit_size, nb_sf,
)
from sports.nfl.props_variants import (
    environment_factor, fit_calibrator, opportunity_shares, usage_mean, vacated_factor,
)
from sports.nfl.schedule import GAMES_CSV_URL, NflSchedule, _get_text

CACHE = Path(".cache/props_2025.json")
LOG = Path("docs/dev/props_variants.md")
EVAL_FROM_WEEK = 9
MARGIN, MAX_SPREAD, SEED, DRAWS = 0.04, 0.08, 20260930, 2000
VARIANTS = ("V0", "V1", "V2", "V3", "V4")
log = logging.getLogger("valemont.dev_props_variants")


def game_bootstrap(by_game: dict[str, list[float]]) -> list[float] | None:
    games = list(by_game.values())
    if len(games) < 2:
        return None
    rng = random.Random(SEED)
    means = []
    for _ in range(DRAWS):
        sample = [r for _ in games for r in rng.choice(games)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return [means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]]


def append_log(variant: str, result: str, notes: str) -> None:
    text = LOG.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ") and line[2:3].isdigit()]
    n = len(rows) + 1
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    LOG.write_text(text.rstrip("\n") + f"\n| {n} | {stamp} | {variant} | {result} | {notes} |\n",
                   encoding="utf-8")


def league_points_before(t: datetime, scores: list[tuple[datetime, float, float]]) -> float | None:
    pts = [p for k, h, a in scores if k < t for p in (h, a)]
    return sum(pts) / len(pts) if pts else None


def run(variant: str) -> dict:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    schedule = NflSchedule.fetch([2024, 2025])
    games = {g.game_id: g for g in schedule.games}
    reports, _ = injuries.load([2025])
    player_games = load_player_games(schedule, [2024, 2025])
    histories = {s: PlayerHistory(v) for s, v in player_games.items()}
    actual = {(g.stat, g.game_id, g.player_id): g.yards for s in player_games.values() for g in s}
    scores = []
    for r in csv.DictReader(io.StringIO(_get_text(GAMES_CSV_URL))):
        g = games.get(r["game_id"])
        if g is not None and r.get("home_score") not in ("", "NA", None):
            scores.append((g.kickoff, float(r["home_score"]), float(r["away_score"])))

    groups: dict[tuple[str, str, str], list[dict]] = {}
    for rung in cache["rungs"]:
        groups.setdefault((rung["stat"], rung["game_id"], rung["player_id"]), []).append(rung)
    ordered = sorted(groups.items(), key=lambda kv: kv[1][0]["kickoff"])

    contexts: dict[tuple[str, str], LeagueContext] = {}
    size_pairs: dict[tuple[str, str, int], list[tuple[int, float]]] = {}
    preds: list[dict] = []
    mae_rows: list[dict] = []
    env_missing = 0

    for (stat, game_id, pid), rungs in ordered:
        game = games[game_id]
        r0 = rungs[0]
        t = datetime.fromisoformat(r0["t"])
        history = histories[stat]
        key = (stat, r0["t"])
        if key not in contexts:
            contexts[key] = LeagueContext.build(history.before(t), t)
        ctx = contexts[key]
        pos, opp_team, team = r0["position"], r0["opponent"], r0["team"]
        mine = history.player_before(pid, t)
        if variant == "V0":
            mean = estimate_mean(player_games=mine, context=ctx, position=pos, opponent=opp_team)
        else:
            scale = 1.0
            if variant == "V4" and stat in ("rec", "rush") and report_public_by(game, t):
                team_before = [g for g in history.before(t) if g.team == team]
                shares = opportunity_shares(team, stat, team_before, t)
                out = {p for p in shares
                       if reports.status(game.season, game.week, team, p) in OUT_STATUSES}
                scale = vacated_factor(pid, shares, out)
            mean = usage_mean(player_games=mine, league_before=history.before(t), context=ctx,
                              position=pos, opponent=opp_team, opp_scale=scale)
            if mean is not None and variant in ("V3", "V4"):
                env = cache["env"].get(game_id, {})
                implied = None
                if env.get("total_mu") is not None and env.get("margin_mu") is not None:
                    tot, mar = env["total_mu"], env["margin_mu"]
                    implied = (tot + mar) / 2 if team == game.home else (tot - mar) / 2
                else:
                    env_missing += 1
                mean *= environment_factor(implied, league_points_before(t, scores))
        if mean is None:
            continue
        prior = [pair for (s, p, w), pairs in size_pairs.items()
                 if s == stat and p == pos and w < game.week for pair in pairs]
        size = fit_size(prior)
        act = actual.get((stat, game_id, pid))
        ladder = []
        for r in rungs:
            p = nb_sf(r["floor"], mean, size)
            preds.append({**r, "p_raw": p, "mean": mean, "size": size})
            ladder.append((r["floor"], (r["bid"] + r["ask"]) / 2))
        if act is not None:
            mae_rows.append({"stat": stat, "week": game.week, "mean": mean, "actual": act,
                             "market_median": market_median(ladder)})
            size_pairs.setdefault((stat, pos, game.week), []).append((act, mean))

    # Calibration layer (V2+): per stat, fitted on earlier weeks only.
    for pr in preds:
        pr["p"] = pr["p_raw"]
    if variant in ("V2", "V3", "V4"):
        weeks = sorted({pr["week"] for pr in preds})
        for stat in ("pass", "rush", "rec"):
            for w in weeks:
                train = [(pr["p_raw"], pr["settle"]) for pr in preds
                         if pr["stat"] == stat and pr["week"] < w and pr["settle"] in (0.0, 1.0)]
                cal = fit_calibrator(train)
                for pr in preds:
                    if pr["stat"] == stat and pr["week"] == w:
                        pr["p"] = cal(pr["p_raw"])

    ev = [pr for pr in preds if pr["week"] >= EVAL_FROM_WEEK]
    report: dict = {"variant": variant, "eval_from_week": EVAL_FROM_WEEK,
                    "n_rungs_eval": len(ev), "environment_missing_player_games": env_missing}
    mae = {}
    for stat in ("pass", "rush", "rec"):
        rows = [r for r in mae_rows if r["stat"] == stat and r["week"] >= EVAL_FROM_WEEK
                and r["market_median"] is not None]
        if rows:
            mae[stat] = {"n": len(rows),
                         "mae_model": statistics.mean(abs(r["mean"] - r["actual"]) for r in rows),
                         "mae_market_median": statistics.mean(abs(r["market_median"] - r["actual"]) for r in rows)}
    report["mae_same_games"] = mae
    binary = [pr for pr in ev if pr["settle"] in (0.0, 1.0)]
    mid = lambda pr: (pr["bid"] + pr["ask"]) / 2
    brier = {"overall": {"n": len(binary),
                         "model": statistics.mean((pr["p"] - pr["settle"]) ** 2 for pr in binary),
                         "market_mid": statistics.mean((mid(pr) - pr["settle"]) ** 2 for pr in binary)}}
    for stat in ("pass", "rush", "rec"):
        b = [pr for pr in binary if pr["stat"] == stat]
        if b:
            brier[stat] = {"n": len(b), "model": statistics.mean((pr["p"] - pr["settle"]) ** 2 for pr in b),
                           "market_mid": statistics.mean((mid(pr) - pr["settle"]) ** 2 for pr in b)}
    report["brier"] = brier
    ordered_b = sorted(binary, key=lambda pr: pr["p"])
    report["reliability_model"] = [
        {"mean_p": round(statistics.mean(pr["p"] for pr in c), 4),
         "hit_rate": round(statistics.mean(pr["settle"] for pr in c), 4), "n": len(c)}
        for k in range(10) if (c := ordered_b[k * len(ordered_b) // 10:(k + 1) * len(ordered_b) // 10])
    ]
    best: dict[tuple, tuple] = {}
    for pr in ev:
        if not (0 < pr["bid"] < pr["ask"] < 1) or pr["ask"] - pr["bid"] > MAX_SPREAD:
            continue
        no_ask = 1 - pr["bid"]
        for side, p_side, price in (("yes", pr["p"], pr["ask"]), ("no", 1 - pr["p"], no_ask)):
            cost = price + taker_fee(price)
            edge = p_side - cost
            k = (pr["game_id"], pr["player_id"], pr["stat"])
            if edge >= MARGIN and (k not in best or edge > best[k][0]):
                best[k] = (edge, pr, side, cost)
    by_game: dict[str, list[float]] = {}
    for edge, pr, side, cost in best.values():
        if pr["settle"] is None:
            continue
        value = pr["settle"] if side == "yes" else 1 - pr["settle"]
        by_game.setdefault(pr["game_id"], []).append((value - cost) / cost)
    flat = [r for v in by_game.values() for r in v]
    report["taker"] = {"n": len(flat), "mean_R": statistics.mean(flat) if flat else None,
                       "ci95_game_clustered": game_bootstrap(by_game)}
    Path(f".cache/props_pred_{variant}.json").write_text(json.dumps(preds), encoding="utf-8")
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=VARIANTS, required=True)
    v = ap.parse_args().variant
    try:
        report = run(v)
    except Exception as exc:
        append_log(v, "FAILED (error)", f"{type(exc).__name__}: {str(exc)[:120]}")
        traceback.print_exc()
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(f"docs/dev/props-{v}-{stamp}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    b, t = report["brier"]["overall"], report["taker"]
    ci = t["ci95_game_clustered"]
    append_log(v, "ran", f"Brier {b['model']:.5f} vs mid {b['market_mid']:.5f} (n={b['n']}); "
                         f"taker R {t['mean_R'] if t['mean_R'] is None else round(t['mean_R'], 4)} "
                         f"CI {None if ci is None else [round(x, 4) for x in ci]} (n={t['n']}); `{out.name}`")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
