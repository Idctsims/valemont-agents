"""The pre-registered props holdout: frozen V1 blend (A4) and P2 (A5), run once.

    python -m jobs.holdout_nfl_props --execute

docs/preregistration_nfl.md §7. Same holdout as `nfl_ml`: 2026 weeks 1–3
minus the three excluded week-3 games (45), every eligible yardage-prop
player-game in them, priced at t = kickoff − 75 min.

**Run once.** Refuses without `--execute`, if any props holdout output exists,
if the game count is not 45, or if an excluded game is in scope. Before any
scoring it runs the props, injury-timing and maker-fill tests (H1). Results go
to `docs/backtests/`, never the ledger.

**Inputs from development, hashed into the report.** The 2025 base-rate pool
(eligible rungs and settlements, `.cache/props_2025.json`) and V1's 2025
means for the negative-binomial size fit (`.cache/props_pred_V1.json`) are
rebuildable with `jobs.props_cache` and `jobs.dev_props_variants --variant V1`.
Their SHA-256 is recorded so the report states exactly what it used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import statistics
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final, Sequence

from jobs.dev_nfl_props import (
    COMMIT_LEAD, SERIES, load_player_games, player_name_from_title, recently_active,
    taker_fee, team_code_of,
)
from jobs.dev_props_blend import w_star
from jobs.dev_props_maker import fill
from jobs.holdout_nfl_ml import EXCLUDED_GAMES, EXPECTED_GAMES, HOLDOUT_SEASON, HOLDOUT_WEEKS
from jobs.props_cache import quote_at
from sports.nfl import injuries
from sports.nfl.injuries import OUT_STATUSES, report_public_by
from sports.nfl.props_model import LeagueContext, PlayerHistory, fit_size, match_player, nb_sf, normalize_name
from sports.nfl.props_variants import usage_mean
from sports.nfl.schedule import NflSchedule
from venues.kalshi.client import KalshiClient
from core.preregistration import PreregistrationMissing, require_registered

log = logging.getLogger("valemont.holdout_nfl_props")

#: The frozen text this runner executes; it refuses unless DB-stamped first (db/017).
PREREG_DOC: Final = "docs/preregistration_nfl.md"
PREREG_SECTION: Final = "## 7. Props amendments (A4, A5) — committed 2026-10-01 before further analysis"

# --- frozen by A4 / A5 -------------------------------------------------------
W_BLEND: Final = 0.30
K_FLAT: Final = 0.145
MARGIN: Final = 0.04
MAX_SPREAD: Final = 0.08
BASE_WINDOW_YDS: Final = 5.0
BASE_MIN_N: Final = 20
BUCKETS: Final = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))
SEED: Final = 20260930
DRAWS: Final = 2000

DEV_RUNGS: Final = Path(".cache/props_2025.json")
DEV_PRED_V1: Final = Path(".cache/props_pred_V1.json")
OUTPUT_DIR: Final = Path("docs/backtests")
OUTPUT_STEM: Final = "nfl_props-holdout"
H1_TESTS: Final = ("tests.test_props_variants", "tests.test_nfl_props_model",
                   "tests.test_nfl_injury_timing")


# ---------------------------------------------------------------------------
# Pure pieces (tested without running the holdout)
# ---------------------------------------------------------------------------

def base_rate(pool: Sequence[tuple[str, float, float, datetime]], stat: str, floor: float,
              t: datetime) -> float:
    """Mean binary settlement of same-stat rungs within ±5 yards whose
    settlement was public before t; 0.5 with fewer than 20.

    Pool entries are (stat, floor, settle, settled_at). Filtering on the
    settlement time, not the kickoff, matters: a 1 p.m. game has kicked off
    but not settled at a 4:25 game's commit instant."""
    hits = [s for st, f, s, settled_at in pool
            if st == stat and abs(f - floor) <= BASE_WINDOW_YDS and settled_at < t]
    return sum(hits) / len(hits) if len(hits) >= BASE_MIN_N else 0.5


def p_blend(p_v1: float, mid: float) -> float:
    return W_BLEND * p_v1 + (1 - W_BLEND) * mid


def p_p2(mid: float, base: float) -> float:
    return (1 - K_FLAT) * mid + K_FLAT * base


def bucket_of(mid: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= mid < hi:
            return f"[{lo:.1f}, {min(hi, 1.0):.1f}{')' if hi < 1 else ']'}"
    raise ValueError(mid)


def select(rows: Sequence[dict], forecast: str, mode: str) -> list[tuple[dict, str, float, float]]:
    """Per (player, stat): the largest-edge (rung, side) with edge ≥ 4¢ and
    spread ≤ 8¢. Returns (row, side, price, cost); taker cost = ask + fee,
    maker cost = limit (the side's bid)."""
    best: dict[tuple, tuple] = {}
    for r in rows:
        if not (0 < r["bid"] < r["ask"] < 1) or r["ask"] - r["bid"] > MAX_SPREAD:
            continue
        p = r[forecast]
        if mode == "taker":
            sides = (("yes", p, r["ask"], r["ask"] + taker_fee(r["ask"])),
                     ("no", 1 - p, 1 - r["bid"], (1 - r["bid"]) + taker_fee(1 - r["bid"])))
        else:
            sides = (("yes", p, r["bid"], r["bid"]), ("no", 1 - p, 1 - r["ask"], 1 - r["ask"]))
        for side, p_side, price, cost in sides:
            edge = p_side - cost
            k = (r["game_id"], r["player_id"], r["stat"])
            if edge >= MARGIN and (k not in best or edge > best[k][0]):
                best[k] = (edge, r, side, price, cost)
    return [(r, side, price, cost) for _, r, side, price, cost in best.values()]


def r_of(row: dict, side: str, cost: float) -> float | None:
    if row["settle"] is None or cost <= 0:
        return None
    value = row["settle"] if side == "yes" else 1 - row["settle"]
    return (value - cost) / cost


def game_ci(by_game: dict[str, list[float]]) -> list[float] | None:
    games = list(by_game.values())
    if len(games) < 2:
        return None
    rng = random.Random(SEED)
    means = []
    for _ in range(DRAWS):
        sample = [x for _ in games for x in rng.choice(games)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return [means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]]


def weight_comparison(rows: Sequence[dict]) -> dict:
    """Closed-form blend weights vs the mid for V1 and the base rate, and a
    paired game bootstrap of their difference (A4's continue rule)."""
    per: dict[str, list[float]] = {}
    for r in rows:
        if r["settle"] not in (0.0, 1.0):
            continue
        e = r["mid"] - r["settle"]
        d1, d2 = r["p_v1"] - r["mid"], r["base"] - r["mid"]
        a = per.setdefault(r["game_id"], [0.0] * 5)
        a[0] += e * e; a[1] += e * d1; a[2] += d1 * d1; a[3] += e * d2; a[4] += d2 * d2
    games = list(per.values())
    if len(games) < 2:
        return {"n_games": len(games), "continue": False, "reason": "too few games"}
    tot = [sum(g[i] for g in games) for i in range(5)]
    w1, w2 = w_star(tot[0], tot[1], tot[2]), w_star(tot[0], tot[3], tot[4])
    rng = random.Random(SEED)
    diffs = []
    for _ in range(DRAWS):
        s = [0.0] * 5
        for _ in games:
            g = rng.choice(games)
            for i in range(5):
                s[i] += g[i]
        diffs.append(w_star(s[0], s[1], s[2]) - w_star(s[0], s[3], s[4]))
    diffs.sort()
    ci = [diffs[int(0.025 * DRAWS)], diffs[int(0.975 * DRAWS) - 1]]
    return {"n_games": len(games), "w_v1": w1, "w_base": w2, "w_diff": w1 - w2,
            "w_diff_ci95": ci, "continue": ci[0] > 0}


def refuse_reasons(games: Sequence, out_dir: Path) -> list[str]:
    reasons = []
    existing = sorted(out_dir.glob(f"{OUTPUT_STEM}*")) if out_dir.exists() else []
    if existing:
        reasons.append(f"props holdout output already exists ({existing[0]}); it runs once")
    leaked = EXCLUDED_GAMES & {g.game_id for g in games}
    if leaked:
        reasons.append(f"excluded games in scope: {sorted(leaked)}")
    if len(games) != EXPECTED_GAMES:
        reasons.append(f"expected {EXPECTED_GAMES} holdout games, found {len(games)}")
    for path in (DEV_RUNGS, DEV_PRED_V1):
        if not path.exists():
            reasons.append(f"missing development input {path}")
    return reasons


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true")
    if not ap.parse_args(argv).execute:
        ap.error("refusing without --execute: the props holdout runs once")

    schedule = NflSchedule.fetch([2024, 2025, HOLDOUT_SEASON])
    games = sorted((g for g in schedule.games if g.season == HOLDOUT_SEASON
                    and g.week in HOLDOUT_WEEKS and g.game_id not in EXCLUDED_GAMES),
                   key=lambda g: g.kickoff)
    reasons = refuse_reasons(games, OUTPUT_DIR)
    if reasons:
        for r in reasons:
            log.error("refusing: %s", r)
        return 2
    try:
        prereg = require_registered(PREREG_DOC, PREREG_SECTION)
    except PreregistrationMissing as exc:
        log.error("refusing: %s", exc)
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(H1_TESTS))
    h1 = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    if not h1.wasSuccessful():
        log.error("H1 failed before scoring")
        return 1
    hashes = {str(DEV_RUNGS): sha256(DEV_RUNGS), str(DEV_PRED_V1): sha256(DEV_PRED_V1)}

    by_id = {g.game_id: g for g in schedule.games}
    holdout_ids = {g.game_id for g in games}
    dev_rungs = json.loads(DEV_RUNGS.read_text(encoding="utf-8"))["rungs"]
    pool = [(r["stat"], r["floor"], r["settle"], datetime.fromisoformat(r["settlement_ts"]))
            for r in dev_rungs if r["settle"] in (0.0, 1.0) and r.get("settlement_ts")]
    reports, _ = injuries.load([2025, HOLDOUT_SEASON])
    player_games = load_player_games(schedule, [2024, 2025, HOLDOUT_SEASON])
    actual = {(g.stat, g.game_id, g.player_id): g.yards for s in player_games.values() for g in s}
    # NB size pairs, week-level as in development: a game's yards count only
    # for strictly later weeks (a same-day earlier game is not final at t).
    pairs: dict[tuple[str, str], list[tuple[int, float, tuple[int, int]]]] = {}
    seen_dev = set()
    for pr in json.loads(DEV_PRED_V1.read_text(encoding="utf-8")):
        key = (pr["stat"], pr["game_id"], pr["player_id"])
        if key in seen_dev or key not in actual:
            continue
        seen_dev.add(key)
        g = by_id.get(pr["game_id"])
        if g is not None:
            pairs.setdefault((pr["stat"], pr["position"]), []).append(
                (actual[key], pr["mean"], (g.season, g.week)))

    client = KalshiClient(min_interval=0.25)
    rows: list[dict] = []
    skipped: dict[str, int] = {}
    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for stat, series in SERIES.items():
        history = PlayerHistory(player_games[stat])
        roster: dict[str, dict[str, str]] = {}
        positions = {}
        for g in player_games[stat]:
            roster.setdefault(g.team, {})[normalize_name(g.name)] = g.player_id
            positions[g.player_id] = g.position
        groups: dict[tuple[str, str, str], list] = {}
        for q in client.markets(series_ticker=series):
            game = schedule.for_event(q.event_ticker)
            if game is None or game.game_id not in holdout_ids or q.floor_strike is None:
                continue
            parts = q.ticker.split("-")
            team = team_code_of(parts[2], game) if len(parts) >= 3 else None
            pid = match_player(player_name_from_title(q.title), roster.get(team, {})) if team else None
            if pid is None:
                skip("player not matched"); continue
            groups.setdefault((game.game_id, pid, team), []).append(q)
        for (game_id, pid, team), quotes in sorted(groups.items(), key=lambda kv: by_id[kv[0][0]].kickoff):
            game = by_id[game_id]
            t = game.kickoff - COMMIT_LEAD
            if not report_public_by(game, t):
                skip("A1c unverifiable"); continue
            status = reports.status(game.season, game.week, team, pid)
            if status is None or status in OUT_STATUSES:
                skip("no report / Out / Doubtful"); continue
            if not recently_active(pid, team, t, schedule, history):
                skip("not recently active"); continue
            pos = positions.get(pid, "")
            opp = game.away if team == game.home else game.home
            ctx = LeagueContext.build(history.before(t), t)
            mean = usage_mean(player_games=history.player_before(pid, t), league_before=history.before(t),
                              context=ctx, position=pos, opponent=opp)
            if mean is None:
                skip("no V1 mean"); continue
            size = fit_size([(y, m) for y, m, wk in pairs.get((stat, pos), [])
                             if wk < (game.season, game.week)])
            for q in quotes:
                ba = quote_at(client, series, q, t)
                if ba is None:
                    skip("stale quote"); continue
                bid, ask = ba
                mid = (bid + ask) / 2
                p_v1 = nb_sf(float(q.floor_strike), mean, size)
                rows.append({
                    "stat": stat, "series": series, "ticker": q.ticker, "game_id": game_id,
                    "week": game.week, "player_id": pid, "t": t.isoformat(),
                    "kickoff": game.kickoff.isoformat(), "floor": float(q.floor_strike),
                    "bid": bid, "ask": ask, "mid": mid, "bucket": bucket_of(mid),
                    "settle": None if q.settlement_value is None else float(q.settlement_value),
                    "settlement_ts": None if q.settlement_ts is None else q.settlement_ts.isoformat(),
                    "p_v1": p_v1, "p_blend": p_blend(p_v1, mid), "mean": mean, "size": size,
                })
            act = actual.get((stat, game_id, pid))
            if act is not None:
                pairs.setdefault((stat, pos), []).append((act, mean, (game.season, game.week)))

    # Second pass: base rate and P2, with holdout rungs joining the pool only
    # once their settlement was public (base_rate filters on settled_at < t).
    pool += [(r["stat"], r["floor"], r["settle"], datetime.fromisoformat(r["settlement_ts"]))
             for r in rows if r["settle"] in (0.0, 1.0) and r["settlement_ts"]]
    for r in rows:
        r["base"] = base_rate(pool, r["stat"], r["floor"], datetime.fromisoformat(r["t"]))
        r["p_p2"] = p_p2(r["mid"], r["base"])

    def trades(sel: list) -> dict:
        taker_by_game: dict[str, list[float]] = {}
        for r, side, price, cost in sel:
            x = r_of(r, side, cost)
            if x is not None:
                taker_by_game.setdefault(r["game_id"], []).append(x)
        flat = [x for v in taker_by_game.values() for x in v]
        return {"n": len(flat), "mean_R": statistics.mean(flat) if flat else None,
                "ci95": game_ci(taker_by_game)}

    def maker(sel: list) -> dict:
        by_game: dict[str, list[float]] = {}
        n_orders = n_filled = 0
        for r, side, limit, _ in sel:
            if limit <= 0:
                continue
            n_orders += 1
            prints = [(float(t.yes_price), float(t.count)) for t in client.trades(
                r["ticker"], datetime.fromisoformat(r["t"]), datetime.fromisoformat(r["kickoff"]))]
            if fill(side, limit, prints):
                n_filled += 1
                x = r_of(r, side, limit)
                if x is not None:
                    by_game.setdefault(r["game_id"], []).append(x)
        flat = [x for v in by_game.values() for x in v]
        return {"n_orders": n_orders, "n_filled": n_filled,
                "fill_rate": n_filled / n_orders if n_orders else None,
                "mean_R_on_fills": statistics.mean(flat) if flat else None, "ci95": game_ci(by_game)}

    binary = [r for r in rows if r["settle"] in (0.0, 1.0)]
    brier = lambda key: statistics.mean((r[key] - r["settle"]) ** 2 for r in binary) if binary else None
    blend_taker, p2_taker = select(rows, "p_blend", "taker"), select(rows, "p_p2", "taker")
    blend_maker, p2_maker = select(rows, "p_blend", "maker"), select(rows, "p_p2", "maker")
    weights = weight_comparison(rows)
    p2_taker_result = trades(p2_taker)
    by_bucket = {}
    for lo, hi in BUCKETS:
        label = bucket_of(lo)
        by_bucket[label] = {"taker": trades([x for x in p2_taker if x[0]["bucket"] == label]),
                            "maker": maker([x for x in p2_maker if x[0]["bucket"] == label])}
    p2_ci = p2_taker_result["ci95"]
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(), "preregistration": prereg, "games": len(games),
        "rungs": len(rows), "binary_rungs": len(binary), "skipped": skipped,
        "dev_input_sha256": hashes,
        "brier": {"mid": brier("mid"), "blend": brier("p_blend"), "p2": brier("p_p2"),
                  "v1_alone": brier("p_v1")},
        "A4_blend": {"weights": weights, "taker": trades(blend_taker), "maker": maker(blend_maker),
                     "pass_continue_rule": weights.get("continue", False)},
        "A5_P2": {"taker": p2_taker_result, "maker": maker(p2_maker), "by_bucket": by_bucket,
                  "pass": p2_ci is not None and p2_ci[0] > 0},
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.json").write_text(json.dumps(report, indent=2, default=str),
                                                            encoding="utf-8")
    with (OUTPUT_DIR / f"{OUTPUT_STEM}-{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
