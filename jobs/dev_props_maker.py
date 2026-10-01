"""Maker test with the pre-registered trade-through rule (props_variants.md).

    python -m jobs.dev_props_maker --variant V2

A resting bid at the side's bid at t, 100 contracts, live from t to kickoff.
Filled only if trades printed strictly through the limit (YES bid:
yes_price < limit; NO bid: yes_price > 1 − limit) totalling ≥ 200 contracts
in [t, kickoff). Trades are Kalshi's own prints, read as-of that window. Props
are `quadratic`: maker fee 0.

Two selections of equal size: the model's (largest maker edge ≥ 4¢ per player
and stat, spread ≤ 8¢) and random (rung, side) pairs (seeded). For each:
fill rate, maker R on fills with a game-clustered CI, and adverse selection
(side outcome and would-be R for filled vs unfilled). Appends to the run log.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jobs.dev_props_variants import EVAL_FROM_WEEK, MARGIN, MAX_SPREAD, SEED, append_log, game_bootstrap
from venues.kalshi.client import KalshiClient

SIZE = 100
FILL_MULTIPLE = 2
TRADES_CACHE = Path(".cache/props_trades.json")


def _ticks(price: float) -> int:
    """Prices in integer hundredths of a cent, so 1 − 0.55 is exactly 0.45.
    In floating point it is 0.44999999999999996, which made a print AT the
    limit count as trading through it — biasing fills upward."""
    return round(price * 10_000)


def fill(side: str, limit: float, prints: list[tuple[float, float]]) -> bool:
    if side == "yes":
        through = sum(c for price, c in prints if _ticks(price) < _ticks(limit))
    else:
        through = sum(c for price, c in prints if _ticks(price) > 10_000 - _ticks(limit))
    return through >= FILL_MULTIPLE * SIZE


def summarize(orders: list[dict]) -> dict:
    filled = [o for o in orders if o["filled"]]
    unfilled = [o for o in orders if not o["filled"]]
    by_game: dict[str, list[float]] = {}
    for o in filled:
        by_game.setdefault(o["game_id"], []).append(o["would_be_R"])
    mean = lambda xs: statistics.mean(xs) if xs else None
    return {
        "n_orders": len(orders), "n_filled": len(filled),
        "fill_rate": len(filled) / len(orders) if orders else None,
        "maker_R_on_fills": mean([o["would_be_R"] for o in filled]),
        "maker_R_ci95_game_clustered": game_bootstrap(by_game),
        "adverse_selection": {
            "filled_mean_side_outcome": mean([o["side_value"] for o in filled]),
            "unfilled_mean_side_outcome": mean([o["side_value"] for o in unfilled]),
            "filled_mean_would_be_R": mean([o["would_be_R"] for o in filled]),
            "unfilled_mean_would_be_R": mean([o["would_be_R"] for o in unfilled]),
            "filled_mean_limit": mean([o["limit"] for o in filled]),
            "unfilled_mean_limit": mean([o["limit"] for o in unfilled]),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    v = ap.parse_args().variant
    preds = [p for p in json.loads(Path(f".cache/props_pred_{v}.json").read_text(encoding="utf-8"))
             if p["week"] >= EVAL_FROM_WEEK and p["settle"] is not None
             and 0 < p["bid"] < p["ask"] < 1 and p["ask"] - p["bid"] <= MAX_SPREAD]

    best: dict[tuple, tuple] = {}
    for p in preds:
        for side, p_side, limit in (("yes", p["p"], p["bid"]), ("no", 1 - p["p"], 1 - p["ask"])):
            edge = p_side - limit
            k = (p["game_id"], p["player_id"], p["stat"])
            if edge >= MARGIN and (k not in best or edge > best[k][0]):
                best[k] = (edge, p, side, limit)
    model_sel = [(p, side, limit) for _, p, side, limit in best.values()]
    rng = random.Random(SEED)
    random_sel = []
    for p in rng.sample(preds, min(len(model_sel), len(preds))):
        side = rng.choice(("yes", "no"))
        random_sel.append((p, side, p["bid"] if side == "yes" else 1 - p["ask"]))

    cache = json.loads(TRADES_CACHE.read_text(encoding="utf-8")) if TRADES_CACHE.exists() else {}
    client = KalshiClient(min_interval=0.25)
    def prints_for(p: dict) -> list[tuple[float, float]]:
        if p["ticker"] not in cache:
            trades = client.trades(
                p["ticker"], datetime.fromisoformat(p["t"]), datetime.fromisoformat(p["kickoff"]),
                settled_at=None if p["settlement_ts"] is None else datetime.fromisoformat(p["settlement_ts"]),
            )
            cache[p["ticker"]] = [(float(t.yes_price), float(t.count)) for t in trades]
        return cache[p["ticker"]]

    results = {}
    for label, sel in (("model", model_sel), ("random", random_sel)):
        orders = []
        for p, side, limit in sel:
            if limit <= 0:
                continue
            side_value = p["settle"] if side == "yes" else 1 - p["settle"]
            orders.append({"game_id": p["game_id"], "limit": limit, "side_value": side_value,
                           "filled": fill(side, limit, prints_for(p)),
                           "would_be_R": (side_value - limit) / limit})
        results[label] = summarize(orders)
    TRADES_CACHE.write_text(json.dumps(cache), encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(f"docs/dev/props-maker-{v}-{stamp}.json")
    out.write_text(json.dumps({"variant": v, **results}, indent=2), encoding="utf-8")
    m, r = results["model"], results["random"]
    fmt = lambda x: None if x is None else round(x, 4)
    append_log(f"maker({v})", "ran",
               f"model: fill {fmt(m['fill_rate'])}, R {fmt(m['maker_R_on_fills'])} CI "
               f"{m['maker_R_ci95_game_clustered'] and [fmt(x) for x in m['maker_R_ci95_game_clustered']]}; "
               f"random: fill {fmt(r['fill_rate'])}, R {fmt(r['maker_R_on_fills'])} CI "
               f"{r['maker_R_ci95_game_clustered'] and [fmt(x) for x in r['maker_R_ci95_game_clustered']]}; `{out.name}`")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
